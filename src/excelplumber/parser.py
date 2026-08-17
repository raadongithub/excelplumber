"""Orchestration: parse() and iter_tables(). No parsing logic lives here;
this module only wires readers, detectors, and normalizers together."""

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .config import ParseOptions
from .detectors.boundaries import TableRegion, _is_blank, scan_regions
from .detectors.headers import ResolvedHeader, _detect_footers, resolve_header
from .detectors.profile import ColumnProfile, normalize_cell, profile_column
from .errors import StructureError
from .models import Column, ParseWarning, TableBlock, WarningCode
from .readers import open_source
from .readers.base import Cell

# Footer rows are trailing only, so an 8-row lag buffer is enough to
# withhold candidates until the stream ends without buffering the table.
_FOOTER_LAG = 8


@dataclass
class TableStream:
    """One detected table: frozen columns plus a lazy normalized row iterator."""

    source_file: str
    sheet_name: str
    table_index: int
    columns: list[Column]
    rows: Iterator[dict]
    warnings: list[ParseWarning]
    table_name: str | None = None
    row_range: tuple[int, int] = (0, 0)
    footer_rows: list[dict] = field(default_factory=list)  # populated after rows is drained
    dtypes: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)


def iter_tables(path: str | Path, options: ParseOptions | None = None) -> Iterator[TableStream]:
    """Stream detected tables from a file one at a time.

    Each yielded TableStream's rows iterator must be consumed before
    advancing to the next table. footer_rows is only complete once the
    row iterator is drained.

    Raises:
        SourceError, CorruptFileError, EncodingError, LimitExceededError.
    """
    opts = options or ParseOptions()
    reader = open_source(path, opts)
    source_file = str(path)
    try:
        emitted = 0
        reader_warnings = list(getattr(reader, "warnings", []))
        for sheet in reader.sheets():
            for region in scan_regions(reader.rows(sheet), sheet.name, opts):
                stream = _build_table(region, source_file, opts, reader_warnings)
                reader_warnings = []  # file-level warnings attach to the first table only
                if stream is not None:
                    emitted += 1
                    yield stream
        if emitted == 0:
            raise StructureError(f"no table found in {source_file}")
    finally:
        reader.close()


def parse(path: str | Path, options: ParseOptions | None = None, **kwargs) -> list[TableBlock]:
    """Parse a csv or xlsx file into fully materialized table blocks.

    The one obvious entrypoint. Keyword arguments are ParseOptions fields,
    so parse(path, locale="eu") works without importing ParseOptions.

    Raises:
        SourceError, CorruptFileError, EncodingError, StructureError,
        LimitExceededError, ConfigError.
    """
    if kwargs:
        base = options or ParseOptions()
        options = ParseOptions(**{**base.__dict__, **kwargs})
    blocks = []
    for ts in iter_tables(path, options):
        rows = list(ts.rows)
        blocks.append(
            TableBlock(
                source_file=ts.source_file,
                sheet_name=ts.sheet_name,
                table_index=ts.table_index,
                columns=ts.columns,
                rows=rows,
                warnings=ts.warnings,
                table_name=ts.table_name,
                footer_rows=ts.footer_rows,
                row_range=ts.row_range,
            )
        )
    return blocks


def _build_table(
    region: TableRegion,
    source_file: str,
    opts: ParseOptions,
    extra_warnings: list[ParseWarning],
) -> TableStream | None:
    header = resolve_header(region, opts)
    if not header.names:
        return None
    warnings = extra_warnings + region.warnings + header.warnings

    data_rows = [
        row
        for i, row in enumerate(region.rows)
        if i >= header.data_start and i not in header.footer_row_indexes
    ]
    footer_cell_rows = [region.rows[i] for i in header.footer_row_indexes]

    profiles = _profile_columns(region, header, data_rows, opts, warnings)
    columns = [
        Column(name=p.name, dtype=p.dtype, unit=p.unit) for p in profiles.values()
    ]
    stream = TableStream(
        source_file=source_file,
        sheet_name=region.sheet,
        table_index=region.table_index,
        columns=columns,
        rows=iter(()),
        warnings=warnings,
        table_name=header.table_name,
        row_range=(region.first_row, region.last_row),
        dtypes={p.name: p.dtype for p in profiles.values()},
        units={p.name: p.unit for p in profiles.values() if p.unit},
    )
    stream.footer_rows = [
        _raw_row_dict(r, header, region.sheet) for r in footer_cell_rows
    ]
    stream.rows = _normalized_rows(
        data_rows, header, profiles, region, opts, warnings, stream
    )
    return stream


