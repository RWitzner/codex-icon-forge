"""Multi-size folder packager.

Reads the composed atlas from ``run_dir/final/spritesheet.{webp,png}``,
extracts each cell using the atlas state catalog, and writes the same
design at multiple target sizes. Used for app icon packs, favicon packs,
and any product where the same illustration ships in several output
dimensions.

Profile params:

  sizes              list of target pixel sizes, e.g. [16, 32, 64, 128, ...]
  image_format       PNG (default) or WEBP
  naming             filename template per size, with {entity_id}, {state},
                     and {size} substitutions. Defaults to "{state}-{size}.png".
  readme_filename    optional. If set, render a README from readme_template.
  readme_template    optional. May use {display_name}, {description},
                     {entity_id}, {state_list}, {size_list}, {file_count}.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from ..packager import PackageContext, register, resolve_output_root
from ..profiles import AtlasProfile, PackagerProfile

_DEFAULT_NAMING = "{state}-{size}.png"


def _locate_source_atlas(run_dir: Path) -> Path:
    for candidate in (
        run_dir / "final" / "spritesheet.webp",
        run_dir / "final" / "spritesheet.png",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no composed atlas found under {run_dir / 'final'} (looked for spritesheet.webp/.png)"
    )


def _render_readme(
    template: str,
    *,
    context: PackageContext,
    atlas: AtlasProfile,
    sizes: list[int],
    file_count: int,
) -> str:
    state_list = "\n".join(
        f"- `{state.id}`: {state.purpose}" for state in atlas.states
    )
    size_list = ", ".join(f"{size}x{size}" for size in sizes)
    return template.format(
        bundle_id=context.entity_id,
        entity_id=context.entity_id,
        display_name=context.display_name,
        description=context.description,
        state_list=state_list,
        size_list=size_list,
        file_count=file_count,
        state_count=len(atlas.states),
    )


@register("multi-size-folder")
def _multi_size_folder(
    profile: PackagerProfile,
    context: PackageContext,
    atlas: AtlasProfile | None,
    *,
    force: bool,
) -> dict[str, Any]:
    if atlas is None:
        raise ValueError(
            f"packager profile {profile.id!r} uses 'multi-size-folder' "
            "but no atlas profile was passed to package()"
        )

    sizes_raw = profile.params.get("sizes", [])
    if not isinstance(sizes_raw, list) or not sizes_raw:
        raise ValueError(
            f"packager profile {profile.id!r} requires non-empty 'sizes' list in params"
        )
    sizes = sorted({int(value) for value in sizes_raw})

    image_format = str(profile.params.get("image_format", "PNG")).upper()
    if image_format not in {"PNG", "WEBP"}:
        raise ValueError(f"image_format must be PNG or WEBP, got {image_format!r}")
    naming = str(profile.params.get("naming", _DEFAULT_NAMING))

    output_dir = resolve_output_root(profile, context)
    readme_filename = profile.params.get("readme_filename")
    readme_path = output_dir / readme_filename if readme_filename else None

    atlas_path = _locate_source_atlas(context.run_dir)
    geometry = atlas.geometry

    targets: list[Path] = []
    for state in atlas.states:
        for size in sizes:
            target = output_dir / naming.format(
                entity_id=context.entity_id,
                state=state.id,
                size=size,
            )
            targets.append(target)

    with Image.open(atlas_path) as opened:
        atlas_image = opened.convert("RGBA")

    if force and output_dir.exists():
        shutil.rmtree(output_dir)
    elif not force:
        existing = [t for t in targets if t.exists()]
        if existing:
            raise FileExistsError(
                f"{output_dir} already contains {len(existing)} packaged files; "
                "pass force=True to overwrite"
            )
        if readme_path is not None and readme_path.exists():
            raise FileExistsError(
                f"{readme_path} already exists; pass force=True to overwrite"
            )

    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    target_iter = iter(targets)
    for state in atlas.states:
        left = 0
        top = state.row * geometry.cell_height
        right = left + geometry.cell_width
        bottom = top + geometry.cell_height
        cell = atlas_image.crop((left, top, right, bottom))
        for size in sizes:
            target = next(target_iter)
            target.parent.mkdir(parents=True, exist_ok=True)
            resized = cell.resize((size, size), Image.Resampling.LANCZOS)
            if image_format == "WEBP":
                resized.save(target, format="WEBP", lossless=True, quality=100, method=6)
            else:
                resized.save(target, format="PNG")
            written.append(str(target))

    if readme_path is not None and profile.params.get("readme_template"):
        readme_text = _render_readme(
            str(profile.params["readme_template"]),
            context=context,
            atlas=atlas,
            sizes=sizes,
            file_count=len(written),
        )
        readme_path.write_text(readme_text, encoding="utf-8")
        written.append(str(readme_path))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "sizes": sizes,
        "file_count": len(written),
    }
