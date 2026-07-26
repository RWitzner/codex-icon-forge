"""Visual review sheet rendering and decoded-output validation."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .chroma import parse_hex_color
from .extractors._helpers import remove_chroma_background
from .manifest import Job, load_manifest, now_iso
from .profiles import Bundle, RootInput, StateSpec, load_bundle_for_run
from .request_manifest import read_request

REVIEW_SCHEMA_VERSION = 1
REVIEW_JSON = "review.json"
REVIEW_SHEET = "review-sheet.png"
MAX_DECODED_PIXELS = 4096 * 4096

_SUPPORTED_FORMATS = {"PNG", "WEBP"}
_SHEET_BACKGROUND = (255, 255, 255, 255)
_TEXT = (25, 25, 25, 255)
_MUTED = (105, 105, 105, 255)
_ERROR = (178, 35, 35, 255)
_OK = (30, 120, 70, 255)
_PENDING = (170, 110, 0, 255)
_LIGHT_CHECKS = ((210, 210, 210, 255), (238, 238, 238, 255))
_DARK_CHECKS = ((44, 44, 44, 255), (68, 68, 68, 255))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_save_image(image: Image.Image, path: Path) -> None:
    tmp_path = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
    image.save(tmp_path, format="PNG")
    os.replace(tmp_path, path)


def _font() -> ImageFont.ImageFont:
    return ImageFont.load_default()


def _logical_size(bundle: Bundle, state: StateSpec) -> dict[str, int]:
    return {
        "width": bundle.atlas.geometry.cell_width * state.frames,
        "height": bundle.atlas.geometry.cell_height,
    }


def _state_by_id(bundle: Bundle) -> dict[str, StateSpec]:
    return {state.id: state for state in bundle.atlas.states}


def _safe_decoded_output_path(run_dir: Path, output_rel: str) -> tuple[Path, str | None]:
    raw = Path(output_rel)
    if raw.is_absolute():
        return raw, "unsafe decoded output path: absolute paths are not allowed"
    if ".." in raw.parts:
        return run_dir / raw, "unsafe decoded output path: parent components are not allowed"
    if not raw.parts or raw.parts[0] != "decoded":
        return run_dir / raw, "unsafe decoded output path: must start with decoded/"

    decoded_root = (run_dir / "decoded").resolve(strict=False)
    candidate = run_dir / raw
    try:
        lexical_resolved = candidate.resolve(strict=False)
    except OSError as exc:
        return candidate, f"unsafe decoded output path: cannot resolve path: {exc}"
    if not _is_relative_to(lexical_resolved, decoded_root):
        return candidate, "unsafe decoded output path: must stay under run_dir/decoded"

    if candidate.exists():
        try:
            real_resolved = candidate.resolve(strict=True)
        except OSError as exc:
            return candidate, f"unsafe decoded output path: cannot resolve existing file: {exc}"
        if not _is_relative_to(real_resolved, decoded_root):
            return candidate, "unsafe decoded output path: symlink escapes run_dir/decoded"

    return candidate, None


def _clean_image_for_review(
    image: Image.Image,
    *,
    bundle: Bundle,
    chroma_key: tuple[int, int, int],
) -> Image.Image:
    if bundle.extractor.strategy.startswith("chroma-key"):
        threshold = float(bundle.extractor.params.get("key_threshold", 96.0))
        alpha_erode_px = int(bundle.extractor.params.get("alpha_erode_px", 1))
        alpha_blur_radius = float(bundle.extractor.params.get("alpha_blur_radius", 1.0))
        return remove_chroma_background(
            image,
            chroma_key,
            threshold,
            alpha_erode_px=alpha_erode_px,
            alpha_blur_radius=alpha_blur_radius,
        )
    return image.convert("RGBA")


def _job_entry(
    *,
    bundle: Bundle,
    run_dir: Path,
    job: Job | None,
    state: StateSpec | None,
    job_id: str,
    chroma_key: tuple[int, int, int],
    cleaned_images: dict[str, Image.Image],
) -> dict[str, Any]:
    if state is not None:
        expected = _logical_size(bundle, state)
    elif job is not None and job.kind == "base":
        expected = {
            "width": bundle.atlas.geometry.cell_width,
            "height": bundle.atlas.geometry.cell_height,
        }
    else:
        expected = {"width": None, "height": None}
    output_rel = job.output_path if job is not None else f"decoded/{job_id}.png"
    output_path, path_error = _safe_decoded_output_path(run_dir, output_rel)
    errors: list[str] = []
    warnings: list[str] = []
    source: dict[str, Any] = {
        "path": str(output_path),
        "exists": output_path.is_file(),
        "width": None,
        "height": None,
        "mode": None,
        "format": None,
        "pixels": None,
        "max_pixels": MAX_DECODED_PIXELS,
    }
    alpha_bbox: list[int] | None = None
    geometry = {
        "aspect_ratio_ok": False,
        "minimum_size_ok": False,
        "scale_x": None,
        "scale_y": None,
    }

    skipped = False
    if job is None:
        errors.append("missing manifest job")
    elif job.status == "pending" and job.review_status == "not-recorded":
        skipped = True
        warnings.append("not recorded yet; skipped until this job is generated")
    elif path_error is not None:
        errors.append(path_error)
    elif not output_path.is_file():
        errors.append("missing decoded output")
    else:
        try:
            with Image.open(output_path) as opened:
                pixels = opened.width * opened.height
                source.update(
                    {
                        "width": opened.width,
                        "height": opened.height,
                        "mode": opened.mode,
                        "format": opened.format,
                        "pixels": pixels,
                    }
                )
                within_pixel_budget = pixels <= MAX_DECODED_PIXELS
                if not within_pixel_budget:
                    errors.append(
                        "decoded output exceeds max decoded pixel budget: "
                        f"{pixels} > {MAX_DECODED_PIXELS}"
                    )
                elif opened.format not in _SUPPORTED_FORMATS:
                    errors.append(
                        "unsupported decoded output format: "
                        f"{opened.format or 'unknown'}"
                    )
                else:
                    cleaned = _clean_image_for_review(
                        opened,
                        bundle=bundle,
                        chroma_key=chroma_key,
                    )
                    cleaned_images[str(output_path)] = cleaned.copy()
                    alpha_bbox_tuple = cleaned.getchannel("A").getbbox()
                    if alpha_bbox_tuple is None:
                        errors.append("no visible alpha content")
                    else:
                        alpha_bbox = list(alpha_bbox_tuple)
        except Image.DecompressionBombError as exc:
            errors.append(f"decoded output exceeds Pillow decompression safety limit: {exc}")
        except (OSError, UnidentifiedImageError) as exc:
            errors.append(f"cannot open decoded output: {exc}")

    if source["width"] and source["height"] and expected["width"] and expected["height"]:
        width = int(source["width"])
        height = int(source["height"])
        expected_width = int(expected["width"])
        expected_height = int(expected["height"])
        geometry["scale_x"] = width / expected_width
        geometry["scale_y"] = height / expected_height
        geometry["aspect_ratio_ok"] = width * expected_height == height * expected_width
        geometry["minimum_size_ok"] = width >= expected_width and height >= expected_height
        if not geometry["aspect_ratio_ok"]:
            errors.append(
                "decoded output aspect ratio does not match logical strip "
                f"{expected_width}:{expected_height}"
            )
        if not geometry["minimum_size_ok"]:
            errors.append(
                "decoded output is smaller than logical strip size "
                f"{expected_width}x{expected_height}"
            )

    if job is not None and job.status != "complete" and not skipped:
        errors.append(f"job status is {job.status}, not complete")

    status = "skipped" if skipped else ("validated" if not errors else "error")

    return {
        "job_id": job_id,
        "state_id": state.id if state is not None else None,
        "kind": job.kind if job is not None else None,
        "review_status": job.review_status if job is not None else "missing",
        "output_path": output_rel,
        "expected": {"logical_size": expected},
        "source": source,
        "geometry": geometry,
        "alpha_bbox": alpha_bbox,
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "ok": not errors,
    }


def _checkerboard(
    size: tuple[int, int],
    colors: tuple[tuple[int, int, int, int], tuple[int, int, int, int]],
) -> Image.Image:
    image = Image.new("RGBA", size, colors[0])
    draw = ImageDraw.Draw(image)
    cell = 16
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            color = colors[((x // cell) + (y // cell)) % 2]
            draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=color)
    return image


def _fit_preview(
    image: Image.Image,
    box_size: tuple[int, int],
) -> Image.Image | None:
    image = image.copy()
    image.thumbnail((box_size[0] - 20, box_size[1] - 20), Image.Resampling.LANCZOS)
    return image


def _draw_preview(
    sheet: Image.Image,
    *,
    path: Path,
    x: int,
    y: int,
    size: tuple[int, int],
    colors: tuple[tuple[int, int, int, int], tuple[int, int, int, int]],
    cleaned_images: dict[str, Image.Image],
) -> None:
    preview = _checkerboard(size, colors)
    cached = cleaned_images.get(str(path))
    fitted = _fit_preview(cached, size) if cached is not None else None
    if fitted is not None:
        offset = (x + (size[0] - fitted.width) // 2, y + (size[1] - fitted.height) // 2)
        preview.alpha_composite(fitted, (offset[0] - x, offset[1] - y))
    else:
        draw = ImageDraw.Draw(preview)
        draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=_ERROR, width=3)
        draw.line((16, 16, size[0] - 16, size[1] - 16), fill=_ERROR, width=3)
        draw.line((size[0] - 16, 16, 16, size[1] - 16), fill=_ERROR, width=3)
    sheet.alpha_composite(preview, (x, y))
    ImageDraw.Draw(sheet).rectangle((x, y, x + size[0] - 1, y + size[1] - 1), outline=(150, 150, 150, 255))


def _status_color(entry: dict[str, Any]) -> tuple[int, int, int, int]:
    if entry["errors"]:
        return _ERROR
    if entry["status"] == "skipped":
        return _MUTED
    if entry["review_status"] == "approved":
        return _OK
    return _PENDING


def _render_sheet(
    *,
    run_dir: Path,
    bundle: Bundle,
    entries: list[dict[str, Any]],
    cleaned_images: dict[str, Image.Image],
) -> tuple[Image.Image, dict[str, Any]]:
    row_height = 240
    header_height = 84
    width = 640
    height = header_height + max(1, len(entries)) * row_height + 24
    sheet = Image.new("RGBA", (width, height), _SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = _font()
    draw.rectangle((0, 0, width, 64), fill=(246, 246, 246, 255))
    draw.text((24, 24), f"Icon Forge Review: {bundle.id}", fill=_TEXT, font=font)
    draw.text((24, 46), f"Run: {run_dir}", fill=_MUTED, font=font)
    layout = {
        "width": width,
        "height": height,
        "header_height": header_height,
        "row_height": row_height,
        "light_preview": {"x": 140, "y_offset": 28, "width": 180, "height": 180},
        "dark_preview": {"x": 390, "y_offset": 28, "width": 180, "height": 180},
    }

    if not entries:
        draw.text((24, header_height + 30), "No visual jobs found", fill=_ERROR, font=font)
        return sheet, layout

    for index, entry in enumerate(entries):
        row_y = header_height + index * row_height
        draw.line((24, row_y, width - 24, row_y), fill=(220, 220, 220, 255))
        draw.text((34, row_y + 30), entry["job_id"], fill=_TEXT, font=font)
        draw.text((34, row_y + 52), entry["review_status"], fill=_MUTED, font=font)
        status_color = _status_color(entry)
        draw.ellipse((34, row_y + 76, 50, row_y + 92), fill=status_color)
        status = entry["status"].upper() if entry["status"] == "skipped" else ("OK" if not entry["errors"] else "ERROR")
        draw.text((58, row_y + 76), status, fill=status_color, font=font)
        if entry["errors"]:
            draw.text((34, row_y + 104), entry["errors"][0][:42], fill=_ERROR, font=font)
        elif entry["warnings"]:
            draw.text((34, row_y + 104), entry["warnings"][0][:42], fill=_MUTED, font=font)

        light = layout["light_preview"]
        dark = layout["dark_preview"]
        output_path = run_dir / entry["output_path"]
        _draw_preview(
            sheet,
            path=output_path,
            x=light["x"],
            y=row_y + light["y_offset"],
            size=(light["width"], light["height"]),
            colors=_LIGHT_CHECKS,
            cleaned_images=cleaned_images,
        )
        _draw_preview(
            sheet,
            path=output_path,
            x=dark["x"],
            y=row_y + dark["y_offset"],
            size=(dark["width"], dark["height"]),
            colors=_DARK_CHECKS,
            cleaned_images=cleaned_images,
        )
        draw.text((light["x"], row_y + 214), "light checker", fill=_MUTED, font=font)
        draw.text((dark["x"], row_y + 214), "dark checker", fill=_MUTED, font=font)

    return sheet, layout


def review_outputs(
    bundle: Bundle,
    run_dir: Path,
    force: bool = False,
    *,
    root: RootInput | None = None,
) -> dict[str, Any]:
    """Validate decoded outputs and render review artifacts for a run.

    ``root`` mirrors ``extract``/``derive``/``finalize``: when the caller passed
    ``--profile-dir`` it must reach the loader, otherwise a run whose private
    profile root has moved can be extracted but not reviewed. The run's own
    persisted bundle still wins over whatever bundle the caller handed in — only
    the root search chain is taken from the caller.
    """

    run_dir = run_dir.expanduser().resolve()
    bundle = load_bundle_for_run(run_dir, root=root)
    # Explicitly read request.json so review uses the same persisted run
    # contract as extract/finalize and fails early for incomplete run folders.
    request = read_request(run_dir)
    chroma_payload = request.get("chroma_key", {})
    if "rgb" in chroma_payload:
        chroma_key = tuple(int(value) for value in chroma_payload["rgb"])
    else:
        chroma_key = parse_hex_color(str(chroma_payload["hex"]))
    manifest = load_manifest(run_dir)
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    json_path = qa_dir / REVIEW_JSON
    sheet_path = qa_dir / REVIEW_SHEET
    if not force and (json_path.exists() or sheet_path.exists()):
        raise FileExistsError(
            f"review artifacts already exist under {qa_dir}; pass force=True to regenerate"
        )

    jobs_by_id = {job.id: job for job in manifest.jobs}
    states_by_id = _state_by_id(bundle)
    entries: list[dict[str, Any]] = []
    cleaned_images: dict[str, Image.Image] = {}
    for state in bundle.atlas.states:
        entries.append(
            _job_entry(
                bundle=bundle,
                run_dir=run_dir,
                job=jobs_by_id.get(state.id),
                state=state,
                job_id=state.id,
                chroma_key=chroma_key,
                cleaned_images=cleaned_images,
            )
        )
    for job_id in sorted(set(jobs_by_id) - set(states_by_id)):
        entries.append(
            _job_entry(
                bundle=bundle,
                run_dir=run_dir,
                job=jobs_by_id[job_id],
                state=None,
                job_id=job_id,
                chroma_key=chroma_key,
                cleaned_images=cleaned_images,
            )
        )
        if jobs_by_id[job_id].kind != "base":
            entries[-1]["errors"].append("recorded job has no matching atlas state")
            entries[-1]["ok"] = False

    top_errors: list[str] = []
    attempted_entries = [entry for entry in entries if entry["status"] != "skipped"]
    if not attempted_entries:
        top_errors.append("no completed visual outputs were available to validate")
    ok = not top_errors and all(
        entry["ok"] for entry in entries if entry["status"] != "skipped"
    )
    sheet, layout = _render_sheet(
        run_dir=run_dir,
        bundle=bundle,
        entries=entries,
        cleaned_images=cleaned_images,
    )
    payload: dict[str, Any] = {
        "ok": ok,
        "schema_version": REVIEW_SCHEMA_VERSION,
        "created_at": now_iso(),
        "bundle": bundle.id,
        "request_bundle": request.get("bundle"),
        "run_dir": str(run_dir),
        "sheet_path": str(sheet_path),
        "json_path": str(json_path),
        "sheet": {"layout": layout},
        "errors": top_errors,
        "jobs": entries,
    }
    _atomic_save_image(sheet, sheet_path)
    _atomic_write_json(json_path, payload)
    return payload
