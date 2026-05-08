"""Layout-guide image rendering driven by AtlasProfile.

Layout guides are construction images attached as input to row-strip imagegen
jobs so the model can follow the correct frame count, spacing, centering,
and safe padding without those guide lines bleeding into the output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .profiles import AtlasProfile, LayoutGuides, StateSpec

GUIDE_SUBDIR = "references/layout-guides"
_DEFAULT_BACKGROUND = "#f7f7f7"
_DEFAULT_CELL_BORDER = "#111111"
_DEFAULT_SAFE_BORDER = "#2f80ed"
_DEFAULT_CENTER_DASHES = "#b8b8b8"


@dataclass(frozen=True)
class GuideMetadata:
    state: str
    path: str
    width: int
    height: int
    frames: int
    cell_width: int
    cell_height: int
    safe_margin_x: int
    safe_margin_y: int
    usage: str = (
        "layout guide input only; do not copy visible guide lines into generated sprite strips"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "cell_width": self.cell_width,
            "cell_height": self.cell_height,
            "safe_margin_x": self.safe_margin_x,
            "safe_margin_y": self.safe_margin_y,
            "usage": self.usage,
        }


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: str,
    dash: int = 8,
    gap: int = 6,
) -> None:
    x1, y1 = start
    x2, y2 = end
    step = dash + gap
    if x1 == x2:
        for y in range(min(y1, y2), max(y1, y2), step):
            draw.line((x1, y, x2, min(y + dash, max(y1, y2))), fill=fill)
        return
    if y1 == y2:
        for x in range(min(x1, x2), max(x1, x2), step):
            draw.line((x, y1, min(x + dash, max(x1, x2)), y2), fill=fill)
        return
    raise ValueError("draw_dashed_line only supports horizontal or vertical lines")


def render_layout_guide(
    path: Path,
    state: StateSpec,
    atlas: AtlasProfile,
) -> GuideMetadata:
    layout = atlas.layout_guides
    geometry = atlas.geometry
    width = state.frames * geometry.cell_width
    height = geometry.cell_height
    cell_width = geometry.cell_width

    style = layout.guide_style
    background = style.get("background", _DEFAULT_BACKGROUND)
    cell_border = style.get("cell_border", _DEFAULT_CELL_BORDER)
    safe_border = style.get("safe_border", _DEFAULT_SAFE_BORDER)
    center_dashes = style.get("center_dashes", _DEFAULT_CENTER_DASHES)

    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    safe_margin_x = layout.safe_margin_x
    safe_margin_y = layout.safe_margin_y

    for index in range(state.frames):
        left = index * cell_width
        right = left + cell_width - 1
        draw.rectangle((left, 0, right, height - 1), outline=cell_border, width=2)

        safe_left = left + safe_margin_x
        safe_top = safe_margin_y
        safe_right = right - safe_margin_x
        safe_bottom = height - 1 - safe_margin_y
        draw.rectangle(
            (safe_left, safe_top, safe_right, safe_bottom),
            outline=safe_border,
            width=2,
        )

        center_x = left + cell_width // 2
        center_y = height // 2
        _draw_dashed_line(draw, (center_x, safe_top), (center_x, safe_bottom), fill=center_dashes)
        _draw_dashed_line(draw, (safe_left, center_y), (safe_right, center_y), fill=center_dashes)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return GuideMetadata(
        state=state.id,
        path=str(path),
        width=width,
        height=height,
        frames=state.frames,
        cell_width=cell_width,
        cell_height=geometry.cell_height,
        safe_margin_x=safe_margin_x,
        safe_margin_y=safe_margin_y,
    )


def render_all(run_dir: Path, atlas: AtlasProfile) -> list[GuideMetadata]:
    if not atlas.layout_guides.enabled:
        return []
    guide_dir = run_dir / GUIDE_SUBDIR
    return [
        render_layout_guide(guide_dir / f"{state.id}.png", state, atlas)
        for state in atlas.states
    ]
