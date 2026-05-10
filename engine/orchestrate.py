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
from .tile_qa import sha256_path


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
        validate_target,
        bundle.atlas,
        bundle.extractor,
        allow_opaque=bool(bundle.extractor.params.get("allow_opaque", False)),
        allow_near_opaque_used_cells=bool(
            bundle.extractor.params.get("allow_near_opaque_used_cells", False)
        ),
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

    if bundle.id == "game-tiles":
        review_path = options.output_run_dir / "qa" / "review.json"
        if not review_path.is_file():
            return {
                "ok": False,
                "stage": "tile-qa",
                "errors": [f"game-tiles finalize requires prior tile QA review: {review_path}"],
                "warnings": validation.warnings,
                "validation_path": str(validation_path),
            }
        review = json.loads(review_path.read_text(encoding="utf-8"))
        expected_state_ids = [state.id for state in bundle.atlas.states]
        contact_sheet = Path(str(review.get("contact_sheet", "")))
        review_tiles = review.get("tiles", [])
        review_state_ids = review.get("state_ids", [])
        errors: list[str] = []
        if review_state_ids != expected_state_ids:
            errors.append(
                f"tile QA state_ids do not match atlas states: {review_state_ids} != {expected_state_ids}"
            )
        if not contact_sheet.is_file():
            errors.append(f"tile QA contact sheet missing: {contact_sheet}")
        if not review.get("approved"):
            errors.append("tile QA review has not been explicitly parent-approved")
        if len(review_tiles) != len(expected_state_ids):
            errors.append("tile QA review does not cover every tile")
        review_tile_ids = [tile.get("id") for tile in review_tiles]
        if review_tile_ids != expected_state_ids:
            errors.append(
                f"tile QA tile entries do not match atlas states: {review_tile_ids} != {expected_state_ids}"
            )
        if len(set(review_tile_ids)) != len(review_tile_ids):
            errors.append(f"tile QA review has duplicate tile entries: {review_tile_ids}")
        for tile in review_tiles:
            tile_id = tile.get("id")
            decoded_path = options.output_run_dir / "decoded" / f"{tile_id}.png"
            if not tile_id:
                errors.append("tile QA entry is missing id")
                continue
            if not decoded_path.is_file():
                errors.append(f"{tile_id}: decoded tile missing")
            elif tile.get("decoded_sha256") != sha256_path(decoded_path):
                errors.append(f"{tile_id}: QA review is stale; decoded_sha256 changed")
            errors.extend(f"{tile_id}: {error}" for error in tile.get("errors", []))

        if not review.get("ok") or errors:
            return {
                "ok": False,
                "stage": "tile-qa",
                "errors": errors or ["game-tiles QA review failed"],
                "warnings": validation.warnings,
                "validation_path": str(validation_path),
                "review_path": str(review_path),
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
