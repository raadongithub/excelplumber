"""Normalizer registry: register_normalizer(dtype, fn) adds a custom
per-cell coercion for a dtype produced by a custom profiler or plugin."""

from collections.abc import Callable

NormalizerFn = Callable[[object, dict], object]

REGISTRY: dict[str, NormalizerFn] = {}


def register_normalizer(dtype: str, fn: NormalizerFn | None = None):
    """Register a normalizer for a dtype; usable as a decorator."""
    if fn is not None:
        REGISTRY[dtype] = fn
        return fn

    def deco(f: NormalizerFn) -> NormalizerFn:
        REGISTRY[dtype] = f
        return f

    return deco
