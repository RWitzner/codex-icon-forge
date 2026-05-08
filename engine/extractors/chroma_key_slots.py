"""Chroma-key + equal-slot extractor.

Removes a flat chroma-key background, then crops each frame as an equal slot
of the strip and fits the resulting alpha-keyed image into the target cell.

Used by sticker-pack and app-icon style bundles where each cell is one whole
illustration that may consist of multiple disconnected sub-shapes (rocket
flames, two diverging arrows, an X mark over a server, etc.). Component-based
extraction would lose the smaller sub-shapes; slot-based extraction keeps
everything in one frame.
"""

from __future__ import annotations

from PIL import Image

from ..extractor import register
from ..profiles import AtlasProfile, ExtractorProfile, StateSpec
from ._helpers import fit_to_cell, remove_chroma_background

_DEFAULT_KEY_THRESHOLD = 96.0
_DEFAULT_ALPHA_ERODE_PX = 1
_DEFAULT_ALPHA_BLUR_RADIUS = 1.0


@register("chroma-key-slots")
def extract(
    strip: Image.Image,
    state: StateSpec,
    atlas: AtlasProfile,
    extractor: ExtractorProfile,
    *,
    chroma_key: tuple[int, int, int],
) -> tuple[list[Image.Image], str]:
    threshold = float(extractor.params.get("key_threshold", _DEFAULT_KEY_THRESHOLD))
    alpha_erode_px = int(
        extractor.params.get("alpha_erode_px", _DEFAULT_ALPHA_ERODE_PX)
    )
    alpha_blur_radius = float(
        extractor.params.get("alpha_blur_radius", _DEFAULT_ALPHA_BLUR_RADIUS)
    )
    cell_width = atlas.geometry.cell_width
    cell_height = atlas.geometry.cell_height

    cleaned = remove_chroma_background(
        strip,
        chroma_key,
        threshold,
        alpha_erode_px=alpha_erode_px,
        alpha_blur_radius=alpha_blur_radius,
    )
    slot_width = cleaned.width / state.frames
    frames = []
    for index in range(state.frames):
        left = round(index * slot_width)
        right = round((index + 1) * slot_width)
        crop = cleaned.crop((left, 0, right, cleaned.height))
        frames.append(fit_to_cell(crop, cell_width, cell_height))
    return frames, "chroma-key-slots"
