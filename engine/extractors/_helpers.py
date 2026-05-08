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

from PIL import Image, ImageFilter


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

    To avoid a magenta halo when blur revives partial alpha on previously
    chroma-killed pixels, RGB is also zeroed wherever alpha drops to 0
    (both at threshold time and after erosion). The visible silhouette's
    RGB inside the alpha mask is left untouched.
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
        alpha_band = alpha_band.filter(
            ImageFilter.GaussianBlur(radius=alpha_blur_radius)
        )

    return Image.merge("RGBA", (red_band, green_band, blue_band, alpha_band))


def fit_to_cell(image: Image.Image, cell_width: int, cell_height: int) -> Image.Image:
    bbox = image.getbbox()
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if bbox is None:
        return target

    sprite = image.crop(bbox)
    max_width = cell_width - 10
    max_height = cell_height - 10
    scale = min(max_width / sprite.width, max_height / sprite.height, 1.0)
    if scale != 1.0:
        sprite = sprite.resize(
            (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
            Image.Resampling.LANCZOS,
        )
    left = (cell_width - sprite.width) // 2
    top = (cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return target
