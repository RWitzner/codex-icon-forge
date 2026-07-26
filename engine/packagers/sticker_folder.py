"""Atlas-extract-folder packager: split atlas cells into individual files.

Used by sticker-pack-style bundles where the atlas is just a transport for
individual emoji-style images. Two output modes:

* Single-size (default): reads the composed atlas from
  ``run_dir/final/spritesheet.{webp,png}``, crops each cell using the atlas
  profile's state catalog, and writes one file per state.
* Multi-size (``params.sizes`` set): reads each state's high-resolution
  decoded source from ``run_dir/<source_dir>/<state-id>.png`` (default
  ``decoded``), runs the same chroma-key cleanup the extract step applies
  (``remove_chroma_background`` + alpha-bbox fit), then resizes the cleaned
  master once per requested size. This bypasses the spritesheet's 128x128
  downsample so 1024-size stickers stay sharp, while still removing the
  chroma background and trimming silhouette padding.

Optionally renders a README from a template in ``profile.params``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from ..extractors._helpers import (
    DEFAULT_CELL_PADDING_PX,
    fit_to_cell,
    remove_chroma_background,
)
from ..packager import PackageContext, register, resolve_output_root
from ..profiles import AtlasProfile, PackagerProfile
from ..request_manifest import read_request

_DEFAULT_KEY_THRESHOLD = 96.0
_DEFAULT_ALPHA_ERODE_PX = 1
_DEFAULT_ALPHA_BLUR_RADIUS = 1.0


def _load_chroma_key_rgb(run_dir: Path) -> tuple[int, int, int]:
    """Read ``chroma_key.rgb`` from the request manifest for multi-size cleanup.

    The multi-size branch reads decoded sources directly (bypassing the
    spritesheet path that the extract step would otherwise feed through),
    so it has to redo chroma cleanup itself. The chroma key is whatever
    ``run_setup`` selected at prepare time and persisted in the run request.
    """

    data = read_request(run_dir)
    rgb = data.get("chroma_key", {}).get("rgb")
    if not isinstance(rgb, list) or len(rgb) != 3:
        raise ValueError(
            f"request manifest chroma_key.rgb must be a 3-element list, got {rgb!r}"
        )
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _locate_source_atlas(run_dir: Path) -> Path:
    for candidate in (run_dir / "final" / "spritesheet.webp", run_dir / "final" / "spritesheet.png"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no composed atlas found under {run_dir / 'final'} (looked for spritesheet.webp/.png)"
    )


def _render_readme(
    template: str,
    *,
    profile: PackagerProfile,
    atlas: AtlasProfile,
    context: PackageContext,
    image_extension: str,
) -> str:
    state_list = "\n".join(
        f"- `:{state.id}:` → `{state.id}{image_extension}` ({state.purpose})"
        for state in atlas.states
    )
    return template.format(
        bundle_id=context.entity_id,
        entity_id=context.entity_id,
        display_name=context.display_name,
        description=context.description,
        state_list=state_list,
        sticker_count=len(atlas.states),
    )


def _render_multisize_readme(
    template: str,
    *,
    profile: PackagerProfile,
    atlas: AtlasProfile,
    context: PackageContext,
    image_extension: str,
    sizes: list[int],
    file_count: int,
) -> str:
    state_list = "\n".join(
        f"- `:{state.id}:` -> `{state.id}/{state.id}-{size}{image_extension}` ({state.purpose})"
        for state in atlas.states
        for size in sizes
    )
    size_list = ", ".join(f"{size}x{size}" for size in sizes)
    return template.format(
        bundle_id=context.entity_id,
        entity_id=context.entity_id,
        display_name=context.display_name,
        description=context.description,
        state_list=state_list,
        sticker_count=len(atlas.states),
        size_list=size_list,
        file_count=file_count,
    )


@register("atlas-extract-folder")
def _atlas_extract_folder(
    profile: PackagerProfile,
    context: PackageContext,
    atlas: AtlasProfile | None,
    *,
    force: bool,
) -> dict[str, Any]:
    if atlas is None:
        raise ValueError(
            f"packager profile {profile.id!r} uses 'atlas-extract-folder' "
            "but no atlas profile was passed to package()"
        )

    output_dir = resolve_output_root(profile, context)
    image_format = str(profile.params.get("image_format", "PNG")).upper()
    if image_format not in {"PNG", "WEBP"}:
        raise ValueError(f"image_format must be PNG or WEBP, got {image_format!r}")
    image_extension = ".png" if image_format == "PNG" else ".webp"

    sizes_raw = profile.params.get("sizes", [])
    if isinstance(sizes_raw, list) and sizes_raw:
        sizes = sorted({int(value) for value in sizes_raw})
        source_dir = context.run_dir / str(profile.params.get("source_dir", "decoded"))

        targets: list[Path] = []
        for state in atlas.states:
            for size in sizes:
                targets.append(
                    output_dir
                    / state.id
                    / f"{state.id}-{size}{image_extension}"
                )

        readme_filename = profile.params.get("readme_filename")
        readme_path = output_dir / readme_filename if readme_filename else None
        existing_targets = [t for t in targets if t.exists()]
        if not force:
            if existing_targets:
                raise FileExistsError(
                    f"{output_dir} already contains {len(existing_targets)} sticker files; "
                    "pass force=True to overwrite"
                )
            if readme_path is not None and readme_path.exists():
                raise FileExistsError(
                    f"{readme_path} already exists; pass force=True to overwrite"
                )

        output_dir.mkdir(parents=True, exist_ok=True)

        chroma_key = _load_chroma_key_rgb(context.run_dir)
        threshold = float(profile.params.get("key_threshold", _DEFAULT_KEY_THRESHOLD))
        alpha_erode_px = int(
            profile.params.get("alpha_erode_px", _DEFAULT_ALPHA_ERODE_PX)
        )
        alpha_blur_radius = float(
            profile.params.get("alpha_blur_radius", _DEFAULT_ALPHA_BLUR_RADIUS)
        )
        cell_padding_px = int(
            profile.params.get("cell_padding_px", DEFAULT_CELL_PADDING_PX)
        )

        written: list[str] = []
        target_iter = iter(targets)
        for state in atlas.states:
            source_path = source_dir / f"{state.id}.png"
            if not source_path.is_file():
                raise FileNotFoundError(f"decoded sticker source not found: {source_path}")
            with Image.open(source_path) as opened:
                source_image = opened.convert("RGBA")
            cleaned = remove_chroma_background(
                source_image,
                chroma_key,
                threshold,
                alpha_erode_px=alpha_erode_px,
                alpha_blur_radius=alpha_blur_radius,
            )
            # Normalise ONCE at the largest requested size, then downscale.
            # Fitting per size applied a constant pixel padding to different
            # cells, so the 128px sticker was not a downscale of the 1024px
            # one (measured fill: 0.922 vs 0.990 of the cell). Every file in a
            # sticker's folder is now the same framing at a different scale.
            master_size = max(sizes)
            master = fit_to_cell(
                cleaned,
                master_size,
                master_size,
                padding_px=cell_padding_px,
                allow_upscale=True,
            )
            for size in sizes:
                target = next(target_iter)
                target.parent.mkdir(parents=True, exist_ok=True)
                resized = (
                    master
                    if size == master_size
                    else master.resize((size, size), Image.Resampling.LANCZOS)
                )
                if image_format == "WEBP":
                    resized.save(target, format="WEBP", lossless=True, quality=100, method=6)
                else:
                    resized.save(target, format="PNG")
                written.append(str(target))

        readme_template = profile.params.get("readme_template")
        if readme_path is not None and readme_template:
            readme_text = _render_multisize_readme(
                readme_template,
                profile=profile,
                atlas=atlas,
                context=context,
                image_extension=image_extension,
                sizes=sizes,
                file_count=len(written),
            )
            readme_path.write_text(readme_text, encoding="utf-8")
            written.append(str(readme_path))

        return {
            "ok": True,
            "output_dir": str(output_dir),
            "files": written,
            "sticker_count": len(atlas.states),
            "sizes": sizes,
            "file_count": len(written),
        }

    targets = [output_dir / f"{state.id}{image_extension}" for state in atlas.states]
    readme_filename = profile.params.get("readme_filename")
    readme_path = output_dir / readme_filename if readme_filename else None
    existing_targets = [t for t in targets if t.exists()]
    if not force:
        if existing_targets:
            raise FileExistsError(
                f"{output_dir} already contains {len(existing_targets)} sticker files; "
                "pass force=True to overwrite"
            )
        if readme_path is not None and readme_path.exists():
            raise FileExistsError(
                f"{readme_path} already exists; pass force=True to overwrite"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    atlas_path = _locate_source_atlas(context.run_dir)
    geometry = atlas.geometry

    written: list[str] = []
    with Image.open(atlas_path) as opened:
        atlas_image = opened.convert("RGBA")
    for state, target in zip(atlas.states, targets):
        left = 0
        top = state.row * geometry.cell_height
        right = left + geometry.cell_width
        bottom = top + geometry.cell_height
        cell = atlas_image.crop((left, top, right, bottom))
        if image_format == "WEBP":
            cell.save(target, format="WEBP", lossless=True, quality=100, method=6)
        else:
            cell.save(target, format="PNG")
        written.append(str(target))

    readme_template = profile.params.get("readme_template")
    if readme_path is not None and readme_template:
        readme_text = _render_readme(
            readme_template,
            profile=profile,
            atlas=atlas,
            context=context,
            image_extension=image_extension,
        )
        readme_path.write_text(readme_text, encoding="utf-8")
        written.append(str(readme_path))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "sticker_count": len(atlas.states),
    }
