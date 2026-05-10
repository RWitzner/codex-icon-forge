"""Bundle-aware run preparation.

Builds the run-folder structure used by the imagegen-driven half of the
pipeline: entity request manifest, prompt files for base + every state, layout
guides, copied references, chroma key selection, and the imagegen job manifest
with dependencies and mirror policies wired up.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .chroma import choose_chroma_key
from .layout_guides import GUIDE_SUBDIR, render_all
from .manifest import ImagegenManifest, Job, JobInput, now_iso
from .profiles import Bundle, StateSpec, VariantSpec, materialize_dynamic_atlas
from .prompts import compose_base_prompt, compose_row_prompt
from .request_manifest import read_request, write_request
from .tile_contracts import contract_for_state, normalize_game_tile_style_notes
from .tile_guides import render_tile_guides

CANONICAL_BASE_PATH = "references/canonical-base.png"
BASE_DECODED_PATH = "decoded/base.png"
CANONICAL_TILE_STYLE_PATH = "references/canonical-tile-style.png"
CANONICAL_TILE_SENTINEL = "__canonical_tile_reference__"
VALID_PARENT_DECISIONS = {"accepted", "rejected", "needs-regeneration"}


@dataclass
class PrepareOptions:
    bundle: Bundle
    entity_id: str
    display_name: str
    description: str
    entity_notes: str
    style_notes: str
    references: list[Path] = field(default_factory=list)
    output_dir: Path = field(default_factory=Path)
    chroma_key: str = "auto"
    force: bool = False
    variants: list[VariantSpec] = field(default_factory=list)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def default_output_dir(entity_id: str) -> Path:
    """Default run-folder path: ``$PWD/output/icon-forge/<entity-id>-<utc>``.

    Runs land in the user's current working directory by default, never in
    Downloads, Desktop, or some agent-chosen location. The UTC timestamp keeps
    re-runs collision-free without requiring ``--force``.
    """

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path.cwd() / "output" / "icon-forge" / f"{entity_id}-{timestamp}"


def _rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _image_metadata(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return {
            "path": str(path),
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "format": image.format,
        }


def _resolved_style_notes(style_notes: str, style) -> str:
    style_notes = style_notes.strip()
    if not style_notes:
        return style.house_style
    suffix = style.user_style_notes_join.format(user_style_notes=style_notes)
    return style.house_style + suffix


def _make_jobs(
    bundle: Bundle,
    reference_inputs: list[JobInput],
    layout_guides_enabled: bool,
    tile_guide_paths: dict[str, str] | None = None,
) -> list[Job]:
    atlas = bundle.atlas
    jobs: list[Job] = []
    identity_reference_paths: list[str] = []

    if atlas.requires_base:
        identity_reference_paths = [CANONICAL_BASE_PATH, BASE_DECODED_PATH]
        jobs.append(
            Job(
                id="base",
                kind="base",
                status="pending",
                prompt_file="prompts/base.md",
                input_images=list(reference_inputs),
                output_path=BASE_DECODED_PATH,
                depends_on=[],
                generation_skill="$imagegen",
                requires_grounded_generation=bool(reference_inputs),
                allow_prompt_only_generation=not reference_inputs,
                identity_reference_paths=[],
                parallelizable_after=[],
                mirror_policy={},
                recording_owner="parent",
            )
        )

    tile_guide_paths = tile_guide_paths or {}
    game_tile_seed_id = atlas.states[0].id if bundle.id == "game-tiles" and atlas.states else None

    for state in atlas.states:
        depends_on: list[str] = []
        extra_inputs: list[JobInput] = []
        mirror_policy: dict[str, Any] = {}

        if atlas.requires_base:
            depends_on.append("base")
        if game_tile_seed_id is not None and state.id != game_tile_seed_id:
            depends_on.append(CANONICAL_TILE_SENTINEL)

        derivation = atlas.derivation_for(state.id)
        if derivation is not None:
            depends_on.append(derivation.source)
            extra_inputs.append(
                JobInput(
                    path=f"decoded/{derivation.source}.png",
                    role=f"{derivation.source} reference for {state.id} derivation decision",
                )
            )
            mirror_policy = {
                "may_derive_from": derivation.source,
                "derivation": derivation.method,
                "requires_explicit_approval": derivation.requires_explicit_approval,
                "fallback_generation_skill": "$imagegen",
            }

        row_inputs: list[JobInput] = list(reference_inputs)
        if layout_guides_enabled:
            row_inputs.append(
                JobInput(
                    path=f"{GUIDE_SUBDIR}/{state.id}.png",
                    role=(
                        f"layout guide for {state.frames} frame slots; "
                        "use for spacing only, do not copy guide lines"
                    ),
                )
            )
        if state.id in tile_guide_paths:
            row_inputs.append(
                JobInput(
                    path=tile_guide_paths[state.id],
                    role=(
                        "tile layout guide for path mouth width and edge-center alignment; "
                        "use for geometry only, do not copy guide color or visible marks"
                    ),
                )
            )
        if atlas.requires_base:
            row_inputs.append(
                JobInput(path=CANONICAL_BASE_PATH, role="canonical identity reference")
            )
            row_inputs.append(JobInput(path=BASE_DECODED_PATH, role="approved base image"))
        if game_tile_seed_id is not None and state.id != game_tile_seed_id:
            row_inputs.append(
                JobInput(
                    path=CANONICAL_TILE_STYLE_PATH,
                    role="approved canonical style reference from the first game tile",
                )
            )
        row_inputs.extend(extra_inputs)

        jobs.append(
            Job(
                id=state.id,
                kind="row-strip" if state.frames > 1 else "single-frame",
                status="pending",
                prompt_file=f"prompts/rows/{state.id}.md",
                input_images=row_inputs,
                output_path=f"decoded/{state.id}.png",
                depends_on=depends_on,
                generation_skill="$imagegen",
                requires_grounded_generation=bool(row_inputs),
                allow_prompt_only_generation=not row_inputs,
                identity_reference_paths=list(identity_reference_paths),
                parallelizable_after=list(depends_on),
                mirror_policy=mirror_policy,
                recording_owner="parent",
            )
        )

    return jobs


def _validate_game_tile_seed_order(bundle: Bundle) -> None:
    if bundle.id != "game-tiles" or len(bundle.atlas.states) < 2:
        return
    first = contract_for_state(bundle.atlas.states[0])
    later_kinds = {
        contract_for_state(state).kind
        for state in bundle.atlas.states[1:]
    }
    if first.kind.startswith("path_") and "base_terrain" in later_kinds:
        raise ValueError(
            "game-tiles uses the first variant as the canonical style seed; "
            "put a base terrain tile such as grass, stone, sand, or water first, "
            "then path/corner/transition tiles after it"
        )


def prepare_run(options: PrepareOptions) -> dict[str, Any]:
    bundle = options.bundle
    if bundle.atlas.is_dynamic:
        materialised_atlas = materialize_dynamic_atlas(bundle.atlas, options.variants)
        bundle = Bundle(
            id=bundle.id,
            description=bundle.description,
            atlas=materialised_atlas,
            style=bundle.style,
            extractor=bundle.extractor,
            packager=bundle.packager,
        )
    elif options.variants:
        raise ValueError(
            f"bundle {bundle.id!r} does not accept --variant; only bundles "
            "whose atlas declares dynamic_states.enabled support per-run variants"
        )
    _validate_game_tile_seed_order(bundle)
    run_dir = options.output_dir.expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()) and not options.force:
        raise FileExistsError(
            f"{run_dir} already exists and is not empty; pass force=True to reuse it"
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    ref_dir = run_dir / "references"
    prompt_dir = run_dir / "prompts"
    row_prompt_dir = prompt_dir / "rows"
    for directory in (ref_dir, prompt_dir, row_prompt_dir, run_dir / "decoded", run_dir / "qa"):
        directory.mkdir(parents=True, exist_ok=True)

    copied_refs: list[dict[str, Any]] = []
    copied_ref_paths: list[Path] = []
    for index, source in enumerate(options.references, start=1):
        source = source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"reference not found: {source}")
        suffix = source.suffix.lower() or ".png"
        copied = ref_dir / f"reference-{index:02d}{suffix}"
        shutil.copy2(source, copied)
        meta = _image_metadata(copied)
        meta["source_path"] = str(source)
        meta["copied_path"] = str(copied)
        copied_refs.append(meta)
        copied_ref_paths.append(copied)

    chroma = choose_chroma_key(bundle.style, copied_ref_paths, options.chroma_key)
    guides = render_all(run_dir, bundle.atlas)
    tile_guides = render_tile_guides(run_dir, bundle.atlas.states) if bundle.id == "game-tiles" else []
    tile_guide_paths = {guide.state_id: guide.path for guide in tile_guides}

    entity_notes = options.entity_notes.strip()
    if not entity_notes:
        if copied_ref_paths:
            entity_notes = "the entity shown in the reference image(s)"
        else:
            entity_notes = f"a {bundle.style.target_kind}"

    base_prompt = compose_base_prompt(
        bundle.style,
        bundle.atlas,
        display_name=options.display_name,
        entity_notes=entity_notes,
        chroma_key_name=chroma["name"],
        chroma_key_hex=chroma["hex"],
        user_style_notes=options.style_notes,
    )
    (prompt_dir / "base.md").write_text(base_prompt + "\n", encoding="utf-8")

    for state in bundle.atlas.states:
        row_style_notes = options.style_notes
        extra_requirement_text = ""
        if bundle.id == "game-tiles":
            row_style_notes = normalize_game_tile_style_notes(options.style_notes)
            extra_requirement_text = "\n\n" + contract_for_state(state).prompt_text
        row_prompt = compose_row_prompt(
            bundle.style,
            bundle.atlas,
            state,
            entity_id=options.entity_id,
            entity_notes=entity_notes,
            chroma_key_name=chroma["name"],
            chroma_key_hex=chroma["hex"],
            user_style_notes=row_style_notes,
            extra_requirement_text=extra_requirement_text,
        )
        (row_prompt_dir / f"{state.id}.md").write_text(row_prompt + "\n", encoding="utf-8")

    request = {
        "bundle": bundle.id,
        "atlas": bundle.atlas.id,
        "style": bundle.style.id,
        "extractor": bundle.extractor.id,
        "packager": bundle.packager.id,
        "entity_id": options.entity_id,
        "display_name": options.display_name,
        "description": options.description,
        "entity_notes": entity_notes,
        "style_notes": options.style_notes,
        "created_at": now_iso(),
        "atlas_geometry": {
            "columns": bundle.atlas.geometry.columns,
            "rows": bundle.atlas.geometry.rows,
            "cell_width": bundle.atlas.geometry.cell_width,
            "cell_height": bundle.atlas.geometry.cell_height,
        },
        "states": [
            {
                "id": state.id,
                "row": state.row,
                "frames": state.frames,
                "purpose": state.purpose,
            }
            for state in bundle.atlas.states
        ],
        "layout_guides": [guide.to_dict() for guide in guides],
        "tile_guides": [guide.to_dict() for guide in tile_guides],
        "references": copied_refs,
        "chroma_key": chroma,
        "variants": [
            {"id": variant.id, "purpose": variant.purpose}
            for variant in options.variants
        ] if bundle.atlas.is_dynamic else [],
    }
    request_path = write_request(run_dir, request)

    reference_inputs = [
        JobInput(path=_rel(Path(str(ref["copied_path"])), run_dir), role="entity reference")
        for ref in copied_refs
    ]
    jobs = _make_jobs(
        bundle,
        reference_inputs,
        bundle.atlas.layout_guides.enabled,
        tile_guide_paths=tile_guide_paths,
    )

    manifest = ImagegenManifest(
        bundle=bundle.id,
        run_dir=str(run_dir),
        jobs=jobs,
        created_at=now_iso(),
    )
    manifest_path = manifest.save(run_dir)

    return {
        "ok": True,
        "run_dir": str(run_dir),
        "request": str(request_path),
        "manifest": str(manifest_path),
        "ready_jobs": [job.id for job in manifest.ready_jobs()],
        "chroma_key": chroma,
    }


def _default_generated_images_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser().resolve()
    return codex_home / "generated_images"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_source_path(
    *, source: Path, run_dir: Path, allow_synthetic_test_source: bool
) -> str:
    """Verify a recorded image came from ``$imagegen``.

    The subagent write boundary is policy; this is the matching mechanism.
    A confused parent or a misbehaving subagent could otherwise fabricate an
    icon locally with Pillow/canvas/etc. and record it as if it were an
    ``$imagegen`` output. Reject anything that isn't ``$CODEX_HOME/
    generated_images/.../ig_*.png``. The threat model is identical for
    animated row strips and static icons; this check is sprite-agnostic.
    """

    if allow_synthetic_test_source:
        return "synthetic-test"
    if _is_relative_to(source, run_dir):
        raise ValueError(
            "source image is inside the run directory; record the original "
            "$imagegen output from $CODEX_HOME/generated_images/.../ig_*.png "
            "instead"
        )
    generated_root = _default_generated_images_root()
    if not _is_relative_to(source, generated_root) or not source.name.startswith("ig_"):
        raise ValueError(
            "source image does not look like a built-in $imagegen output; "
            f"expected {generated_root}/.../ig_*.png. Do not ingest locally "
            "drawn or post-processed images as visual job outputs."
        )
    return "built-in-imagegen"


def validate_required_grounding(job: Job, run_dir: Path) -> None:
    """Ensure grounded jobs still have all their reference images on disk.

    Pure file-existence check — no sprite-sheet logic. Skipped automatically
    for prompt-only jobs (e.g. no-reference icon bundles) because their
    ``allow_prompt_only_generation`` flag is ``True``.
    """

    if job.allow_prompt_only_generation:
        return
    if not job.input_images:
        raise ValueError(
            f"job {job.id!r} requires grounded generation but lists no input_images"
        )
    missing = [
        str(run_dir / item.path)
        for item in job.input_images
        if not (run_dir / item.path).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"job {job.id!r} is missing required grounding image(s): "
            + ", ".join(missing)
        )


def validate_dependencies_satisfied(job: Job, completed_job_ids: set[str]) -> None:
    missing = [dependency for dependency in job.depends_on if dependency not in completed_job_ids]
    if missing:
        raise ValueError(
            f"job {job.id!r} cannot be recorded before dependencies are complete: "
            + ", ".join(missing)
        )


def record_result(
    run_dir: Path,
    job_id: str,
    source: Path,
    *,
    allow_synthetic_test_source: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Ingest a generated image as a job's decoded output.

    Safe against parallel ``record_result`` calls in the same run directory:
    the manifest read-modify-write is serialised under a sibling lock file
    so two callers cannot drop each other's status updates.

    Provenance is enforced: ``source`` must point at a real ``$imagegen``
    output unless ``allow_synthetic_test_source`` is set (test-only escape
    hatch).

    Refuses to overwrite an already-recorded job unless ``force`` is set, so
    a stale subagent result or a double-record bug cannot silently replace
    an approved decoded output.
    """

    from .manifest import acquire_manifest_lock, load_manifest, release_manifest_lock

    run_dir = run_dir.resolve()
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"recorded source not found: {source}")
    source_provenance = validate_source_path(
        source=source,
        run_dir=run_dir,
        allow_synthetic_test_source=allow_synthetic_test_source,
    )

    lock = acquire_manifest_lock(run_dir)
    try:
        manifest = load_manifest(run_dir)
        job = manifest.job(job_id)
        completed = {item.id for item in manifest.jobs if item.status == "complete"}
        validate_dependencies_satisfied(job, completed)
        validate_required_grounding(job, run_dir)
        target = run_dir / job.output_path
        if target.exists() and not force:
            raise FileExistsError(
                f"job {job_id!r} already has a recorded output at {target}; "
                "pass force=True to replace it"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

        extras: list[str] = []
        if job_id == "base":
            canonical = run_dir / CANONICAL_BASE_PATH
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, canonical)
            extras.append(str(canonical))

        job.status = "complete"
        job.source = str(source)
        job.recorded_at = now_iso()
        manifest.save(run_dir)

        return {
            "ok": True,
            "job_id": job_id,
            "decoded_path": str(target),
            "additional_writes": extras,
            "source_provenance": source_provenance,
            "next_ready_jobs": [j.id for j in manifest.ready_jobs()],
        }
    finally:
        release_manifest_lock(lock)


