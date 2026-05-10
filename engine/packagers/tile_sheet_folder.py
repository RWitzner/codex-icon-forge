"""Tile-sheet folder packager.

Writes static game tiles as individual PNGs plus a compact row-major
tilesheet and JSON manifest. The internal atlas may be 1-column by N rows;
this packager owns the public sheet layout.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from ..packager import PackageContext, register, resolve_output_root
from ..profiles import AtlasProfile, PackagerProfile


def _layout(tile_count: int) -> tuple[int, int]:
    if tile_count <= 0:
        raise ValueError("tile-sheet-folder requires at least one tile")
    columns = 1 if tile_count == 1 else min(4, tile_count)
    rows = math.ceil(tile_count / columns)
    return columns, rows


def _atlas_fallback_path(context: PackageContext) -> Path:
    for candidate in (
        context.run_dir / "final" / "spritesheet.webp",
        context.run_dir / "final" / "spritesheet.png",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no composed atlas found under {context.run_dir / 'final'} "
        "(looked for spritesheet.webp/.png)"
    )


def _load_tile_source(
    context: PackageContext,
    state_id: str,
    fallback_atlas: Image.Image,
    atlas: AtlasProfile,
) -> Image.Image:
    decoded_path = context.run_dir / "decoded" / f"{state_id}.png"
    if decoded_path.is_file():
        with Image.open(decoded_path) as opened:
            return opened.convert("RGBA")

    state = atlas.state(state_id)
    left = 0
    top = state.row * atlas.geometry.cell_height
    right = left + atlas.geometry.cell_width
    bottom = top + atlas.geometry.cell_height
    return fallback_atlas.crop((left, top, right, bottom)).convert("RGBA")


def _fit_full_bleed(source: Image.Image, tile_size: int) -> Image.Image:
    return ImageOps.fit(
        source.convert("RGBA"),
        (tile_size, tile_size),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def _render_readme(
    template: str,
    *,
    context: PackageContext,
    atlas: AtlasProfile,
    tile_size: int,
    columns: int,
    rows: int,
) -> str:
    tile_list = "\n".join(
        f"- `tiles/{state.id}.png` - {state.purpose}" for state in atlas.states
    )
    return template.format(
        entity_id=context.entity_id,
        display_name=context.display_name,
        description=context.description,
        tile_count=len(atlas.states),
        tile_size=tile_size,
        columns=columns,
        rows=rows,
        tile_list=tile_list,
    )


@register("tile-sheet-folder")
def _tile_sheet_folder(
    profile: PackagerProfile,
    context: PackageContext,
    atlas: AtlasProfile | None,
    *,
    force: bool,
) -> dict[str, Any]:
    if atlas is None:
        raise ValueError(
            f"packager profile {profile.id!r} uses 'tile-sheet-folder' "
            "but no atlas profile was passed to package()"
        )
    if not atlas.states:
        raise ValueError("tile-sheet-folder requires a materialized atlas with states")

    image_format = str(profile.params.get("image_format", "PNG")).upper()
    if image_format != "PNG":
        raise ValueError(f"image_format must be PNG for tile-sheet-folder, got {image_format!r}")

    tile_size = int(profile.params.get("tile_size", 256))
    columns, rows = _layout(len(atlas.states))
    output_dir = resolve_output_root(profile, context)
    tiles_dir = output_dir / "tiles"
    sheet_path = output_dir / "tilesheet.png"
    manifest_path = output_dir / "manifest.json"
    readme_filename = profile.params.get("readme_filename")
    readme_path = output_dir / str(readme_filename) if readme_filename else None

    targets = [sheet_path, manifest_path]
    targets.extend(tiles_dir / f"{state.id}.png" for state in atlas.states)
    if readme_path is not None:
        targets.append(readme_path)

    fallback_atlas_path = _atlas_fallback_path(context)
    with Image.open(fallback_atlas_path) as opened:
        fallback_atlas = opened.convert("RGBA")

    if force and output_dir.exists():
        shutil.rmtree(output_dir)
    elif not force:
        existing = [target for target in targets if target.exists()]
        if existing:
            raise FileExistsError(
                f"{output_dir} already contains {len(existing)} packaged files; "
                "pass force=True to overwrite"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    sheet = Image.new("RGBA", (columns * tile_size, rows * tile_size), (0, 0, 0, 0))
    written: list[str] = []
    manifest_tiles: list[dict[str, Any]] = []

    for index, state in enumerate(atlas.states):
        row = index // columns
        column = index % columns
        x = column * tile_size
        y = row * tile_size

        source = _load_tile_source(context, state.id, fallback_atlas, atlas)
        tile = _fit_full_bleed(source, tile_size)

        tile_path = tiles_dir / f"{state.id}.png"
        tile.save(tile_path, format="PNG")
        sheet.alpha_composite(tile, (x, y))
        written.append(str(tile_path))

        manifest_tiles.append(
            {
                "id": state.id,
                "purpose": state.purpose,
                "file": f"tiles/{state.id}.png",
                "row": row,
                "column": column,
                "x": x,
                "y": y,
                "width": tile_size,
                "height": tile_size,
            }
        )

    sheet.save(sheet_path, format="PNG")
    written.append(str(sheet_path))

    manifest = {
        "bundle": "game-tiles",
        "entity_id": context.entity_id,
        "tile_size": tile_size,
        "columns": columns,
        "rows": rows,
        "tilesheet": "tilesheet.png",
        "tiles": manifest_tiles,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    written.append(str(manifest_path))

    readme_template = profile.params.get("readme_template")
    if readme_path is not None and readme_template:
        readme_text = _render_readme(
            str(readme_template),
            context=context,
            atlas=atlas,
            tile_size=tile_size,
            columns=columns,
            rows=rows,
        )
        readme_path.write_text(readme_text, encoding="utf-8")
        written.append(str(readme_path))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "tile_count": len(atlas.states),
        "tile_size": tile_size,
        "columns": columns,
        "rows": rows,
        "manifest": manifest,
    }
