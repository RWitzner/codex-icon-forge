"""End-to-end orchestration for an icon-forge run.

Wires composer + validator + packager into a single ``finalize_run`` call.
Image generation, prompt authoring, and result ingestion are handled by the
prepare/status/record/extract CLI flow before this module packages outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import composer, validator
from .packager import PackageContext, package
from .profiles import Bundle


@dataclass
class FinalizeOptions:
    entity_id: str
    display_name: str
    description: str
    frames_root: Path
    output_run_dir: Path
    package_overrides: dict[str, str] | None = None
    force: bool = False
    write_png: bool = True
    write_webp: bool = True


def finalize_run(bundle: Bundle, options: FinalizeOptions) -> dict[str, Any]:
    options.output_run_dir.mkdir(parents=True, exist_ok=True)
    final_dir = options.output_run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    atlas_image = composer.compose_from_frames(options.frames_root, bundle.atlas)

    png_path = final_dir / "spritesheet.png" if options.write_png else None
    webp_path = final_dir / "spritesheet.webp" if options.write_webp else None
    composer.save_outputs(
        atlas_image,
        png_path or webp_path,
        webp_path if png_path else None,
    )

    validate_target = webp_path or png_path
    validation = validator.validate_atlas(
        validate_target, bundle.atlas, bundle.extractor
    )
    validation_path = options.output_run_dir / "validation.json"
    validation_path.write_text(
        json.dumps(validation.to_dict(include_cells=False), indent=2) + "\n",
        encoding="utf-8",
    )

    if not validation.ok:
        return {
            "ok": False,
            "stage": "validate",
            "errors": validation.errors,
            "warnings": validation.warnings,
            "validation_path": str(validation_path),
        }

    context = PackageContext(
        entity_id=options.entity_id,
        display_name=options.display_name,
        description=options.description,
        run_dir=options.output_run_dir,
        overrides=options.package_overrides,
    )
    pkg_result = package(
        bundle.packager,
        context,
        atlas=bundle.atlas,
        force=options.force,
    )

    return {
        "ok": True,
        "stage": "package",
        "validation_path": str(validation_path),
        "validation": validation.to_dict(include_cells=False),
        "package": pkg_result,
    }
