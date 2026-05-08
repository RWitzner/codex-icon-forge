"""End-to-end test for the app-icons bundle.

Drives prepare -> synthetic imagegen -> record -> extract -> finalize and
verifies the multi-size-folder packager writes 8 sized PNGs plus a README
to the configured output root.

Run from the skill root:
    python -m unittest tests.test_app_icons_bundle -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import load_bundle  # noqa: E402
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402
from engine.packager import registered as registered_packagers  # noqa: E402


def _seed_icon_frame(frames_root: Path, bundle) -> None:
    geo = bundle.atlas.geometry
    state = bundle.atlas.states[0]
    state_dir = frames_root / state.id
    state_dir.mkdir(parents=True)
    icon = Image.new("RGBA", (geo.cell_width - 80, geo.cell_height - 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.ellipse((10, 10, icon.width - 10, icon.height - 10), fill=(220, 90, 30, 255), outline=(0, 0, 0, 255), width=12)
    cx = icon.width // 2
    cy = icon.height // 2
    draw.rectangle((cx - 80, cy - 80, cx + 80, cy + 80), fill=(255, 220, 130, 255), outline=(0, 0, 0, 255), width=10)
    icon.save(state_dir / "00.png")


class AppIconsBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("app-icons")

    def test_multi_size_folder_strategy_registered(self) -> None:
        self.assertIn("multi-size-folder", registered_packagers())

    def test_bundle_shape(self) -> None:
        self.assertEqual(self.bundle.id, "app-icons")
        self.assertEqual(self.bundle.atlas.id, "app-icons")
        self.assertEqual(self.bundle.atlas.geometry.cell_width, 1024)
        self.assertEqual(self.bundle.atlas.geometry.cell_height, 1024)
        self.assertEqual(len(self.bundle.atlas.states), 1)
        self.assertEqual(self.bundle.atlas.states[0].id, "icon")
        self.assertFalse(self.bundle.atlas.requires_base)
        self.assertEqual(self.bundle.style.id, "launcher-tile")
        self.assertEqual(self.bundle.extractor.strategy, "chroma-key-slots")
        self.assertEqual(self.bundle.packager.strategy, "multi-size-folder")
        self.assertEqual(
            self.bundle.packager.params["sizes"],
            [16, 32, 64, 128, 180, 256, 512, 1024],
        )

    def test_row_prompt_contains_launcher_tile_wrapper(self) -> None:
        """app-icons must produce launcher-tile-wrapped prompts after the
        Task 7-8 bundle switch. Without this assertion, only the style.id
        wiring is verified end-to-end — but a future regression in
        compose_row_prompt or _resolve_purpose for single-state atlases
        would slip through."""
        from engine.prompts import compose_row_prompt
        state = self.bundle.atlas.states[0]
        prompt = compose_row_prompt(
            self.bundle.style,
            self.bundle.atlas,
            state,
            entity_id="testapp",
            entity_notes="modern minimalist",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        )
        self.assertIn("expressing the visual theme of", prompt.lower())
        self.assertIn(f'"{state.purpose}"', prompt)

    def test_finalize_writes_eight_sized_pngs_and_readme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_home = tmp_root / "icon-forge"
            _seed_icon_frame(frames_root, self.bundle)

            options = FinalizeOptions(
                entity_id="solar",
                display_name="Solar",
                description="A sunlit minimalist icon.",
                frames_root=frames_root,
                output_run_dir=run_dir,
                package_overrides={"ICON_FORGE_HOME": str(icon_home)},
            )
            result = finalize_run(self.bundle, options)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["stage"], "package")
            self.assertEqual(result["package"]["sizes"], [16, 32, 64, 128, 180, 256, 512, 1024])

            icon_dir = icon_home / "app-icons" / "solar"
            self.assertTrue(icon_dir.is_dir())
            for size in (16, 32, 64, 128, 180, 256, 512, 1024):
                target = icon_dir / f"solar-{size}.png"
                self.assertTrue(target.is_file(), msg=f"missing {target}")
                with Image.open(target) as image:
                    self.assertEqual(image.size, (size, size))
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.mode, "RGBA")

            readme_path = icon_dir / "README.md"
            self.assertTrue(readme_path.is_file())
            readme = readme_path.read_text(encoding="utf-8")
            self.assertIn("Solar app icon pack", readme)
            self.assertIn("16x16", readme)
            self.assertIn("1024x1024", readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
