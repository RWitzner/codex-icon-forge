"""Deterministic contracts for game-tile coherence.

The image model still owns the artwork. These contracts make the geometry and
style constraints explicit enough that prompts, guides, and QA can agree on the
same tile topology.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .profiles import StateSpec

DEFAULT_PATH_WIDTH_PX = 64
DEFAULT_PATH_CENTER_PX = 128
DEFAULT_PATH_TOLERANCE_PX = 6


@dataclass(frozen=True)
class TileContract:
    kind: str
    exits: tuple[str, ...]
    prompt_text: str
    path_width_px: int | None = None
    path_center_px: int | None = None
    path_tolerance_px: int | None = None


def normalize_game_tile_style_notes(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    lowered = text.lower()
    icon_like = re.search(r"\b(app icon|icon set|launcher icon|vector icon|svg icon)\b", lowered)
    vector_like = re.search(r"\b(vector-style|vector style|vector art|svg|flat vector)\b", lowered)
    if icon_like or vector_like:
        return (
            "crisp HD top-down game terrain art with clean readable shapes, "
            "controlled cel-style shading, polished edges, and no app-icon, "
            "logo, isolated vector symbol, or sticker treatment"
        )
    return text


def _tokens(state: StateSpec) -> str:
    return f"{state.id} {state.purpose}".lower()


def _direction_words(text: str) -> tuple[str, ...]:
    if "all four" in text or "four-way" in text or "crossroad" in text:
        return ("top", "right", "bottom", "left")
    if "west to east" in text or "east to west" in text:
        return ("left", "right")
    if "north to south" in text or "south to north" in text:
        return ("top", "bottom")

    aliases = {
        "north": "top",
        "up": "top",
        "top": "top",
        "south": "bottom",
        "down": "bottom",
        "bottom": "bottom",
        "east": "right",
        "right": "right",
        "west": "left",
        "left": "left",
    }
    found: list[str] = []
    for word, direction in aliases.items():
        if re.search(rf"\b{re.escape(word)}\b", text) and direction not in found:
            found.append(direction)
    order = ("top", "right", "bottom", "left")
    return tuple(direction for direction in order if direction in found)


def _path_contract(kind: str, exits: tuple[str, ...]) -> TileContract:
    exit_text = ", ".join(f"{exit_name} edge midpoint" for exit_name in exits)
    prompt = (
        "Tile coherence contract:\n"
        "- Match the canonical style reference for palette, lighting, grass density, "
        "outline/edge softness, and material detail.\n"
        f"- This is a {kind.replace('_', ' ')} tile with exits at: {exit_text}.\n"
        f"- Every path exit must be centered on the tile edge at pixel {DEFAULT_PATH_CENTER_PX}.\n"
        f"- Every path mouth must be {DEFAULT_PATH_WIDTH_PX}px wide, tolerance +/- {DEFAULT_PATH_TOLERANCE_PX}px.\n"
        "- Path width must stay visually identical across straight, corner, T-junction, and crossroads tiles.\n"
        "- Keep path edges rounded but do not shift the edge mouths away from the centerline."
    )
    return TileContract(
        kind=kind,
        exits=exits,
        prompt_text=prompt,
        path_width_px=DEFAULT_PATH_WIDTH_PX,
        path_center_px=DEFAULT_PATH_CENTER_PX,
        path_tolerance_px=DEFAULT_PATH_TOLERANCE_PX,
    )


def contract_for_state(state: StateSpec) -> TileContract:
    text = _tokens(state)
    explicit_exits = _direction_words(text)
    if "cross" in text or "crossroad" in text:
        return _path_contract("path_cross", explicit_exits or ("top", "right", "bottom", "left"))
    if "t-junction" in text or " t " in f" {text} ":
        return _path_contract("path_t", explicit_exits or ("left", "right", "bottom"))
    if "corner" in text or "curving" in text:
        return _path_contract("path_corner", explicit_exits or ("right", "bottom"))
    if "path" in text or "road" in text or "pathway" in text:
        if explicit_exits == ("left", "right") or "horizontal" in text:
            return _path_contract("path_horizontal", ("left", "right"))
        if explicit_exits:
            return _path_contract("path_custom", explicit_exits)
        return _path_contract("path_vertical", ("top", "bottom"))
    if "transition" in text or "shoreline" in text or re.search(r"\bedge\b", text):
        return TileContract(
            kind="transition",
            exits=(),
            prompt_text=(
                "Tile coherence contract:\n"
                "- Match the canonical style reference for palette, lighting, grass density, "
                "outline/edge softness, and material detail.\n"
                "- Make the material boundary intentional and tile-usable; avoid arbitrary diagonal drift unless the purpose asks for it.\n"
                "- Keep each material's rendering language consistent with its standalone base tile."
            ),
        )
    return TileContract(
        kind="base_terrain",
        exits=(),
        prompt_text=(
            "Tile coherence contract:\n"
            "- Match the canonical style reference for palette, lighting, grass density, outline/edge softness, and material detail.\n"
            "- Keep material texture scale consistent with the rest of the pack.\n"
            "- If seamless/tileable, opposite edges should visually connect without obvious seams."
        ),
    )
