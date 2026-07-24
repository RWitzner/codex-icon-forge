"""Tests for the web-brand-kit bundle and packager."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import load_bundle  # noqa: E402
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402
from engine.packager import PackageContext, package, registered, resolve_output_root  # noqa: E402
from engine.profiles import ProfileError  # noqa: E402
from engine.prompts import compose_row_prompt  # noqa: E402


EXPECTED_FILES = [
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon-48x48.png",
    "favicon.ico",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "site.webmanifest",
    "README.md",
]


def _seed_brand_frame(frames_root: Path, bundle) -> None:
    geo = bundle.atlas.geometry
    state = bundle.atlas.states[0]
    state_dir = frames_root / state.id
    state_dir.mkdir(parents=True)
    icon = Image.new("RGBA", (geo.cell_width - 160, geo.cell_height - 160), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle(
        (40, 40, icon.width - 40, icon.height - 40),
        radius=170,
        fill=(30, 118, 210, 255),
    )
    draw.ellipse(
        (250, 220, icon.width - 250, icon.height - 220),
        fill=(255, 214, 102, 255),
    )
    icon.save(state_dir / "00.png")


class WebBrandKitProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("web-brand-kit")

    def test_strategy_is_registered(self) -> None:
        self.assertIn("web-brand-kit", registered())

    def test_bundle_shape_and_prompt_role(self) -> None:
        self.assertEqual(self.bundle.id, "web-brand-kit")
        self.assertEqual(self.bundle.atlas.id, "web-brand-kit")
        self.assertEqual(self.bundle.atlas.geometry.cell_width, 1024)
        self.assertEqual(self.bundle.atlas.geometry.cell_height, 1024)
        self.assertEqual(self.bundle.atlas.geometry.columns, 1)
        self.assertEqual(self.bundle.atlas.geometry.rows, 1)
        self.assertEqual(len(self.bundle.atlas.states), 1)
        self.assertEqual(self.bundle.atlas.states[0].id, "brand")
        self.assertEqual(self.bundle.atlas.states[0].role, "web-brand")
        self.assertFalse(self.bundle.atlas.requires_base)
        self.assertFalse(self.bundle.atlas.is_dynamic)
        self.assertEqual(self.bundle.style.id, "launcher-tile")
        self.assertIn("web-brand", self.bundle.style.roles)
        self.assertEqual(self.bundle.extractor.strategy, "chroma-key-slots")
        self.assertEqual(self.bundle.packager.strategy, "web-brand-kit")

    def test_web_brand_prompt_emphasizes_browser_mark_contract(self) -> None:
        state = self.bundle.atlas.states[0]
        prompt = compose_row_prompt(
            self.bundle.style,
            self.bundle.atlas,
            state,
            entity_id="acme",
            entity_notes="calm modern product",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        ).lower()
        self.assertIn("web brand mark", prompt)
        self.assertIn("browser", prompt)
        self.assertIn("pwa", prompt)
        self.assertIn("16px", prompt)
        self.assertIn("edge breathing room", prompt)
        self.assertIn("ui glyph", prompt)

    def test_output_root_uses_web_brand_kits_path(self) -> None:
        context = PackageContext(
            entity_id="acme",
            display_name="Acme",
            description="A brand kit.",
            run_dir=Path("."),
            overrides={"ICON_FORGE_HOME": "/tmp/icon-forge-test"},
        )
        self.assertEqual(
            resolve_output_root(self.bundle.packager, context),
            Path("/tmp/icon-forge-test/web-brand-kits/acme").resolve(),
        )


class WebBrandKitFinalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("web-brand-kit")

    def test_finalize_writes_canonical_web_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_home = tmp_root / "icon-forge"
            _seed_brand_frame(frames_root, self.bundle)

            result = finalize_run(
                self.bundle,
                FinalizeOptions(
                    entity_id="acme",
                    display_name="Acme Studio",
                    description="A crisp browser brand mark.",
                    frames_root=frames_root,
                    output_run_dir=run_dir,
                    package_overrides={"ICON_FORGE_HOME": str(icon_home)},
                ),
            )

            self.assertTrue(result["ok"], msg=result)
            package_result = result["package"]
            output_dir = icon_home / "web-brand-kits" / "acme"
            self.assertEqual(Path(package_result["output_dir"]), output_dir.resolve())
            self.assertEqual(package_result["sizes"], [16, 32, 48, 180, 192, 512])
            self.assertEqual(package_result["file_count"], len(EXPECTED_FILES))
            self.assertEqual(
                [Path(path).name for path in package_result["files"]],
                EXPECTED_FILES,
            )
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), sorted(EXPECTED_FILES))

            expected_png_sizes = {
                "favicon-16x16.png": (16, 16),
                "favicon-32x32.png": (32, 32),
                "favicon-48x48.png": (48, 48),
                "apple-touch-icon.png": (180, 180),
                "icon-192.png": (192, 192),
                "icon-512.png": (512, 512),
            }
            for filename, size in expected_png_sizes.items():
                with Image.open(output_dir / filename) as image:
                    self.assertEqual(image.size, size)
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.format, "PNG")

            with Image.open(output_dir / "favicon.ico") as ico:
                self.assertEqual(ico.ico.sizes(), {(16, 16), (32, 32), (48, 48)})

            manifest = json.loads((output_dir / "site.webmanifest").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "Acme Studio")
            self.assertEqual(manifest["short_name"], "Acme Studio")
            self.assertEqual(
                manifest["icons"],
                [
                    {
                        "src": "icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": "icon-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any",
                    },
                ],
            )
            self.assertTrue((output_dir / "site.webmanifest").read_text(encoding="utf-8").endswith("\n"))

            readme = (output_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("Acme Studio web brand kit", readme)
            self.assertIn("favicon.ico", readme)
            self.assertIn("apple-touch-icon.png", readme)
            self.assertIn("site.webmanifest", readme)
            self.assertIn("<link rel=\"manifest\" href=\"site.webmanifest\">", readme)

    def test_overwrite_guard_and_force_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_home = tmp_root / "icon-forge"
            _seed_brand_frame(frames_root, self.bundle)
            options = FinalizeOptions(
                entity_id="acme",
                display_name="Acme Studio",
                description="A crisp browser brand mark.",
                frames_root=frames_root,
                output_run_dir=run_dir,
                package_overrides={"ICON_FORGE_HOME": str(icon_home)},
            )

            first = finalize_run(self.bundle, options)
            self.assertTrue(first["ok"], msg=first)
            with self.assertRaises(FileExistsError):
                finalize_run(self.bundle, options)
            forced = finalize_run(self.bundle, FinalizeOptions(**{**options.__dict__, "force": True}))
            self.assertTrue(forced["ok"], msg=forced)

    def test_packager_rejects_missing_or_multi_state_atlas_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            out_home = Path(tmp) / "icon-forge"
            context = PackageContext(
                entity_id="acme",
                display_name="Acme",
                description="A brand kit.",
                run_dir=run_dir,
                overrides={"ICON_FORGE_HOME": str(out_home)},
            )

            with self.assertRaises(ValueError):
                package(self.bundle.packager, context, atlas=None)
            self.assertFalse((out_home / "web-brand-kits" / "acme").exists())

            import dataclasses

            second_state = dataclasses.replace(self.bundle.atlas.states[0], id="alt", row=1)
            bad_atlas = dataclasses.replace(
                self.bundle.atlas,
                geometry=dataclasses.replace(self.bundle.atlas.geometry, rows=2),
                states=(self.bundle.atlas.states[0], second_state),
            )
            with self.assertRaises(ValueError):
                package(self.bundle.packager, context, atlas=bad_atlas)
            self.assertFalse((out_home / "web-brand-kits" / "acme").exists())

    def test_packager_rejects_wrong_size_source_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            run_dir = tmp_root / "run"
            final_dir = run_dir / "final"
            final_dir.mkdir(parents=True)
            Image.new("RGBA", (512, 512), (30, 118, 210, 255)).save(
                final_dir / "spritesheet.png"
            )
            out_home = tmp_root / "icon-forge"
            context = PackageContext(
                entity_id="acme",
                display_name="Acme",
                description="A brand kit.",
                run_dir=run_dir,
                overrides={"ICON_FORGE_HOME": str(out_home)},
            )

            with self.assertRaisesRegex(ValueError, "expected composed atlas"):
                package(self.bundle.packager, context, atlas=self.bundle.atlas)
            self.assertFalse((out_home / "web-brand-kits" / "acme").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
