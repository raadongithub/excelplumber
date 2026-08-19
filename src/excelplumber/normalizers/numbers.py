"""Locale-aware numeric, currency, and percent parsing.

All numbers become int or Decimal, never float: float repr differences
are the classic source of non-deterministic output across platforms.
"""

import re
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
CURRENCY_CODES = frozenset({"USD", "EUR", "GBP", "JPY", "INR", "CAD", "AUD", "CHF", "PKR"})

_NUM_CORE = re.compile(r"^[+-]?[\d.,\s]+$")
_SCI_CORE = re.compile(r"^[+-]?[\d.,\s]+[eE][+-]?\d+$")
_UNIT_SUFFIX = re.compile(r"^([+-]?[\d.,\s]+)\s*([A-Za-z%°/][A-Za-z%°/.\s]*)$")


def looks_scientific(s: str) -> bool:
    """True when a string carries an explicit exponent, like 1.23E+15."""
    return bool(_SCI_CORE.match(s.strip()))


def extract_symbol(s: str) -> tuple[str, str | None]:
    """Strip a leading/trailing currency symbol or code, returning it."""
    s = s.strip()
    for sym, code in CURRENCY_SYMBOLS.items():
        if s.startswith(sym):
            return s[len(sym):].strip(), code
        if s.startswith("-" + sym):
            return "-" + s[len(sym) + 1:].strip(), code
        if s.endswith(sym):
            return s[: -len(sym)].strip(), code
    parts = s.split()
    if len(parts) == 2 and parts[0].upper() in CURRENCY_CODES:
        return parts[1], parts[0].upper()
    if len(parts) == 2 and parts[1].upper() in CURRENCY_CODES:
        return parts[0], parts[1].upper()
    return s, None


def extract_unit(s: str) -> tuple[str, str | None]:
    """Strip a trailing unit (kg, %, km/h) from a numeric-looking string."""
    s = s.strip()
    if s.endswith("%"):
        return s[:-1].strip(), "%"
    m = _UNIT_SUFFIX.match(s)
    if m and _NUM_CORE.match(m.group(1).strip()):
        return m.group(1).strip(), m.group(2).strip()
    return s, None


def sniff_locale(samples: list[str]) -> tuple[str | None, float]:
    """Decide 1,234.56 vs 1.234,56 column-wide from separator evidence.

    Returns:
        The locale ('us' or 'eu') and a confidence, or (None, 0.0) when
        the sample carries no decisive evidence.
    """
    us_votes = eu_votes = 0
    for s in samples:
        s = s.strip().lstrip("+-")
        has_comma, has_dot = "," in s, "." in s
        if has_comma and has_dot:
            if s.rfind(".") > s.rfind(","):
                us_votes += 1
            else:
                eu_votes += 1
        elif has_dot:
            frac = s.rsplit(".", 1)[1]
            if len(frac) != 3 or s.count(".") > 1:
                us_votes += 1  # 1.5 or 1.23 style decimal
        elif has_comma:
            frac = s.rsplit(",", 1)[1]
            if len(frac) != 3 or s.count(",") > 1:
                eu_votes += 1
            elif s.count(",") > 1:
                us_votes += 1
    total = us_votes + eu_votes
    if total == 0:
        return None, 0.0
    if us_votes >= eu_votes:
        return "us", us_votes / total
    return "eu", eu_votes / total


def parse_number(s: str, locale: str) -> int | Decimal | None:
    """Parse a bare numeric string under a frozen locale decision.

    Exponent forms are parsed as Decimal so that the written digits
    round-trip; going through float would not preserve them.

    Returns:
        int when the value has no fractional part, Decimal otherwise,
        None when the string is not a number under this locale.
    """
    s = s.strip().replace(" ", "").replace(" ", "")
    if not s or not (_NUM_CORE.match(s) or _SCI_CORE.match(s)):
        return None
    if locale == "eu":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    if s in ("", "+", "-", "."):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d == d.to_integral_value() and "." not in s.split("e")[0].split("E")[0]:
        return int(d)
    return d


def parse_percent(s: str, locale: str) -> int | Decimal | None:
    """Parse '35%' style strings to the numeric percentage."""
    s = s.strip()
    if not s.endswith("%"):
        return None
    return parse_number(s[:-1], locale)