def _profile_columns(
    region: TableRegion,
    header: ResolvedHeader,
    data_rows: list[list[Cell]],
    opts: ParseOptions,
    warnings: list[ParseWarning],
) -> dict[int, ColumnProfile]:
    sample_rows = data_rows[: opts.profile_sample_rows]
    samples: dict[int, list[object]] = {c: [] for c in header.col_index}
    for row in sample_rows:
        for cell in row:
            if cell.col in samples:
                samples[cell.col].append(cell.value)
    profiles: dict[int, ColumnProfile] = {}
    for col, pos in header.col_index.items():
        name = header.names[pos]
        p = profile_column(name, samples.get(col, []), opts, region.sheet)
        warnings.extend(p.warnings)
        profiles[col] = p
    return profiles


def _raw_row_dict(row: list[Cell], header: ResolvedHeader, sheet: str) -> dict:
    d: dict = {}
    for cell in row:
        pos = header.col_index.get(cell.col)
        if pos is not None:
            d[header.names[pos]] = cell.value
    return d


def _normalized_rows(
    data_rows: list[list[Cell]],
    header: ResolvedHeader,
    profiles: dict[int, ColumnProfile],
    region: TableRegion,
    opts: ParseOptions,
    warnings: list[ParseWarning],
    stream: "TableStream",
) -> Iterator[dict]:
    names = header.names
    col_index = dict(header.col_index)
    merged_warned: set[str] = set()
    ragged_warned = False

    def emit(row: list[Cell]) -> dict:
        nonlocal ragged_warned
        d = {n: None for n in names}
        for cell in row:
            pos = col_index.get(cell.col)
            if pos is None:
                # Ragged row wider than the header: synthesize a name once.
                pos = len(names)
                name = f"column_{pos + 1}"
                names.append(name)
                col_index[cell.col] = pos
                if not ragged_warned:
                    warnings.append(
                        ParseWarning(
                            WarningCode.RAGGED_ROW,
                            f"row wider than header; synthetic column {name} added",
                            confidence=0.9,
                            sheet=region.sheet,
                            row=cell.row,
                        )
                    )
                    ragged_warned = True
                profiles[cell.col] = ColumnProfile(name=name, dtype="text")
                stream.columns.append(Column(name=name, dtype="text"))
                stream.dtypes[name] = "text"
            name = names[pos]
            profile = profiles[cell.col]
            value, warning = normalize_cell(cell.value, profile, region.sheet, cell.row)
            if cell.merged_from is not None and name not in merged_warned:
                merged_warned.add(name)
                warnings.append(
                    ParseWarning(
                        WarningCode.MERGED_VALUE_PROPAGATED,
                        f"merged group value propagated down column {name!r}",
                        sheet=region.sheet,
                        column=name,
                    )
                )
            if cell.merged_from is not None and opts.merge_policy == "blank":
                value = None
            if warning is not None:
                warnings.append(warning)
            d[name] = value
        return d

    for row in data_rows:
        if not _is_blank(row):
            yield emit(row)

    if not region.complete:
        # The remainder streams; withhold a small tail so trailing summary
        # rows can be moved to footer_rows once the stream ends.
        lag: deque[list[Cell]] = deque(maxlen=_FOOTER_LAG)
        for row in region.rest:
            if _is_blank(row):
                continue
            if len(lag) == lag.maxlen:
                yield emit(lag.popleft())
            lag.append(row)
            stream.row_range = (stream.row_range[0], row[0].row)
        tail = list(lag)
        footer_idx = set(
            _detect_footers(tail, names, col_index, region, warnings)
        )
        for i, row in enumerate(tail):
            if i in footer_idx:
                stream.footer_rows.append(_raw_row_dict(row, header, region.sheet))
            else:
                yield emit(row)
