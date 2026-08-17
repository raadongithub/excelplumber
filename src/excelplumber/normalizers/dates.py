"""Date normalization: Excel serial numbers and multi-format strings."""

import datetime
import re
from decimal import Decimal

from dateutil import parser as dateutil_parser

# Excel's day 0 is 1899-12-30 (its serial 1 = 1900-01-01, with the fictitious
# 1900-02-29 already accounted for by the -30 offset for serials > 59).
_EXCEL_EPOCH = datetime.date(1899, 12, 30)
_SERIAL_MIN, _SERIAL_MAX = 61, 2_958_465  # 1900-03-01 .. 9999-12-31

_DATE_LIKE = re.compile(
    r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\s*$|^\s*\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\s*$"
    r"|^\s*[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\s*$"
)

_SLASHY = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")


def looks_like_date(s: str) -> bool:
    return bool(_DATE_LIKE.match(s))


def serial_to_date(n: int | float | Decimal) -> datetime.date | None:
    """Convert an Excel serial day number to a date when in plausible range."""
    n = int(n)
    if not _SERIAL_MIN <= n <= _SERIAL_MAX:
        return None
    return _EXCEL_EPOCH + datetime.timedelta(days=n)


def resolve_day_order(samples: list[str]) -> tuple[bool, bool]:
    """Decide day-first vs month-first for slashy dates in a column.

    Any sample with a first component > 12 proves day-first; a second
    component > 12 proves month-first. No proof either way means the
    month-first default applies, flagged as ambiguous by the caller.

    Returns:
        (dayfirst, ambiguous)
    """
    for s in samples:
        m = _SLASHY.match(s)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            return True, False
        if b > 12 and a <= 12:
            return False, False
    has_slashy = any(_SLASHY.match(s) for s in samples)
    return False, has_slashy


def parse_date(s: str, dayfirst: bool) -> datetime.date | None:
    """Parse one date string under a frozen day-order decision.

    Cheap explicit formats run first; dateutil is the slow last resort.
    """
    m = _ISO.match(s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _SLASHY.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        day, month = (a, b) if dayfirst else (b, a)
        if month > 12 and day <= 12:
            day, month = month, day
        try:
            return datetime.date(y, month, day)
        except ValueError:
            return None
    try:
        return dateutil_parser.parse(s, dayfirst=dayfirst, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None
