"""Reader registry and source dispatch.

A new format is one new module defining a Reader plus one @register_reader
decorator. Third-party packages register through the excelplumber.readers
entry-point group.
"""

import warnings as _pywarnings
from importlib.metadata import entry_points
from pathlib import Path

from ..config import ParseOptions
from ..errors import SourceError
from .base import Cell, LimitGuard, Reader, RowStream, SheetInfo

__all__ = [
    "Cell",
    "LimitGuard",
    "Reader",
    "RowStream",
    "SheetInfo",
    "REGISTRY",
    "register_reader",
    "open_source",
]

REGISTRY: dict[str, type] = {}

_SNIFF_HEAD = 8192


def register_reader(cls: type) -> type:
    """Class decorator adding a Reader implementation to the registry."""
    REGISTRY[cls.name] = cls
    return cls


def _load_entry_point_readers() -> None:
    # A broken third-party plugin must degrade to a warning, never break import.
    try:
        eps = entry_points(group="excelplumber.readers")
    except Exception:
        return
    for ep in eps:
        try:
            register_reader(ep.load())
        except Exception as exc:
            _pywarnings.warn(f"could not load reader plugin {ep.name}: {exc}", stacklevel=1)


def open_source(path: str | Path, opts: ParseOptions | None = None) -> Reader:
    """Resolve a path to an opened Reader.

    Resolution order: explicit opts.format override, then content sniffing
    over the first 8 KB, then file extension. Content beats extension
    because misnamed files are common in the wild.

    Raises:
        SourceError: If the file is missing or no registered reader claims it.
    """
    opts = opts or ParseOptions()
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"file not found: {path}")
    if opts.format:
        cls = REGISTRY.get(opts.format)
        if cls is None:
            raise SourceError(
                f"unknown format {opts.format!r}; registered: {sorted(REGISTRY)}"
            )
    else:
        try:
            with path.open("rb") as f:
                head = f.read(_SNIFF_HEAD)
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {exc}") from exc
        scored = sorted(
            ((c.sniff(head, path), name, c) for name, c in REGISTRY.items()),
            key=lambda t: (-t[0], t[1]),
        )
        best_score, _, cls = scored[0]
        # Below 0.2 nothing actually recognized the content; refusing beats
        # decoding binary noise as a one-column CSV.
        if best_score < 0.2:
            raise SourceError(
                f"no registered reader recognizes {path.name}; "
                f"registered formats: {sorted(REGISTRY)}"
            )
    reader = cls()
    reader.open(path, opts)
    return reader


from . import csv_reader as _csv_reader  # noqa: E402,F401
from . import xlsx_reader as _xlsx_reader  # noqa: E402,F401

_load_entry_point_readers()
