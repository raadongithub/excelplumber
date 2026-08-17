"""All tunable defaults in one place: ParseOptions and ChunkOptions."""

from dataclasses import dataclass

from .errors import ConfigError


@dataclass(frozen=True)
class ParseOptions:
    """Options controlling reading, structure detection, and typing.

    All defaults are chosen so that parse(path) with no options handles
    the common case. Ambiguity defaults follow the US convention
    (1,234.56 and month-first dates) and can be overridden via locale.
    """

    format: str | None = None
    sheet: str | None = None
    include_hidden: bool = False
    locale: str = "us"  # "us" -> 1,234.56  "eu" -> 1.234,56
    merge_policy: str = "propagate"  # or "blank"
    blank_row_gap: int = 2
    blank_col_gap: int = 1
    min_table_rows: int = 2
    header_scan_depth: int = 25
    profile_sample_rows: int = 200
    dtype_threshold: float = 0.8
    min_confidence: float = 0.5
    max_rows: int | None = None
    max_cells: int | None = None
    timeout_seconds: float | None = None
    encoding: str | None = None
    delimiter: str | None = None

    def __post_init__(self) -> None:
        if self.locale not in ("us", "eu"):
            raise ConfigError(f"locale must be 'us' or 'eu', got {self.locale!r}")
        if self.merge_policy not in ("propagate", "blank"):
            raise ConfigError(
                f"merge_policy must be 'propagate' or 'blank', got {self.merge_policy!r}"
            )
        if self.blank_row_gap < 1:
            raise ConfigError("blank_row_gap must be >= 1")
        if self.min_table_rows < 1:
            raise ConfigError("min_table_rows must be >= 1")
        if not 0.0 < self.dtype_threshold <= 1.0:
            raise ConfigError("dtype_threshold must be in (0, 1]")
        for name in ("max_rows", "max_cells"):
            v = getattr(self, name)
            if v is not None and v < 1:
                raise ConfigError(f"{name} must be >= 1 when set")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ConfigError("timeout_seconds must be > 0 when set")


@dataclass(frozen=True)
class ChunkOptions:
    """Options controlling token-budgeted chunk packing."""

    max_tokens: int = 512
    wide_table_ratio: float = 0.5
    wide_strategy: str = "column_groups"  # or "sentences"
    serializer: str = "pipe"

    def __post_init__(self) -> None:
        if self.max_tokens < 16:
            raise ConfigError("max_tokens must be >= 16")
        if not 0.0 < self.wide_table_ratio < 1.0:
            raise ConfigError("wide_table_ratio must be in (0, 1)")
        if self.wide_strategy not in ("column_groups", "sentences"):
            raise ConfigError(
                f"wide_strategy must be 'column_groups' or 'sentences', got {self.wide_strategy!r}"
            )
