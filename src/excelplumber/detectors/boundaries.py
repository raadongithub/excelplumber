"""Table boundary detection: blank-row gaps close regions, blank-column
runs split them horizontally.

Regions are yielded lazily: a bounded head window is buffered for header
and profile decisions, and the remainder streams. Only side-by-side
regions (a blank-column split seen in the head) are fully materialized,
because their segments cannot share one lazy stream.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from ..config import ParseOptions
from ..models import ParseWarning, WarningCode
from ..readers.base import Cell, RowStream


@dataclass
class TableRegion:
    """One candidate table. rows is the buffered head window; rest streams
    the remainder when complete is False. Coordinates are absolute."""

    sheet: str
    table_index: int
    first_row: int
    last_row: int  # last row seen so far; final only when complete
    first_col: int
    last_col: int
    rows: list[list[Cell]]
    rest: Iterator[list[Cell]] = field(default_factory=lambda: iter(()))
    complete: bool = True
    warnings: list[ParseWarning] = field(default_factory=list)


def _is_blank(cells: list[Cell]) -> bool:
    return all(
        c.value is None or (isinstance(c.value, str) and not c.value.strip())
        for c in cells
    )


def scan_regions(
    stream: RowStream, sheet_name: str, opts: ParseOptions
) -> Iterator[TableRegion]:
    """Split a row stream into table regions on blank-row gaps.

    A run of blank_row_gap or more blank rows closes a region. Each
    region's rest iterator must be drained before requesting the next
    region. Regions shorter than min_table_rows are emitted tagged with
    a SHORT_FRAGMENT warning rather than dropped.
    """
    it = iter(stream)
    head_size = opts.header_scan_depth + opts.profile_sample_rows
    table_index = 0

    while True:
        first = _next_nonblank(it)
        if first is None:
            return
        head = [first]
        blank_tail: list[list[Cell]] = []
        closed = False
        exhausted = False
        while len(head) < head_size:
            row = next(it, None)
            if row is None:
                exhausted = True
                break
            if _is_blank(row):
                blank_tail.append(row)
                if len(blank_tail) >= opts.blank_row_gap:
                    closed = True
                    break
            else:
                head.extend(blank_tail)
                blank_tail = []
                head.append(row)

        if closed or exhausted:
            for region in _emit_complete(head, sheet_name, table_index, opts):
                table_index += 1
                yield region
            if exhausted:
                return
            continue

        # Head window filled without closing: decide segmentation from the
        # head. Multiple segments force materialization; one segment streams.
        segments = _split_columns(head, opts)
        if len(segments) > 1:
            tail, closed_after = _collect_until_gap(it, opts)
            head.extend(blank_tail)
            head.extend(tail)
            for region in _emit_complete(head, sheet_name, table_index, opts):
                table_index += 1
                yield region
            if not closed_after:
                return
            continue

        c1, c2, _ = segments[0] if segments else (0, 0, [])
        holder: dict = {"pending": None, "exhausted": False}
        region = TableRegion(
            sheet=sheet_name,
            table_index=table_index,
            first_row=head[0][0].row if head[0] else 0,
            last_row=_last_populated_row(head),
            first_col=c1,
            last_col=c2,
            rows=head,
            rest=_stream_rest(it, blank_tail, opts, holder),
            complete=False,
        )
        table_index += 1
        yield region
        # Drain whatever the consumer left, so the next region starts clean.
        for _ in region.rest:
            pass
        if holder["exhausted"]:
            return


def _next_nonblank(it: Iterator[list[Cell]]) -> list[Cell] | None:
    for row in it:
        if not _is_blank(row):
            return row
    return None


def _collect_until_gap(
    it: Iterator[list[Cell]], opts: ParseOptions
) -> tuple[list[list[Cell]], bool]:
    rows: list[list[Cell]] = []
    blanks: list[list[Cell]] = []
    for row in it:
        if _is_blank(row):
            blanks.append(row)
            if len(blanks) >= opts.blank_row_gap:
                return rows, True
        else:
            rows.extend(blanks)
            blanks = []
            rows.append(row)
    return rows, False


def _stream_rest(
    it: Iterator[list[Cell]],
    carried_blanks: list[list[Cell]],
    opts: ParseOptions,
    holder: dict,
) -> Iterator[list[Cell]]:
    blanks = list(carried_blanks)
    for row in it:
        if _is_blank(row):
            blanks.append(row)
            if len(blanks) >= opts.blank_row_gap:
                return
        else:
            for b in blanks:
                yield b
            blanks = []
            yield row
    holder["exhausted"] = True


def _last_populated_row(rows: list[list[Cell]]) -> int:
    for row in reversed(rows):
        if row and not _is_blank(row):
            return row[0].row
    return 0


def _emit_complete(
    buffer: list[list[Cell]], sheet_name: str, start_index: int, opts: ParseOptions
) -> list[TableRegion]:
    while buffer and _is_blank(buffer[-1]):
        buffer.pop()
    populated = [row for row in buffer if row and not _is_blank(row)]
    if not populated:
        return []
    first_row = populated[0][0].row
    last_row = populated[-1][0].row
    regions = []
    for idx, (c1, c2, rows) in enumerate(_split_columns(buffer, opts)):
        region = TableRegion(
            sheet=sheet_name,
            table_index=start_index + idx,
            first_row=first_row,
            last_row=last_row,
            first_col=c1,
            last_col=c2,
            rows=rows,
            complete=True,
        )
        data_rows = sum(1 for r in rows if not _is_blank(r))
        if data_rows < opts.min_table_rows:
            region.warnings.append(
                ParseWarning(
                    WarningCode.SHORT_FRAGMENT,
                    f"region of {data_rows} row(s) below min_table_rows="
                    f"{opts.min_table_rows}; retained and flagged",
                    confidence=0.5,
                    sheet=sheet_name,
                    row=first_row,
                )
            )
        regions.append(region)
    return regions


def _split_columns(
    buffer: list[list[Cell]], opts: ParseOptions
) -> list[tuple[int, int, list[list[Cell]]]]:
    """Split a region on fully blank interior column runs."""
    occupied: set[int] = set()
    for row in buffer:
        for cell in row:
            if cell.value is not None and (
                not isinstance(cell.value, str) or cell.value.strip()
            ):
                occupied.add(cell.col)
    if not occupied:
        return []
    lo, hi = min(occupied), max(occupied)
    segments: list[tuple[int, int]] = []
    seg_start: int | None = None
    gap = 0
    for c in range(lo, hi + 2):
        if c in occupied:
            if seg_start is None:
                seg_start = c
            gap = 0
        else:
            gap += 1
            if seg_start is not None and (gap >= opts.blank_col_gap or c > hi):
                segments.append((seg_start, c - gap))
                seg_start = None
    if seg_start is not None:
        segments.append((seg_start, hi))
    return [
        (c1, c2, [[cell for cell in row if c1 <= cell.col <= c2] for row in buffer])
        for c1, c2 in segments
    ]
