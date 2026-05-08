"""Chroma-key selection.

Picks the chroma-key colour that maximises distance from any non-background
pixel in the supplied references. The candidate list comes from the active
style profile.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from PIL import Image

from .profiles import StyleProfile

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def parse_hex_color(value: str) -> tuple[int, int, int]:
    if not _HEX_COLOR.match(value):
        raise ValueError(f"invalid chroma key color: {value}; expected #RRGGBB")
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def _color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _sampled_reference_pixels(paths: list[Path]) -> list[tuple[int, int, int]]:
    pixels: list[tuple[int, int, int]] = []
    for path in paths:
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
            image.thumbnail((128, 128), Image.Resampling.LANCZOS)
            data = image.tobytes()
            for index in range(0, len(data), 4):
                red, green, blue, alpha = data[index : index + 4]
                if alpha <= 16:
                    continue
                pixels.append((red, green, blue))

    non_background = [
        pixel
        for pixel in pixels
        if not (pixel[0] > 244 and pixel[1] > 244 and pixel[2] > 244)
    ]
    return non_background or pixels


def choose_chroma_key(
    style: StyleProfile,
    reference_paths: list[Path],
    requested: str = "auto",
) -> dict[str, Any]:
    if requested.lower() != "auto":
        rgb = parse_hex_color(requested)
        return {
            "hex": rgb_to_hex(rgb),
            "rgb": list(rgb),
            "name": "user-selected",
            "selection": "manual",
        }

    candidates = style.chroma_key.candidates
    if not candidates:
        raise ValueError(f"style profile {style.id!r} has no chroma_key candidates")

    pixels = _sampled_reference_pixels(reference_paths)
    if not pixels:
        first = candidates[0]
        rgb = parse_hex_color(first.hex)
        return {
            "hex": rgb_to_hex(rgb),
            "rgb": list(rgb),
            "name": first.name,
            "selection": "fallback",
        }

    scored: list[tuple[float, int, str, tuple[int, int, int]]] = []
    for preference_index, candidate in enumerate(candidates):
        rgb = parse_hex_color(candidate.hex)
        distances = sorted(_color_distance(rgb, pixel) for pixel in pixels)
        percentile_index = max(0, min(len(distances) - 1, int(len(distances) * 0.01)))
        scored.append((distances[percentile_index], -preference_index, candidate.name, rgb))

    score, _preference, name, rgb = max(scored)
    return {
        "hex": rgb_to_hex(rgb),
        "rgb": list(rgb),
        "name": name,
        "selection": "auto",
        "score": round(score, 2),
    }
