"""The format contract: Cell, SheetInfo, and the Reader protocol.

Everything above the readers operates only on these types. No detector,
normalizer, or chunker may import a format library or branch on a format
name; they branch on SheetInfo.capabilities instead.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from ..config import ParseOptions
from ..errors import LimitExceededError


@dataclass(frozen=True)
class Cell:
    """One populated cell, with absolute 0-indexed coordinates.

    raw holds the original string form when it differs from value (or when
    the source is textual); merged_from points at the merge origin when the
    cell's value was filled from a merged range.
    """

    row: int
    col: int
    value: object
    raw: str | None = None
    hidden: bool = False
    merged_from: tuple[int, int] | None = None


RowStream = Iterator[list[Cell]]


@dataclass
class SheetInfo:
    """Structural metadata for one sheet, gathered before value iteration."""

    name: str
    index: int = 0
    hidden: bool = False
    merged_ranges: list[tuple[int, int, int, int]] = field(default_factory=list)
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    dimension: tuple[int, int] | None = None
    capabilities: frozenset[str] = frozenset()


class LimitGuard:
    """Row, cell, and wall-clock limit enforcement for reader loops.

    Cheap enough to be always on: the deadline is checked once per 1000
    rows, counters on every call.
    """

    _CHECK_EVERY = 1000

    def __init__(self, opts: ParseOptions) -> None:
        self.max_rows = opts.max_rows
        self.max_cells = opts.max_cells
        self.deadline = (
            time.monotonic() + opts.timeout_seconds if opts.timeout_seconds else None
        )
        self.rows = 0
        self.cells = 0

    def tick(self, ncells: int) -> None:
        self.rows += 1
        self.cells += ncells
        if self.max_rows is not None and self.rows > self.max_rows:
            raise LimitExceededError(f"row limit of {self.max_rows} exceeded")
        if self.max_cells is not None and self.cells > self.max_cells:
            raise LimitExceededError(f"cell limit of {self.max_cells} exceeded")
        if (
            self.deadline is not None
            and self.rows % self._CHECK_EVERY == 0
            and time.monotonic() > self.deadline
        ):
            raise LimitExceededError("timeout_seconds exceeded while reading")


@runtime_checkable
class Reader(Protocol):
    """One source format. Implementations must be lazy and honor limits."""

    extensions: ClassVar[tuple[str, ...]]
    name: ClassVar[str]

    @classmethod
    def sniff(cls, head: bytes, path: Path) -> float: ...

    def open(self, path: Path, opts: ParseOptions) -> None: ...

    def sheets(self) -> Iterator[SheetInfo]: ...

    def rows(self, sheet: SheetInfo) -> RowStream: ...

    def close(self) -> None: ...
