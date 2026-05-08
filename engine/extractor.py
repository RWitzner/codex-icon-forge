"""Extractor strategy registry.

Each strategy takes a horizontal sprite strip image and a target frame count,
and returns N frames cropped/processed into the target cell size. The active
strategy is selected by ExtractorProfile.strategy.
"""

from __future__ import annotations

from typing import Callable, Protocol

from PIL import Image

from .profiles import AtlasProfile, ExtractorProfile, StateSpec


class ExtractStrategy(Protocol):
    def __call__(
        self,
        strip: Image.Image,
        state: StateSpec,
        atlas: AtlasProfile,
        extractor: ExtractorProfile,
        *,
        chroma_key: tuple[int, int, int],
    ) -> tuple[list[Image.Image], str]:
        """Return (frames, method_used) for the given strip."""


_REGISTRY: dict[str, ExtractStrategy] = {}


def register(name: str) -> Callable[[ExtractStrategy], ExtractStrategy]:
    def decorator(strategy: ExtractStrategy) -> ExtractStrategy:
        _REGISTRY[name] = strategy
        return strategy

    return decorator


def get(name: str) -> ExtractStrategy:
    if name not in _REGISTRY:
        raise KeyError(f"no extractor strategy registered for {name!r} (have {list(_REGISTRY)})")
    return _REGISTRY[name]


def registered() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# Importing the package's bundled strategies populates the registry.
from .extractors import chroma_key_slots  # noqa: E402,F401
from .extractors import slot_only  # noqa: E402,F401
