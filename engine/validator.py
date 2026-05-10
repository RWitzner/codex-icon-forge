"""Atlas validation driven by AtlasProfile + ExtractorProfile.

Mirrors the behaviour of the legacy scripts/validate_atlas.py. The legacy
script is preserved verbatim under scripts/ for parity tests.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from .profiles import AtlasProfile, ExtractorProfile

_DEFAULT_MIN_USED_PIXELS = 50
_DEFAULT_NEAR_OPAQUE_THRESHOLD = 0.95


@dataclass
class ValidationResult:
    ok: bool
    file: str
    format: str | None
    mode: str | None
    width: int
    height: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cells: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, include_cells: bool = True) -> dict[str, Any]:
        data = {
            "ok": self.ok,
            "file": self.file,
            "format": self.format,
            "mode": self.mode,
            "width": self.width,
            "height": self.height,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
        if include_cells:
            data["cells"] = list(self.cells)
        return data


def _alpha_nonzero_count(image: Image.Image) -> int:
    alpha = image.getchannel("A")
    return sum(alpha.histogram()[1:])


def validate_atlas(
    atlas_path: Path,
    atlas_profile: AtlasProfile,
    extractor_profile: ExtractorProfile,
    *,
    allow_opaque: bool = False,
    allow_near_opaque_used_cells: bool = False,
) -> ValidationResult:
    geo = atlas_profile.geometry
    state_by_row = {state.row: state for state in atlas_profile.states}
    min_used = int(extractor_profile.params.get("min_used_pixels", _DEFAULT_MIN_USED_PIXELS))
    near_opaque = float(
        extractor_profile.params.get("near_opaque_threshold", _DEFAULT_NEAR_OPAQUE_THRESHOLD)
    )
    require_full_bleed = bool(extractor_profile.params.get("require_full_bleed", False))
    full_bleed_alpha_threshold = float(
        extractor_profile.params.get("full_bleed_alpha_threshold", 0.98)
    )

    try:
        with Image.open(atlas_path) as opened:
            source_mode = opened.mode
            source_format = opened.format
            image = opened.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(
            ok=False,
            file=str(atlas_path),
            format=None,
            mode=None,
            width=0,
            height=0,
            errors=[f"could not open atlas: {exc}"],
        )

    result = ValidationResult(
        ok=False,
        file=str(atlas_path),
        format=source_format,
        mode=source_mode,
        width=image.width,
        height=image.height,
    )

    if image.size != (geo.width, geo.height):
        result.errors.append(
            f"expected {geo.width}x{geo.height}, got {image.width}x{image.height}"
        )

    if source_format not in {"PNG", "WEBP"}:
        result.errors.append(f"expected PNG or WebP, got {source_format}")

    if "A" not in (source_mode or "") and not allow_opaque:
        result.errors.append("atlas does not have an alpha channel")

    near_opaque_used: dict[str, list[int]] = defaultdict(list)

    for row_index in range(geo.rows):
        state = state_by_row.get(row_index)
        state_id = state.id if state is not None else f"row-{row_index}"
        used_columns = state.frames if state is not None else 0
        for column_index in range(geo.columns):
            left = column_index * geo.cell_width
            top = row_index * geo.cell_height
            cell = image.crop((left, top, left + geo.cell_width, top + geo.cell_height))
            nontransparent = _alpha_nonzero_count(cell)
            used = column_index < used_columns
            result.cells.append(
                {
                    "state": state_id,
                    "row": row_index,
                    "column": column_index,
                    "used": used,
                    "nontransparent_pixels": nontransparent,
                }
            )
            if used and nontransparent < min_used:
                result.errors.append(
                    f"{state_id} row {row_index} column {column_index} "
                    f"is empty or too sparse ({nontransparent} pixels)"
                )
            if (
                used
                and require_full_bleed
                and nontransparent
                < geo.cell_width * geo.cell_height * full_bleed_alpha_threshold
            ):
                result.errors.append(
                    f"{state_id} row {row_index} column {column_index} "
                    "is not full-bleed enough for this extractor profile "
                    f"({nontransparent}/{geo.cell_width * geo.cell_height} alpha pixels)"
                )
            if used and nontransparent > geo.cell_width * geo.cell_height * near_opaque:
                near_opaque_used[f"{state_id} row {row_index}"].append(column_index)
            if not used and nontransparent != 0:
                result.errors.append(
                    f"{state_id} row {row_index} unused column {column_index} "
                    f"is not transparent ({nontransparent} pixels)"
                )

    for row_label, columns in near_opaque_used.items():
        message = (
            f"{row_label} has {len(columns)} nearly opaque used cells; "
            "this usually means the sprite has a non-transparent background"
        )
        if allow_near_opaque_used_cells:
            result.warnings.append(message)
        else:
            result.errors.append(message)

    if _alpha_nonzero_count(image) == geo.width * geo.height:
        message = "atlas is fully opaque; transparent icon/sticker outputs require an alpha background"
        if allow_opaque:
            result.warnings.append(message)
        else:
            result.errors.append(message)

    result.ok = not result.errors
    return result
