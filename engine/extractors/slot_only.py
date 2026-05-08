"""Slot-only extractor: equal-width crops, no chroma key, no components.

Useful for styles where the model emits transparent backgrounds directly or
where component separation cannot be relied on (soft-edged painterly art).
"""

from __future__ import annotations

from PIL import Image

from ..extractor import register
from ..profiles import AtlasProfile, ExtractorProfile, StateSpec
from ._helpers import fit_to_cell


@register("slot-only")
def extract(
    strip: Image.Image,
    state: StateSpec,
    atlas: AtlasProfile,
    extractor: ExtractorProfile,
    *,
    chroma_key: tuple[int, int, int],
) -> tuple[list[Image.Image], str]:
    cell_width = atlas.geometry.cell_width
    cell_height = atlas.geometry.cell_height
    rgba = strip.convert("RGBA")
    slot_width = rgba.width / state.frames
    frames = []
    for index in range(state.frames):
        left = round(index * slot_width)
        right = round((index + 1) * slot_width)
        crop = rgba.crop((left, 0, right, rgba.height))
        frames.append(fit_to_cell(crop, cell_width, cell_height))
    return frames, "slots"
