"""XLSX reader: structural XML pre-pass, then openpyxl read-only values.

openpyxl in read_only mode does not populate merged_cells or hidden
row/column dimensions, and dropping read_only would load whole sheets into
memory. So structure (merges, hidden rows/cols, sheet visibility) is read
directly from the worksheet XML first, then values are streamed.
"""

import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar
from xml.etree import ElementTree

from ..config import ParseOptions
from ..errors import CorruptFileError, SourceError
from ..models import ParseWarning, WarningCode
from . import register_reader
from .base import Cell, LimitGuard, RowStream, SheetInfo

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")

# Declared-size cap against zip bombs: refuse members claiming > 4 GB uncompressed.
_MAX_MEMBER_SIZE = 4 * 1024**3


def _col_to_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parse_ref(ref: str) -> tuple[int, int]:
    m = _CELL_REF.fullmatch(ref)
    if not m:
        raise CorruptFileError(f"bad cell reference {ref!r}")
    return int(m.group(2)) - 1, _col_to_index(m.group(1))


def _parse_range(ref: str) -> tuple[int, int, int, int]:
    if ":" in ref:
        a, b = ref.split(":", 1)
        r1, c1 = _parse_ref(a)
        r2, c2 = _parse_ref(b)
        return r1, c1, r2, c2
    r, c = _parse_ref(ref)
    return r, c, r, c


