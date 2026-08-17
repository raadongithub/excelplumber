"""Column profiling: decide dtype, locale, unit, and date order once per
column from a bounded sample, then freeze the decision.

Cells that fail their frozen profile keep their raw string and emit a
per-cell warning; they are never silently re-guessed.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from ..config import ParseOptions
from ..models import ParseWarning, WarningCode
from ..normalizers import REGISTRY as NORMALIZER_REGISTRY
from ..normalizers.dates import (
    looks_like_date,
    parse_date,
    resolve_day_order,
    serial_to_date,
)
from ..normalizers.numbers import (
    extract_symbol,
    extract_unit,
    parse_number,
    parse_percent,
    sniff_locale,
)
from ..normalizers.text import clean, is_na, strip_footnote


@dataclass
class ColumnProfile:
    """A frozen per-column decision."""

    name: str
    dtype: str  # int | decimal | date | bool | text
    unit: str | None = None
    locale: str = "us"
    dayfirst: bool = False
    percent_convention: str | None = None  # "symbol" or "fraction"
    warnings: list[ParseWarning] = field(default_factory=list)


_BOOL = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False}


def _classify(value: object, locale: str) -> tuple[str, str | None]:
    """Classify one sample cell: (kind, unit) where unit may be a currency
    code, a physical unit, or '%'."""
    if isinstance(value, bool):
        return "bool", None
    if isinstance(value, (datetime.date, datetime.datetime)):
        return "date", None
    if isinstance(value, int):
        return "int", None
    if isinstance(value, (float, Decimal)):
        return "decimal", None
    s = clean(value)
    if not s or is_na(s):
        return "na", None
    if s.lower() in _BOOL:
        return "bool", None
    stripped, marker = strip_footnote(s)
    body, sym = extract_symbol(stripped)
    body2, unit = extract_unit(body)
    unit = sym or unit
    if looks_like_date(body2):
        return "date", None
    n = parse_number(body2, locale)
    if n is None:
        return "text", None
    # Leading zeros pin the column to text: the raw string is authoritative.
    core = body2.lstrip("+-")
    if core.startswith("0") and len(core) > 1 and not core.startswith("0.") and not core.startswith("0,"):
        return "id_text", None
    kind = "int" if isinstance(n, int) else "decimal"
    return kind, unit


def profile_column(
    name: str,
    samples: list[object],
    opts: ParseOptions,
    sheet: str,
) -> ColumnProfile:
    """Freeze a column's dtype, locale, unit, and date-order decision."""
    warnings: list[ParseWarning] = []
    str_samples = [clean(v) for v in samples if isinstance(v, str) and clean(v) and not is_na(clean(v))]
    locale, loc_conf = sniff_locale(
        [extract_unit(extract_symbol(strip_footnote(s)[0])[0])[0] for s in str_samples]
    )
    ambiguous_locale = locale is None
    if locale is None:
        locale = opts.locale

    kinds: dict[str, int] = {}
    units: dict[str, int] = {}
    pct = 0
    nonnull = 0
    for v in samples:
        kind, unit = _classify(v, locale)
        if kind == "na":
            continue
        nonnull += 1
        if unit == "%":
            pct += 1
        elif unit:
            units[unit] = units.get(unit, 0) + 1
        kinds[kind] = kinds.get(kind, 0) + 1
    if nonnull == 0:
        return ColumnProfile(name=name, dtype="text", locale=locale)

    if kinds.get("id_text", 0) > 0 and kinds.get("id_text", 0) + kinds.get("int", 0) >= nonnull * 0.9:
        warnings.append(
            ParseWarning(
                WarningCode.LEADING_ZERO_PRESERVED,
                f"column {name!r} has leading-zero values; kept as text",
                sheet=sheet,
                column=name,
            )
        )
        return ColumnProfile(name=name, dtype="text", locale=locale, warnings=warnings)

    numeric = kinds.get("int", 0) + kinds.get("decimal", 0)
    best_kind, best_count = max(kinds.items(), key=lambda kv: kv[1])
    if best_kind in ("int", "decimal"):
        best_count = numeric
        best_kind = "decimal" if kinds.get("decimal", 0) else "int"

    if best_count / nonnull < opts.dtype_threshold:
        warnings.append(
            ParseWarning(
                WarningCode.MIXED_TYPE,
                f"column {name!r} is {best_count}/{nonnull} {best_kind}; kept as text",
                confidence=best_count / nonnull,
                sheet=sheet,
                column=name,
            )
        )
        return ColumnProfile(name=name, dtype="text", locale=locale, warnings=warnings)

    dtype = best_kind
    unit: str | None = None
    percent_convention: str | None = None

    if dtype in ("int", "decimal"):
        if ambiguous_locale and any("," in s or "." in s for s in str_samples):
            warnings.append(
                ParseWarning(
                    WarningCode.AMBIGUOUS_NUMBER_LOCALE,
                    f"column {name!r} separators are ambiguous; assuming "
                    f"{'1,234.56' if locale == 'us' else '1.234,56'}",
                    confidence=0.5,
                    sheet=sheet,
                    column=name,
                )
            )
        if pct >= nonnull * 0.5:
            unit = "%"
            percent_convention = "symbol"
        if unit is None and units:
            top_unit, top_count = max(units.items(), key=lambda kv: kv[1])
            if top_count >= nonnull * 0.9:
                unit = top_unit
            elif len(units) > 1:
                warnings.append(
                    ParseWarning(
                        WarningCode.MIXED_CURRENCY,
                        f"column {name!r} mixes units/currencies {sorted(units)}; "
                        "values kept per cell",
                        confidence=0.6,
                        sheet=sheet,
                        column=name,
                    )
                )

    dayfirst = False
    if dtype == "date":
        date_strs = [s for s in str_samples if looks_like_date(s)]
        dayfirst, ambiguous = resolve_day_order(date_strs)
        if ambiguous:
            warnings.append(
                ParseWarning(
                    WarningCode.AMBIGUOUS_DATE_FORMAT,
                    f"column {name!r} dates are MM/DD vs DD/MM ambiguous; "
                    "assuming month-first",
                    confidence=0.5,
                    sheet=sheet,
                    column=name,
                )
            )

    return ColumnProfile(
        name=name,
        dtype=dtype,
        unit=unit,
        locale=locale,
        dayfirst=dayfirst,
        percent_convention=percent_convention,
        warnings=warnings,
    )


