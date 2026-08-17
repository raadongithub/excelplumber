"""Header resolution: title rows, multi-row header spans, flattening,
merged headers, footers, and header hygiene."""

import re
from dataclasses import dataclass, field

from ..config import ParseOptions
from ..models import ParseWarning, WarningCode
from ..normalizers.dates import looks_like_date
from ..readers.base import Cell
from .boundaries import TableRegion

_FOOTER_KEY = re.compile(
    r"^\s*(total|subtotal|sum|grand\s+total|average|avg|note[s]?|source|\*+)\b",
    re.IGNORECASE,
)

_NUMERIC_RE = re.compile(r"^[\s$€£¥+-]*[\d.,]+\s*%?\s*$")


@dataclass
class ResolvedHeader:
    """The outcome of header detection for one region."""

    names: list[str]
    col_index: dict[int, int]  # absolute source col -> position in names
    header_rows: int
    data_start: int  # index into region.rows where data begins
    table_name: str | None = None
    footer_row_indexes: list[int] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)
    confidence: float = 1.0


def _text(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _looks_numeric(v: object) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    s = _text(v)
    return bool(s) and bool(_NUMERIC_RE.match(s))


def _row_map(row: list[Cell]) -> dict[int, Cell]:
    return {c.col: c for c in row}


def resolve_header(region: TableRegion, opts: ParseOptions) -> ResolvedHeader:
    """Resolve title rows, the header block, column names, and footers.

    Operates on the first header_scan_depth rows of the region. Every
    guess carries a confidence; low confidence attaches a warning rather
    than raising.
    """
    warnings: list[ParseWarning] = []
    rows = region.rows
    width = region.last_col - region.first_col + 1
    scan = rows[: opts.header_scan_depth]

    modal_width = _modal_populated(rows)
    title_rows, table_name = _detect_title_rows(scan, modal_width, region, warnings)

    body = rows[title_rows:]
    header_span, confidence = _detect_header_span(body[: opts.header_scan_depth])
    if header_span == 0:
        # No header-looking rows: synthesize names, all data.
        names, col_index = _synthesize_names(region)
        warnings.append(
            ParseWarning(
                WarningCode.LOW_CONFIDENCE_HEADER,
                "no header row detected; synthetic column names assigned",
                confidence=0.4,
                sheet=region.sheet,
                row=region.first_row,
            )
        )
        footer_idx = (
            _detect_footers(body, names, col_index, region, warnings)
            if region.complete
            else []
        )
        return ResolvedHeader(
            names=names,
            col_index=col_index,
            header_rows=0,
            data_start=title_rows,
            table_name=table_name,
            footer_row_indexes=[title_rows + i for i in footer_idx],
            warnings=warnings,
            confidence=0.4,
        )

    if confidence < opts.min_confidence:
        warnings.append(
            ParseWarning(
                WarningCode.LOW_CONFIDENCE_HEADER,
                f"header span of {header_span} row(s) detected at low confidence",
                confidence=confidence,
                sheet=region.sheet,
                row=region.first_row + title_rows,
            )
        )

    names, col_index = _flatten_header(
        body[:header_span], region, warnings
    )
    footer_idx = (
        _detect_footers(body[header_span:], names, col_index, region, warnings)
        if region.complete
        else []
    )
    return ResolvedHeader(
        names=names,
        col_index=col_index,
        header_rows=header_span,
        data_start=title_rows + header_span,
        table_name=table_name,
        footer_row_indexes=[title_rows + header_span + i for i in footer_idx],
        warnings=warnings,
        confidence=confidence,
    )


def _modal_populated(rows: list[list[Cell]]) -> int:
    counts: dict[int, int] = {}
    for row in rows:
        n = sum(1 for c in row if _text(c.value))
        if n:
            counts[n] = counts.get(n, 0) + 1
    if not counts:
        return 0
    return max(counts, key=lambda k: (counts[k], k))


def _detect_title_rows(
    scan: list[list[Cell]],
    modal_width: int,
    region: TableRegion,
    warnings: list[ParseWarning],
) -> tuple[int, str | None]:
    """Leading sparse rows are titles/metadata, captured not discarded."""
    title_rows = 0
    table_name: str | None = None
    if modal_width <= 2:
        return 0, None
    for row in scan:
        populated = [c for c in row if _text(c.value)]
        distinct_positions = {c.col for c in populated if c.merged_from is None or c.merged_from == (c.row, c.col)}
        is_merged_span = all(
            c.merged_from is not None or len({p.value for p in populated}) == 1
            for c in populated
        ) and len({_text(c.value) for c in populated}) == 1
        if populated and (len(distinct_positions) <= 2 and len(populated) <= 2 or is_merged_span) and modal_width > max(2, len(populated)):
            if table_name is None:
                table_name = _text(populated[0].value)
            title_rows += 1
        else:
            break
    if title_rows:
        warnings.append(
            ParseWarning(
                WarningCode.TITLE_ROW_DETECTED,
                f"{title_rows} title/metadata row(s) captured as table name",
                confidence=0.8,
                sheet=region.sheet,
                row=region.first_row,
            )
        )
    return title_rows, table_name


def _header_score(row: list[Cell], below: list[list[Cell]]) -> float:
    """Score a candidate header row; higher means more header-like."""
    populated = [c for c in row if _text(c.value)]
    if not populated:
        return 0.0
    texty = sum(1 for c in populated if not _looks_numeric(c.value))
    text_ratio = texty / len(populated)
    values = [_text(c.value).lower() for c in populated]
    uniqueness = len(set(values)) / len(values)
    fill = len(populated) / max(len(row), 1) if row else 0
    dissim = 0.0
    if below:
        sample = below[: min(5, len(below))]
        num_below = 0
        tot_below = 0
        for r in sample:
            for c in r:
                if _text(c.value):
                    tot_below += 1
                    if _looks_numeric(c.value):
                        num_below += 1
        if tot_below:
            below_num_ratio = num_below / tot_below
            row_num_ratio = 1.0 - text_ratio
            dissim = abs(below_num_ratio - row_num_ratio)
    return 0.4 * text_ratio + 0.25 * uniqueness + 0.15 * fill + 0.2 * dissim


def _looks_data(v: object) -> bool:
    import datetime

    if isinstance(v, bool) or isinstance(v, (int, float, datetime.date)):
        return True
    s = _text(v)
    return _looks_numeric(s) or looks_like_date(s)


def _text_ratio(row: list[Cell]) -> float | None:
    populated = [c for c in row if _text(c.value)]
    if not populated:
        return None
    return sum(1 for c in populated if not _looks_data(c.value)) / len(populated)


def _detect_header_span(rows: list[list[Cell]]) -> tuple[int, float]:
    """Leading header block: row 0 must be texty or a merged span; later
    rows extend it only via merges or a strong text row sitting directly
    above data-like rows. A data row never joins the span."""
    if not rows:
        return 0, 0.0

    def has_merge(row: list[Cell]) -> bool:
        return any(c.merged_from is not None for c in row)

    r0 = _text_ratio(rows[0])
    if r0 is None or (r0 < 0.5 and not has_merge(rows[0])):
        return 0, 0.0
    span = 1
    max_span = min(3, len(rows) - 1)
    while span < max_span:
        row = rows[span]
        tr = _text_ratio(row)
        if tr is None:
            break
        nxt = _text_ratio(rows[span + 1]) if span + 1 < len(rows) else None
        next_is_data = nxt is not None and (1.0 - nxt) >= 0.5
        if (has_merge(row) and tr >= 0.5) or (tr >= 0.7 and next_is_data):
            span += 1
        else:
            break
    scores = [_header_score(rows[i], rows[span:]) for i in range(span)]
    return span, min(0.95, sum(scores) / len(scores) + 0.15)


def _synthesize_names(region: TableRegion) -> tuple[list[str], dict[int, int]]:
    cols = sorted(
        {c.col for row in region.rows for c in row if _text(c.value)}
    )
    names = [f"column_{i + 1}" for i in range(len(cols))]
    return names, {col: i for i, col in enumerate(cols)}


def _flatten_header(
    header_rows: list[list[Cell]],
    region: TableRegion,
    warnings: list[ParseWarning],
) -> tuple[list[str], dict[int, int]]:
    """Top-down join of header rows with forward fill across merges."""
    cols = sorted(
        {
            c.col
            for row in region.rows
            for c in row
            if _text(c.value) or c.merged_from is not None
        }
    )
    parts: dict[int, list[str]] = {c: [] for c in cols}
    for row in header_rows:
        by_col = _row_map(row)
        last_val: str | None = None
        last_was_merge_or_value = False
        for col in cols:
            cell = by_col.get(col)
            if cell is not None and _text(cell.value):
                val = re.sub(r"\s+", " ", _text(cell.value))
                if cell.merged_from is not None:
                    parts[col].append(val)
                    last_val, last_was_merge_or_value = val, True
                else:
                    parts[col].append(val)
                    last_val, last_was_merge_or_value = val, True
            else:
                # Blank continuation under a multi-row header forward-fills
                # only in upper rows (group labels), where the row is not final.
                if row is not header_rows[-1] and last_was_merge_or_value and last_val:
                    parts[col].append(last_val)
                # Blank in the final header row contributes nothing.
    # Trailing columns with no header label at all belong to the ragged-row
    # path, not the header; interior blanks stay and get synthetic names.
    while cols and not parts[cols[-1]]:
        cols.pop()
    names_raw = [" | ".join(dict.fromkeys(parts[c])) if parts[c] else "" for c in cols]
    names = _dedupe_names(names_raw, region, warnings)
    return names, {col: i for i, col in enumerate(cols)}


def _dedupe_names(
    raw: list[str], region: TableRegion, warnings: list[ParseWarning]
) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, name in enumerate(raw):
        name = name.strip()
        if not name:
            name = f"column_{i + 1}"
            warnings.append(
                ParseWarning(
                    WarningCode.BLANK_HEADER,
                    f"blank header named {name}",
                    confidence=1.0,
                    sheet=region.sheet,
                    column=name,
                )
            )
        key = name.lower()
        if key in seen:
            seen[key] += 1
            new = f"{name}_{seen[key]}"
            warnings.append(
                ParseWarning(
                    WarningCode.DUPLICATE_HEADER,
                    f"duplicate header {name!r} renamed to {new!r}",
                    confidence=1.0,
                    sheet=region.sheet,
                    column=new,
                )
            )
            name = new
        else:
            seen[key] = 1
        out.append(name)
    return out


def _detect_footers(
    data_rows: list[list[Cell]],
    names: list[str],
    col_index: dict[int, int],
    region: TableRegion,
    warnings: list[ParseWarning],
) -> list[int]:
    """Trailing summary rows: keyword or blank first column with numbers."""
    if not col_index:
        return []
    first_col = min(col_index)
    footer_idx: list[int] = []
    for i in range(len(data_rows) - 1, -1, -1):
        row = data_rows[i]
        if not row:
            break
        by_col = _row_map(row)
        key_cell = by_col.get(first_col)
        key = _text(key_cell.value) if key_cell else ""
        numeric_cells = sum(1 for c in row if _looks_numeric(c.value))
        is_footer = (
            (_FOOTER_KEY.match(key) is not None and numeric_cells >= 1)
            or (not key and numeric_cells >= 1 and len(row) < len(names))
            or _FOOTER_KEY.match(key) is not None and len(row) <= 2
        )
        if is_footer:
            footer_idx.append(i)
        else:
            break
    footer_idx.reverse()
    if footer_idx:
        warnings.append(
            ParseWarning(
                WarningCode.FOOTER_ROW_DETECTED,
                f"{len(footer_idx)} trailing summary/footnote row(s) moved to footer_rows",
                confidence=0.75,
                sheet=region.sheet,
                row=region.first_row,
            )
        )
    return footer_idx
