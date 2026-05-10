"""Render tile-topology guide images for game-tiles runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .profiles import StateSpec
from .tile_contracts import contract_for_state

GUIDE_SUBDIR = "references/tile-guides"


@dataclass(frozen=True)
class TileGuide:
    state_id: str
    path: str
    role: str

    def to_dict(self) -> dict[str, str]:
        return {"state_id": self.state_id, "path": self.path, "role": self.role}


def _draw_path(draw: ImageDraw.ImageDraw, exits: tuple[str, ...], width: int, center: int) -> None:
    half = width // 2
    fill = (255, 0, 255, 180)
    if "top" in exits:
        draw.rectangle((center - half, 0, center + half, center), fill=fill)
    if "bottom" in exits:
        draw.rectangle((center - half, center, center + half, 255), fill=fill)
    if "left" in exits:
        draw.rectangle((0, center - half, center, center + half), fill=fill)
    if "right" in exits:
        draw.rectangle((center, center - half, 255, center + half), fill=fill)
    draw.ellipse((center - half, center - half, center + half, center + half), fill=fill)


def render_tile_guides(run_dir: Path, states: list[StateSpec] | tuple[StateSpec, ...]) -> list[TileGuide]:
    guide_dir = run_dir / GUIDE_SUBDIR
    guides: list[TileGuide] = []
    for state in states:
        contract = contract_for_state(state)
        if not contract.exits or contract.path_width_px is None or contract.path_center_px is None:
            continue
        guide_dir.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        _draw_path(draw, contract.exits, contract.path_width_px, contract.path_center_px)
        target = guide_dir / f"{state.id}.png"
        image.save(target)
        guides.append(
            TileGuide(
                state_id=state.id,
                path=f"{GUIDE_SUBDIR}/{state.id}.png",
                role=(
                    "tile layout guide for path mouth width and edge-center alignment; "
                    "use for geometry only, do not copy guide color or visible marks"
                ),
            )
        )
    return guides
