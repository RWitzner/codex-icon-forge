"""Persisted review, approval, rejection, and resume workflow tests."""

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

from engine.manifest import (  # noqa: E402
    SCHEMA_VERSION,
    ImagegenManifest,
    Job,
    load_manifest,
)
from engine.run_setup import (  # noqa: E402
    PrepareOptions,
    approve_results,
    prepare_run,
    record_result,
    reject_result,
    resume_run,
)
from engine import VariantSpec, load_bundle  # noqa: E402


class WorkflowFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("app-icon-set")

    def prepare(self, run_dir: Path, count: int = 2) -> dict:
        variants = [
            VariantSpec(id="main", purpose="primary app icon"),
            VariantSpec(id="share-ext", purpose="share extension icon"),
        ][:count]
        return prepare_run(
            PrepareOptions(
                bundle=self.bundle,
                entity_id="myapp",
                display_name="My App",
                description="Workflow state test.",
                entity_notes="a simple geometric mark",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=variants,
            )
        )

    @staticmethod
    def source(root: Path, name: str, color: tuple[int, int, int, int]) -> Path:
        path = root / name
        Image.new("RGBA", (1024, 1024), color).save(path)
        return path


class ManifestMigrationTests(unittest.TestCase):
    def test_schema_v2_review_defaults_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            manifest = {
                "schema_version": 2,
                "bundle": "legacy",
                "run_dir": str(run_dir),
                "created_at": "2025-01-01T00:00:00+00:00",
                "jobs": [
                    {
                        "id": "done",
                        "kind": "single-frame",
                        "status": "complete",
                        "prompt_file": "prompts/rows/done.md",
                        "input_images": [],
                        "output_path": "decoded/done.png",
                    },
                    {
                        "id": "todo",
                        "kind": "single-frame",
                        "status": "pending",
                        "prompt_file": "prompts/rows/todo.md",
                        "input_images": [],
                        "output_path": "decoded/todo.png",
                    },
                ],
            }
            (run_dir / "imagegen-jobs.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            loaded = load_manifest(run_dir)

            self.assertGreater(SCHEMA_VERSION, 2)
            self.assertEqual(loaded.schema_version, SCHEMA_VERSION)
            self.assertEqual(loaded.job("done").review_status, "approved")
            self.assertEqual(loaded.job("todo").review_status, "not-recorded")
            self.assertIsNone(loaded.approval_gate_job_id)


class ReviewWorkflowTests(WorkflowFixture):
    def test_first_job_gates_multi_job_fanout_until_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            result = self.prepare(run_dir)

            self.assertEqual(result["ready_jobs"], ["main"])
            manifest = load_manifest(run_dir)
            self.assertEqual(manifest.approval_gate_job_id, "main")
            self.assertEqual(
                [job.review_status for job in manifest.jobs],
                ["not-recorded", "not-recorded"],
            )

            record_result(
                run_dir,
                "main",
                self.source(root, "main.png", (200, 20, 20, 255)),
                allow_synthetic_test_source=True,
            )
            self.assertEqual(load_manifest(run_dir).ready_jobs(), [])
            self.assertEqual(resume_run(run_dir)["next_action"], "review")

            approved = approve_results(run_dir, job_ids=["main"], note="Looks good.")
            self.assertEqual(approved["approved_jobs"], ["main"])
            self.assertEqual(approved["ready_jobs"], ["share-ext"])

            manifest = load_manifest(run_dir)
            self.assertEqual(manifest.job("main").review_status, "approved")
            self.assertEqual(manifest.job("main").review_note, "Looks good.")
            self.assertIsNotNone(manifest.job("main").reviewed_at)

    def test_record_rejects_a_job_blocked_by_the_approval_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir)

            for force in (False, True):
                with self.subTest(force=force):
                    with self.assertRaisesRegex(ValueError, "not ready"):
                        record_result(
                            run_dir,
                            "share-ext",
                            self.source(
                                root,
                                f"blocked-{force}.png",
                                (20, 20, 200, 255),
                            ),
                            allow_synthetic_test_source=True,
                            force=force,
                        )

    def test_force_rerecord_cannot_bypass_a_newly_rejected_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir)
            record_result(
                run_dir,
                "main",
                self.source(root, "main.png", (200, 20, 20, 255)),
                allow_synthetic_test_source=True,
            )
            approve_results(run_dir, job_ids=["main"], note="Gate approved.")
            record_result(
                run_dir,
                "share-ext",
                self.source(root, "share-ext.png", (20, 20, 200, 255)),
                allow_synthetic_test_source=True,
            )
            reject_result(run_dir, "main", "Gate needs a new direction.")

            with self.assertRaisesRegex(ValueError, "not ready"):
                record_result(
                    run_dir,
                    "share-ext",
                    self.source(root, "replacement.png", (20, 200, 20, 255)),
                    allow_synthetic_test_source=True,
                    force=True,
                )

    def test_rejecting_gate_invalidates_approved_fanout_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir)
            original_gate = self.source(
                root, "main-original.png", (200, 20, 20, 255)
            )
            downstream = self.source(
                root, "share-ext.png", (20, 20, 200, 255)
            )
            record_result(
                run_dir,
                "main",
                original_gate,
                allow_synthetic_test_source=True,
            )
            approve_results(run_dir, job_ids=["main"], note="Gate approved.")
            record_result(
                run_dir,
                "share-ext",
                downstream,
                allow_synthetic_test_source=True,
            )
            approval = approve_results(
                run_dir,
                job_ids=["share-ext"],
                note="Fanout approved.",
            )
            self.assertEqual(approval["approved_jobs"], ["main", "share-ext"])
            self.assertEqual(approval["newly_approved_jobs"], ["share-ext"])
            self.assertEqual(resume_run(run_dir)["next_action"], "extract")

            rejected = reject_result(
                run_dir, "main", "Gate needs a new direction."
            )

            self.assertEqual(rejected["invalidated_jobs"], ["share-ext"])
            manifest = load_manifest(run_dir)
            invalidated = manifest.job("share-ext")
            self.assertEqual(invalidated.status, "pending")
            self.assertEqual(invalidated.review_status, "rejected")
            self.assertIn("main", invalidated.review_note or "")
            self.assertIsNotNone(invalidated.reviewed_at)
            self.assertEqual(invalidated.source, str(downstream.resolve()))

            replacement_gate = self.source(
                root, "main-replacement.png", (20, 200, 20, 255)
            )
            record_result(
                run_dir,
                "main",
                replacement_gate,
                allow_synthetic_test_source=True,
            )
            approve_results(
                run_dir, job_ids=["main"], note="Replacement gate approved."
            )
            resumed = resume_run(run_dir)
            self.assertEqual(resumed["next_action"], "regenerate")
            self.assertEqual(resumed["ready_jobs"], ["share-ext"])
            self.assertEqual(resumed["rejected_jobs"], ["share-ext"])

    def test_rejecting_dependency_invalidates_transitive_dependents_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            def completed(job_id: str, depends_on: list[str]) -> Job:
                return Job(
                    id=job_id,
                    kind="single-frame",
                    status="complete",
                    prompt_file=f"prompts/rows/{job_id}.md",
                    input_images=[],
                    output_path=f"decoded/{job_id}.png",
                    depends_on=depends_on,
                    source=f"/generated/{job_id}.png",
                    recorded_at="2025-01-01T00:00:00+00:00",
                    review_status="approved",
                    reviewed_at="2025-01-01T00:01:00+00:00",
                    review_note="Approved.",
                )

            ImagegenManifest(
                bundle="dependency-graph",
                run_dir=str(run_dir),
                jobs=[
                    completed("root", []),
                    completed("child", ["root"]),
                    completed("grandchild", ["child"]),
                    completed("unrelated", []),
                ],
                created_at="2025-01-01T00:00:00+00:00",
            ).save(run_dir)

            result = reject_result(run_dir, "root", "Root direction changed.")

            self.assertEqual(result["invalidated_jobs"], ["child", "grandchild"])
            manifest = load_manifest(run_dir)
            for job_id in ("root", "child", "grandchild"):
                with self.subTest(job_id=job_id):
                    job = manifest.job(job_id)
                    self.assertEqual(job.status, "pending")
                    self.assertEqual(job.review_status, "rejected")
                    self.assertIsNotNone(job.reviewed_at)
            self.assertIn("root", manifest.job("child").review_note or "")
            unrelated = manifest.job("unrelated")
            self.assertEqual(unrelated.status, "complete")
            self.assertEqual(unrelated.review_status, "approved")

    def test_rejection_is_generation_ready_and_preserves_decision_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir)
            first = self.source(root, "first.png", (200, 20, 20, 255))
            second = self.source(root, "second.png", (20, 200, 20, 255))
            record_result(
                run_dir, "main", first, allow_synthetic_test_source=True
            )

            rejected = reject_result(run_dir, "main", "Mark is too detailed.")

            self.assertEqual(rejected["ready_jobs"], ["main"])
            manifest = load_manifest(run_dir)
            job = manifest.job("main")
            self.assertEqual(job.status, "pending")
            self.assertEqual(job.review_status, "rejected")
            self.assertEqual(job.review_note, "Mark is too detailed.")
            self.assertIsNotNone(job.reviewed_at)
            self.assertEqual(job.source, str(first.resolve()))
            self.assertEqual(resume_run(run_dir)["next_action"], "regenerate")

            rerecorded = record_result(
                run_dir, "main", second, allow_synthetic_test_source=True
            )
            self.assertTrue(rerecorded["ok"])
            rerecorded_job = load_manifest(run_dir).job("main")
            self.assertEqual(rerecorded_job.review_status, "pending")
            self.assertEqual(rerecorded_job.review_note, "Mark is too detailed.")

    def test_resume_reports_grouped_jobs_and_extract_when_all_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir, count=1)

            initial = resume_run(run_dir)
            self.assertEqual(initial["next_action"], "generate")
            self.assertEqual(initial["ready_jobs"], ["main"])
            self.assertEqual(initial["pending_review_jobs"], [])
            self.assertEqual(initial["rejected_jobs"], [])
            self.assertEqual(initial["approved_jobs"], [])

            record_result(
                run_dir,
                "main",
                self.source(root, "main.png", (20, 20, 200, 255)),
                allow_synthetic_test_source=True,
            )
            review = resume_run(run_dir)
            self.assertEqual(review["next_action"], "review")
            self.assertEqual(review["pending_review_jobs"], ["main"])

            approve_results(run_dir, approve_all=True)
            complete = resume_run(run_dir)
            self.assertEqual(complete["next_action"], "extract")
            self.assertEqual(complete["approved_jobs"], ["main"])

    def test_approve_reject_invalid_job_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare(run_dir, count=1)
            with self.assertRaises(ValueError):
                approve_results(run_dir, job_ids=["main"])
            with self.assertRaises(ValueError):
                reject_result(run_dir, "main", "No generated result yet.")
            with self.assertRaises(ValueError):
                approve_results(run_dir)

    def test_dependencies_require_approval_before_the_gate_is_ready(self) -> None:
        base = Job(
            id="base",
            kind="base",
            status="pending",
            prompt_file="prompts/base.md",
            input_images=[],
            output_path="decoded/base.png",
        )
        state = Job(
            id="main",
            kind="single-frame",
            status="pending",
            prompt_file="prompts/rows/main.md",
            input_images=[],
            output_path="decoded/main.png",
            depends_on=["base"],
        )
        manifest = ImagegenManifest(
            bundle="base-dependent",
            run_dir="/tmp/run",
            jobs=[base, state],
            created_at="2025-01-01T00:00:00+00:00",
            approval_gate_job_id="main",
        )

        self.assertEqual([job.id for job in manifest.ready_jobs()], ["base"])
        base.status = "complete"
        base.review_status = "pending"
        self.assertEqual(manifest.ready_jobs(), [])
        base.review_status = "approved"
        self.assertEqual([job.id for job in manifest.ready_jobs()], ["main"])


