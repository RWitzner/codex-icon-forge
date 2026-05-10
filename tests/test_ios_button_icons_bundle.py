"""End-to-end test for the ios-button-icons bundle.

This bundle covers in-app iOS symbols: tab bar, toolbar, list-row, and button
glyphs. It is deliberately separate from launcher app icons.

Run from the skill root:
    python -m unittest tests.test_ios_button_icons_bundle -v
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
from engine.packager import PackageContext, package  # noqa: E402
from engine.prompts import compose_row_prompt  # noqa: E402

EXPECTED_SYMBOL_SIZES = [24, 25, 48, 50, 72, 75]


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


def _seed_symbol_frames(frames_root: Path, bundle: Bundle) -> None:
    for state in bundle.atlas.states:
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True)
        symbol = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        draw = ImageDraw.Draw(symbol)
        draw.ellipse((192, 192, 832, 832), outline=(0, 0, 0, 255), width=96)
        draw.line((512, 320, 512, 704), fill=(0, 0, 0, 255), width=96)
        draw.line((320, 512, 704, 512), fill=(0, 0, 0, 255), width=96)
        symbol.save(state_dir / "00.png")


class IosButtonIconsBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template_bundle = load_bundle("ios-button-icons")

    def test_template_atlas_is_dynamic_symbol_family(self) -> None:
        atlas = self.template_bundle.atlas
        self.assertEqual(self.template_bundle.id, "ios-button-icons")
        self.assertTrue(atlas.is_dynamic)
        self.assertIsNotNone(atlas.dynamic_states)
        self.assertEqual(atlas.dynamic_states.max_states, 12)
        self.assertEqual(len(atlas.states), 0)
        self.assertEqual(atlas.geometry.cell_width, 1024)
        self.assertEqual(atlas.geometry.cell_height, 1024)
        self.assertEqual(self.template_bundle.style.id, "ios-symbol")
        self.assertEqual(self.template_bundle.style.target_kind, "iOS in-app symbol")
        self.assertEqual(self.template_bundle.extractor.strategy, "chroma-key-slots")
        self.assertEqual(self.template_bundle.packager.strategy, "multi-size-folder")
        self.assertEqual(
            sorted(self.template_bundle.packager.params["sizes"]),
            EXPECTED_SYMBOL_SIZES,
        )

    def test_row_prompt_rejects_launcher_tiles(self) -> None:
        variants = [VariantSpec(id="search", purpose="magnifying glass for search tab")]
        bundle = _materialise(self.template_bundle, variants)
        state = bundle.atlas.states[0]

        prompt = compose_row_prompt(
            bundle.style,
            bundle.atlas,
            state,
            entity_id="journal",
            entity_notes="calm journaling app",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        )

        self.assertIn("monochrome", prompt.lower())
        self.assertIn("sf symbols-style", prompt.lower())
        self.assertIn("not as an app launcher", prompt.lower())
        self.assertIn("transparent ui glyph", prompt.lower())

    def test_finalize_symbols_writes_subfolders_at_ios_sizes(self) -> None:
        variants = [
            VariantSpec(id="search", purpose="magnifying glass for search tab"),
            VariantSpec(id="settings", purpose="simple gear for settings button"),
        ]
        bundle = _materialise(self.template_bundle, variants)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_home = tmp_root / "icon-forge"
            _seed_symbol_frames(frames_root, bundle)

            options = FinalizeOptions(
                entity_id="journal",
                display_name="Journal",
                description="In-app symbols for the Journal iOS app.",
                frames_root=frames_root,
                output_run_dir=run_dir,
                package_overrides={"ICON_FORGE_HOME": str(icon_home)},
            )
            result = finalize_run(bundle, options)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["stage"], "package")
            self.assertEqual(result["package"]["sizes"], EXPECTED_SYMBOL_SIZES)
            self.assertEqual(result["package"]["file_count"], len(variants) * len(EXPECTED_SYMBOL_SIZES) + 1)

            family_root = icon_home / "ios-button-icons" / "journal"
            self.assertTrue(family_root.is_dir())
            for variant in variants:
                variant_dir = family_root / variant.id
                self.assertTrue(variant_dir.is_dir(), msg=f"missing {variant_dir}")
                for size in EXPECTED_SYMBOL_SIZES:
                    target = variant_dir / f"{variant.id}-{size}.png"
                    self.assertTrue(target.is_file(), msg=f"missing {target}")
                    with Image.open(target) as image:
                        self.assertEqual(image.size, (size, size))
                        self.assertEqual(image.format, "PNG")
                        self.assertEqual(image.mode, "RGBA")

            readme = (family_root / "README.md").read_text(encoding="utf-8")
            self.assertIn("Journal iOS in-app symbol pack", readme)
            self.assertIn("tab bars, toolbars, buttons, and list rows", readme)
            for variant in variants:
                self.assertIn(f"`{variant.id}`", readme)

    def test_force_removes_stale_symbol_outputs(self) -> None:
        first_variants = [
            VariantSpec(id="search", purpose="magnifying glass for search tab"),
            VariantSpec(id="settings", purpose="simple gear for settings button"),
        ]
        second_variants = [
            VariantSpec(id="search", purpose="magnifying glass for search tab"),
        ]
        first_bundle = _materialise(self.template_bundle, first_variants)
        second_bundle = _materialise(self.template_bundle, second_variants)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            icon_home = tmp_root / "icon-forge"
            for run_name, bundle in (("run-a", first_bundle), ("run-b", second_bundle)):
                _seed_symbol_frames(tmp_root / f"frames-{run_name}", bundle)

            kwargs = dict(
                entity_id="journal",
                display_name="Journal",
                description="In-app symbols for the Journal iOS app.",
                package_overrides={"ICON_FORGE_HOME": str(icon_home)},
            )
            finalize_run(
                first_bundle,
                FinalizeOptions(
                    frames_root=tmp_root / "frames-run-a",
                    output_run_dir=tmp_root / "run-a",
                    **kwargs,
                ),
            )
            result = finalize_run(
                second_bundle,
                FinalizeOptions(
                    frames_root=tmp_root / "frames-run-b",
                    output_run_dir=tmp_root / "run-b",
                    force=True,
                    **kwargs,
                ),
            )

            self.assertTrue(result["ok"], msg=result)
            family_root = icon_home / "ios-button-icons" / "journal"
            self.assertEqual(
                sorted(path.name for path in family_root.iterdir() if path.is_dir()),
                ["search"],
            )

    def test_force_missing_atlas_does_not_delete_existing_output(self) -> None:
        bundle = _materialise(
            self.template_bundle,
            [VariantSpec(id="search", purpose="magnifying glass for search tab")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            output_root = tmp_root / "icon-forge"
            existing = output_root / "ios-button-icons" / "journal" / "search"
            existing.mkdir(parents=True)
            sentinel = existing / "search-24.png"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                package(
                    bundle.packager,
                    PackageContext(
                        entity_id="journal",
                        display_name="Journal",
                        description="Missing atlas direct package call.",
                        run_dir=tmp_root / "run-without-final",
                        overrides={"ICON_FORGE_HOME": str(output_root)},
                    ),
                    atlas=bundle.atlas,
                    force=True,
                )

            self.assertTrue(sentinel.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
