#!/usr/bin/env python3
"""icon-forge CLI — bundle-driven icon and sticker pack pipeline.

Subcommands:

    bundles                List available bundles.
    show <bundle>          Print resolved profile data for a bundle.
    prepare                Build a run folder + prompts + imagegen-jobs manifest.
    status                 Report ready/pending jobs in a run.
    record                 Ingest a generated image as a job's decoded output.
    approve                Approve one or more recorded outputs.
    reject                 Reject one output and reopen it for generation.
    resume                 Report the next persisted workflow action.
    extract                Run the bundle's extractor over decoded strips.
    derive                 Apply a derivation rule (rare for icon bundles).
    finalize               Compose + validate + package an existing frames root.

Image generation itself is delegated to the installed `$imagegen` skill via
the manifest contract; this CLI does not call image APIs directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import (  # noqa: E402
    PROFILES_ROOT,
    VariantSpec,
    load_bundle,
    load_bundle_for_run,
)
from engine import extractor as engine_extractor  # noqa: E402
from engine.chroma import parse_hex_color  # noqa: E402
from engine.manifest import load_manifest  # noqa: E402
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402
from engine.request_manifest import read_request  # noqa: E402
from engine.run_setup import (  # noqa: E402
    PrepareOptions,
    approve_results,
    default_output_dir,
    derive_mirror,
    prepare_run,
    record_result,
    reject_result,
    resume_run,
)
from PIL import Image  # noqa: E402


def _parse_variant_arg(raw: str) -> VariantSpec:
    if ":" not in raw:
        raise argparse.ArgumentTypeError(
            f"--variant expects 'id:purpose', got {raw!r}"
        )
    variant_id, _, purpose = raw.partition(":")
    return VariantSpec(id=variant_id.strip(), purpose=purpose.strip())


def _list_bundles(_args: argparse.Namespace) -> int:
    bundles_dir = PROFILES_ROOT / "bundles"
    if not bundles_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"no bundles dir at {bundles_dir}"}))
        return 1
    bundles = []
    for path in sorted(bundles_dir.glob("*.json")):
        bundle = load_bundle(path.stem)
        bundles.append(
            {
                "id": bundle.id,
                "description": bundle.description,
                "atlas": bundle.atlas.id,
                "style": bundle.style.id,
                "extractor": bundle.extractor.id,
                "packager": bundle.packager.id,
            }
        )
    print(json.dumps({"ok": True, "bundles": bundles}, indent=2))
    return 0


def _show(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.bundle)
    summary = {
        "id": bundle.id,
        "description": bundle.description,
        "atlas": {
            "id": bundle.atlas.id,
            "geometry": asdict(bundle.atlas.geometry),
            "requires_base": bundle.atlas.requires_base,
            "states": [
                {"id": state.id, "row": state.row, "frames": state.frames, "purpose": state.purpose}
                for state in bundle.atlas.states
            ],
            "derivations": [asdict(d) for d in bundle.atlas.derivations],
            "layout_guides_enabled": bundle.atlas.layout_guides.enabled,
        },
        "style": {
            "id": bundle.style.id,
            "target_kind": bundle.style.target_kind,
            "state_requirement_keys": sorted(bundle.style.state_requirements.keys()),
            "chroma_key_candidates": [
                asdict(candidate) for candidate in bundle.style.chroma_key.candidates
            ],
        },
        "extractor": {
            "id": bundle.extractor.id,
            "strategy": bundle.extractor.strategy,
            "params": bundle.extractor.params,
        },
        "packager": {
            "id": bundle.packager.id,
            "strategy": bundle.packager.strategy,
            "output_root": bundle.packager.output_root,
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


def _prepare(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.bundle)
    references = [Path(ref) for ref in (args.reference or [])]
    variants = list(args.variant or [])
    output_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir
        else default_output_dir(args.entity_id)
    )
    options = PrepareOptions(
        bundle=bundle,
        entity_id=args.entity_id,
        display_name=args.display_name or args.entity_id,
        description=args.description,
        entity_notes=args.notes,
        style_notes=args.style_notes,
        references=references,
        output_dir=output_dir,
        chroma_key=args.chroma_key,
        force=args.force,
        variants=variants,
    )
    result = prepare_run(options)
    print(json.dumps(result, indent=2))
    return 0


def _status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    pending = [j for j in manifest.jobs if j.status == "pending"]
    complete = [j for j in manifest.jobs if j.status == "complete"]
    ready = manifest.ready_jobs()
    summary = {
        "ok": True,
        "bundle": manifest.bundle,
        "run_dir": manifest.run_dir,
        "totals": {
            "all": len(manifest.jobs),
            "pending": len(pending),
            "complete": len(complete),
            "ready": len(ready),
        },
        "approval_gate_job_id": manifest.approval_gate_job_id,
        "ready_jobs": [
            {
                "id": job.id,
                "kind": job.kind,
                "prompt_file": job.prompt_file,
                "output_path": job.output_path,
                "input_images": [item.to_dict() for item in job.input_images],
                "depends_on": job.depends_on,
                "mirror_policy": job.mirror_policy,
                "review_status": job.review_status,
            }
            for job in ready
        ],
        "blocked_jobs": [
            {
                "id": job.id,
                "depends_on": job.depends_on,
                "review_status": job.review_status,
            }
            for job in pending
            if job not in ready
        ],
        "review_jobs": [
            {
                "id": job.id,
                "review_status": job.review_status,
                "reviewed_at": job.reviewed_at,
                "review_note": job.review_note,
            }
            for job in complete
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


def _record(args: argparse.Namespace) -> int:
    result = record_result(
        Path(args.run_dir).resolve(),
        args.job_id,
        Path(args.source),
        allow_synthetic_test_source=getattr(args, "allow_synthetic_test_source", False),
        force=getattr(args, "force", False),
    )
    print(json.dumps(result, indent=2))
    return 0


def _approve(args: argparse.Namespace) -> int:
    result = approve_results(
        Path(args.run_dir).resolve(),
        job_ids=list(args.job_id or []),
        approve_all=bool(args.approve_all),
        note=args.note,
    )
    print(json.dumps(result, indent=2))
    return 0


def _reject(args: argparse.Namespace) -> int:
    result = reject_result(
        Path(args.run_dir).resolve(),
        args.job_id,
        args.note,
    )
    print(json.dumps(result, indent=2))
    return 0


def _resume(args: argparse.Namespace) -> int:
    print(json.dumps(resume_run(Path(args.run_dir).resolve()), indent=2))
    return 0


def _extract(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    bundle = load_bundle_for_run(run_dir)
    manifest = load_manifest(run_dir)
    request = read_request(run_dir)
    chroma_hex = request["chroma_key"]["hex"]
    chroma_rgb = parse_hex_color(chroma_hex)

    states_arg = (args.states or "all").strip()
    if states_arg == "all":
        states = list(bundle.atlas.states)
    else:
        wanted = {item.strip() for item in states_arg.split(",") if item.strip()}
        unknown = sorted(wanted - set(bundle.atlas.state_ids))
        if unknown:
            raise SystemExit("unknown state id(s): " + ", ".join(unknown))
        states = [state for state in bundle.atlas.states if state.id in wanted]

    unapproved = [
        state.id
        for state in states
        if (
            manifest.job(state.id).status != "complete"
            or manifest.job(state.id).review_status != "approved"
        )
    ]
    if unapproved:
        raise SystemExit(
            "cannot extract state(s) that are not approved: "
            + ", ".join(unapproved)
        )

    frames_root = run_dir / "frames"
    frames_root.mkdir(parents=True, exist_ok=True)
    strategy = engine_extractor.get(bundle.extractor.strategy)

    manifest_data = {
        "ok": True,
        "chroma_key": {"hex": chroma_hex, "rgb": list(chroma_rgb)},
        "rows": [],
    }
    for state in states:
        strip_path = run_dir / f"decoded/{state.id}.png"
        if not strip_path.is_file():
            raise SystemExit(f"missing decoded strip for {state.id}: {strip_path}")
        with Image.open(strip_path) as opened:
            strip = opened.convert("RGBA")
        frames, method = strategy(
            strip,
            state,
            bundle.atlas,
            bundle.extractor,
            chroma_key=chroma_rgb,
        )
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for index, frame in enumerate(frames):
            target = state_dir / f"{index:02d}.png"
            frame.save(target)
            outputs.append(str(target))
        manifest_data["rows"].append(
            {"state": state.id, "method": method, "frames": outputs}
        )

    (frames_root / "frames-manifest.json").write_text(
        json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": True, "frames_root": str(frames_root), "states": [s.id for s in states]}, indent=2))
    return 0


def _derive(args: argparse.Namespace) -> int:
    result = derive_mirror(
        Path(args.run_dir).resolve(),
        args.target,
        args.decision_note,
    )
    print(json.dumps(result, indent=2))
    return 0


def _finalize(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.bundle)
    output_run_dir = Path(args.output_run_dir).expanduser().resolve()
    if bundle.atlas.is_dynamic:
        try:
            bundle = load_bundle_for_run(output_run_dir)
        except FileNotFoundError as exc:
            raise SystemExit(
                f"bundle {bundle.id!r} is dynamic; finalize requires a prior "
                f"`prepare` run at {output_run_dir} so variants can be reloaded"
            ) from exc
    overrides: dict[str, str] = {}
    if args.icon_forge_home:
        overrides["ICON_FORGE_HOME"] = args.icon_forge_home
    options = FinalizeOptions(
        entity_id=args.entity_id,
        display_name=args.display_name or args.entity_id,
        description=args.description,
        frames_root=Path(args.frames).expanduser().resolve(),
        output_run_dir=output_run_dir,
        package_overrides=overrides or None,
        force=args.force,
    )
    result = finalize_run(bundle, options)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bundles", help="list available bundles")

    show = sub.add_parser("show", help="print bundle data")
    show.add_argument("bundle")

    prepare = sub.add_parser("prepare", help="build run folder + prompts + manifest")
    prepare.add_argument("--bundle", required=True)
    prepare.add_argument("--entity-id", required=True)
    prepare.add_argument("--display-name", default="")
    prepare.add_argument("--description", required=True)
    prepare.add_argument("--notes", default="", help="Stable description used in prompts.")
    prepare.add_argument("--style-notes", default="", help="Optional user style overrides.")
    prepare.add_argument("--reference", action="append", default=[], help="Optional reference image; repeatable.")
    prepare.add_argument(
        "--output-dir",
        default="",
        help=(
            "Run folder path. Defaults to "
            "$PWD/output/icon-forge/<entity-id>-<UTC-timestamp>. "
            "Override only when the user names a different path explicitly."
        ),
    )
    prepare.add_argument("--chroma-key", default="auto", help="`auto` or #RRGGBB to override.")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument(
        "--variant",
        action="append",
        type=_parse_variant_arg,
        default=[],
        help=(
            "Variant for dynamic bundles (e.g. app-icon-set), repeatable. "
            "Format: 'id:purpose'. Example: --variant 'main:primary app icon'."
        ),
    )

    status = sub.add_parser("status", help="show ready and pending jobs")
    status.add_argument("--run-dir", required=True)

    record = sub.add_parser("record", help="ingest a generated image as a job's decoded output")
    record.add_argument("--run-dir", required=True)
    record.add_argument("--job-id", required=True)
    record.add_argument("--source", required=True)
    record.add_argument(
        "--force",
        action="store_true",
        help="Replace an already-recorded decoded output for this job.",
    )
    record.add_argument(
        "--allow-synthetic-test-source",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    approve = sub.add_parser("approve", help="approve recorded visual results")
    approve.add_argument("--run-dir", required=True)
    approval_target = approve.add_mutually_exclusive_group(required=True)
    approval_target.add_argument(
        "--job-id",
        action="append",
        default=[],
        help="Recorded job id to approve; repeatable.",
    )
    approval_target.add_argument(
        "--all",
        dest="approve_all",
        action="store_true",
        help="Approve every currently recorded result.",
    )
    approve.add_argument("--note", default="", help="Optional review note.")

    reject = sub.add_parser("reject", help="reject a result and reopen generation")
    reject.add_argument("--run-dir", required=True)
    reject.add_argument("--job-id", required=True)
    reject.add_argument("--note", required=True, help="Reason for rejection.")

    resume = sub.add_parser("resume", help="show the next persisted workflow action")
    resume.add_argument("--run-dir", required=True)

    extract = sub.add_parser("extract", help="run extractor strategy over decoded strips")
    extract.add_argument("--run-dir", required=True)
    extract.add_argument("--states", default="all", help='Comma-separated state ids or "all".')

    derive = sub.add_parser("derive", help="apply a derivation rule (e.g. mirror)")
    derive.add_argument("--run-dir", required=True)
    derive.add_argument("--target", required=True, help="State id to derive (must have a derivation rule).")
    derive.add_argument("--decision-note", required=True, help="One-sentence rationale recorded in the manifest.")

    finalize = sub.add_parser("finalize", help="compose + validate + package an existing frames root")
    finalize.add_argument("--bundle", required=True)
    finalize.add_argument("--frames", required=True)
    finalize.add_argument("--entity-id", required=True)
    finalize.add_argument("--display-name", default="")
    finalize.add_argument("--description", required=True)
    finalize.add_argument("--output-run-dir", required=True)
    finalize.add_argument("--icon-forge-home", default="", help="Override ${ICON_FORGE_HOME} for the packager output root.")
    finalize.add_argument("--force", action="store_true")

    args = parser.parse_args()
    handlers = {
        "bundles": _list_bundles,
        "show": _show,
        "prepare": _prepare,
        "status": _status,
        "record": _record,
        "approve": _approve,
        "reject": _reject,
        "resume": _resume,
        "extract": _extract,
        "derive": _derive,
        "finalize": _finalize,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
