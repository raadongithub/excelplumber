"""Text-level normalization: trim, N/A sentinels, footnote markers."""

import re

NA_SENTINELS = frozenset(
    {"n/a", "na", "n.a.", "-", "--", "—", "none", "null", "nil", "#n/a", ""}
)

_FOOTNOTE = re.compile(r"^(.*?)([\*†‡]+|\(\d{1,2}\))$")


def clean(value: object) -> str:
    """Trim and collapse internal whitespace of a cell's string form."""
    return re.sub(r"\s+", " ", str(value)).strip()


def is_na(s: str) -> bool:
    return s.strip().lower() in NA_SENTINELS


def strip_footnote(s: str) -> tuple[str, str | None]:
    """Split a trailing footnote marker (120*, 45†, 12(1)) off a value.

    Returns:
        The value without the marker and the marker itself, or None when
        no marker is present.
    """
    m = _FOOTNOTE.match(s.strip())
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2)
    return s.strip(), None
