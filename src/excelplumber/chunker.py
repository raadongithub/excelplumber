"""Token-budgeted chunking: row batching, wide-table column grouping,
and the compact pipe serialization."""

import datetime
import hashlib
from collections.abc import Callable, Iterable, Iterator
from decimal import Decimal

from .config import ChunkOptions
from .models import Chunk, ChunkMetadata, Column, TableBlock
from .parser import TableStream, iter_tables
from .tokens import TokenCounter, estimate_tokens

SERIALIZERS: dict[str, Callable] = {}


def register_serializer(name: str, fn: Callable | None = None):
    """Register a chunk text serializer; usable as a decorator."""
    if fn is not None:
        SERIALIZERS[name] = fn
        return fn

    def deco(f: Callable) -> Callable:
        SERIALIZERS[name] = f
        return f

    return deco


def canonical_str(value: object) -> str:
    """One deterministic string form shared by text rendering and JSON."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, int)):
        return str(value)
    return str(value)


def _field(value: object) -> str:
    # Pipes inside values would corrupt the serialization; escape them.
    return canonical_str(value).replace("|", "\\|")


def _col_label(col: Column) -> str:
    return f"{col.name} ({col.unit})" if col.unit else col.name


@register_serializer("pipe")
def _serialize_pipe(
    table_label: str, columns: list[Column], rows: list[dict]
) -> str:
    lines = [table_label, " | ".join(_col_label(c) for c in columns)]
    for row in rows:
        lines.append(" | ".join(_field(row.get(c.name)) for c in columns))
    return "\n".join(lines)


@register_serializer("markdown")
def _serialize_markdown(
    table_label: str, columns: list[Column], rows: list[dict]
) -> str:
    lines = [
        table_label,
        "| " + " | ".join(_col_label(c) for c in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(_field(row.get(c.name)) for c in columns) + " |"
        )
    return "\n".join(lines)


@register_serializer("sentences")
def _serialize_sentences(
    table_label: str, columns: list[Column], rows: list[dict]
) -> str:
    lines = [table_label]
    for row in rows:
        parts = [
            f"{_col_label(c)} is {canonical_str(row.get(c.name))}"
            for c in columns
            if row.get(c.name) is not None
        ]
        lines.append("; ".join(parts) + ".")
    return "\n".join(lines)


def chunk(
    blocks: list[TableBlock] | TableBlock,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    options: ChunkOptions | None = None,
) -> list[Chunk]:
    """Chunk parsed table blocks into token-budgeted, self-describing chunks.

    Every chunk repeats the table label and column header so it stands
    alone out of order in a vector store.
    """
    if isinstance(blocks, TableBlock):
        blocks = [blocks]
    opts = _resolve_opts(max_tokens, options)
    counter = token_counter or estimate_tokens
    out: list[Chunk] = []
    for block in blocks:
        out.extend(
            _chunk_table(
                block.source_file,
                block.sheet_name,
                block.table_index,
                block.table_name,
                block.columns,
                iter(block.rows),
                block.row_range,
                [str(w) for w in block.warnings],
                {c.name: c.dtype for c in block.columns},
                {c.name: c.unit for c in block.columns if c.unit},
                opts,
                counter,
            )
        )
    return out


def iter_chunks(
    path,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    options: ChunkOptions | None = None,
    parse_options=None,
) -> Iterator[Chunk]:
    """Stream chunks straight from a file at bounded memory.

    Peak memory is the header scan window plus one chunk's rows, so a
    500k-row sheet processes at constant memory.
    """
    opts = _resolve_opts(max_tokens, options)
    counter = token_counter or estimate_tokens
    for ts in iter_tables(path, parse_options):
        yield from _chunk_table(
            ts.source_file,
            ts.sheet_name,
            ts.table_index,
            ts.table_name,
            ts.columns,
            ts.rows,
            ts.row_range,
            [str(w) for w in ts.warnings],
            dict(ts.dtypes),
            dict(ts.units),
            opts,
            counter,
        )


def _resolve_opts(max_tokens: int | None, options: ChunkOptions | None) -> ChunkOptions:
    opts = options or ChunkOptions()
    if max_tokens is not None:
        opts = ChunkOptions(
            max_tokens=max_tokens,
            wide_table_ratio=opts.wide_table_ratio,
            wide_strategy=opts.wide_strategy,
            serializer=opts.serializer,
        )
    return opts


def _key_columns(columns: list[Column]) -> list[Column]:
    """The identifier column repeated in every column group: the first
    text column, else the first column."""
    for c in columns:
        if c.dtype == "text":
            return [c]
    return columns[:1] if columns else []


def _column_groups(
    columns: list[Column],
    table_label: str,
    opts: ChunkOptions,
    counter: TokenCounter,
    serialize: Callable,
) -> list[list[Column]]:
    header_cost = counter(serialize(table_label, columns, []))
    if header_cost <= opts.max_tokens * opts.wide_table_ratio:
        return [columns]
    keys = _key_columns(columns)
    key_set = {c.name for c in keys}
    budget = opts.max_tokens * opts.wide_table_ratio
    groups: list[list[Column]] = []
    current = list(keys)
    for col in columns:
        if col.name in key_set:
            continue
        candidate = current + [col]
        if (
            counter(serialize(table_label, candidate, [])) > budget
            and len(current) > len(keys)
        ):
            groups.append(current)
            current = list(keys) + [col]
        else:
            current = candidate
    if len(current) > len(keys) or not groups:
        groups.append(current)
    return groups


def _chunk_table(
    source_file: str,
    sheet_name: str,
    table_index: int,
    table_name: str | None,
    columns: list[Column],
    rows: Iterable[dict],
    row_range: tuple[int, int],
    warnings: list[str],
    dtypes: dict[str, str],
    units: dict[str, str],
    opts: ChunkOptions,
    counter: TokenCounter,
) -> Iterator[Chunk]:
    serialize = SERIALIZERS[opts.serializer]
    label = f"Table: {table_name}, {sheet_name}" if table_name else f"Table: {sheet_name}"
    if not columns:
        return
    use_sentences = False
    groups = _column_groups(columns, label, opts, counter, serialize)
    if opts.wide_strategy == "sentences" and len(groups) > 1:
        use_sentences = True
        groups = [columns]
        serialize = SERIALIZERS["sentences"]

    if len(groups) == 1 and not use_sentences:
        yield from _pack_rows(
            source_file, sheet_name, table_index, table_name, groups[0], 0,
            rows, row_range, warnings, dtypes, units, opts, counter, serialize, label,
        )
        return
    # Multiple groups need every row for every group; materialize once.
    all_rows = list(rows)
    for gi, group in enumerate(groups):
        yield from _pack_rows(
            source_file, sheet_name, table_index, table_name, group, gi,
            iter(all_rows), row_range, warnings, dtypes, units, opts, counter,
            serialize, label,
        )


def _pack_rows(
    source_file: str,
    sheet_name: str,
    table_index: int,
    table_name: str | None,
    columns: list[Column],
    group_index: int,
    rows: Iterable[dict],
    row_range: tuple[int, int],
    warnings: list[str],
    dtypes: dict[str, str],
    units: dict[str, str],
    opts: ChunkOptions,
    counter: TokenCounter,
    serialize: Callable,
    label: str,
) -> Iterator[Chunk]:
    # Header cost is measured once and subtracted, so a chunk never
    # overflows because of its own header.
    header_text = serialize(label, columns, [])
    header_cost = counter(header_text)
    budget = max(opts.max_tokens - header_cost, 1)

    batch: list[dict] = []
    lines: list[str] = []
    batch_cost = 0
    first_in_batch = row_range[0]

    def flush() -> Chunk:
        # Reuse the per-row lines measured during packing; serializing the
        # batch a second time would double the serialization cost.
        text = "\n".join([header_text, *lines]) if lines else header_text
        return _make_chunk(
            source_file, sheet_name, table_index, table_name, columns,
            group_index, batch, (first_in_batch, first_in_batch + len(batch) - 1),
            warnings, dtypes, units, counter, text,
        )

    for row in rows:
        line = serialize("", columns, [row]).rsplit("\n", 1)[-1]
        cost = counter(line) + 1
        if batch and batch_cost + cost > budget:
            yield flush()
            first_in_batch += len(batch)
            batch, lines, batch_cost = [], [], 0
        batch.append(row)
        lines.append(line)
        batch_cost += cost
    if batch:
        yield flush()


def _make_chunk(
    source_file: str,
    sheet_name: str,
    table_index: int,
    table_name: str | None,
    columns: list[Column],
    group_index: int,
    batch: list[dict],
    rr: tuple[int, int],
    warnings: list[str],
    dtypes: dict[str, str],
    units: dict[str, str],
    counter: TokenCounter,
    text: str,
) -> Chunk:
    cid = hashlib.sha1(
        f"{source_file}|{sheet_name}|{table_index}|{group_index}|{rr[0]}-{rr[1]}".encode()
    ).hexdigest()[:16]
    names = [c.name for c in columns]
    data = tuple({n: row.get(n) for n in names} for row in batch)
    return Chunk(
        id=cid,
        text=text,
        data=data,
        token_count=counter(text),
        metadata=ChunkMetadata(
            source_file=source_file,
            sheet_name=sheet_name,
            table_index=table_index,
            row_range=rr,
            columns=names,
            warnings=list(warnings),
            table_name=table_name,
            column_group=group_index,
            dtypes={n: dtypes[n] for n in names if n in dtypes},
            units={n: units[n] for n in names if n in units},
        ),
    )
