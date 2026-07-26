"""End-to-end test for the app-icon-set bundle.

Proves dynamic-states materialisation: the user supplies N variants at
prepare time, each variant becomes one independent ``$imagegen`` job, and
the multi-size packager fans every variant out to the standard 8 platform
sizes in its own subfolder.

Run from the skill root:
    python -m unittest tests.test_app_icon_set_bundle -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import (  # noqa: E402
    Bundle,
    VariantSpec,
    load_bundle,
    materialize_dynamic_atlas,
)
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402

EXPECTED_SIZES = [16, 32, 64, 128, 180, 256, 512, 1024]


def _materialise(bundle, variants: list[VariantSpec]) -> Bundle:
    atlas = materialize_dynamic_atlas(bundle.atlas, variants)
    return Bundle(
        id=bundle.id,
        description=bundle.description,
        atlas=atlas,
        style=bundle.style,
        extractor=bundle.extractor,
        packager=bundle.packager,
    )


def _seed_icon_frames(frames_root: Path, bundle: Bundle) -> None:
    palette = [
        (220, 60, 60),
        (60, 200, 110),
        (240, 180, 50),
        (90, 160, 240),
    ]
    for state in bundle.atlas.states:
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True)
        icon = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        draw = ImageDraw.Draw(icon)
        color = palette[state.row % len(palette)] + (255,)
        draw.rounded_rectangle(
            (96, 96, 928, 928),
            radius=160,
            fill=color,
            outline=(0, 0, 0, 255),
            width=12,
        )
        icon.save(state_dir / "00.png")


class AppIconSetBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template_bundle = load_bundle("app-icon-set")

    def test_template_atlas_is_dynamic(self) -> None:
        atlas = self.template_bundle.atlas
        self.assertTrue(atlas.is_dynamic)
        self.assertIsNotNone(atlas.dynamic_states)
        self.assertEqual(atlas.dynamic_states.max_states, 12)
        self.assertEqual(len(atlas.states), 0)

    def test_finalize_three_variants_writes_subfolders_at_eight_sizes(self) -> None:
        variants = [
            VariantSpec(id="main", purpose="primary app icon"),
            VariantSpec(id="share-ext", purpose="share extension, simpler"),
            VariantSpec(id="watch", purpose="1-bit silhouette for watchOS"),
        ]
        bundle = _materialise(self.template_bundle, variants)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            sprite_home = tmp_root / "icon-forge"
            _seed_icon_frames(frames_root, bundle)

            options = FinalizeOptions(
                entity_id="myapp",
                display_name="MyApp",
                description="App icon family E2E test.",
                frames_root=frames_root,
                output_run_dir=run_dir,
                package_overrides={"ICON_FORGE_HOME": str(sprite_home)},
            )
            result = finalize_run(bundle, options)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["stage"], "package")
            pkg = result["package"]
            self.assertEqual(pkg["sizes"], EXPECTED_SIZES)
            # transparent files at every size, plus one flattened 1024 per
            # variant for stores that reject an alpha channel, plus the README.
            expected_files = (
                len(variants) * len(EXPECTED_SIZES) + len(variants) + 1
            )
            self.assertEqual(pkg["file_count"], expected_files)

            family_root = sprite_home / "app-icon-sets" / "myapp"
            self.assertTrue(family_root.is_dir())
            for variant in variants:
                variant_dir = family_root / variant.id
                self.assertTrue(variant_dir.is_dir(), msg=f"missing {variant_dir}")
                for size in EXPECTED_SIZES:
                    target = variant_dir / f"{variant.id}-{size}.png"
                    self.assertTrue(target.is_file(), msg=f"missing {target}")
                    with Image.open(target) as image:
                        self.assertEqual(image.size, (size, size))
                        self.assertEqual(image.format, "PNG")

                # App Store Connect rejects icons carrying an alpha channel
                # (ITMS-90717), so the flattened copy must have none at all.
                opaque = variant_dir / f"{variant.id}-1024-opaque.png"
                self.assertTrue(opaque.is_file(), msg=f"missing {opaque}")
                with Image.open(opaque) as image:
                    self.assertEqual(image.size, (1024, 1024))
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertNotIn("A", image.getbands())

            readme_path = family_root / "README.md"
            self.assertTrue(readme_path.is_file())
            readme = readme_path.read_text(encoding="utf-8")
            self.assertIn("MyApp app icon family", readme)
            self.assertIn("3", readme)  # variant count
            for variant in variants:
                self.assertIn(f"`{variant.id}`", readme)
            # The README must not send users to the transparent 1024 file.
            self.assertIn("-1024-opaque.png", readme)
            self.assertIn(f"{expected_files} files total", readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
