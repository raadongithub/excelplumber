"""CLI: excelplumber parse|inspect."""

import argparse
import dataclasses
import datetime
import json
import sys
from decimal import Decimal

from .chunker import canonical_str, iter_chunks
from .config import ChunkOptions, ParseOptions
from .errors import ExcelPlumberError
from .parser import parse as parse_file


class _Encoder(json.JSONEncoder):
    """Emits Decimal as a bare number literal and dates as ISO strings."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o) if o != o.to_integral_value() else int(o)
        if isinstance(o, (datetime.date, datetime.datetime)):
            return canonical_str(o)
        return super().default(o)

    def iterencode(self, o, _one_shot=False):
        # Route Decimals through raw text so 0.1 does not become float noise.
        def scrub(obj):
            if isinstance(obj, Decimal):
                return _RawDecimal(obj)
            if isinstance(obj, dict):
                return {k: scrub(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [scrub(v) for v in obj]
            return obj

        return super().iterencode(scrub(o), _one_shot)


class _RawDecimal(float):
    """Carries a Decimal through json.dump with exact repr."""

    def __new__(cls, d: Decimal):
        obj = super().__new__(cls, float(d))
        obj._d = d
        return obj

    def __repr__(self) -> str:
        return canonical_str(self._d)


def _chunk_to_dict(c) -> dict:
    d = dataclasses.asdict(c)
    d["data"] = list(d["data"])
    return d


def _cmd_parse(args) -> int:
    popts = ParseOptions(sheet=args.sheet)
    copts = ChunkOptions(max_tokens=args.max_tokens, serializer=args.serializer)
    out = sys.stdout if args.out in (None, "-") else open(args.out, "w", encoding="utf-8")
    try:
        chunks = iter_chunks(args.file, options=copts, parse_options=popts)
        if args.format == "json":
            json.dump([_chunk_to_dict(c) for c in chunks], out, cls=_Encoder, indent=2)
            out.write("\n")
        else:
            for c in chunks:
                json.dump(_chunk_to_dict(c), out, cls=_Encoder)
                out.write("\n")
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


def _cmd_inspect(args) -> int:
    popts = ParseOptions(sheet=args.sheet)
    blocks = parse_file(args.file, popts)
    for b in blocks:
        title = f" ({b.table_name})" if b.table_name else ""
        print(f"Table {b.table_index} on sheet {b.sheet_name!r}{title}")
        print(f"  rows {b.row_range[0]}..{b.row_range[1]}, {len(b.rows)} data row(s)")
        for col in b.columns:
            unit = f" [{col.unit}]" if col.unit else ""
            print(f"  - {col.name}: {col.dtype}{unit}")
        if b.footer_rows:
            print(f"  {len(b.footer_rows)} footer row(s) held out of data")
        for w in b.warnings:
            print(f"  ! {w}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="excelplumber")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="parse a file into RAG-ready chunks")
    p.add_argument("file")
    p.add_argument("--out", default=None, help="output path, default stdout")
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--sheet", default=None)
    p.add_argument("--format", choices=("jsonl", "json"), default="jsonl")
    p.add_argument("--serializer", default="pipe")
    p.set_defaults(fn=_cmd_parse)

    i = sub.add_parser("inspect", help="human-readable structure report")
    i.add_argument("file")
    i.add_argument("--sheet", default=None)
    i.set_defaults(fn=_cmd_inspect)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ExcelPlumberError as exc:
        print(f"error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
