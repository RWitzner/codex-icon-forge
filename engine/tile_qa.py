"""Deterministic QA artifacts for game-tile runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .manifest import now_iso
from .profiles import StateSpec
from .tile_contracts import contract_for_state


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_path_pixel(pixel: tuple[int, int, int, int]) -> bool:
    r, g, b, a = pixel
    return a > 0 and r > 120 and g > 80 and b < 130 and r >= g >= b


def _edge_span(image: Image.Image, side: str, border: int = 18) -> int:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pix = rgba.load()
    axis = width if side in {"top", "bottom"} else height
    projection = [False] * axis
    for i in range(axis):
        for j in range(border):
            if side == "top":
                x, y = i, j
            elif side == "bottom":
                x, y = i, height - border + j
            elif side == "left":
                x, y = j, i
            else:
                x, y = width - border + j, i
            if _is_path_pixel(pix[x, y]):
                projection[i] = True
                break
    longest = 0
    index = 0
    while index < axis:
        if not projection[index]:
            index += 1
            continue
        start = index
        while index < axis and projection[index]:
            index += 1
        longest = max(longest, index - start)
    return longest


def _write_contact_sheet(run_dir: Path, states: list[StateSpec] | tuple[StateSpec, ...]) -> Path:
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    columns = min(4, max(1, len(states)))
    rows = (len(states) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * 256, rows * 292), (24, 24, 24, 255))
    draw = ImageDraw.Draw(sheet)
    for index, state in enumerate(states):
        row = index // columns
        column = index % columns
        x = column * 256
        y = row * 292
        tile_path = run_dir / "decoded" / f"{state.id}.png"
        if tile_path.is_file():
            with Image.open(tile_path) as opened:
                sheet.alpha_composite(opened.convert("RGBA").resize((256, 256)), (x, y))
        draw.text((x + 8, y + 262), state.id, fill=(255, 255, 255, 255))
    target = qa_dir / "contact-sheet.png"
    sheet.save(target)
    return target


def review_tiles(run_dir: Path, states: list[StateSpec] | tuple[StateSpec, ...]) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    tiles: list[dict[str, Any]] = []
    for state in states:
        contract = contract_for_state(state)
        tile_errors: list[str] = []
        tile_warnings: list[str] = []
        tile_path = run_dir / "decoded" / f"{state.id}.png"
        if not tile_path.is_file():
            tile_errors.append(f"decoded tile missing: {tile_path}")
            tiles.append(
                {
                    "id": state.id,
                    "kind": contract.kind,
                    "decoded_sha256": None,
                    "errors": tile_errors,
                    "warnings": tile_warnings,
                }
            )
            continue
        with Image.open(tile_path) as opened:
            original_size = opened.size
            image = opened.convert("RGBA")
        if original_size != (256, 256):
            tile_errors.append(f"expected 256x256 tile, got {original_size[0]}x{original_size[1]}")
            image = image.resize((256, 256))
        if contract.exits and contract.path_width_px is not None and contract.path_tolerance_px is not None:
            low = contract.path_width_px - contract.path_tolerance_px
            high = contract.path_width_px + contract.path_tolerance_px
            for side in contract.exits:
                span = _edge_span(image, side)
                message = (
                    f"path mouth width on {side} is {span}px; "
                    f"expected {contract.path_width_px}px +/- {contract.path_tolerance_px}px"
                )
                if span < low or span > high:
                    tile_errors.append(message)
        tiles.append(
            {
                "id": state.id,
                "kind": contract.kind,
                "decoded_sha256": sha256_path(tile_path),
                "errors": tile_errors,
                "warnings": tile_warnings,
            }
        )

    contact_sheet = _write_contact_sheet(run_dir, states)
    result = {
        "ok": not any(tile["errors"] for tile in tiles),
        "approved": False,
        "reviewed_at": now_iso(),
        "state_ids": [state.id for state in states],
        "contact_sheet": str(contact_sheet),
        "tiles": tiles,
    }
    (qa_dir / "review.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
