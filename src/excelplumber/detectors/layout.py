"""Table-shape checks that run once the columns are resolved.

A cross-tab parses cleanly and is still the wrong shape to embed: the
period lives in the column name and the only key is the row label, so a
serialized row reads as a list of unlabeled numbers. The layout is
flagged, never reshaped, because un-pivoting is a lossy guess about
which column is the measure.
"""

import re

from ..models import Column, ParseWarning, WarningCode
from ..normalizers.dates import looks_like_date

_MONTHS = frozenset(
    {
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
        "january", "february", "march", "april", "june",
        "july", "august", "september", "october", "november", "december",
    }
)

_PERIOD = re.compile(
    r"^(q[1-4]|h[12]|fy\s?\d{2,4}|\d{4}|\d{4}[-/]\d{1,2})$", re.IGNORECASE
)

# Two value columns can share a period label by coincidence; three that all
# do is a shape, not an accident.
_MIN_VALUE_COLUMNS = 3
_PERIOD_RATIO = 0.8


def _is_period_label(name: str) -> bool:
    """True when a column name reads as one period in a series."""
    # Multi-row headers flatten to "2024 | Q1", where the period is the leaf.
    label = name.rsplit("|", 1)[-1].strip()
    if label.lower() in _MONTHS:
        return True
    if _PERIOD.match(label):
        return True
    return looks_like_date(label)


def detect_pivot_layout(
    columns: list[Column], sheet: str, row: int
) -> ParseWarning | None:
    """Flag a cross-tab: a text key column followed by period value columns.

    Args:
        columns: The resolved columns of one table, in source order.
        sheet: Sheet name recorded on the warning.
        row: First source row of the table, recorded on the warning.

    Returns:
        A warning whose confidence is the share of value columns that
        carry period labels, or None when the shape does not match.
    """
    if len(columns) < _MIN_VALUE_COLUMNS + 1:
        return None
    key, values = columns[0], columns[1:]
    if key.dtype != "text":
        return None
    if any(c.dtype not in ("int", "decimal") for c in values):
        return None
    periodic = sum(1 for c in values if _is_period_label(c.name))
    ratio = periodic / len(values)
    if ratio < _PERIOD_RATIO:
        return None
    return ParseWarning(
        WarningCode.PIVOT_LAYOUT_SUSPECTED,
        f"{periodic} of {len(values)} value columns are period labels over "
        f"the text key column {key.name!r}; layout looks like a cross-tab",
        confidence=round(ratio, 2),
        sheet=sheet,
        row=row,
    )
