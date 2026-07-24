"""Web brand kit packager.

Reads the composed atlas from ``run_dir/final/spritesheet.{webp,png}``,
crops the single 1024x1024 brand cell, and writes the canonical browser/PWA
asset set from that master.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from ..packager import PackageContext, register, resolve_output_root
from ..profiles import AtlasProfile, PackagerProfile

_PNG_TARGETS = [
    ("favicon-16x16.png", 16),
    ("favicon-32x32.png", 32),
    ("favicon-48x48.png", 48),
    ("apple-touch-icon.png", 180),
    ("icon-192.png", 192),
    ("icon-512.png", 512),
]
_ICO_SIZES = [(16, 16), (32, 32), (48, 48)]
_SIZES = [16, 32, 48, 180, 192, 512]
_ORDERED_FILES = [
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon-48x48.png",
    "favicon.ico",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "site.webmanifest",
    "README.md",
]


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


def _load_master(source_atlas: Path, atlas: AtlasProfile) -> Image.Image:
    geometry = atlas.geometry
    state = atlas.states[0]
    left = 0
    top = state.row * geometry.cell_height
    right = left + geometry.cell_width
    bottom = top + geometry.cell_height
    with Image.open(source_atlas) as opened:
        expected_size = (geometry.width, geometry.height)
        if opened.size != expected_size:
            raise ValueError(
                f"expected composed atlas {source_atlas} to be "
                f"{expected_size[0]}x{expected_size[1]} for atlas {atlas.id!r}, "
                f"got {opened.size[0]}x{opened.size[1]}"
            )
        atlas_image = opened.convert("RGBA")
    return atlas_image.crop((left, top, right, bottom))


def _manifest(context: PackageContext) -> dict[str, Any]:
    return {
        "name": context.display_name,
        "short_name": context.display_name,
        "icons": [
            {
                "src": "icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    }


def _readme(context: PackageContext) -> str:
    return (
        f"# {context.display_name} web brand kit\n\n"
        f"{context.description}\n\n"
        "## Files\n\n"
        "- `favicon-16x16.png` - small browser tab favicon.\n"
        "- `favicon-32x32.png` - standard browser favicon.\n"
        "- `favicon-48x48.png` - Windows and legacy browser favicon fallback.\n"
        "- `favicon.ico` - ICO containing 16x16, 32x32, and 48x48 entries.\n"
        "- `apple-touch-icon.png` - 180x180 icon for iOS home-screen saves.\n"
        "- `icon-192.png` - PWA manifest icon.\n"
        "- `icon-512.png` - PWA manifest icon.\n"
        "- `site.webmanifest` - web app manifest referencing the 192 and 512 PNGs.\n\n"
        "## HTML usage\n\n"
        "```html\n"
        "<link rel=\"icon\" href=\"favicon.ico\" sizes=\"any\">\n"
        "<link rel=\"icon\" type=\"image/png\" sizes=\"32x32\" href=\"favicon-32x32.png\">\n"
        "<link rel=\"apple-touch-icon\" href=\"apple-touch-icon.png\">\n"
        "<link rel=\"manifest\" href=\"site.webmanifest\">\n"
        "```\n"
    )


@register("web-brand-kit")
def _web_brand_kit(
    profile: PackagerProfile,
    context: PackageContext,
    atlas: AtlasProfile | None,
    *,
    force: bool,
) -> dict[str, Any]:
    if atlas is None:
        raise ValueError(
            f"packager profile {profile.id!r} uses 'web-brand-kit' "
            "but no atlas profile was passed to package()"
        )
    if len(atlas.states) != 1:
        raise ValueError(
            f"packager profile {profile.id!r} requires exactly one atlas state, "
            f"got {len(atlas.states)}"
        )

    source_atlas = _locate_source_atlas(context.run_dir)
    output_dir = resolve_output_root(profile, context)
    targets = [output_dir / filename for filename in _ORDERED_FILES]
    if not force:
        existing = [target for target in targets if target.exists()]
        if existing:
            raise FileExistsError(
                f"{output_dir} already contains {len(existing)} web brand files; "
                "pass force=True to overwrite"
            )

    master = _load_master(source_atlas, atlas)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for filename, size in _PNG_TARGETS[:3]:
        target = output_dir / filename
        master.resize((size, size), Image.Resampling.LANCZOS).save(target, format="PNG")
        written.append(str(target))

    ico_target = output_dir / "favicon.ico"
    master.save(ico_target, format="ICO", sizes=_ICO_SIZES)
    written.append(str(ico_target))

    for filename, size in _PNG_TARGETS[3:]:
        target = output_dir / filename
        master.resize((size, size), Image.Resampling.LANCZOS).save(target, format="PNG")
        written.append(str(target))

    manifest_target = output_dir / "site.webmanifest"
    manifest_target.write_text(
        json.dumps(_manifest(context), indent=2) + "\n",
        encoding="utf-8",
    )
    written.append(str(manifest_target))

    readme_target = output_dir / "README.md"
    readme_target.write_text(_readme(context), encoding="utf-8")
    written.append(str(readme_target))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "sizes": _SIZES,
        "file_count": len(written),
    }
