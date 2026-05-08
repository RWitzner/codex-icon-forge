"""Atlas composition driven by an AtlasProfile.

Mirrors the behaviour of the legacy scripts/compose_atlas.py, but reads grid,
cell, and state geometry from a profile instead of module-level constants.
The legacy script is preserved verbatim under scripts/ for parity tests.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .profiles import AtlasProfile

IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}
_ASPECT_TOLERANCE = 0.02


def image_files(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def find_row_frames(root: Path, state_id: str, row_index: int) -> list[Path]:
    candidates = [
        root / state_id,
        root / f"row-{row_index}",
        root / f"row{row_index}",
        root / f"{row_index}-{state_id}",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            files = image_files(candidate)
            if files:
                return files
    globs = [
        f"{state_id}_*",
        f"{state_id}-*",
        f"row{row_index}_*",
        f"row-{row_index}-*",
    ]
    files: list[Path] = []
    for pattern in globs:
        files.extend(p for p in root.glob(pattern) if p.suffix.lower() in IMAGE_SUFFIXES)
    return sorted(set(files))


def paste_centered(
    atlas: Image.Image,
    source: Image.Image,
    row: int,
    column: int,
    profile: AtlasProfile,
) -> None:
    geo = profile.geometry
    frame = source.convert("RGBA")
    if frame.size != (geo.cell_width, geo.cell_height):
        frame.thumbnail((geo.cell_width, geo.cell_height), Image.Resampling.LANCZOS)
    left = column * geo.cell_width + (geo.cell_width - frame.width) // 2
    top = row * geo.cell_height + (geo.cell_height - frame.height) // 2
    atlas.alpha_composite(frame, (left, top))


def compose_from_source_atlas(
    path: Path,
    profile: AtlasProfile,
    *,
    resize_source: bool = False,
) -> Image.Image:
    geo = profile.geometry
    target_size = (geo.width, geo.height)
    target_aspect = geo.width / geo.height

    with Image.open(path) as opened:
        source = opened.convert("RGBA")
    if source.size != target_size:
        if not resize_source:
            raise ValueError(
                f"source atlas must be {geo.width}x{geo.height}; "
                f"got {source.width}x{source.height}"
            )
        source_aspect = source.width / source.height
        if abs(source_aspect - target_aspect) > _ASPECT_TOLERANCE:
            raise ValueError(
                "refusing to resize source atlas because its aspect ratio does not match "
                f"the target atlas ratio {target_aspect:.3f}; got {source_aspect:.3f}."
            )
        source = source.resize(target_size, Image.Resampling.LANCZOS)

    atlas = Image.new("RGBA", target_size, (0, 0, 0, 0))
    for state in profile.states:
        for column in range(state.frames):
            left = column * geo.cell_width
            top = state.row * geo.cell_height
            cell = source.crop((left, top, left + geo.cell_width, top + geo.cell_height))
            atlas.alpha_composite(cell, (left, top))
    return atlas


def compose_from_frames(root: Path, profile: AtlasProfile) -> Image.Image:
    geo = profile.geometry
    atlas = Image.new("RGBA", (geo.width, geo.height), (0, 0, 0, 0))
    for state in profile.states:
        files = find_row_frames(root, state.id, state.row)
        if len(files) < state.frames:
            raise ValueError(
                f"{state.id} row needs {state.frames} frames, found {len(files)} under {root}"
            )
        for column, frame_path in enumerate(files[: state.frames]):
            with Image.open(frame_path) as frame:
                paste_centered(atlas, frame, state.row, column, profile)
    return atlas


def save_outputs(
    atlas: Image.Image,
    output: Path,
    webp_output: Path | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(output)
    if webp_output is not None:
        webp_output.parent.mkdir(parents=True, exist_ok=True)
        atlas.save(webp_output, format="WEBP", lossless=True, quality=100, method=6)
