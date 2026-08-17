"""CSV reader: stdlib csv over a lazily decoded stream."""

import csv
import io
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from ..config import ParseOptions
from ..detectors.encoding import HEAD_SIZE, decode_head, detect_delimiter, detect_encoding
from ..errors import CorruptFileError, SourceError
from ..models import ParseWarning, WarningCode
from . import register_reader
from .base import Cell, LimitGuard, RowStream, SheetInfo


@register_reader
class CsvReader:
    extensions: ClassVar[tuple[str, ...]] = (".csv",)
    name: ClassVar[str] = "csv"
    dialect_delimiter: ClassVar[str | None] = None

    def __init__(self) -> None:
        self._path: Path | None = None
        self._opts = ParseOptions()
        self._encoding = "utf-8"
        self._delimiter = ","
        self.warnings: list[ParseWarning] = []

    @classmethod
    def sniff(cls, head: bytes, path: Path) -> float:
        if head.startswith(b"PK\x03\x04"):
            return 0.0
        if b"\x00" in head[:1024] and not head[:4].startswith((b"\xff\xfe", b"\xfe\xff")):
            return 0.1
        score = 0.4
        if path.suffix.lower() in cls.extensions:
            score = 0.8
        return score

    def open(self, path: Path, opts: ParseOptions) -> None:
        self._path = path
        self._opts = opts
        try:
            with path.open("rb") as f:
                head = f.read(HEAD_SIZE)
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {exc}") from exc
        if opts.encoding:
            self._encoding = opts.encoding
        else:
            det = detect_encoding(head)
            _, self._encoding = decode_head(head, det)
            if det.confidence < opts.min_confidence:
                self.warnings.append(
                    ParseWarning(
                        WarningCode.LOW_CONFIDENCE_ENCODING,
                        f"encoding guessed as {self._encoding} ({det.evidence})",
                        det.confidence,
                    )
                )
        if opts.delimiter:
            self._delimiter = opts.delimiter
        else:
            text_head = head.decode(self._encoding, errors="replace")
            det = detect_delimiter(text_head)
            self._delimiter = det.value
            if det.confidence < opts.min_confidence:
                self.warnings.append(
                    ParseWarning(
                        WarningCode.LOW_CONFIDENCE_DELIMITER,
                        f"delimiter guessed as {det.value!r} ({det.evidence})",
                        det.confidence,
                    )
                )

    def sheets(self) -> Iterator[SheetInfo]:
        assert self._path is not None
        yield SheetInfo(name=self._path.stem, index=0)

    def rows(self, sheet: SheetInfo) -> RowStream:
        assert self._path is not None
        guard = LimitGuard(self._opts)
        # newline="" is required for csv to handle embedded newlines in quoted fields.
        with self._path.open("r", encoding=self._encoding, newline="") as f:
            reader = csv.reader(
                f, delimiter=self.dialect_delimiter or self._delimiter, strict=True
            )
            try:
                for r, record in enumerate(reader):
                    cells = [
                        Cell(row=r, col=c, value=v, raw=v)
                        for c, v in enumerate(record)
                        if v.strip() != ""
                    ]
                    guard.tick(len(cells))
                    yield cells
            except csv.Error as exc:
                raise CorruptFileError(
                    f"broken CSV framing near line {reader.line_num}: {exc}"
                ) from exc
            except UnicodeDecodeError as exc:
                raise CorruptFileError(
                    f"undecodable bytes past the sampled head: {exc}"
                ) from exc

    def close(self) -> None:
        pass


@register_reader
class TsvReader(CsvReader):
    """Tab-separated variant of the CSV reader with a fixed dialect."""

    extensions: ClassVar[tuple[str, ...]] = (".tsv",)
    name: ClassVar[str] = "tsv"
    dialect_delimiter: ClassVar[str | None] = "\t"

    @classmethod
    def sniff(cls, head: bytes, path: Path) -> float:
        if path.suffix.lower() in cls.extensions:
            return 0.85
        return 0.0
