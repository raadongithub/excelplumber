# excelplumber

[![Python](<https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg>)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/raadongithub/excelplumber/blob/main/LICENSE)
[![Build: hatchling](https://img.shields.io/badge/build-hatchling-4051b5.svg)](https://hatch.pypa.io/latest/)

Edge-case-hardened CSV and Excel parsing for RAG chunking and embeddings.

Messy file in, structurally sound and context-preserving chunks out.

## Contents

- [The problem](#the-problem)
- [What excelplumber does](#what-excelplumber-does)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Core concepts](#core-concepts)
- [Public API](#public-api)
- [Configuration](#configuration)
- [Input formats](#input-formats)
- [Structural cases handled](#structural-cases-handled)
- [Warnings](#warnings)
- [Errors](#errors)
- [Command line interface](#command-line-interface)
- [Common workflows](#common-workflows)
- [Extension points](#extension-points)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Packaging and distribution](#packaging-and-distribution)
- [License](#license)

## The problem

Every CSV and Excel reader in wide use assumes the file is a rectangle: one header row on
top, one table per sheet, one type per column, one value per cell. Spreadsheets written by
people satisfy none of that. They carry report titles above the header, headers stacked two
or three rows deep, merged cells standing in for repeated values, several tables on one
sheet, totals glued to the bottom, hidden rows left over from last quarter, and numbers
that are secretly text because someone typed a currency symbol.

The damage is not that these files fail to parse. It is that they parse:

- A two-row header becomes one header row plus a first data row that is really labels.
- A merged region becomes one value followed by a column of blanks.
- A totals row becomes a data row, so every aggregate downstream is double counted.
- `1.234` meaning one thousand two hundred thirty-four is read as `1.234`, off by 1000x.
- `02134` becomes `2134`, and the zip code matches nothing.
- A formula saved with no stored result becomes an empty cell.

Nothing raises. You get a table whose shape looks plausible, and the mistake surfaces weeks
later as a number nobody can reconcile.

In a retrieval pipeline the silence costs more, because there is no reconciliation step at
all. Corrupted rows are serialized into chunk text, embedded, and indexed, and the vector
store answers confidently out of them. Quality degrades with no exception, no failed build,
and no log line naming the file responsible.

## What excelplumber does

It detects the structures naive readers flatten, so a multi-row header stays a header and a
totals row stays a total. It refuses to guess quietly: every ambiguity it resolves carries a
stable warning code and a confidence, and genuinely broken input raises a typed exception
instead. And it chunks for retrieval, emitting token-budgeted, self-describing chunks that
carry the exact typed values alongside the text you embed.

Use it when you are:

- Indexing spreadsheets into a vector store and need each chunk to stand alone.
- Ingesting files from customers, regulators, or an internal reporting tool, where the
  layout changes without notice and you need to know when it did.
- Extracting typed values (`int`, `Decimal`, `datetime.date`) rather than strings, without
  pulling in pandas.
- Auditing a batch of files before ingestion to find the ones that need a human look.

Runtime dependencies are [openpyxl](https://openpyxl.readthedocs.io/),
[charset-normalizer](https://github.com/jawah/charset_normalizer), and
[python-dateutil](https://github.com/dateutil/dateutil). No pandas, no numpy.

## How it works

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

Each stage is a registry. Readers, normalizers, and chunk serializers can be replaced or
added without touching the orchestration in `src/excelplumber/parser.py`. See
[Extension points](#extension-points).

## Installation

excelplumber runs on CPython 3.10, 3.11, 3.12, and 3.13. Install it into a virtual
environment so the `excelplumber` command lands on your `PATH` without touching the system
Python.

### Linux

On Debian and Ubuntu the `venv` module ships separately from the interpreter, so install it
first:

```bash
sudo apt install python3-venv    # Debian/Ubuntu only; Fedora and Arch include it
```

Then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install excelplumber
```

### macOS

The system Python at `/usr/bin/python3` works, but Apple ships it for its own tooling and
it lags the current release. A [Homebrew](https://brew.sh) Python (`brew install python`)
tracks upstream and is the usual choice:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install excelplumber
```

If `python3` resolves to the system interpreter and you want the Homebrew one, name it
explicitly: `/opt/homebrew/bin/python3 -m venv .venv` on Apple silicon, or
`/usr/local/bin/python3 -m venv .venv` on Intel.

### Windows

PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install excelplumber
```

Command Prompt (`cmd.exe`):

```bat
py -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install excelplumber
```


### Verifying the install

```bash
python -c "import excelplumber; print(excelplumber.__version__)"
excelplumber --help
```

The first prints the version. The second confirms the console script resolved: installing
the package puts an `excelplumber` executable in `.venv/bin` on Linux and macOS, and
`.venv\Scripts\excelplumber.exe` on Windows. Both are on `PATH` while the environment is
active. `python -m excelplumber.cli` runs the same entry point if you would rather not rely
on `PATH`.

### Installing from source

```bash
git clone https://github.com/raadongithub/excelplumber.git
cd excelplumber
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The `dev` extra adds pytest.

## Quick start

Take a workbook with a title block above the header, currency and percent columns, and a
totals row at the bottom:

```
Quarterly Sales Report
Generated 2024-10-01
Region    Revenue    Share
West      $120,000   35%
East      $98,000    28%
North     $76,500    22%
South     $51,200    15%
Total     $345,700   100%
```

```python
import excelplumber

blocks = excelplumber.parse("sales_report.xlsx")
block = blocks[0]

print(block.table_name)
print([(c.name, c.dtype, c.unit) for c in block.columns])
for row in block.rows:
    print(row)
print(block.footer_rows)
for warning in block.warnings:
    print("!", warning)
```

```text
Quarterly Sales Report
[('Region', 'text', None), ('Revenue', 'int', 'USD'), ('Share', 'int', '%')]
{'Region': 'West', 'Revenue': 120000, 'Share': 35}
{'Region': 'East', 'Revenue': 98000, 'Share': 28}
{'Region': 'North', 'Revenue': 76500, 'Share': 22}
{'Region': 'South', 'Revenue': 51200, 'Share': 15}
[{'Region': 'Total', 'Revenue': '$345,700', 'Share': '100%'}]
! title_row_detected: 2 title/metadata row(s) captured as table name [Q3 r0]
! footer_row_detected: 1 trailing summary/footnote row(s) moved to footer_rows [Q3 r0]
! ambiguous_number_locale: column 'Revenue' separators are ambiguous; assuming 1,234.56 [Q3 col Revenue]
```

The title rows became `table_name` instead of data. The currency symbol and percent sign
moved to `Column.unit` and the values are bare integers. The totals row is in
`footer_rows`, out of `rows`, so a downstream sum does not double count. The comma in
`$120,000` could mean either a thousands separator or a decimal comma, so the parser took
the configured default and said so.

Turn the blocks into chunks:

```python
chunks = excelplumber.chunk(blocks)
print(chunks[0].text)
print(chunks[0].token_count)
print(chunks[0].data[0])
print(chunks[0].metadata.units)
```

```text
Table: Quarterly Sales Report, Q3
Region | Revenue (USD) | Share (%)
West | 120000 | 35
East | 98000 | 28
North | 76500 | 22
South | 51200 | 15
70
{'Region': 'West', 'Revenue': 120000, 'Share': 35}
{'Revenue': 'USD', 'Share': '%'}
```

`text` is what you embed. `data` is the same rows as exact typed values, so downstream code
computes on real numbers instead of re-parsing the rendered string.

For files too large to materialize, stream instead:

```python
for chunk in excelplumber.iter_chunks("500k_rows.xlsx", max_tokens=512):
    index(chunk)
```

## Core concepts

### Readers

A reader turns one file format into a stream of `Cell` records with absolute 0-indexed row
and column coordinates. Nothing above the reader layer knows what format it came from.
Three readers ship in the box, registered under the names `csv`, `tsv`, and `xlsx`.

Format resolution is content-first. An explicit `format` option wins outright. Otherwise
every registered reader scores the first 8 KB of the file, and the highest score wins, with
ties broken by reader name. Extension feeds into that score instead of acting as a separate
step, so a `.csv` file that is really a workbook still routes to the XLSX reader. If no
reader scores above 0.2, `SourceError` is raised rather than decoding binary noise as a
one-column table.

### Table regions

A sheet is not assumed to hold one table. A run of `blank_row_gap` or more blank rows closes
a region, and a run of `blank_col_gap` or more fully blank columns splits a region
horizontally into side-by-side tables. Each region becomes its own `TableBlock` with its own
header, its own column types, and its own `table_index`.

Regions are produced lazily. A bounded head window (`header_scan_depth + profile_sample_rows`
rows) is buffered so the header and the column types can be decided, and the rest of the
region streams. Only side-by-side regions are fully materialized, because their segments
cannot share one lazy row stream.

### Header resolution

Within a region, leading sparse rows are treated as title or metadata rows and captured as
`table_name` instead of being discarded. The header block below them spans up to three rows;
merged spans and a strong text row sitting directly above data-like rows extend it. Multi-row
headers flatten top-down with forward fill across merges, producing names like
`2024 | Q1 Revenue`. Blank names become `column_N`, duplicates get a `_2` suffix, and both
emit a warning. Trailing summary rows (`Total`, `Subtotal`, `Average`, `Notes`, `Source`, or
a bare marker row) move to `footer_rows`.

### Column profiles

Each column's type is decided once from a bounded sample (`profile_sample_rows` rows) and
then frozen. The profile fixes the dtype, the number locale, the currency or physical unit,
the percent convention, and the day-first versus month-first date order. A cell that does
not fit its frozen profile keeps its cleaned raw string and emits a `nonconforming_cell`
warning. Nothing is re-guessed per cell and nothing is silently coerced.

Five dtypes exist: `text`, `int`, `decimal`, `date`, and `bool`. Numbers become `int` or
`decimal.Decimal`, never `float`, because float repr differences are the usual source of
output that differs between machines. Dates become `datetime.date`, or `datetime.datetime`
when the source carries a non-midnight time.

### Chunks

A `Chunk` is a token-budgeted slice of one table. Every chunk repeats the table label and
the column header, so it still makes sense when a vector store returns it on its own and out
of order. Rows are packed until the next row would exceed the budget, and the header cost is
measured once and subtracted up front so a chunk never overflows because of its own header.

When the header alone costs more than `max_tokens * wide_table_ratio`, the table counts as
wide. Under the default `wide_strategy="column_groups"`, columns are split into groups that
each fit the budget, and a key column (the first text column, or the first column if there
is none) is repeated in every group so the groups can be joined back together. Under
`wide_strategy="sentences"`, the table is instead rendered one row per sentence.

## Public API

Everything below is importable from the package root and listed in `__all__`. Nothing else
is public.

| Name                                   | Kind       | Purpose                                                   |
| -------------------------------------- | ---------- | --------------------------------------------------------- |
| `parse`                              | function   | Read a file into a list of`TableBlock`                  |
| `iter_tables`                        | function   | Stream`TableStream` objects, one per detected table     |
| `chunk`                              | function   | Turn`TableBlock` objects into `Chunk` objects         |
| `iter_chunks`                        | function   | Stream`Chunk` objects straight from a file              |
| `estimate_tokens`                    | function   | Dependency-free BPE token estimate for a string           |
| `TokenCounter`                       | type alias | `Callable[[str], int]`                                  |
| `TableBlock`                         | dataclass  | One detected table, fully materialized                    |
| `Column`                             | dataclass  | `name`, `dtype`, optional `unit`                    |
| `Chunk`                              | dataclass  | `id`, `text`, `data`, `token_count`, `metadata` |
| `ChunkMetadata`                      | dataclass  | Provenance attached to each chunk                         |
| `ParseWarning`                       | dataclass  | One flagged guess, with code, confidence, location        |
| `WarningCode`                        | str enum   | The 21 stable warning identifiers                         |
| `ParseOptions`                       | dataclass  | Reading, detection, and typing options                    |
| `ChunkOptions`                       | dataclass  | Chunk packing options                                     |
| `ExcelPlumberError` and 6 subclasses | exceptions | See[Errors](#errors)                                       |
| `register_reader`                    | function   | Add a format reader                                       |
| `register_normalizer`                | function   | Add a per-dtype cell coercion                             |
| `register_serializer`                | function   | Add a chunk text serializer                               |
| `__version__`                        | str        | Package version                                           |

### parse

```python
parse(path, options=None, **kwargs) -> list[TableBlock]
```

Reads the whole file and returns one `TableBlock` per detected table. Keyword arguments are
`ParseOptions` field names, so the common case needs no import:

```python
blocks = excelplumber.parse("report.xlsx", locale="eu", include_hidden=True)
```

Passing both `options` and keyword arguments applies the keywords on top of `options`. An
unknown keyword raises `TypeError`.

### iter_tables

```python
iter_tables(path, options=None) -> Iterator[TableStream]
```

The streaming counterpart to `parse`. Each `TableStream` has the same fields as a
`TableBlock` plus `dtypes` and `units` dicts, except that `rows` is a lazy iterator rather
than a list. Two rules follow from that:

- Consume a stream's `rows` before advancing to the next table.
- `footer_rows` is only complete once `rows` has been drained, because trailing summary rows
  cannot be recognized until the stream ends.

```python
from excelplumber import iter_tables

for table in iter_tables("sales_report.xlsx"):
    print(table.sheet_name, table.table_index, [c.name for c in table.columns])
    print("  rows:", sum(1 for _ in table.rows), "footer:", table.footer_rows)
```

```text
Q3 0 ['Region', 'Revenue', 'Share']
  rows: 4 footer: [{'Region': 'Total', 'Revenue': '$345,700', 'Share': '100%'}]
```

### chunk

```python
chunk(blocks, max_tokens=None, token_counter=None, options=None) -> list[Chunk]
```

Accepts a single `TableBlock` or a list of them. `max_tokens` is a shortcut that overrides
that one field of `options`.

### iter_chunks

```python
iter_chunks(path, max_tokens=None, token_counter=None, options=None,
            parse_options=None) -> Iterator[Chunk]
```

Parses and chunks in one streaming pass. Peak memory is the region head window plus one
chunk's rows, so a 500k-row sheet processes without the file ever being materialized. The
exception is a wide table split into column groups: every group needs every row, so that
table's rows are materialized once.

### Token counting

`estimate_tokens` is the default counter. It scores ASCII words at roughly one token per
four characters (minimum one) and digits and punctuation at one each, which tracks BPE more
closely than `len(text) / 4` while staying dependency free.

```python
>>> excelplumber.estimate_tokens("hello world")
4
>>> excelplumber.estimate_tokens("")
0
```

Pass any `Callable[[str], int]` as `token_counter` for exact counts from your own tokenizer:

```python
import tiktoken

enc = tiktoken.get_encoding("cl100k_base")
chunks = excelplumber.chunk(blocks, max_tokens=512,
                            token_counter=lambda s: len(enc.encode(s)))
```

## Configuration

Both option classes are frozen dataclasses that validate on construction and raise
`ConfigError` on a bad value.

### ParseOptions

| Field                   | Default         | Meaning                                                                       |
| ----------------------- | --------------- | ----------------------------------------------------------------------------- |
| `format`              | `None`        | Force a reader by registered name, skipping content sniffing                  |
| `sheet`               | `None`        | Restrict to one sheet by name (XLSX only)                                     |
| `include_hidden`      | `False`       | Keep hidden rows, columns, and sheets                                         |
| `locale`              | `"us"`        | Fallback number convention:`"us"` is `1,234.56`, `"eu"` is `1.234,56` |
| `merge_policy`        | `"propagate"` | `"propagate"` fills merged values down, `"blank"` writes `None`         |
| `blank_row_gap`       | `2`           | Blank rows needed to close a table region                                     |
| `blank_col_gap`       | `1`           | Blank columns needed to split side-by-side tables                             |
| `min_table_rows`      | `2`           | Regions below this are kept and flagged`short_fragment`                     |
| `header_scan_depth`   | `25`          | Rows examined when looking for titles and the header block                    |
| `profile_sample_rows` | `200`         | Rows sampled to decide each column's type                                     |
| `dtype_threshold`     | `0.8`         | Share of non-null cells that must agree before a dtype sticks                 |
| `min_confidence`      | `0.5`         | Below this, a detector decision emits a low-confidence warning                |
| `max_rows`            | `None`        | Hard row cap; exceeding it raises`LimitExceededError`                       |
| `max_cells`           | `None`        | Hard cell cap; exceeding it raises`LimitExceededError`                      |
| `timeout_seconds`     | `None`        | Wall-clock cap on reading; exceeding it raises`LimitExceededError`          |
| `encoding`            | `None`        | Force a text encoding instead of detecting it (CSV and TSV only)              |
| `delimiter`           | `None`        | Force a delimiter instead of detecting it (CSV only)                          |

Validated on construction: `locale` must be `"us"` or `"eu"`; `merge_policy` must be
`"propagate"` or `"blank"`; `blank_row_gap` and `min_table_rows` must be at least 1;
`dtype_threshold` must be in `(0, 1]`; `max_rows` and `max_cells` must be at least 1 when
set; `timeout_seconds` must be greater than 0 when set. The remaining fields are not range
checked.

The locale option is a fallback, not an override. Each numeric column votes on its own
separator evidence first, and `locale` applies only when that vote is inconclusive, which
also emits `ambiguous_number_locale`.

### ChunkOptions

| Field                | Default             | Meaning                                                             |
| -------------------- | ------------------- | ------------------------------------------------------------------- |
| `max_tokens`       | `512`             | Token budget per chunk, header included                             |
| `wide_table_ratio` | `0.5`             | Header cost above this share of the budget marks a table wide       |
| `wide_strategy`    | `"column_groups"` | `"column_groups"` or `"sentences"`                              |
| `serializer`       | `"pipe"`          | Registered serializer name:`pipe`, `markdown`, or `sentences` |

Validated on construction: `max_tokens` must be at least 16; `wide_table_ratio` must be in
`(0, 1)` exclusive; `wide_strategy` must be `"column_groups"` or `"sentences"`. The
serializer name is not checked here, so an unregistered name surfaces as a `KeyError` at
chunk time.

## Input formats

| Format | Extensions           | Reader name | Handling                                                                                                                                                                                                   |
| ------ | -------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CSV    | `.csv`             | `csv`     | stdlib`csv` in strict mode; BOM sniff then charset-normalizer over a 256 KB head; delimiter chosen by `csv.Sniffer` and, failing that, by a column-count-stability vote over `,` `;` tab and `\|` |
| TSV    | `.tsv`             | `tsv`     | The CSV reader with the tab delimiter pinned                                                                                                                                                               |
| Excel  | `.xlsx`, `.xlsm` | `xlsx`    | openpyxl in read-only streaming mode, plus a separate pass over the sheet XML for merged ranges and hidden row, column, and sheet metadata                                                                 |

Legacy binary `.xls` is not supported. Convert it to `.xlsx` first.

The XLSX reader makes two passes because openpyxl's read-only mode does not populate merged
ranges or hidden dimensions, and dropping read-only mode would load whole sheets into memory.
The first pass walks the worksheet XML with `iterparse` to collect merges, hidden rows and
columns, sheet visibility, and formula cells with no cached result. The second streams
values. Workbook members declaring more than 4 GB uncompressed are refused as a zip-bomb
guard.

Formula cells are read as the result the writing application stored. openpyxl reads values
with `data_only`, and no formula engine is included, so a formula saved without a cached
result reads as empty. That case is reported as `formula_no_cache` with the first affected
coordinate and an exact count, so it is never confused with a genuinely blank cell.

Output is the same across runs, processes, and hash seeds. Golden-snapshot tests in
`tests/test_determinism.py` enforce that, including a subprocess run under varied
`PYTHONHASHSEED`.

## Structural cases handled

Each row here has a fixture and a passing test behind it.

| #  | Case                                 | Handling                                                                                                                             |
| -- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1  | Multiple tables per sheet            | Blank-row gap scanning splits regions; each becomes its own`TableBlock`                                                            |
| 2  | Multi-row and nested headers         | Header span detected and flattened top-down:`2024 \| Q1 Revenue`                                                                    |
| 3  | Merged cells                         | Header merges replicated across their span; data merges forward-filled (see`merge_policy`), warned once per column                 |
| 4  | Title and metadata rows              | Leading sparse rows captured as`table_name`, not discarded                                                                         |
| 5  | Trailing summary and total rows      | Moved to`footer_rows`, never silently dropped, warned                                                                              |
| 6  | Ragged rows                          | Extra cells get synthetic`column_N` names (warned); short rows padded with `None`                                                |
| 7  | Blank separator rows and columns     | Row gaps split vertically, blank column runs split side-by-side tables                                                               |
| 8  | Hidden rows, columns, sheets         | Excluded by default with a warning;`include_hidden=True` keeps them                                                                |
| 9  | Locale-variant numbers               | `1,234.56` versus `1.234,56` decided once per column from separator evidence; ambiguity warns                                    |
| 10 | Currency symbols and units           | `$1,200` and `120 kg` hoisted to `Column.unit` once, values stored bare                                                        |
| 11 | Percentages                          | `35%` columns normalized to the numeric percentage with `unit="%"`                                                               |
| 12 | Dates                                | Excel serials, several string formats,`MM/DD` versus `DD/MM` resolved by any component above 12, else month-first with a warning |
| 13 | Mixed-type columns                   | Frozen per-column dtype; nonconforming cells keep raw text plus a per-cell warning                                                   |
| 14 | Leading-zero loss                    | All-digit columns carrying leading zeros pinned to text, raw preserved                                                               |
| 15 | Scientific-notation identifiers      | Exponent forms parse to`Decimal`, never `float`, so the written digits round-trip; column flagged                                |
| 16 | Delimiter detection                  | `csv.Sniffer`, then a column-count-stability vote                                                                                  |
| 17 | Encoding detection                   | BOM sniff, then charset-normalizer on a 256 KB head; strict decode with one fallback retry                                           |
| 18 | Quoted fields with embedded newlines | stdlib csv in strict mode; broken quoting raises`CorruptFileError`                                                                 |
| 19 | Duplicate and blank headers          | Deduplicated as`name` and `name_2`; blanks named `column_N`; both warned                                                       |
| 20 | Header whitespace and casing         | Trimmed and internal whitespace collapsed, deterministic left to right                                                               |
| 21 | Cross-tab layouts                    | Flagged`pivot_layout_suspected`, left as found                                                                                     |
| 22 | Not-available sentinels              | `n/a`, `na`, `null`, `none`, `nil`, `#n/a`, and dash-only cells become `None`                                          |
| 23 | Footnote markers                     | A trailing`*`, dagger, or `(1)` stripped off a value, warned per cell                                                            |

## Warnings

Ambiguity warns; it does not raise. Warnings collect on `TableBlock.warnings` as
`ParseWarning` objects and are copied as strings onto `ChunkMetadata.warnings`. Each carries
a `WarningCode`, a message, a `confidence` in `[0.0, 1.0]`, and the sheet, row, and column
name it came from where those apply.

`WarningCode` is a `str` enum, so a member compares equal to its string value and formats as
that value. The 21 members:

| Code                         | Raised when                                                         |
| ---------------------------- | ------------------------------------------------------------------- |
| `low_confidence_encoding`  | The text encoding was guessed below`min_confidence`               |
| `low_confidence_delimiter` | The delimiter was guessed below`min_confidence`                   |
| `low_confidence_header`    | No header row was found, or the header span scored low              |
| `title_row_detected`       | Leading sparse rows were captured as`table_name`                  |
| `footer_row_detected`      | Trailing rows were moved to`footer_rows`                          |
| `short_fragment`           | A region held fewer than`min_table_rows` rows and was kept anyway |
| `ragged_row`               | A row was wider than the header, so a synthetic column was added    |
| `duplicate_header`         | A repeated column name was renamed with a numeric suffix            |
| `blank_header`             | An empty header cell was named`column_N`                          |
| `merged_value_propagated`  | A merged data value was filled down a column                        |
| `hidden_excluded`          | A hidden sheet, row, or column was dropped                          |
| `mixed_type`               | Too few cells agreed on a dtype, so the column stayed text          |
| `nonconforming_cell`       | One cell did not fit its column's frozen dtype; raw kept            |
| `ambiguous_number_locale`  | Separator evidence was inconclusive;`locale` applied              |
| `ambiguous_date_format`    | No sample proved day order; month-first assumed                     |
| `mixed_currency`           | A column mixed units or currencies; kept per cell                   |
| `leading_zero_preserved`   | A numeric-looking column with leading zeros was pinned to text      |
| `sci_notation_preserved`   | A column held exponent notation, parsed as`Decimal`               |
| `formula_no_cache`         | Formula cells had no stored result and read as empty                |
| `pivot_layout_suspected`   | A text key column sits beside period-labelled value columns         |
| `footnote_marker_stripped` | A trailing footnote marker was removed from a value                 |

Because the codes are stable, pipelines can route on them instead of matching message text:

```python
from excelplumber import WarningCode, parse

BLOCKING = {WarningCode.MIXED_TYPE, WarningCode.LOW_CONFIDENCE_HEADER}

for block in parse("sales_report.xlsx"):
    for w in block.warnings:
        if w.code in BLOCKING:
            print(f"review {block.source_file}: {w.code} at {w.column} ({w.confidence})")
        else:
            print(f"note {w.code}: {w.message}")
```

```text
note title_row_detected: 2 title/metadata row(s) captured as table name
note footer_row_detected: 1 trailing summary/footnote row(s) moved to footer_rows
note ambiguous_number_locale: column 'Revenue' separators are ambiguous; assuming 1,234.56
```

## Errors

Broken input raises. Every exception derives from `ExcelPlumberError`, so one `except` clause
catches anything the library raises on purpose.

```text
ExcelPlumberError                  # base class
+-- SourceError                    # missing, unreadable, or unsupported file
+-- CorruptFileError               # container or framing is broken
+-- EncodingError                  # undecodable after the fallback retry
+-- StructureError                 # no table structure resolved at the minimum confidence
+-- LimitExceededError             # a configured row, cell, or time cap was hit
`-- ConfigError                    # contradictory or out-of-range options
```

`StructureError` is also what you get for an empty file, and for a `sheet` name that matches
nothing in the workbook.

```python
from excelplumber import (
    CorruptFileError,
    LimitExceededError,
    SourceError,
    StructureError,
    parse,
)

for path in ("sales_report.xlsx", "missing.csv"):
    try:
        blocks = parse(path, max_rows=100_000, timeout_seconds=30)
    except SourceError as exc:
        print("skip:", exc)
    except (CorruptFileError, StructureError) as exc:
        print("quarantine:", exc)
    except LimitExceededError as exc:
        print("too big:", exc)
    else:
        print("ok:", path, len(blocks), "table(s)")
```

```text
ok: sales_report.xlsx 1 table(s)
skip: file not found: missing.csv
```

The row, cell, and timeout caps are enforced inside the reader loop, not after the fact, so
an untrusted file cannot exhaust memory before the check fires. Counters run on every row and
the deadline is checked once per 1000 rows.

## Command line interface

Two subcommands. Both exit 0 on success and 2 on any `ExcelPlumberError`, printing
`error: <ExceptionName>: <message>` to stderr. Argument parsing errors also exit 2.

### excelplumber inspect

```
excelplumber inspect FILE [--sheet SHEET]
```

A human-readable structure report. This is the fastest way to find out why a file parsed the
way it did.

```console
$ excelplumber inspect sales_report.xlsx
Table 0 on sheet 'Q3' (Quarterly Sales Report)
  rows 0..7, 4 data row(s)
  - Region: text
  - Revenue: int [USD]
  - Share: int [%]
  1 footer row(s) held out of data
  ! title_row_detected: 2 title/metadata row(s) captured as table name [Q3 r0]
  ! footer_row_detected: 1 trailing summary/footnote row(s) moved to footer_rows [Q3 r0]
  ! ambiguous_number_locale: column 'Revenue' separators are ambiguous; assuming 1,234.56 [Q3 col Revenue]
```

### excelplumber parse

```
excelplumber parse FILE [--out OUT] [--max-tokens N] [--sheet SHEET]
                        [--format {jsonl,json}] [--serializer NAME]
```

| Flag             | Default    | Meaning                                                           |
| ---------------- | ---------- | ----------------------------------------------------------------- |
| `--out`        | stdout     | Output path;`-` also means stdout                               |
| `--max-tokens` | `512`    | Token budget per chunk                                            |
| `--sheet`      | all sheets | Restrict to one sheet by name                                     |
| `--format`     | `jsonl`  | `jsonl` for one chunk per line, `json` for one indented array |
| `--serializer` | `pipe`   | `pipe`, `markdown`, `sentences`, or a name you registered   |

JSONL is the default so output pipes straight into an indexing job. Each line is one chunk
with its `id`, `text`, `data`, `token_count`, and `metadata`.

```console
$ excelplumber parse sales_report.xlsx --max-tokens 64
{"id": "122cfa3c0ef36bcf", "text": "Table: Quarterly Sales Report, Q3\nRegion | Revenue (USD) | Share (%)\nWest | 120000 | 35\nEast | 98000 | 28\nNorth | 76500 | 22", "data": [{"Region": "West", "Revenue": 120000, "Share": 35}, ...], "token_count": 59, "metadata": {"source_file": "sales_report.xlsx", "sheet_name": "Q3", "table_index": 0, "row_range": [0, 2], ...}}
{"id": "e93cc743288944c8", "text": "Table: Quarterly Sales Report, Q3\nRegion | Revenue (USD) | Share (%)\nSouth | 51200 | 15", "data": [{"Region": "South", "Revenue": 51200, "Share": 15}], "token_count": 38, "metadata": {"source_file": "sales_report.xlsx", "sheet_name": "Q3", "table_index": 0, "row_range": [3, 3], ...}}
```

(`...` marks fields elided for width; the real output is one complete JSON object per line.)

Decimal values are written as bare JSON number literals with their exact digits rather than
being routed through `float`. Dates and datetimes are written as ISO strings.

The CLI covers the chunking path only. Parse-side options beyond `--sheet`, such as `locale`
or `include_hidden`, are available through the Python API.

## Common workflows

### Index a directory into a vector store

```python
from pathlib import Path

import excelplumber

for path in sorted(Path("inbox").glob("*.xlsx")):
    for chunk in excelplumber.iter_chunks(path, max_tokens=512):
        store.add(
            id=chunk.id,
            text=chunk.text,
            metadata={
                "source": chunk.metadata.source_file,
                "sheet": chunk.metadata.sheet_name,
                "table": chunk.metadata.table_index,
                "rows": list(chunk.metadata.row_range),
            },
        )
```

`Chunk.id` is a 16-character SHA-1 prefix over the source path, sheet, table index, column
group, and row range. It is stable across runs, which makes it usable as an upsert key: a
re-ingest of an unchanged file overwrites the same documents rather than duplicating them.

`ChunkMetadata.row_range` starts at the table's first source row and advances by the number
of rows already emitted, so consecutive chunks partition the table's data rows without gaps.
It is not an exact source-row lookup when title or header rows sit inside the table's span.
`TableBlock.row_range` is the absolute source span of the region.

### Gate ingestion on parse quality

```python
import excelplumber
from excelplumber import WarningCode

NEEDS_REVIEW = {
    WarningCode.LOW_CONFIDENCE_HEADER,
    WarningCode.MIXED_TYPE,
    WarningCode.PIVOT_LAYOUT_SUSPECTED,
    WarningCode.FORMULA_NO_CACHE,
}


def is_clean(path) -> bool:
    blocks = excelplumber.parse(path)
    return not any(w.code in NEEDS_REVIEW for b in blocks for w in b.warnings)
```

### Force encoding and delimiter for a known feed

Detection is for files you did not produce. When the format is contractual, pin it and skip
the guessing:

```python
blocks = excelplumber.parse("feed.csv", encoding="cp1252", delimiter=";", locale="eu")
```

### Cap work on untrusted uploads

```python
from excelplumber import LimitExceededError, ParseOptions, parse

options = ParseOptions(max_rows=500_000, max_cells=20_000_000, timeout_seconds=60)
try:
    blocks = parse(upload_path, options)
except LimitExceededError as exc:
    reject(upload_path, str(exc))
```

### Keep hidden data

Hidden rows, columns, and sheets are dropped by default and reported as `hidden_excluded`.
For an audit pass where the hidden content matters:

```python
blocks = excelplumber.parse("report.xlsx", include_hidden=True)
```

## Extension points

Three registries are public. A fourth mechanism, the entry-point group, lets a separate
package add a reader without any import from your code.

### Custom chunk serializers

`register_serializer(name, fn=None)` takes a callable
`(table_label: str, columns: list[Column], rows: list[dict]) -> str`. It works as a decorator
or as a direct call. Select it with `ChunkOptions(serializer=name)`.

```python
from excelplumber import ChunkOptions, chunk, parse, register_serializer


@register_serializer("tsv")
def serialize_tsv(table_label, columns, rows):
    header = "\t".join(c.name for c in columns)
    lines = [table_label, header]
    for row in rows:
        lines.append("\t".join(
            "" if row.get(c.name) is None else str(row[c.name]) for c in columns
        ))
    return "\n".join(lines)


chunks = chunk(parse("sales_report.xlsx"), options=ChunkOptions(serializer="tsv"))
print(chunks[0].text)
```

```text
Table: Quarterly Sales Report, Q3
Region	Revenue	Share
West	120000	35
East	98000	28
North	76500	22
South	51200	15
```

The serializer is called twice per row batch during packing: once with an empty row list to
measure the header, and once per row to measure that row. Keep it cheap and keep it pure.
The built-in `pipe` and `markdown` serializers escape literal `|` characters in values; a
custom serializer is responsible for its own escaping.

### Custom normalizers

`register_normalizer(dtype, fn=None)` overrides the per-cell coercion for one dtype. The
callable takes `(value, context)` and returns the normalized value. `context` is a dict with
`profile` (the frozen `ColumnProfile`), `sheet`, and `row`. A registered normalizer replaces
the built-in path for that dtype entirely, including its warning emission.

```python
from excelplumber import parse, register_normalizer


@register_normalizer("text")
def upper_text(value, context):
    return value.upper() if isinstance(value, str) else value


block = parse("sales_report.xlsx")[0]
print([r["Region"] for r in block.rows])
```

```text
['WEST', 'EAST', 'NORTH', 'SOUTH']
```

Registration is global and process-wide. Register at import time in application code rather
than inside a library that others import.

### Custom readers

`register_reader` is a class decorator. A reader is any class satisfying the `Reader`
protocol in `src/excelplumber/readers/base.py`: two class attributes (`name`, `extensions`)
and five methods (`sniff`, `open`, `sheets`, `rows`, `close`). `rows` must be lazy and must
call `LimitGuard.tick` once per row so the configured caps are enforced.

The built-in TSV reader is the smallest possible example, a subclass that pins the delimiter
and claims its own extension:

```python
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
```

A reader for a new format from scratch, here fixed-width text:

```python
from pathlib import Path
from typing import ClassVar

from excelplumber import ParseOptions, parse, register_reader
from excelplumber.readers.base import Cell, LimitGuard, RowStream, SheetInfo

WIDTHS = (10, 8)


@register_reader
class FixedWidthReader:
    extensions: ClassVar[tuple[str, ...]] = (".fw",)
    name: ClassVar[str] = "fixedwidth"

    @classmethod
    def sniff(cls, head: bytes, path: Path) -> float:
        return 0.9 if path.suffix.lower() in cls.extensions else 0.0

    def open(self, path: Path, opts: ParseOptions) -> None:
        self._path = path
        self._opts = opts

    def sheets(self):
        yield SheetInfo(name=self._path.stem, index=0)

    def rows(self, sheet: SheetInfo) -> RowStream:
        guard = LimitGuard(self._opts)
        with self._path.open(encoding="utf-8") as f:
            for r, line in enumerate(f):
                cells = []
                start = 0
                for c, width in enumerate(WIDTHS):
                    value = line[start:start + width].strip()
                    start += width
                    if value:
                        cells.append(Cell(row=r, col=c, value=value, raw=value))
                guard.tick(len(cells))
                yield cells

    def close(self) -> None:
        pass


block = parse("inventory.fw")[0]
print([(c.name, c.dtype) for c in block.columns])
print(block.rows)
```

Given an `inventory.fw` holding `Item`/`Qty` columns in 10- and 8-character fields:

```text
[('Item', 'text'), ('Qty', 'int')]
[{'Item': 'Widget', 'Qty': 40}, {'Item': 'Gadget', 'Qty': 12}]
```

Detectors, normalizers, and the chunker never branch on a format name. They branch on
`SheetInfo.capabilities`, a set of strings the reader declares. The XLSX reader declares
`merges`, `hidden`, and `formulas`. A reader that cannot report merges simply omits the
capability. A test in `tests/test_readers.py` fails the build if a format literal appears
outside `src/excelplumber/readers/` and `src/excelplumber/cli.py`.

### Shipping a reader as a plugin

A separate distribution can register a reader through the `excelplumber.readers`
[entry-point group](https://packaging.python.org/en/latest/specifications/entry-points/). In
the plugin's `pyproject.toml`:

```toml
[project.entry-points."excelplumber.readers"]
parquet = "excelplumber_parquet:ParquetReader"
```

The entry point must resolve to the reader class itself. excelplumber loads the group when
`excelplumber.readers` is first imported and passes each loaded object to `register_reader`.
A plugin that fails to import degrades to a `UserWarning` naming the entry point rather than
breaking the import of excelplumber.

## Limitations

Cross-tab layouts are flagged as `pivot_layout_suspected`, not un-pivoted, because choosing
which column is the measure is a guess and getting it wrong is worse than leaving the table
as found. Cross-sheet relationships are not resolved: each sheet is parsed on its own.

Legacy binary `.xls`, charts, images, and cell formatting are out of scope. So are formulas:
a cell whose formula was saved without a cached result reads as empty and is reported, never
evaluated.

A chunk is never split mid-row, so a row whose serialized form is larger than `max_tokens`
produces one oversized chunk rather than a truncated one. Under
`wide_strategy="sentences"`, whole rows routinely exceed the budget, because the sentence
form repeats every column name in every row. The column-group strategy bounds the header
cost but not the row cost.

`estimate_tokens` is a character-class heuristic tuned for ASCII. Its accuracy drops for CJK
and RTL text. Pass an exact `token_counter` when the budget has to be tight.

The header block spans at most three rows. Deeper header stacks lose their upper levels.

Registries are process-global, so a registered reader, normalizer, or serializer affects
every caller in the process.

## Troubleshooting

**`SourceError: no registered reader recognizes <file>`.** No reader scored above 0.2 on the
first 8 KB. The file is probably not tabular text or a zip container. If you know the format,
bypass sniffing with `parse(path, format="csv")`.

**The whole file parsed as one column.** The delimiter vote picked the wrong character.
Check `block.warnings` for `low_confidence_delimiter`, then pin it:
`parse(path, delimiter=";")`.

**Accented characters came out wrong.** Encoding detection reads a 256 KB head; a file whose
distinguishing bytes appear later can be misread. Look for `low_confidence_encoding` and pin
the encoding: `parse(path, encoding="cp1252")`.

**Numbers are off by a factor of 1000.** A `1.234` value was read under the wrong separator
convention. Look for `ambiguous_number_locale`. Set `parse(path, locale="eu")` for
`1.234,56` files.

**The first data row became the header, or the header became a data row.** Look for
`low_confidence_header` and run `excelplumber inspect` to see the resolved header span. A
sheet with fewer than `min_confidence` worth of evidence gets synthetic `column_N` names.

**One table came back as two, or two came back as one.** Region splitting keys off blank
runs. Raise `blank_row_gap` to merge tables separated by a single blank row; lower it to
split more eagerly. `blank_col_gap` does the same horizontally.

**A `Total` row is missing from `rows`.** It is in `footer_rows`, which is the point. See the
`footer_row_detected` warning.

**`footer_rows` is empty on a `TableStream`.** Footer detection needs the end of the stream.
Drain `rows` before reading `footer_rows`. `parse` does this for you.

**`KeyError` when chunking.** `ChunkOptions.serializer` names a serializer that is not
registered. The built-ins are `pipe`, `markdown`, and `sentences`.

**`TypeError: ParseOptions.__init__() got an unexpected keyword argument`.** A keyword passed
to `parse` is not a `ParseOptions` field. See [ParseOptions](#parseoptions) for the list.

**`StructureError: no table found`.** The file is empty, holds no populated cells, or
`sheet` names a sheet that does not exist in the workbook.

**Memory grows on a large file even with `iter_chunks`.** The table is wide enough to be
split into column groups, and every group needs every row. Raise `max_tokens`, or set
`wide_strategy="sentences"` to keep a single pass.

**`.\.venv\Scripts\Activate.ps1` is blocked on Windows.** See
[Windows](#windows) for the execution-policy fix.

## Development

```bash
git clone https://github.com/raadongithub/excelplumber.git
cd excelplumber
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Conventions worth knowing before sending a patch:

- No format library or format-name literal outside `src/excelplumber/readers/` and
  `src/excelplumber/cli.py`. A test enforces this.
- Numbers parse to `int` or `Decimal`, never `float`, so output stays identical across
  platforms.
- Ambiguity produces a `ParseWarning` with a stable code. Only broken input raises.
- Adding a warning means adding a `WarningCode` member, which is a public API change.

Bug reports and pull requests go to
[github.com/raadongithub/excelplumber/issues](https://github.com/raadongithub/excelplumber/issues).

## Packaging and distribution

Built with [hatchling](https://hatch.pypa.io/latest/). The version is read from
`__version__` in `src/excelplumber/__init__.py`, so that string is the single source of
truth for both the installed package and `pyproject.toml`.

```bash
pip install build twine
python -m build          # writes dist/excelplumber-<version>-py3-none-any.whl and .tar.gz
twine check dist/*
```

Notes for downstream packagers:

- Import name and distribution name are both `excelplumber`.
- The wheel is pure Python and platform independent (`py3-none-any`).
- `py.typed` ships in the package, so type checkers use the inline annotations directly. No
  stub package is needed.
- The console script is declared as `excelplumber = "excelplumber.cli:main"`. `main` returns
  the process exit code.
- The `excelplumber.readers` entry-point group is declared empty in `pyproject.toml` so the
  group exists for plugins to extend.
- Runtime pins are lower bounds: `openpyxl>=3.1`, `charset-normalizer>=3.0`,
  `python-dateutil>=2.8`. The only extra is `dev`, which adds `pytest>=8.0`.

Version history is in [CHANGELOG.md](https://github.com/raadongithub/excelplumber/blob/main/CHANGELOG.md), which follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The public API for versioning
purposes is the `__all__` list in `src/excelplumber/__init__.py` plus the `WarningCode`
values and the CLI flags.

## License

MIT. See [LICENSE](https://github.com/raadongithub/excelplumber/blob/main/LICENSE).
