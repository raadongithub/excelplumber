"""Token counting: a fast character-class estimator plus a pluggable
counter interface for exact tokenizers like tiktoken."""

import re
from collections.abc import Callable

TokenCounter = Callable[[str], int]

_WORD = re.compile(r"[A-Za-z]+|\d|[^\sA-Za-z\d]")


def estimate_tokens(text: str) -> int:
    """Estimate BPE token count from character classes.

    ASCII words cost roughly one token per 4 characters (minimum one),
    digits and punctuation roughly one each. Meaningfully closer to BPE
    than len/4 while staying dependency free.
    """
    total = 0
    for m in _WORD.finditer(text):
        piece = m.group()
        if piece[0].isalpha():
            total += max(1, (len(piece) + 3) // 4)
        else:
            total += 1
    return max(total, 1) if text else 0