def normalize_cell(
    value: object,
    profile: ColumnProfile,
    sheet: str,
    row: int,
) -> tuple[object, ParseWarning | None]:
    """Coerce one cell under its column's frozen profile.

    Returns:
        The normalized value and an optional warning. A cell that does
        not conform keeps its cleaned raw string.
    """
    custom = NORMALIZER_REGISTRY.get(profile.dtype)
    if custom is not None:
        return custom(value, {"profile": profile, "sheet": sheet, "row": row}), None
    if value is None:
        return None, None
    if isinstance(value, str):
        s = clean(value)
        if not s or is_na(s):
            return None, None
    if profile.dtype == "text":
        return clean(value) if isinstance(value, str) else _canonical(value), None
    if profile.dtype == "bool":
        if isinstance(value, bool):
            return value, None
        b = _BOOL.get(clean(value).lower())
        if b is not None:
            return b, None
        return _nonconform(value, profile, sheet, row)
    if profile.dtype == "date":
        if isinstance(value, datetime.datetime):
            return value.date() if value.time() == datetime.time.min else value, None
        if isinstance(value, datetime.date):
            return value, None
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            d = serial_to_date(value)
            if d is not None:
                return d, None
            return _nonconform(value, profile, sheet, row)
        d = parse_date(clean(value), profile.dayfirst)
        if d is not None:
            return d, None
        return _nonconform(value, profile, sheet, row)
    # int / decimal
    if isinstance(value, bool):
        return _nonconform(value, profile, sheet, row)
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        d = Decimal(str(value))
        return int(d) if d == d.to_integral_value() else d, None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else value, None
    s = clean(value)
    stripped, marker = strip_footnote(s)
    body, _sym = extract_symbol(stripped)
    if profile.unit == "%":
        n = parse_percent(body, profile.locale)
        if n is None:
            body2, u = extract_unit(body)
            n = parse_number(body2, profile.locale) if u in (None, "%") else None
    else:
        body2, _u = extract_unit(body)
        n = parse_number(body2, profile.locale)
    if n is None:
        return _nonconform(value, profile, sheet, row)
    warning = None
    if marker:
        warning = ParseWarning(
            WarningCode.FOOTNOTE_MARKER_STRIPPED,
            f"footnote marker {marker!r} stripped",
            sheet=sheet,
            row=row,
            column=profile.name,
        )
    if profile.dtype == "int" and isinstance(n, Decimal):
        return n, warning
    return n, warning


def _nonconform(
    value: object, profile: ColumnProfile, sheet: str, row: int
) -> tuple[object, ParseWarning]:
    raw = clean(value) if isinstance(value, str) else _canonical(value)
    return raw, ParseWarning(
        WarningCode.NONCONFORMING_CELL,
        f"value {raw!r} does not conform to column dtype {profile.dtype!r}; raw kept",
        confidence=1.0,
        sheet=sheet,
        row=row,
        column=profile.name,
    )


def _canonical(value: object) -> object:
    if isinstance(value, float):
        d = Decimal(str(value))
        return int(d) if d == d.to_integral_value() else d
    return value