class WorkflowCliTests(WorkflowFixture):
    def run_cli(
        self, *args: str, expect_success: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "scripts/icon_forge.py", *args],
            cwd=str(SKILL_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if expect_success and result.returncode != 0:
            self.fail(
                f"CLI failed ({result.returncode}): {' '.join(args)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def test_approve_reject_and_resume_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir)
            source = self.source(root, "main.png", (100, 100, 220, 255))
            record_result(
                run_dir, "main", source, allow_synthetic_test_source=True
            )

            resume = json.loads(
                self.run_cli("resume", "--run-dir", str(run_dir)).stdout
            )
            self.assertEqual(resume["next_action"], "review")

            approved = json.loads(
                self.run_cli(
                    "approve",
                    "--run-dir",
                    str(run_dir),
                    "--job-id",
                    "main",
                    "--note",
                    "Approved in CLI.",
                ).stdout
            )
            self.assertEqual(approved["approved_jobs"], ["main"])
            self.assertEqual(approved["ready_jobs"], ["share-ext"])

            rejected = json.loads(
                self.run_cli(
                    "reject",
                    "--run-dir",
                    str(run_dir),
                    "--job-id",
                    "main",
                    "--note",
                    "Changed my mind.",
                ).stdout
            )
            self.assertEqual(rejected["rejected_job"], "main")
            self.assertEqual(rejected["next_action"], "regenerate")

    def test_extract_rejects_unapproved_and_unknown_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare(run_dir, count=1)
            record_result(
                run_dir,
                "main",
                self.source(root, "main.png", (100, 100, 220, 255)),
                allow_synthetic_test_source=True,
            )

            unapproved = self.run_cli(
                "extract",
                "--run-dir",
                str(run_dir),
                "--states",
                "main",
                expect_success=False,
            )
            self.assertNotEqual(unapproved.returncode, 0)
            self.assertIn("not approved", unapproved.stderr.lower())

            unknown = self.run_cli(
                "extract",
                "--run-dir",
                str(run_dir),
                "--states",
                "does-not-exist",
                expect_success=False,
            )
            self.assertNotEqual(unknown.returncode, 0)
            self.assertIn("unknown state", unknown.stderr.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
