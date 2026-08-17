"""Public data model: columns, tables, chunks, and warnings."""

from dataclasses import dataclass, field
from enum import Enum


class WarningCode(str, Enum):
    """Stable warning identifiers; part of the public API surface."""

    LOW_CONFIDENCE_ENCODING = "low_confidence_encoding"
    LOW_CONFIDENCE_DELIMITER = "low_confidence_delimiter"
    LOW_CONFIDENCE_HEADER = "low_confidence_header"
    TITLE_ROW_DETECTED = "title_row_detected"
    FOOTER_ROW_DETECTED = "footer_row_detected"
    SHORT_FRAGMENT = "short_fragment"
    RAGGED_ROW = "ragged_row"
    DUPLICATE_HEADER = "duplicate_header"
    BLANK_HEADER = "blank_header"
    MERGED_VALUE_PROPAGATED = "merged_value_propagated"
    HIDDEN_EXCLUDED = "hidden_excluded"
    MIXED_TYPE = "mixed_type"
    NONCONFORMING_CELL = "nonconforming_cell"
    AMBIGUOUS_NUMBER_LOCALE = "ambiguous_number_locale"
    AMBIGUOUS_DATE_FORMAT = "ambiguous_date_format"
    MIXED_CURRENCY = "mixed_currency"
    LEADING_ZERO_PRESERVED = "leading_zero_preserved"
    SCI_NOTATION_PRESERVED = "sci_notation_preserved"
    FORMULA_NO_CACHE = "formula_no_cache"
    PIVOT_LAYOUT_SUSPECTED = "pivot_layout_suspected"
    FOOTNOTE_MARKER_STRIPPED = "footnote_marker_stripped"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ParseWarning:
    """A flagged ambiguity or guess made while parsing.

    Coordinates are absolute, 0-indexed positions in the source sheet,
    or None when the warning applies to the whole table or file.
    """

    code: WarningCode
    message: str
    confidence: float = 1.0
    sheet: str | None = None
    row: int | None = None
    column: str | None = None

    def __str__(self) -> str:
        loc = ""
        if self.sheet is not None:
            loc = f" [{self.sheet}"
            if self.row is not None:
                loc += f" r{self.row}"
            if self.column is not None:
                loc += f" col {self.column}"
            loc += "]"
        return f"{self.code.value}: {self.message}{loc}"


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str
    unit: str | None = None


@dataclass
class TableBlock:
    source_file: str
    sheet_name: str
    table_index: int
    columns: list[Column]
    rows: list[dict]
    warnings: list[ParseWarning] = field(default_factory=list)
    table_name: str | None = None
    footer_rows: list[dict] = field(default_factory=list)
    row_range: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class ChunkMetadata:
    source_file: str
    sheet_name: str
    table_index: int
    row_range: tuple[int, int]
    columns: list[str]
    warnings: list[str]
    table_name: str | None = None
    column_group: int = 0
    dtypes: dict[str, str] = field(default_factory=dict, hash=False, compare=False)
    units: dict[str, str] = field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    data: tuple[dict, ...]
    token_count: int
    metadata: ChunkMetadata
