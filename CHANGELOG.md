# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-17

First release.

### Added

- `parse()` and `iter_tables()` for turning a CSV, TSV, or Excel file into
  typed `TableBlock` objects.
- `chunk()` and `iter_chunks()` for token-budgeted, self-describing chunks
  with provenance metadata.
- Reader layer with content-first format sniffing: CSV, TSV, and a two-pass
  XLSX reader that streams values while still resolving merged ranges and
  hidden rows, columns, and sheets.
- Structure detection: multi-table boundaries, multi-row header flattening,
  title rows, trailing summary rows, and ragged rows.
- Type profiling and normalization: locale-aware numbers, currency and unit
  hoisting, percentages, Excel serial dates, and mixed-type columns with
  per-cell warnings instead of silent coercion.
- Typed error hierarchy, configurable row, cell, and timeout caps, and stable
  `WarningCode` values for every guess the parser makes.
- `excelplumber parse` and `excelplumber inspect` command line entry points.
- Registries for readers, normalizers, and chunk serializers, plus an
  `excelplumber.readers` entry-point group for out-of-tree formats.
