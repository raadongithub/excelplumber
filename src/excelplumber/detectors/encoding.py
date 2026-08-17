"""Encoding and delimiter detection for textual sources."""

import csv
from dataclasses import dataclass

from charset_normalizer import from_bytes

from ..errors import EncodingError

# 256 KB head is enough evidence for both charset and dialect; never read the whole file.
HEAD_SIZE = 256 * 1024

_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

_DELIMITERS = (",", ";", "\t", "|")


@dataclass(frozen=True)
class Detection:
    """One detector decision with its confidence and supporting evidence."""

    value: str
    confidence: float
    evidence: str
    runner_up: str | None = None


def detect_encoding(head: bytes) -> Detection:
    """Detect the text encoding of a byte head.

    BOM wins outright when present; otherwise charset-normalizer votes on
    the sample and the runner up is kept for a one-shot decode retry.
    """
    for bom, name in _BOMS:
        if head.startswith(bom):
            return Detection(name, 1.0, f"byte order mark {bom!r}")
    results = from_bytes(head)
    best = results.best()
    if best is None:
        return Detection("utf-8", 0.3, "no charset candidate; defaulting", "latin-1")
    ranked = [r.encoding for r in results]
    runner_up = next((e for e in ranked if e != best.encoding), "latin-1")
    confidence = 1.0 - best.chaos
    return Detection(best.encoding, confidence, "charset-normalizer vote", runner_up)


def decode_head(head: bytes, det: Detection) -> tuple[str, str]:
    """Decode a head strictly, retrying once with the runner up encoding.

    Returns:
        The decoded text and the encoding that succeeded.

    Raises:
        EncodingError: If both the detected and fallback encodings fail.
    """
    try:
        return head.decode(det.value, errors="strict"), det.value
    except (UnicodeDecodeError, LookupError):
        fallback = det.runner_up or "latin-1"
        try:
            return head.decode(fallback, errors="strict"), fallback
        except (UnicodeDecodeError, LookupError) as exc:
            raise EncodingError(
                f"could not decode file as {det.value} or {fallback}: {exc}"
            ) from exc


def detect_delimiter(text_head: str) -> Detection:
    """Detect the CSV delimiter from a decoded head.

    csv.Sniffer runs first; if it fails or the sample is degenerate, the
    candidate whose per-line field count is most stable over the first 50
    non-blank lines wins.
    """
    sample = text_head[:HEAD_SIZE]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_DELIMITERS))
        return Detection(dialect.delimiter, 0.9, "csv.Sniffer")
    except csv.Error:
        pass
    lines = [ln for ln in sample.splitlines() if ln.strip()][:50]
    if not lines:
        return Detection(",", 0.3, "empty sample; defaulting to comma")
    best_delim, best_score = ",", -1.0
    for d in _DELIMITERS:
        counts = [ln.count(d) for ln in lines]
        if max(counts) == 0:
            continue
        modal = max(set(counts), key=counts.count)
        if modal == 0:
            continue
        stability = counts.count(modal) / len(counts)
        # Prefer the delimiter that splits lines consistently; ties go to wider splits.
        score = stability + modal * 0.001
        if score > best_score:
            best_delim, best_score = d, score
    if best_score < 0:
        return Detection(",", 0.3, "no candidate splits any line; defaulting")
    return Detection(best_delim, min(best_score, 0.85), "column-count stability vote")
