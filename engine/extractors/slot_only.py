"""Slot-only extractor: equal-width crops, no chroma key, no components.

Useful for styles where the model emits transparent backgrounds directly or
where component separation cannot be relied on (soft-edged painterly art).
"""

from __future__ import annotations

from PIL import Image, ImageOps

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
    preserve_full_bleed = bool(extractor.params.get("preserve_full_bleed", False))
    rgba = strip.convert("RGBA")
    slot_width = rgba.width / state.frames
    frames = []
    for index in range(state.frames):
        left = round(index * slot_width)
        right = round((index + 1) * slot_width)
        crop = rgba.crop((left, 0, right, rgba.height))
        if preserve_full_bleed:
            frames.append(
                ImageOps.fit(
                    crop,
                    (cell_width, cell_height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            )
        else:
            frames.append(fit_to_cell(crop, cell_width, cell_height))
    return frames, "slots"
