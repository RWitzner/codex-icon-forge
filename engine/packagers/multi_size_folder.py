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
  flatten_background optional #RRGGBB. When set, an extra opaque copy is
                     written for each size in flatten_sizes. Stores that
                     reject an alpha channel (App Store Connect, ITMS-90717)
                     need this; the transparent files stay untouched.
  flatten_sizes      optional list of sizes to flatten. Defaults to the
                     largest entry in `sizes`. Every entry must be in `sizes`.
  flatten_naming     optional filename template for the flattened copies.
                     Defaults to `naming` with "-opaque" before the suffix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from ..chroma import parse_hex_color
from ..packager import PackageContext, register, resolve_output_root
from ..profiles import AtlasProfile, PackagerProfile

_DEFAULT_NAMING = "{state}-{size}.png"


def _default_flatten_naming(naming: str) -> str:
    stem, dot, suffix = naming.rpartition(".")
    if not dot:
        return naming + "-opaque"
    return f"{stem}-opaque.{suffix}"


def _flatten_config(
    profile: PackagerProfile, sizes: list[int], naming: str
) -> tuple[tuple[int, int, int] | None, set[int], str]:
    """Resolve the flatten params, or (None, set(), naming) when disabled."""

    raw_background = profile.params.get("flatten_background")
    if raw_background in (None, "", False):
        return None, set(), naming

    background = parse_hex_color(str(raw_background))

    raw_sizes = profile.params.get("flatten_sizes")
    if raw_sizes is None:
        flatten_sizes = {max(sizes)}
    else:
        if not isinstance(raw_sizes, list) or not raw_sizes:
            raise ValueError(
                f"packager profile {profile.id!r}: 'flatten_sizes' must be a "
                "non-empty list when present"
            )
        flatten_sizes = {int(value) for value in raw_sizes}
        unknown = sorted(flatten_sizes - set(sizes))
        if unknown:
            raise ValueError(
                f"packager profile {profile.id!r}: flatten_sizes {unknown} are "
                f"not in sizes {sizes}"
            )

    flatten_naming = str(
        profile.params.get("flatten_naming") or _default_flatten_naming(naming)
    )
    if flatten_naming == naming:
        raise ValueError(
            f"packager profile {profile.id!r}: 'flatten_naming' must differ from "
            "'naming', otherwise the opaque copy overwrites the transparent one"
        )
    return background, flatten_sizes, flatten_naming


def _flattened(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """Composite onto a solid background and drop the alpha channel."""

    plate = Image.new("RGBA", image.size, (*background, 255))
    plate.alpha_composite(image)
    return plate.convert("RGB")


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

    flatten_background, flatten_sizes, flatten_naming = _flatten_config(
        profile, sizes, naming
    )

    output_dir = resolve_output_root(profile, context)
    readme_filename = profile.params.get("readme_filename")
    readme_path = output_dir / readme_filename if readme_filename else None

    atlas_path = _locate_source_atlas(context.run_dir)
    geometry = atlas.geometry

    targets: list[Path] = []
    flatten_targets: list[Path] = []
    for state in atlas.states:
        for size in sizes:
            target = output_dir / naming.format(
                entity_id=context.entity_id,
                state=state.id,
                size=size,
            )
            targets.append(target)
            if size in flatten_sizes:
                flatten_targets.append(
                    output_dir
                    / flatten_naming.format(
                        entity_id=context.entity_id,
                        state=state.id,
                        size=size,
                    )
                )

    if not force:
        existing = [t for t in (*targets, *flatten_targets) if t.exists()]
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
    with Image.open(atlas_path) as opened:
        atlas_image = opened.convert("RGBA")

    target_iter = iter(targets)
    flatten_iter = iter(flatten_targets)
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

            if flatten_background is not None and size in flatten_sizes:
                flat_target = next(flatten_iter)
                flat_target.parent.mkdir(parents=True, exist_ok=True)
                flat = _flattened(resized, flatten_background)
                if image_format == "WEBP":
                    flat.save(
                        flat_target,
                        format="WEBP",
                        lossless=True,
                        quality=100,
                        method=6,
                    )
                else:
                    flat.save(flat_target, format="PNG")
                written.append(str(flat_target))

    if readme_path is not None and profile.params.get("readme_template"):
        readme_text = _render_readme(
            str(profile.params["readme_template"]),
            context=context,
            atlas=atlas,
            sizes=sizes,
            # The README counts itself: it is written immediately below, so the
            # number it quotes must match what the directory ends up holding.
            file_count=len(written) + 1,
        )
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(readme_text, encoding="utf-8")
        written.append(str(readme_path))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "sizes": sizes,
        "file_count": len(written),
    }
