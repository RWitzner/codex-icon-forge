"""Visual review sheet and decoded-output validation tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import VariantSpec, load_bundle  # noqa: E402
from engine.manifest import Job, load_manifest  # noqa: E402
from engine.review import review_outputs  # noqa: E402
from engine.run_setup import (  # noqa: E402
    PrepareOptions,
    approve_results,
    prepare_run,
    record_result,
)


def _write_source(path: Path, size: tuple[int, int], *, transparent: bool = False) -> Path:
    image = Image.new("RGBA", size, (0, 0, 0, 0) if transparent else (0, 0, 0, 0))
    if not transparent:
        draw = ImageDraw.Draw(image)
        margin = max(4, min(size) // 8)
        draw.rounded_rectangle(
            (margin, margin, size[0] - margin - 1, size[1] - margin - 1),
            radius=max(4, min(size) // 10),
            fill=(220, 90, 30, 255),
            outline=(20, 20, 20, 255),
            width=max(1, min(size) // 64),
        )
    image.save(path)
    return path


def _write_chroma_source(
    path: Path,
    size: tuple[int, int],
    *,
    foreground: bool,
    chroma: tuple[int, int, int] = (255, 0, 255),
) -> Path:
    image = Image.new("RGBA", size, chroma + (255,))
    if foreground:
        draw = ImageDraw.Draw(image)
        margin = max(8, min(size) // 4)
        draw.ellipse(
            (margin, margin, size[0] - margin - 1, size[1] - margin - 1),
            fill=(30, 120, 220, 255),
            outline=(20, 20, 20, 255),
            width=max(1, min(size) // 64),
        )
    image.save(path)
    return path


class ReviewFixture(unittest.TestCase):
    def prepare_app_icon(self, run_dir: Path) -> None:
        prepare_run(
            PrepareOptions(
                bundle=load_bundle("app-icons"),
                entity_id="solar",
                display_name="Solar",
                description="A test app icon.",
                entity_notes="a simple geometric sun mark",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
            )
        )

    def prepare_sticker(self, run_dir: Path) -> None:
        prepare_run(
            PrepareOptions(
                bundle=load_bundle("slack-stickers"),
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="A one-sticker test pack.",
                entity_notes="developer workflow sticker",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=[VariantSpec(id="ship", purpose="cargo ship sailing")],
            )
        )

    def prepare_stickers(self, run_dir: Path, count: int) -> None:
        variants = [
            VariantSpec(id="ship", purpose="cargo ship sailing"),
            VariantSpec(id="debug", purpose="magnifying glass on bug"),
        ][:count]
        prepare_run(
            PrepareOptions(
                bundle=load_bundle("slack-stickers"),
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="A sticker test pack.",
                entity_notes="developer workflow sticker",
                style_notes="",
                references=[],
                output_dir=run_dir,
                chroma_key="auto",
                force=True,
                variants=variants,
            )
        )

    @staticmethod
    def record(run_dir: Path, job_id: str, source: Path) -> None:
        record_result(
            run_dir,
            job_id,
            source,
            allow_synthetic_test_source=True,
        )

    @staticmethod
    def mark_complete(run_dir: Path, job_id: str) -> None:
        manifest = load_manifest(run_dir)
        job = manifest.job(job_id)
        job.status = "complete"
        job.source = "synthetic-test"
        job.recorded_at = "2026-07-24T00:00:00+00:00"
        job.review_status = "pending"
        manifest.save(run_dir)


class ReviewValidationTests(ReviewFixture):
    def test_valid_high_resolution_sticker_source_passes_proportional_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_sticker(run_dir)
            self.record(run_dir, "ship", _write_source(root / "ship.png", (1024, 1024)))

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["bundle"], "slack-stickers")
            item = result["jobs"][0]
            self.assertEqual(item["job_id"], "ship")
            self.assertEqual(item["expected"]["logical_size"], {"width": 128, "height": 128})
            self.assertEqual(item["source"]["width"], 1024)
            self.assertEqual(item["source"]["height"], 1024)
            self.assertEqual(item["geometry"]["scale_x"], 8.0)
            self.assertEqual(item["geometry"]["scale_y"], 8.0)
            self.assertEqual(item["errors"], [])

    def test_valid_app_icon_passes_exact_square_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "icon.png", (1024, 1024)))

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertTrue(result["ok"], msg=result)
            item = result["jobs"][0]
            self.assertEqual(item["expected"]["logical_size"], {"width": 1024, "height": 1024})
            self.assertEqual(item["source"]["format"], "PNG")
            self.assertIsNotNone(item["alpha_bbox"])
            self.assertGreater(item["alpha_bbox"][0], 100)
            self.assertGreater(item["alpha_bbox"][1], 100)
            self.assertLess(item["alpha_bbox"][2], 930)
            self.assertLess(item["alpha_bbox"][3], 930)

    def test_wrong_strip_aspect_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_sticker(run_dir)
            self.record(run_dir, "ship", _write_source(root / "wide.png", (1024, 512)))

            result = review_outputs(load_bundle("slack-stickers"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("aspect ratio", result["jobs"][0]["errors"][0])

    def test_proportional_but_undersized_strip_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_sticker(run_dir)
            self.record(run_dir, "ship", _write_source(root / "tiny.png", (64, 64)))

            result = review_outputs(load_bundle("slack-stickers"), run_dir)

            self.assertFalse(result["ok"])
            self.assertTrue(
                any(
                    "smaller than logical strip size" in error
                    for error in result["jobs"][0]["errors"]
                ),
                msg=result["jobs"][0]["errors"],
            )

    def test_unsupported_actual_image_format_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare_app_icon(run_dir)
            decoded = run_dir / "decoded" / "icon.png"
            Image.new("RGB", (1024, 1024), (200, 80, 20)).save(decoded, format="JPEG")
            self.mark_complete(run_dir, "icon")

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("unsupported decoded output format: JPEG", result["jobs"][0]["errors"])

    def test_zero_completed_visual_jobs_fails_with_top_level_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare_app_icon(run_dir)

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertEqual(result["jobs"][0]["job_id"], "icon")
            self.assertEqual(result["jobs"][0]["status"], "skipped")
            self.assertIn("not recorded yet", result["jobs"][0]["warnings"][0])
            self.assertTrue(
                any("no completed visual outputs" in error for error in result["errors"]),
                msg=result["errors"],
            )
            self.assertTrue((run_dir / "qa" / "review-sheet.png").is_file())
            self.assertTrue((run_dir / "qa" / "review.json").is_file())

    def test_gate_only_review_skips_future_jobs_until_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_stickers(run_dir, 2)
            self.record(run_dir, "ship", _write_source(root / "ship.png", (1024, 1024)))

            result = review_outputs(load_bundle("slack-stickers"), run_dir)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual([job["job_id"] for job in result["jobs"]], ["ship", "debug"])
            self.assertEqual(result["jobs"][0]["status"], "validated")
            self.assertEqual(result["jobs"][0]["errors"], [])
            self.assertEqual(result["jobs"][1]["status"], "skipped")
            self.assertIn("not recorded yet", result["jobs"][1]["warnings"][0])
            self.assertEqual(result["jobs"][1]["errors"], [])

            approve_results(run_dir, job_ids=["ship"], note="Gate approved.")
            self.record(run_dir, "debug", _write_source(root / "debug.png", (1024, 1024)))
            refreshed = review_outputs(load_bundle("slack-stickers"), run_dir, force=True)

            self.assertTrue(refreshed["ok"], msg=refreshed)
            self.assertEqual([job["status"] for job in refreshed["jobs"]], ["validated", "validated"])

    def test_complete_job_missing_output_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare_app_icon(run_dir)
            self.mark_complete(run_dir, "icon")

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("missing decoded output", result["jobs"][0]["errors"])

    def test_corrupt_file_is_reported_without_blocking_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare_app_icon(run_dir)
            decoded = run_dir / "decoded" / "icon.png"
            decoded.write_bytes(b"not an image")
            self.mark_complete(run_dir, "icon")

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"], [])
            self.assertIn("cannot open decoded output", result["jobs"][0]["errors"][0])
            self.assertTrue((run_dir / "qa" / "review-sheet.png").is_file())

    def test_empty_visually_blank_source_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(
                run_dir,
                "icon",
                _write_source(root / "blank.png", (1024, 1024), transparent=True),
            )

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("no visible alpha content", result["jobs"][0]["errors"])

    def test_opaque_pure_chroma_source_is_visually_blank_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_sticker(run_dir)
            self.record(
                run_dir,
                "ship",
                _write_chroma_source(root / "chroma.png", (1024, 1024), foreground=False),
            )

            result = review_outputs(load_bundle("slack-stickers"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("no visible alpha content", result["jobs"][0]["errors"])
            self.assertEqual(result["jobs"][0]["source"]["mode"], "RGBA")

    def test_opaque_chroma_source_with_foreground_passes_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_sticker(run_dir)
            self.record(
                run_dir,
                "ship",
                _write_chroma_source(root / "foreground.png", (1024, 1024), foreground=True),
            )

            result = review_outputs(load_bundle("slack-stickers"), run_dir)

            self.assertTrue(result["ok"], msg=result)
            bbox = result["jobs"][0]["alpha_bbox"]
            self.assertIsNotNone(bbox)
            self.assertGreater(bbox[0], 0)
            self.assertGreater(bbox[1], 0)
            with Image.open(run_dir / "qa" / "review-sheet.png") as sheet:
                self.assertEqual(sheet.getpixel((170, 122))[:3], (238, 238, 238))

    def test_review_sheet_is_deterministic_with_checker_previews_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "icon.png", (1024, 1024)))

            first = review_outputs(load_bundle("app-icons"), run_dir)
            first_bytes = (run_dir / "qa" / "review-sheet.png").read_bytes()
            second = review_outputs(load_bundle("app-icons"), run_dir, force=True)
            second_bytes = (run_dir / "qa" / "review-sheet.png").read_bytes()

            self.assertEqual(hashlib.sha256(first_bytes).hexdigest(), hashlib.sha256(second_bytes).hexdigest())
            self.assertEqual(first["sheet"]["layout"], second["sheet"]["layout"])
            self.assertEqual(first["jobs"][0]["review_status"], "pending")
            with Image.open(run_dir / "qa" / "review-sheet.png") as sheet:
                self.assertEqual(sheet.getpixel((170, 122))[:3], (238, 238, 238))
                self.assertEqual(sheet.getpixel((420, 122))[:3], (68, 68, 68))
                self.assertNotEqual(sheet.getpixel((36, 36))[:3], (255, 255, 255))

    def test_existing_artifact_overwrite_guard_and_force_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "icon.png", (1024, 1024)))

            review_outputs(load_bundle("app-icons"), run_dir)
            with self.assertRaises(FileExistsError):
                review_outputs(load_bundle("app-icons"), run_dir)

            result = review_outputs(load_bundle("app-icons"), run_dir, force=True)
            self.assertTrue(result["ok"], msg=result)

    def test_base_jobs_are_included_without_atlas_state_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "icon.png", (1024, 1024)))
            manifest = load_manifest(run_dir)
            manifest.jobs.insert(
                0,
                Job(
                    id="base",
                    kind="base",
                    status="complete",
                    prompt_file="prompts/base.md",
                    input_images=[],
                    output_path="decoded/base.png",
                    source="synthetic-test",
                    recorded_at="2026-07-24T00:00:00+00:00",
                    review_status="pending",
                ),
            )
            manifest.save(run_dir)
            _write_source(run_dir / "decoded" / "base.png", (1024, 1024))

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual([job["job_id"] for job in result["jobs"]], ["icon", "base"])
            self.assertEqual(result["jobs"][1]["expected"]["logical_size"], {"width": 1024, "height": 1024})

    def test_valid_decoded_manifest_path_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "icon.png", (1024, 1024)))

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["jobs"][0]["output_path"], "decoded/icon.png")

    def test_absolute_manifest_output_path_is_rejected_without_opening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            outside = _write_source(root / "outside.png", (1024, 1024))
            manifest = load_manifest(run_dir)
            job = manifest.job("icon")
            job.status = "complete"
            job.review_status = "pending"
            job.output_path = str(outside)
            manifest.save(run_dir)

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("unsafe decoded output path", result["jobs"][0]["errors"][0])

    def test_traversal_manifest_output_path_is_rejected_without_opening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            _write_source(root / "outside.png", (1024, 1024))
            manifest = load_manifest(run_dir)
            job = manifest.job("icon")
            job.status = "complete"
            job.review_status = "pending"
            job.output_path = "../outside.png"
            manifest.save(run_dir)

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("unsafe decoded output path", result["jobs"][0]["errors"][0])

    def test_parent_component_is_rejected_even_when_it_resolves_under_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            _write_source(run_dir / "decoded" / "icon.png", (1024, 1024))
            manifest = load_manifest(run_dir)
            job = manifest.job("icon")
            job.status = "complete"
            job.review_status = "pending"
            job.output_path = "decoded/../decoded/icon.png"
            manifest.save(run_dir)

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("unsafe decoded output path", result["jobs"][0]["errors"][0])

    def test_missing_decoded_directory_does_not_block_review_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare_app_icon(run_dir)
            shutil.rmtree(run_dir / "decoded")

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("no completed visual outputs", result["errors"][0])
            self.assertTrue((run_dir / "qa" / "review-sheet.png").is_file())

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks not supported")
    def test_symlink_escape_manifest_output_path_is_rejected_without_opening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            outside = _write_source(root / "outside.png", (1024, 1024))
            link = run_dir / "decoded" / "escape.png"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation unsupported: {exc}")
            manifest = load_manifest(run_dir)
            job = manifest.job("icon")
            job.status = "complete"
            job.review_status = "pending"
            job.output_path = "decoded/escape.png"
            manifest.save(run_dir)

            result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("unsafe decoded output path", result["jobs"][0]["errors"][0])

    def test_oversized_decoded_image_is_rejected_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "large.png", (32, 32)))

            with mock.patch("engine.review.MAX_DECODED_PIXELS", 100), mock.patch(
                "engine.review.remove_chroma_background"
            ) as cleanup:
                result = review_outputs(load_bundle("app-icons"), run_dir)

            self.assertFalse(result["ok"])
            self.assertIn("exceeds max decoded pixel budget", result["jobs"][0]["errors"][0])
            self.assertEqual(result["jobs"][0]["source"]["pixels"], 1024)
            self.assertEqual(result["jobs"][0]["source"]["max_pixels"], 100)
            cleanup.assert_not_called()


class ReviewCliTests(ReviewFixture):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "icon_forge.py"), *args],
            cwd=SKILL_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_review_exit_zero_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            self.prepare_app_icon(run_dir)
            self.record(run_dir, "icon", _write_source(root / "icon.png", (1024, 1024)))

            proc = self.run_cli("review", "--run-dir", str(run_dir))

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"], msg=payload)
            self.assertTrue(Path(payload["sheet_path"]).is_file())
            self.assertTrue(Path(payload["json_path"]).is_file())

    def test_cli_review_exit_one_and_json_output_on_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.prepare_app_icon(run_dir)

            proc = self.run_cli("review", "--run-dir", str(run_dir))

            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ok"])
            self.assertTrue(
                any("no completed visual outputs" in error for error in payload["errors"]),
                msg=payload["errors"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
