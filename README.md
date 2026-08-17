# excelplumber

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Build: hatchling](https://img.shields.io/badge/build-hatchling-4051b5.svg)](https://hatch.pypa.io/latest/)

Edge-case-hardened CSV/Excel parsing for RAG chunking and embeddings.

Real-world spreadsheets break naive parsers constantly: multi-table sheets, multi-row
headers, merged cells, locale-variant numbers, Excel serial dates. When parsing silently
fails, the resulting chunks are garbage and retrieval quality quietly degrades with no
error thrown. excelplumber detects and normalizes these edge cases, then turns the result
into token-budgeted, self-describing chunks ready to embed.

**Messy file in, structurally-sound and context-preserving chunks out.**

## Workflow

```
  file (.csv / .tsv / .xlsx / .xlsm)
            |
   reader   |  content sniffing, encoding and delimiter detection,
            |  hidden rows/columns/sheets filtered, merged ranges resolved
            v
 detectors  |  table boundaries -> header span -> per-column type profiling
            |
            v
normalizers |  numbers, currencies, percentages, dates, text
            |
            v
 TableBlock |  frozen columns + typed rows + footer rows + warnings
            |
            v
  chunker   |  token budgeting, wide-table splitting, serialization
            |
            v
  Chunk[]   |  text to embed + exact typed data + provenance metadata
```

Every stage is a registry. Readers, normalizers, and serializers can be replaced or
extended without touching the orchestration in `parser.py`.

## Installation

Requires Python 3.10 or newer.

```bash
pip install excelplumber
```

From source:

```bash
git clone https://github.com/raadongithub/excel-parser.git
cd excel-parser
pip install -e ".[dev]"
pytest
```

Runtime dependencies are deliberately small: `openpyxl`, `charset-normalizer`, and
`python-dateutil`. No pandas, no numpy.

## Quickstart

```python
import excelplumber

blocks = excelplumber.parse("report.xlsx")   # list[TableBlock], edge cases already handled
chunks = excelplumber.chunk(blocks)          # list[Chunk], ready to embed

chunks[0].text
# Table: Regional Sales, Sheet1
# Region | Q1 Revenue (USD) | Q2 Revenue (USD)
# West | 120000 | 135000
# East | 98000 | 101000

chunks[0].data       # exact typed rows behind the text (Decimal/int/date, never float)
chunks[0].metadata   # source file, sheet, table, row range, columns, dtypes, units, warnings
```

Large files stream at constant memory:

```python
for chunk in excelplumber.iter_chunks("500k_rows.xlsx", max_tokens=512):
    index(chunk)   # peak memory = header scan window + one chunk
```

`iter_tables()` is the streaming counterpart to `parse()` when you want table structure
without chunking.

## Command line

```bash
excelplumber parse report.xlsx --out chunks.jsonl --max-tokens 512
excelplumber parse report.xlsx --format json --serializer markdown --sheet "Q3"
excelplumber inspect report.xlsx   # tables found, headers, dtypes, footer rows, warnings
```

`parse` writes JSONL by default so the output pipes straight into an indexing job.
`inspect` prints a human-readable structure report and is the fastest way to find out why
a file parsed the way it did. Both exit with status 2 and a typed error name on failure.

## Supported formats

| Format | Extensions | Handling |
|--------|------------|----------|
| CSV | `.csv` | stdlib `csv` in strict mode; BOM sniff then [charset-normalizer](https://github.com/jawah/charset_normalizer) over a 256 KB head; delimiter chosen by `csv.Sniffer` plus a column-count-stability vote |
| TSV | `.tsv` | CSV reader with the tab delimiter pinned, registered in 12 lines as the reference plugin |
| Excel | `.xlsx`, `.xlsm` | [openpyxl](https://openpyxl.readthedocs.io/) in read-only streaming mode, with a separate structural pass over the sheet XML for merged ranges and hidden row/column/sheet metadata |

Format resolution is content-first: an explicit `format` option wins, then content
sniffing over the first 8 KB, then the file extension. Misnamed files are common in the
wild, so a `.csv` that is really a workbook still routes to the right reader. If nothing
recognizes the content, `SourceError` is raised rather than decoding binary noise as a
one-column table.

## Edge cases handled

Every row of this table has a fixture and a passing test behind it
(`tests/fixtures/`, one file per case, regenerable via `make_fixtures.py`).

| # | Edge case | Handled how |
|---|-----------|-------------|
| 1 | Multiple tables per sheet | Blank-row gap scanning splits regions; each becomes its own TableBlock |
| 2 | Multi-row / nested headers | Header span detected and flattened top-down: `2024 \| Q1 Revenue` |
| 3 | Merged cells | Header merges replicated across their span; data merges forward-filled (configurable via `merge_policy`), warned once per column |
| 4 | Title/metadata rows | Leading sparse rows captured as `table_name`, not discarded |
| 5 | Trailing summary/total rows | Moved to `footer_rows`, never silently dropped, warned |
| 6 | Ragged rows | Extra cells get synthetic `column_N` names (warned); short rows padded with None |
| 7 | Blank separator rows/columns | Row gaps split vertically, blank column runs split side-by-side tables |
| 8 | Hidden rows/columns/sheets | Excluded by default with a warning; `include_hidden=True` to keep |
| 9 | Locale-variant numbers | `1,234.56` vs `1.234,56` decided once per column from separator evidence; ambiguity warns |
| 10 | Currency symbols and units | `$1,200`, `120 kg` hoisted to `Column.unit` once, values stored bare |
| 11 | Inconsistent percentages | `35%` columns normalized to numeric percentage with `unit="%"` |
| 12 | Dates | Excel serials, multiple string formats, `MM/DD` vs `DD/MM` resolved by day>12 evidence, else month-first + warning |
| 13 | Mixed-type columns | Frozen per-column dtype; nonconforming cells keep raw text + per-cell warning, never silent coercion |
| 14 | Leading-zero loss | All-digit columns with leading zeros pinned to text, raw preserved |
| 15 | Scientific-notation IDs | Numbers parse to int/Decimal, never float; raw round-trips exactly |
| 16 | Delimiter detection | `csv.Sniffer`, then column-count-stability vote over `, ; \t \|` |
| 17 | Encoding detection | BOM sniff, then charset-normalizer on a 256 KB head; strict decode with one fallback retry |
| 18 | Quoted fields, embedded newlines | stdlib csv in strict mode; broken quoting raises `CorruptFileError` |
| 19 | Duplicate/blank headers | De-duplicated `name`, `name_2`; blanks named `column_N`, both warned |
| 20 | Header whitespace/casing | Trimmed, internal whitespace collapsed, deterministic left-to-right |

## Output data model

Five frozen or explicit dataclasses, all importable from the package root:

- `TableBlock`: one detected table. Carries `columns`, typed `rows`, `footer_rows`,
  `warnings`, `table_name`, and the absolute `row_range` in the source sheet.
- `Column`: `name`, `dtype`, and an optional `unit` hoisted out of the values.
- `Chunk`: `id`, `text` to embed, `data` as the exact typed rows behind that text,
  `token_count`, and `metadata`.
- `ChunkMetadata`: source file, sheet, table index, row range, column names, dtypes,
  units, column group, and the warnings that applied.
- `ParseWarning`: a stable `WarningCode`, a message, a confidence, and the sheet, row, and
  column it came from.

Chunks repeat the table label and column header, so each one stands alone when a vector
store returns it out of order. The typed `data` travels alongside the text, which means
downstream code can compute on the real values instead of re-parsing the rendered string.

## Errors and warnings

Broken input raises. Ambiguous input warns. The two are never mixed.

```text
excelplumber.ExcelPlumberError     # base class
├── SourceError                    # missing, unreadable, or unsupported file
├── CorruptFileError               # container or framing is broken
├── EncodingError                  # undecodable after the fallback retry
├── StructureError                 # no structure resolved at the minimum confidence
├── LimitExceededError             # a configured row, cell, or time cap was hit
└── ConfigError                    # contradictory or out-of-range options
```

Every guess the parser makes carries a confidence and one of 21 stable `WarningCode`
values (`ambiguous_date_format`, `mixed_type`, `pivot_layout_suspected`, and so on). The
codes are part of the public API, so pipelines can route on them instead of matching on
message text. Nothing is ever silently coerced or silently dropped.

Output is byte-identical across runs, processes, and hash seeds, which is enforced by
golden-snapshot tests. Row, cell, and timeout caps are available on `ParseOptions` for
untrusted input.

## Options

```python
excelplumber.parse(path, locale="eu", include_hidden=True, max_rows=100_000)
excelplumber.chunk(blocks, max_tokens=512, token_counter=my_tiktoken_counter)
```

All knobs live on two frozen dataclasses, `ParseOptions` and `ChunkOptions`, and both
validate on construction. Keyword arguments to `parse()` are `ParseOptions` fields, so the
common case needs no imports. Every default is chosen so zero-config handles ordinary
files: US number and date conventions, hidden data excluded, merged values propagated, a
512-token budget, and the pipe serializer.

Token counting defaults to a fast character-class estimator. Pass any
`Callable[[str], int]` as `token_counter` for exact counts from your own tokenizer.

## Extending

New formats register without touching anything else. The `.tsv` reader is 12 lines:

```python
from excelplumber import register_reader
from excelplumber.readers.csv_reader import CsvReader

@register_reader
class TsvReader(CsvReader):
    extensions = (".tsv",)
    name = "tsv"
    dialect_delimiter = "\t"
```

Third-party packages can ship readers through the `excelplumber.readers` entry-point
group, and a broken plugin degrades to a warning rather than breaking the import. There
are matching registries for normalizers (`register_normalizer`) and chunk serializers
(`register_serializer`, with pipe, markdown, and sentence forms built in).

## Known limitations

Pivot layouts are flagged, not un-pivoted. Cross-sheet relationships are not resolved.
Legacy binary `.xls`, charts, and images are out of scope. Token estimation accuracy drops
for RTL and CJK text when the default estimator is used. Formula cells in workbooks saved
without a cached value are reported as `formula_no_cache` rather than evaluated.

## License

MIT. See [LICENSE](LICENSE).