def promote_tile_reference(run_dir: Path, job_id: str, *, force: bool = False) -> dict[str, Any]:
    """Promote the approved seed tile into the canonical game-tile reference."""

    from .manifest import acquire_manifest_lock, load_manifest, release_manifest_lock

    run_dir = run_dir.resolve()
    source = run_dir / "decoded" / f"{job_id}.png"
    if not source.is_file():
        raise FileNotFoundError(f"decoded tile for {job_id!r} is missing: {source}")
    target = run_dir / CANONICAL_TILE_STYLE_PATH
    if target.exists() and not force:
        raise FileExistsError(
            f"canonical tile style reference already exists at {target}; pass force=True to replace it"
        )

    lock = acquire_manifest_lock(run_dir)
    try:
        manifest = load_manifest(run_dir)
        if manifest.bundle == "game-tiles" and manifest.jobs and manifest.jobs[0].id != job_id:
            raise ValueError(
                "game-tiles canonical reference must be promoted from the first seed job "
                f"{manifest.jobs[0].id!r}, got {job_id!r}"
            )
        seed = manifest.job(job_id)
        if seed.status != "complete":
            raise ValueError(f"job {job_id!r} must be recorded before promotion")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        unlocked: list[str] = []
        for job in manifest.jobs:
            if CANONICAL_TILE_SENTINEL in job.depends_on:
                job.depends_on = [dep for dep in job.depends_on if dep != CANONICAL_TILE_SENTINEL]
                job.parallelizable_after = [
                    dep for dep in job.parallelizable_after if dep != CANONICAL_TILE_SENTINEL
                ]
                unlocked.append(job.id)
        manifest.save(run_dir)
        return {
            "ok": True,
            "job_id": job_id,
            "canonical_reference": str(target),
            "unlocked_jobs": unlocked,
            "next_ready_jobs": [job.id for job in manifest.ready_jobs()],
        }
    finally:
        release_manifest_lock(lock)


