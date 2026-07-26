"""Shared image-processing helpers for extractor strategies.

Two utilities used by every chroma-key-aware extractor:

* ``remove_chroma_background`` turns flat chroma-key pixels transparent
  and applies an alpha-only edge cleanup pass (erode + Gaussian blur) to
  remove anti-aliased halo pixels that the colour-distance test alone
  cannot catch. RGB is left untouched so legitimate design colours are
  never altered.
* ``fit_to_cell`` crops to the non-transparent bounding box, scales the
  result to fit a target cell with safe padding, and centres it.
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageFilter


def color_distance(red: int, green: int, blue: int, key: tuple[int, int, int]) -> float:
    return math.sqrt(
        (red - key[0]) ** 2 + (green - key[1]) ** 2 + (blue - key[2]) ** 2
    )


def remove_chroma_background(
    image: Image.Image,
    chroma_key: tuple[int, int, int],
    threshold: float,
    *,
    alpha_erode_px: int = 1,
    alpha_blur_radius: float = 1.0,
) -> Image.Image:
    """Zero alpha for chroma-key pixels and clean up the edge halo.

    The colour-distance threshold catches solid chroma-coloured pixels but
    leaves anti-aliased fringe pixels (e.g. ``(120, 0, 120)`` next to a
    magenta key) intact, which shows up as a visible halo at large cell
    sizes. The alpha-only erode + blur pass shrinks the alpha mask by
    ``alpha_erode_px`` pixels and softens its edge with a Gaussian blur.

    To avoid a magenta halo, RGB is also zeroed wherever alpha drops to 0
    (both at threshold time and after erosion). The visible silhouette's
    RGB inside the alpha mask is left untouched.

    The blur is clamped so it can only lower alpha. Left unclamped it raised
    alpha back up on exactly those zeroed-RGB pixels, trading the magenta
    halo for a black one — which is what the shipped examples show.
    """

    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            if color_distance(red, green, blue, chroma_key) <= threshold:
                pixels[x, y] = (0, 0, 0, 0)

    if alpha_erode_px <= 0 and alpha_blur_radius <= 0:
        return rgba

    red_band, green_band, blue_band, alpha_band = rgba.split()

    if alpha_erode_px > 0:
        kernel = 2 * alpha_erode_px + 1
        alpha_band = alpha_band.filter(ImageFilter.MinFilter(kernel))
        # Where erosion just zeroed alpha (fringe band), zero RGB too so
        # the upcoming blur cannot revive a magenta-tinted edge.
        keep_mask = alpha_band.point(lambda value: 255 if value > 0 else 0)
        black = Image.new("L", alpha_band.size, 0)
        red_band = Image.composite(red_band, black, keep_mask)
        green_band = Image.composite(green_band, black, keep_mask)
        blue_band = Image.composite(blue_band, black, keep_mask)

    if alpha_blur_radius > 0:
        # Clamp the blur so it can only ever LOWER alpha. An unclamped
        # Gaussian raises alpha on pixels just outside the silhouette, whose
        # RGB was zeroed above — reviving them paints a dark ring. Measured on
        # a saturated disc: 548 of 1068 partial-alpha pixels came back
        # near-black; clamped, none do. The same ring is visible in the
        # shipped monoline-suite masters (1258-1798 dark fringe pixels each).
        alpha_band = ImageChops.darker(
            alpha_band,
            alpha_band.filter(ImageFilter.GaussianBlur(radius=alpha_blur_radius)),
        )

    return Image.merge("RGBA", (red_band, green_band, blue_band, alpha_band))


DEFAULT_CELL_PADDING_PX = 10


def fit_to_cell(
    image: Image.Image,
    cell_width: int,
    cell_height: int,
    *,
    padding_px: int = DEFAULT_CELL_PADDING_PX,
    allow_upscale: bool = False,
) -> Image.Image:
    """Crop to the visible bounding box, scale into the cell, and centre it.

    ``padding_px`` is the total space reserved across each axis, so the sprite
    can occupy at most ``(cell - padding_px)`` pixels in each direction. It is
    a parameter rather than a constant because ``validator.validate_atlas``
    has to compute the same attainable area to decide whether a cell kept its
    background — the two read it from the same extractor profile key
    (``cell_padding_px``), and they must not drift apart.

    Callers that need the same design at several sizes should fit ONCE at the
    largest size and downscale the result. Fitting per size applies a constant
    pixel padding to different cell sizes, which makes the small file not a
    downscale of the large one.

    ``allow_upscale`` controls whether a sprite smaller than the cell is
    enlarged to fill it. Extraction leaves it off: an atlas cell should show
    the art at its generated resolution. Pack normalisation turns it on,
    because otherwise a design whose silhouette happens to be smaller ships at
    a different visual weight than its packmates — measured at 0.87 against
    0.98 of the cell in the same run.
    """

    bbox = image.getbbox()
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if bbox is None:
        return target

    sprite = image.crop(bbox)
    max_width = max(1, cell_width - padding_px)
    max_height = max(1, cell_height - padding_px)
    scale = min(max_width / sprite.width, max_height / sprite.height)
    if not allow_upscale:
        scale = min(scale, 1.0)
    if scale != 1.0:
        sprite = sprite.resize(
            (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
            Image.Resampling.LANCZOS,
        )
    left = (cell_width - sprite.width) // 2
    top = (cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return target
