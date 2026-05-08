"""Run-preparation and concurrency tests.

Validates that ``prepare_run`` produces sane folder structure and a job
manifest for icon-style bundles, and that parallel ``record_result`` calls
do not drop manifest updates.

Run from the skill root:
    python -m unittest tests.test_run_setup -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import VariantSpec, load_bundle, load_bundle_for_run  # noqa: E402
from engine.profiles import ProfileError, materialize_dynamic_atlas  # noqa: E402
from engine.run_setup import (  # noqa: E402
    PrepareOptions,
    default_output_dir,
    prepare_run,
    record_result,
)


# Canonical dev-pack preset for the slack-stickers bundle. Mirrors the
# variant strings documented in README.md. Tests use this so the bundle
# can be materialised to its previous fixed shape (12 stickers) without
# the atlas profile having to declare them.
_DEV_PACK_VARIANTS = [
    VariantSpec(id="shipping-it",    purpose="joyful 'we shipped it' celebration"),
    VariantSpec(id="tests-passing",  purpose="all-green test suite, calm confident vibe"),
    VariantSpec(id="merge-conflict", purpose="tangled conflict knot, flustered but recoverable"),
    VariantSpec(id="ci-failed",      purpose="red broken pipeline, frustrated face"),
    VariantSpec(id="deploy",         purpose="rocket lifting off, confident motion"),
    VariantSpec(id="hotfix",         purpose="bandage on a server, urgent but stable"),
    VariantSpec(id="retry",          purpose="circular arrow, second-attempt energy"),
    VariantSpec(id="lgtm",           purpose="thumbs-up, looks good to me"),
    VariantSpec(id="wip",            purpose="work-in-progress sign, hard hat"),
    VariantSpec(id="debug",          purpose="magnifying glass on bug, focused"),
    VariantSpec(id="refactor",       purpose="tidied gears or clean broom, satisfied"),
    VariantSpec(id="ship",           purpose="cargo ship sailing, steady and committed"),
]


class RunSetupSlackStickersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("slack-stickers")

    def test_prepare_omits_base_and_layout_guides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            options = PrepareOptions(
                bundle=self.bundle,
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="Dev-themed Slack stickers.",
                entity_notes="dev-themed stickers",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=_DEV_PACK_VARIANTS,
            )
            result = prepare_run(options)
            self.assertTrue(result["ok"])

            manifest = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))
            jobs = manifest["jobs"]
            self.assertEqual(len(jobs), 12)
            for job in jobs:
                self.assertNotIn("base", job["depends_on"])
                input_paths = [item["path"] for item in job["input_images"]]
                self.assertNotIn("references/canonical-base.png", input_paths)
                self.assertFalse(any("layout-guides" in p for p in input_paths))
                self.assertEqual(job["mirror_policy"], {})

            ready = result["ready_jobs"]
            expected_ids = {variant.id for variant in _DEV_PACK_VARIANTS}
            self.assertEqual(set(ready), expected_ids)

            self.assertFalse((run_dir / "references" / "layout-guides").exists())

    def test_prepare_with_explicit_chroma_key_marks_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            options = PrepareOptions(
                bundle=self.bundle,
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="With explicit chroma key.",
                entity_notes="dev-themed stickers",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="#00FF00",
                force=True,
                variants=_DEV_PACK_VARIANTS[:1],
            )
            result = prepare_run(options)
            self.assertEqual(result["chroma_key"]["selection"], "manual")
            self.assertEqual(result["chroma_key"]["hex"], "#00FF00")


class RunSetupAppIconsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("app-icons")

    def test_prepare_creates_single_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            options = PrepareOptions(
                bundle=self.bundle,
                entity_id="solar",
                display_name="Solar",
                description="A sunlit minimalist icon.",
                entity_notes="a stylised sun emblem with three rays",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
            )
            result = prepare_run(options)
            self.assertTrue(result["ok"])
            self.assertEqual(result["ready_jobs"], ["icon"])

            manifest = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["jobs"]), 1)
            self.assertEqual(manifest["jobs"][0]["id"], "icon")
            self.assertEqual(manifest["jobs"][0]["depends_on"], [])


class CliEndToEndTests(unittest.TestCase):
    """Drives the icon_forge CLI through prepare for a sticker bundle.

    Skips the heavy imagegen + extract + finalize path; that flow is covered
    by the bundle-level tests in test_slack_stickers_bundle and
    test_app_icons_bundle. This test asserts the CLI shells out cleanly and
    its JSON output matches what the engine returns directly.
    """

    def _run_cli(self, *args: str) -> dict:
        cmd = [sys.executable, "scripts/icon_forge.py", *args]
        result = subprocess.run(
            cmd, cwd=str(SKILL_DIR), capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise AssertionError(
                f"CLI failed: {' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise AssertionError(f"non-JSON CLI output: {result.stdout!r}")

    def test_bundles_subcommand_lists_both(self) -> None:
        result = self._run_cli("bundles")
        bundle_ids = {entry["id"] for entry in result["bundles"]}
        self.assertIn("slack-stickers", bundle_ids)
        self.assertIn("app-icons", bundle_ids)

    def test_show_subcommand_resolves_app_icons(self) -> None:
        result = self._run_cli("show", "app-icons")
        self.assertEqual(result["id"], "app-icons")
        self.assertEqual(result["packager"]["strategy"], "multi-size-folder")
        self.assertFalse(result["atlas"]["requires_base"])

    def test_prepare_subcommand_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            args = [
                "prepare",
                "--bundle", "slack-stickers",
                "--entity-id", "dev-pack",
                "--display-name", "Dev Pack",
                "--description", "CLI smoke test.",
                "--notes", "dev-themed stickers",
                "--output-dir", str(run_dir),
                "--force",
            ]
            for variant in _DEV_PACK_VARIANTS:
                args.extend(["--variant", f"{variant.id}:{variant.purpose}"])
            result = self._run_cli(*args)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["ready_jobs"]), 12)


class ConcurrentRecordTests(unittest.TestCase):
    """Parallel ``record_result`` calls must not drop manifest updates."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("slack-stickers")

    def test_parallel_records_do_not_drop_status_updates(self) -> None:
        import concurrent.futures

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            run_dir = tmp_root / "run"
            fake_imagegen = tmp_root / "fake-imagegen"
            fake_imagegen.mkdir(parents=True)

            options = PrepareOptions(
                bundle=self.bundle,
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="Concurrency test.",
                entity_notes="dev stickers",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=_DEV_PACK_VARIANTS,
            )
            prepare_run(options)

            jobs = [variant.id for variant in _DEV_PACK_VARIANTS]
            sources: dict[str, Path] = {}
            for job_id in jobs:
                src = fake_imagegen / f"{job_id}.png"
                Image.new("RGBA", (128, 128), (200, 50, 50, 255)).save(src)
                sources[job_id] = src

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures = [
                    pool.submit(
                        record_result,
                        run_dir,
                        job_id,
                        sources[job_id],
                        allow_synthetic_test_source=True,
                    )
                    for job_id in jobs
                ]
                results = [future.result() for future in futures]

            self.assertEqual(len(results), len(jobs))
            self.assertTrue(all(r["ok"] for r in results))

            from engine.manifest import load_manifest

            manifest = load_manifest(run_dir)
            statuses = {job.id: job.status for job in manifest.jobs}
            for job_id in jobs:
                self.assertEqual(
                    statuses[job_id],
                    "complete",
                    f"{job_id} status should be complete after parallel record",
                )