def record_tile_qa(
    run_dir: Path,
    job_id: str,
    selected_source: Path,
    *,
    subagent_note: str,
    parent_decision: str,
    parent_note: str,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    selected_source = selected_source.expanduser().resolve()
    if parent_decision not in VALID_PARENT_DECISIONS:
        raise ValueError(
            f"parent_decision must be one of {sorted(VALID_PARENT_DECISIONS)}, got {parent_decision!r}"
        )
    qa_dir = run_dir / "qa" / "jobs"
    qa_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job_id,
        "selected_source": str(selected_source),
        "subagent_note": subagent_note,
        "parent_decision": parent_decision,
        "parent_note": parent_note,
        "recorded_at": now_iso(),
    }
    target = qa_dir / f"{job_id}.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "qa_path": str(target)}


def derive_mirror(run_dir: Path, target_state: str, decision_note: str) -> dict[str, Any]:
    from .manifest import acquire_manifest_lock, load_manifest, release_manifest_lock
    from .profiles import load_bundle

    run_dir = run_dir.resolve()
    request = read_request(run_dir)
    bundle = load_bundle(str(request["bundle"]))

    derivation = bundle.atlas.derivation_for(target_state)
    if derivation is None:
        raise ValueError(f"atlas {bundle.atlas.id!r} has no derivation rule for {target_state!r}")
    if derivation.method != "horizontal-mirror":
        raise NotImplementedError(f"derivation method {derivation.method!r} not yet supported")

    source_decoded = run_dir / f"decoded/{derivation.source}.png"
    target_decoded = run_dir / f"decoded/{target_state}.png"
    if not source_decoded.is_file():
        raise FileNotFoundError(f"source decoded image missing: {source_decoded}")

    lock = acquire_manifest_lock(run_dir)
    try:
        with Image.open(source_decoded) as opened:
            mirrored = opened.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            mirrored.save(target_decoded)
        manifest = load_manifest(run_dir)
        job = manifest.job(target_state)
        job.status = "complete"
        job.source = f"derived:{derivation.method}:{derivation.source}"
        job.recorded_at = now_iso()
        job.mirror_policy = dict(job.mirror_policy)
        job.mirror_policy["applied"] = True
        job.mirror_policy["decision_note"] = decision_note
        manifest.save(run_dir)
    finally:
        release_manifest_lock(lock)

    return {
        "ok": True,
        "target": target_state,
        "source": derivation.source,
        "method": derivation.method,
        "decoded_path": str(target_decoded),
        "decision_note": decision_note,
    }
