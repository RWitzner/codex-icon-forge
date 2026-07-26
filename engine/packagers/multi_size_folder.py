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

import json

from ..chroma import parse_hex_color
from ..packager import (
    PackageContext,
    register,
    render_schema,
    resolve_output_root,
    resolve_within,
)
from ..profiles import AtlasProfile, PackagerProfile

_DEFAULT_NAMING = "{state}-{size}.png"


def _explicit_targets(profile: PackagerProfile) -> list[tuple[int, str]] | None:
    """Parse ``targets``: explicit pixel-size -> output-path pairs.

    ``naming`` can only substitute ``{size}``, so it cannot express any layout
    whose filenames are not a pure function of pixel size — an Xcode
    ``.appiconset``, a macOS ``.iconset`` (ten names over six distinct sizes),
    or Android density folders. ``targets`` states the mapping outright.
    Returns None when the profile uses the older ``sizes`` + ``naming`` form.
    """

    raw = profile.params.get("targets")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"packager profile {profile.id!r}: 'targets' must be a non-empty list"
        )
    pairs: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    for index, entry in enumerate(raw):
        where = f"packager profile {profile.id!r}: targets[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object with 'px' and 'path'")
        if "px" not in entry or "path" not in entry:
            raise ValueError(f"{where} requires both 'px' and 'path'")
        try:
            px = int(entry["px"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where}: 'px' must be an integer") from exc
        if px < 1:
            raise ValueError(f"{where}: 'px' must be >= 1, got {px}")
        path = str(entry["path"])
        if not path:
            raise ValueError(f"{where}: 'path' must not be empty")
        if path in seen_paths:
            raise ValueError(f"{where}: duplicate path {path!r}")
        seen_paths.add(path)
        pairs.append((px, path))
    return pairs


def _emit_files(
    profile: PackagerProfile,
    context: PackageContext,
    output_dir: Path,
    *,
    extra_format_values: dict[str, Any],
) -> list[Path]:
    """Write the profile's ``emit_files`` entries and return their paths.

    Each entry is ``{path, json}`` or ``{path, template}``. JSON goes through
    ``render_schema``, never ``str.format`` — a literal ``{"icons": ...}`` in a
    format string raises KeyError on its own braces, which this repo has
    already been bitten by once.
    """

    raw = profile.params.get("emit_files")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"packager profile {profile.id!r}: 'emit_files' must be a list"
        )

    written: list[Path] = []
    for index, entry in enumerate(raw):
        where = f"packager profile {profile.id!r}: emit_files[{index}]"
        if not isinstance(entry, dict) or "path" not in entry:
            raise ValueError(f"{where} must be an object with a 'path'")
        has_json = "json" in entry
        has_template = "template" in entry
        if has_json == has_template:
            raise ValueError(f"{where} requires exactly one of 'json' or 'template'")

        target = resolve_within(
            output_dir,
            str(entry["path"]).format(entity_id=context.entity_id),
            what="emit_files path",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if has_json:
            payload = render_schema(entry["json"], context)
            target.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        else:
            target.write_text(
                str(entry["template"]).format(**extra_format_values), encoding="utf-8"
            )
        written.append(target)
    return written


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

    explicit_targets = _explicit_targets(profile)
    if explicit_targets is None:
        sizes_raw = profile.params.get("sizes", [])
        if not isinstance(sizes_raw, list) or not sizes_raw:
            raise ValueError(
                f"packager profile {profile.id!r} requires a non-empty 'sizes' "
                "list or a non-empty 'targets' list in params"
            )
        sizes = sorted({int(value) for value in sizes_raw})
    else:
        sizes = sorted({px for px, _path in explicit_targets})

    image_format = str(profile.params.get("image_format", "PNG")).upper()
    if image_format not in {"PNG", "WEBP"}:
        raise ValueError(f"image_format must be PNG or WEBP, got {image_format!r}")
    naming = str(profile.params.get("naming", _DEFAULT_NAMING))

    flatten_background, flatten_sizes, flatten_naming = _flatten_config(
        profile, sizes, naming
    )

    output_dir = resolve_output_root(profile, context)
    readme_filename = profile.params.get("readme_filename")
    readme_path = (
        resolve_within(output_dir, str(readme_filename), what="readme_filename")
        if readme_filename
        else None
    )

    atlas_path = _locate_source_atlas(context.run_dir)
    geometry = atlas.geometry

    def _render(template: str, *, state_id: str, size: int, what: str) -> Path:
        return resolve_within(
            output_dir,
            template.format(
                entity_id=context.entity_id, state=state_id, size=size
            ),
            what=what,
        )

    # (state, px, path) triples, in write order. With 'targets' one pixel size
    # may map to several paths (a macOS .iconset needs icon_16x16.png and
    # icon_8x8@2x.png from the same 16px render), so the render is keyed on
    # size and reused rather than repeated.
    plan: list[tuple[str, int, Path]] = []
    flatten_plan: list[tuple[str, int, Path]] = []
    for state in atlas.states:
        pairs = (
            explicit_targets
            if explicit_targets is not None
            else [(size, naming) for size in sizes]
        )
        for px, template in pairs:
            plan.append(
                (state.id, px, _render(template, state_id=state.id, size=px, what="target path"))
            )
        for size in sizes:
            if size in flatten_sizes:
                flatten_plan.append(
                    (
                        state.id,
                        size,
                        _render(
                            flatten_naming,
                            state_id=state.id,
                            size=size,
                            what="flatten_naming path",
                        ),
                    )
                )

    targets = [path for _state, _px, path in plan]
    flatten_targets = [path for _state, _px, path in flatten_plan]

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

    def _save(image: Image.Image, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if image_format == "WEBP":
            image.save(target, format="WEBP", lossless=True, quality=100, method=6)
        else:
            image.save(target, format="PNG")

    for state in atlas.states:
        left = 0
        top = state.row * geometry.cell_height
        right = left + geometry.cell_width
        bottom = top + geometry.cell_height
        cell = atlas_image.crop((left, top, right, bottom))

        rendered: dict[int, Image.Image] = {}
        for state_id, px, target in plan:
            if state_id != state.id:
                continue
            if px not in rendered:
                rendered[px] = cell.resize((px, px), Image.Resampling.LANCZOS)
            _save(rendered[px], target)
            written.append(str(target))

        if flatten_background is not None:
            for state_id, px, flat_target in flatten_plan:
                if state_id != state.id:
                    continue
                if px not in rendered:
                    rendered[px] = cell.resize((px, px), Image.Resampling.LANCZOS)
                _save(_flattened(rendered[px], flatten_background), flat_target)
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

    for emitted in _emit_files(
        profile,
        context,
        output_dir,
        extra_format_values={
            "entity_id": context.entity_id,
            "display_name": context.display_name,
            "description": context.description,
            "size_list": ", ".join(f"{size}x{size}" for size in sizes),
            "state_list": "\n".join(
                f"- `{state.id}`: {state.purpose}" for state in atlas.states
            ),
            "state_count": len(atlas.states),
            "file_count": len(written),
        },
    ):
        written.append(str(emitted))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "sizes": sizes,
        "file_count": len(written),
    }
