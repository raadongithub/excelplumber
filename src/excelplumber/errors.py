"""Typed exception hierarchy. Broken input raises; ambiguous input warns."""


class ExcelPlumberError(Exception):
    """Base class for every error raised by excelplumber."""


class SourceError(ExcelPlumberError):
    """The source file is missing, unreadable, or of an unsupported format."""


class CorruptFileError(ExcelPlumberError):
    """The file exists but its container or framing is broken."""


class EncodingError(ExcelPlumberError):
    """The file could not be decoded after trying the fallback encoding."""


class StructureError(ExcelPlumberError):
    """No table structure could be resolved at the minimum confidence."""


class LimitExceededError(ExcelPlumberError):
    """A configured row, cell, or time limit was exceeded during parsing."""


class ConfigError(ExcelPlumberError):
    """The supplied options are contradictory or out of range."""
