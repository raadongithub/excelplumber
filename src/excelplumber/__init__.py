"""ExcelPlumber: edge-case-hardened CSV/Excel parsing for RAG chunking.

Quickstart:

    import excelplumber

    blocks = excelplumber.parse("report.xlsx")
    chunks = excelplumber.chunk(blocks)
"""

from .chunker import chunk, iter_chunks, register_serializer
from .config import ChunkOptions, ParseOptions
from .errors import (
    ConfigError,
    CorruptFileError,
    EncodingError,
    ExcelPlumberError,
    LimitExceededError,
    SourceError,
    StructureError,
)
from .models import (
    Chunk,
    ChunkMetadata,
    Column,
    ParseWarning,
    TableBlock,
    WarningCode,
)
from .normalizers import register_normalizer
from .parser import iter_tables, parse
from .readers import register_reader
from .tokens import TokenCounter, estimate_tokens

__version__ = "0.1.0"

__all__ = [
    "parse",
    "iter_tables",
    "chunk",
    "iter_chunks",
    "estimate_tokens",
    "TokenCounter",
    "TableBlock",
    "Column",
    "Chunk",
    "ChunkMetadata",
    "ParseWarning",
    "WarningCode",
    "ParseOptions",
    "ChunkOptions",
    "ExcelPlumberError",
    "SourceError",
    "CorruptFileError",
    "EncodingError",
    "StructureError",
    "LimitExceededError",
    "ConfigError",
    "register_reader",
    "register_normalizer",
    "register_serializer",
    "__version__",
]