class RecordProvenanceTests(unittest.TestCase):
    """``record_result`` must reject sources that did not come from $imagegen."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("slack-stickers")

    def _prepare(self, run_dir: Path) -> None:
        prepare_run(
            PrepareOptions(
                bundle=self.bundle,
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="Provenance test.",
                entity_notes="dev stickers",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=_DEV_PACK_VARIANTS[:1],
            )
        )

    def test_rejects_source_inside_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._prepare(run_dir)
            inside = run_dir / "fake.png"
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(inside)
            with self.assertRaises(ValueError):
                record_result(run_dir, _DEV_PACK_VARIANTS[0].id, inside)

    def test_rejects_source_outside_generated_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            run_dir = tmp_root / "run"
            self._prepare(run_dir)
            outside = tmp_root / "ig_fake.png"
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(outside)
            with self.assertRaises(ValueError):
                record_result(run_dir, _DEV_PACK_VARIANTS[0].id, outside)

    def test_synthetic_test_flag_bypasses_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            run_dir = tmp_root / "run"
            self._prepare(run_dir)
            outside = tmp_root / "fabricated.png"
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(outside)
            result = record_result(
                run_dir,
                _DEV_PACK_VARIANTS[0].id,
                outside,
                allow_synthetic_test_source=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["source_provenance"], "synthetic-test")

    def test_refuses_to_overwrite_completed_job_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            run_dir = tmp_root / "run"
            self._prepare(run_dir)
            job_id = _DEV_PACK_VARIANTS[0].id
            first = tmp_root / "first.png"
            second = tmp_root / "second.png"
            Image.new("RGBA", (16, 16), (200, 0, 0, 255)).save(first)
            Image.new("RGBA", (16, 16), (0, 200, 0, 255)).save(second)

            record_result(
                run_dir, job_id, first, allow_synthetic_test_source=True
            )
            with self.assertRaises(FileExistsError):
                record_result(
                    run_dir, job_id, second, allow_synthetic_test_source=True
                )
            result = record_result(
                run_dir,
                job_id,
                second,
                allow_synthetic_test_source=True,
                force=True,
            )
            self.assertTrue(result["ok"])


class GroundingFlagConsistencyTests(unittest.TestCase):
    """No-reference icon bundles must be marked prompt-only, not grounded."""

    def test_slack_stickers_without_references_are_prompt_only(self) -> None:
        bundle = load_bundle("slack-stickers")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="dev-pack",
                    display_name="Dev Pack",
                    description="Flag consistency test.",
                    entity_notes="dev stickers",
                    style_notes="",
                    references=[],
                    output_dir=run_dir,
                    chroma_key="auto",
                    force=True,
                    variants=_DEV_PACK_VARIANTS[:1],
                )
            )
            manifest = json.loads(
                (run_dir / "imagegen-jobs.json").read_text(encoding="utf-8")
            )
            for job in manifest["jobs"]:
                self.assertEqual(job["input_images"], [])
                self.assertFalse(job["requires_grounded_generation"], msg=job["id"])
                self.assertTrue(job["allow_prompt_only_generation"], msg=job["id"])


class DynamicVariantValidationTests(unittest.TestCase):
    """Variant input is rejected at materialise time for the obvious shapes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("app-icon-set")

    def test_zero_variants_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(self.bundle.atlas, [])

    def test_too_many_variants_rejected(self) -> None:
        too_many = [VariantSpec(id=f"v{i:02d}", purpose="x") for i in range(13)]
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(self.bundle.atlas, too_many)

    def test_invalid_id_rejected(self) -> None:
        for bad_id in ("Main", "main_icon", "share ext", "-leading", "x" * 32, ""):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ProfileError):
                    materialize_dynamic_atlas(
                        self.bundle.atlas,
                        [VariantSpec(id=bad_id, purpose="x")],
                    )

    def test_duplicate_ids_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(
                self.bundle.atlas,
                [
                    VariantSpec(id="main", purpose="first"),
                    VariantSpec(id="main", purpose="second"),
                ],
            )

    def test_empty_purpose_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(
                self.bundle.atlas,
                [VariantSpec(id="main", purpose="   ")],
            )

    def test_overlong_purpose_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(
                self.bundle.atlas,
                [VariantSpec(id="main", purpose="x" * 201)],
            )

    def test_static_bundle_rejects_variants(self) -> None:
        # app-icons is a static (non-dynamic) bundle: a single hardcoded
        # "icon" state with no dynamic_states block. Passing variants must
        # be rejected. (slack-stickers used to be the static bundle here,
        # but it now ships dynamic so the contract moved to app-icons.)
        static_bundle = load_bundle("app-icons")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            options = PrepareOptions(
                bundle=static_bundle,
                entity_id="solar",
                display_name="Solar",
                description="reject variants",
                entity_notes="dev",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=[VariantSpec(id="main", purpose="x")],
            )
            with self.assertRaises(ValueError):
                prepare_run(options)

    def test_materialise_assigns_sequential_rows(self) -> None:
        atlas = materialize_dynamic_atlas(
            self.bundle.atlas,
            [
                VariantSpec(id="main", purpose="primary"),
                VariantSpec(id="alt", purpose="alternate"),
                VariantSpec(id="watch", purpose="1-bit watchOS"),
            ],
        )
        self.assertEqual(atlas.geometry.columns, 1)
        self.assertEqual(atlas.geometry.rows, 3)
        self.assertEqual([s.id for s in atlas.states], ["main", "alt", "watch"])
        self.assertEqual([s.row for s in atlas.states], [0, 1, 2])