@register_reader
class XlsxReader:
    extensions: ClassVar[tuple[str, ...]] = (".xlsx", ".xlsm")
    name: ClassVar[str] = "xlsx"

    def __init__(self) -> None:
        self._path: Path | None = None
        self._opts = ParseOptions()
        self._wb = None
        self._sheet_infos: list[SheetInfo] = []
        self.warnings: list[ParseWarning] = []

    @classmethod
    def sniff(cls, head: bytes, path: Path) -> float:
        if head.startswith(b"PK\x03\x04"):
            return 0.9 if path.suffix.lower() in cls.extensions else 0.7
        return 0.0

    def open(self, path: Path, opts: ParseOptions) -> None:
        self._path = path
        self._opts = opts
        try:
            self._sheet_infos = self._structural_pass(path)
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
            raise CorruptFileError(f"cannot read {path.name} as xlsx: {exc}") from exc
        try:
            import openpyxl

            # A file handle sidesteps openpyxl's extension check, so a
            # misnamed .csv that is really a workbook still opens.
            self._fh = path.open("rb")
            self._wb = openpyxl.load_workbook(self._fh, read_only=True, data_only=True)
        except Exception as exc:
            raise CorruptFileError(f"openpyxl cannot open {path.name}: {exc}") from exc
        if not opts.include_hidden:
            for info in self._sheet_infos:
                if info.hidden:
                    self.warnings.append(
                        ParseWarning(
                            WarningCode.HIDDEN_EXCLUDED,
                            f"hidden sheet {info.name!r} excluded (include_hidden=False)",
                            sheet=info.name,
                        )
                    )

    def _structural_pass(self, path: Path) -> list[SheetInfo]:
        infos: list[SheetInfo] = []
        with zipfile.ZipFile(path) as zf:
            for member in zf.infolist():
                if member.file_size > _MAX_MEMBER_SIZE:
                    raise CorruptFileError(
                        f"zip member {member.filename} declares {member.file_size} bytes"
                    )
            sheet_targets = self._workbook_index(zf)
            for idx, (name, state, target) in enumerate(sheet_targets):
                info = SheetInfo(
                    name=name,
                    index=idx,
                    hidden=state in ("hidden", "veryHidden"),
                    capabilities=frozenset({"merges", "hidden", "formulas"}),
                )
                if target and target in zf.namelist():
                    self._scan_sheet_xml(zf, target, info)
                infos.append(info)
        return infos

    def _workbook_index(self, zf: zipfile.ZipFile) -> list[tuple[str, str, str | None]]:
        rels: dict[str, str] = {}
        rels_path = "xl/_rels/workbook.xml.rels"
        if rels_path in zf.namelist():
            root = ElementTree.fromstring(zf.read(rels_path))
            for rel in root:
                target = rel.get("Target", "")
                if target.startswith("/"):
                    target = target.lstrip("/")
                elif not target.startswith("xl/"):
                    target = "xl/" + target
                rels[rel.get("Id", "")] = target
        sheets: list[tuple[str, str, str | None]] = []
        root = ElementTree.fromstring(zf.read("xl/workbook.xml"))
        for sheet in root.iter(f"{_NS}sheet"):
            rid = sheet.get(f"{_REL_NS}id", "")
            sheets.append(
                (
                    sheet.get("name", f"Sheet{len(sheets) + 1}"),
                    sheet.get("state", "visible"),
                    rels.get(rid),
                )
            )
        return sheets

    def _scan_sheet_xml(self, zf: zipfile.ZipFile, member: str, info: SheetInfo) -> None:
        # iterparse with element clearing keeps memory at O(merged + hidden ranges).
        with zf.open(member) as f:
            for _, elem in ElementTree.iterparse(f, events=("end",)):
                tag = elem.tag
                if tag == f"{_NS}mergeCell":
                    ref = elem.get("ref")
                    if ref:
                        info.merged_ranges.append(_parse_range(ref))
                elif tag == f"{_NS}row":
                    if elem.get("hidden") in ("1", "true"):
                        r = elem.get("r")
                        if r:
                            info.hidden_rows.add(int(r) - 1)
                    elem.clear()
                elif tag == f"{_NS}col":
                    if elem.get("hidden") in ("1", "true"):
                        lo = int(elem.get("min", "1")) - 1
                        hi = int(elem.get("max", "1")) - 1
                        info.hidden_cols.update(range(lo, hi + 1))
                elif tag == f"{_NS}dimension":
                    ref = elem.get("ref", "")
                    if ref:
                        try:
                            _, _, r2, c2 = _parse_range(ref)
                            info.dimension = (r2 + 1, c2 + 1)
                        except CorruptFileError:
                            pass
                elif tag in (f"{_NS}sheetData", f"{_NS}worksheet"):
                    elem.clear()

    def sheets(self) -> Iterator[SheetInfo]:
        for info in self._sheet_infos:
            if info.hidden and not self._opts.include_hidden:
                continue
            if self._opts.sheet is not None and info.name != self._opts.sheet:
                continue
            yield info

    def rows(self, sheet: SheetInfo) -> RowStream:
        assert self._wb is not None
        try:
            ws = self._wb[sheet.name]
        except KeyError as exc:
            raise SourceError(f"sheet {sheet.name!r} not found") from exc
        guard = LimitGuard(self._opts)
        include_hidden = self._opts.include_hidden
        # Merge origin lookup: every covered coordinate -> origin coordinate.
        merge_map: dict[tuple[int, int], tuple[int, int]] = {}
        merge_origins: set[tuple[int, int]] = set()
        origin_values: dict[tuple[int, int], object] = {}
        for r1, c1, r2, c2 in sheet.merged_ranges:
            merge_origins.add((r1, c1))
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    if (r, c) != (r1, c1):
                        merge_map[(r, c)] = (r1, c1)
        try:
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if not include_hidden and r in sheet.hidden_rows:
                    continue
                cells: list[Cell] = []
                for c, value in enumerate(row):
                    if not include_hidden and c in sheet.hidden_cols:
                        continue
                    if value is None:
                        origin = merge_map.get((r, c))
                        if origin is not None and origin in origin_values:
                            cells.append(
                                Cell(row=r, col=c, value=origin_values[origin], merged_from=origin)
                            )
                        continue
                    if (r, c) in merge_origins:
                        origin_values[(r, c)] = value
                    cells.append(Cell(row=r, col=c, value=value))
                guard.tick(len(cells))
                yield cells
        except Exception as exc:
            if isinstance(exc, (SourceError, CorruptFileError)) or exc.__class__.__module__.startswith(
                "excelplumber"
            ):
                raise
            raise CorruptFileError(f"error while streaming {sheet.name!r}: {exc}") from exc

    def close(self) -> None:
        if self._wb is not None:
            self._wb.close()
            self._wb = None
        fh = getattr(self, "_fh", None)
        if fh is not None:
            fh.close()
            self._fh = None