class SlackStickersDynamicValidationTests(unittest.TestCase):
    """slack-stickers is dynamic too; the same guards must apply.

    These mirror DynamicVariantValidationTests but root the assertions in
    the slack-stickers bundle so its dynamic_states config (enabled=True,
    max_states=12) is explicitly exercised. Without these, a future
    change that lowers max_states in the slack-stickers atlas would slip
    past coverage.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("slack-stickers")

    def test_atlas_advertises_dynamic_with_twelve_max(self) -> None:
        self.assertTrue(self.bundle.atlas.is_dynamic)
        self.assertIsNotNone(self.bundle.atlas.dynamic_states)
        self.assertEqual(self.bundle.atlas.dynamic_states.max_states, 12)

    def test_zero_variants_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(self.bundle.atlas, [])

    def test_too_many_variants_rejected(self) -> None:
        too_many = [VariantSpec(id=f"v{i:02d}", purpose="x") for i in range(13)]
        with self.assertRaises(ProfileError):
            materialize_dynamic_atlas(self.bundle.atlas, too_many)


class DynamicVariantPrepareTests(unittest.TestCase):
    """``prepare_run`` materialises the atlas and writes variants into the request."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("app-icon-set")

    def test_prepare_with_variants_emits_one_job_per_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            variants = [
                VariantSpec(id="main", purpose="primary app icon"),
                VariantSpec(id="share-ext", purpose="share extension"),
            ]
            options = PrepareOptions(
                bundle=self.bundle,
                entity_id="myapp",
                display_name="MyApp",
                description="prepare with 2 variants",
                entity_notes="modern minimalist",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=variants,
            )
            result = prepare_run(options)
            self.assertTrue(result["ok"])
            self.assertEqual(set(result["ready_jobs"]), {"main", "share-ext"})

            request = json.loads(
                (run_dir / "request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["request"], str(run_dir / "request.json"))
            from engine.request_manifest import LEGACY_REQUEST_FILENAME

            self.assertFalse((run_dir / LEGACY_REQUEST_FILENAME).exists())
            self.assertEqual(
                request["variants"],
                [
                    {"id": "main", "purpose": "primary app icon"},
                    {"id": "share-ext", "purpose": "share extension"},
                ],
            )
            self.assertEqual(request["atlas_geometry"]["rows"], 2)

            manifest = json.loads(
                (run_dir / "imagegen-jobs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest["jobs"]), 2)
            self.assertEqual({j["id"] for j in manifest["jobs"]}, {"main", "share-ext"})

    def test_load_bundle_for_run_reads_request_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "request.json").write_text(
                json.dumps(
                    {
                        "bundle": "app-icon-set",
                        "variants": [
                            {"id": "main", "purpose": "primary app icon"},
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            bundle = load_bundle_for_run(run_dir)

            self.assertEqual(bundle.atlas.state_ids, ("main",))

    def test_load_bundle_for_run_reads_legacy_request_filename(self) -> None:
        from engine.request_manifest import LEGACY_REQUEST_FILENAME

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / LEGACY_REQUEST_FILENAME).write_text(
                json.dumps(
                    {
                        "bundle": "app-icon-set",
                        "variants": [
                            {"id": "main", "purpose": "primary app icon"},
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            bundle = load_bundle_for_run(run_dir)

            self.assertEqual(bundle.atlas.state_ids, ("main",))


class DynamicVariantCliTests(unittest.TestCase):
    """CLI exposes --variant and validates input shapes early."""

    def _run_cli(
        self, *args: str, expect_success: bool = True
    ) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "scripts/icon_forge.py", *args]
        result = subprocess.run(
            cmd, cwd=str(SKILL_DIR), capture_output=True, text=True, check=False
        )
        if expect_success and result.returncode != 0:
            raise AssertionError(
                f"CLI failed: {' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def test_prepare_with_variants_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = self._run_cli(
                "prepare",
                "--bundle", "app-icon-set",
                "--entity-id", "myapp",
                "--display-name", "MyApp",
                "--description", "CLI variants smoke test",
                "--notes", "modern minimalist",
                "--output-dir", str(run_dir),
                "--variant", "main:primary app icon",
                "--variant", "share-ext:share extension simpler",
                "--force",
            )
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(set(payload["ready_jobs"]), {"main", "share-ext"})

    def test_prepare_rejects_malformed_variant_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = self._run_cli(
                "prepare",
                "--bundle", "app-icon-set",
                "--entity-id", "myapp",
                "--description", "malformed",
                "--output-dir", str(run_dir),
                "--variant", "no-colon-here",
                "--force",
                expect_success=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--variant", result.stderr)

    def test_prepare_rejects_duplicate_variant_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = self._run_cli(
                "prepare",
                "--bundle", "app-icon-set",
                "--entity-id", "myapp",
                "--description", "duplicates",
                "--output-dir", str(run_dir),
                "--variant", "main:first",
                "--variant", "main:second",
                "--force",
                expect_success=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate", result.stderr.lower())

    def test_prepare_rejects_invalid_variant_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = self._run_cli(
                "prepare",
                "--bundle", "app-icon-set",
                "--entity-id", "myapp",
                "--description", "invalid id",
                "--output-dir", str(run_dir),
                "--variant", "Main:primary",
                "--force",
                expect_success=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    def test_prepare_rejects_too_many_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            args = [
                "prepare",
                "--bundle", "app-icon-set",
                "--entity-id", "myapp",
                "--description", "cap test",
                "--output-dir", str(run_dir),
                "--force",
            ]
            for i in range(13):
                args.extend(["--variant", f"v{i:02d}:purpose"])
            result = self._run_cli(*args, expect_success=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("at most 12", result.stderr)


class DefaultOutputDirTests(unittest.TestCase):
    """``--output-dir`` defaults to the current working directory, never Downloads."""

    def test_default_output_dir_under_cwd(self) -> None:
        path = default_output_dir("myapp")
        cwd = Path.cwd().resolve()
        self.assertEqual(path.parts[: len(cwd.parts)], cwd.parts)
        self.assertIn("output", path.parts)
        self.assertIn("icon-forge", path.parts)
        self.assertTrue(path.name.startswith("myapp-"))

    def test_cli_prepare_without_output_dir_uses_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp).resolve()
            cmd = [
                sys.executable,
                str(SKILL_DIR / "scripts" / "icon_forge.py"),
                "prepare",
                "--bundle", "slack-stickers",
                "--entity-id", "smoke",
                "--description", "default-output-dir test",
                "--notes", "dev stickers",
                "--variant", f"{_DEV_PACK_VARIANTS[0].id}:{_DEV_PACK_VARIANTS[0].purpose}",
                "--force",
            ]
            result = subprocess.run(
                cmd, cwd=str(tmp_root), capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                raise AssertionError(
                    f"CLI failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            run_dir = Path(payload["run_dir"]).resolve()
            self.assertEqual(run_dir.parts[: len(tmp_root.parts)], tmp_root.parts)
            self.assertIn("output", run_dir.parts)
            self.assertIn("icon-forge", run_dir.parts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
