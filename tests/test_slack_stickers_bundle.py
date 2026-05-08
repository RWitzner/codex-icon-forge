"""End-to-end test for the slack-stickers bundle.

Proves the abstraction: a dynamic sticker product (1-12 user-defined
single-frame stickers in flat-vector style, chroma-key-slots extractor,
atlas-extract-folder packager) runs through ``finalize_run`` end-to-end
with no new pipeline code. The atlas template ships dynamic; tests
materialise it with the canonical dev-pack variants documented in
README.md so the pre-existing examples/dev-pack/ output is reproducible.

Run from the skill root:
    python -m unittest tests.test_slack_stickers_bundle -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import VariantSpec, load_bundle  # noqa: E402
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402
from engine.packager import registered as registered_packagers  # noqa: E402
from engine.profiles import Bundle, materialize_dynamic_atlas  # noqa: E402


# Canonical dev-pack preset. Mirrors README.md so the examples/dev-pack/
# output stays reproducible after slack-stickers became dynamic.
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


def _materialise_dev_pack(template) -> Bundle:
    """Apply the dev-pack variants to a freshly loaded slack-stickers bundle."""
    atlas = materialize_dynamic_atlas(template.atlas, _DEV_PACK_VARIANTS)
    return Bundle(
        id=template.id,
        description=template.description,
        atlas=atlas,
        style=template.style,
        extractor=template.extractor,
        packager=template.packager,
    )


def _seed_sticker_frames(frames_root: Path, bundle) -> None:
    palette = [
        (220, 60, 60),
        (60, 200, 110),
        (240, 180, 50),
        (90, 160, 240),
        (200, 90, 220),
        (40, 180, 200),
        (240, 120, 60),
        (140, 220, 60),
        (110, 80, 220),
        (220, 150, 110),
        (80, 220, 180),
        (220, 80, 150),
    ]
    for state in bundle.atlas.states:
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True)
        sticker = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sticker)
        color = palette[state.row % len(palette)] + (255,)
        draw.rounded_rectangle(
            (8, 8, 91, 91),
            radius=18,
            fill=color,
            outline=(0, 0, 0, 255),
            width=3,
        )
        sticker.save(state_dir / "00.png")



_TEST_CHROMA_RGB = (255, 0, 255)


def _seed_request(run_dir: Path, *, chroma_rgb: tuple[int, int, int] = _TEST_CHROMA_RGB) -> None:
    """Persist a minimal request.json so the multi-size packager can resolve chroma_key.

    The multi-size sticker_folder branch reads ``chroma_key.rgb`` from
    request.json to redo chroma cleanup on each decoded source. In production
    this file is written by ``run_setup`` at prepare time; tests have to seed
    it manually.
    """

    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "chroma_key": {
            "hex": "#{:02x}{:02x}{:02x}".format(*chroma_rgb),
            "rgb": list(chroma_rgb),
            "name": "test-fixture",
            "selection": "manual",
        }
    }
    (run_dir / "request.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _seed_decoded_sources(run_dir: Path, bundle) -> None:
    decoded_dir = run_dir / "decoded"
    decoded_dir.mkdir(parents=True)
    _seed_request(run_dir)
    for state in bundle.atlas.states:
        source = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        color = (40 + state.row * 11, 90 + state.row * 7, 180 - state.row * 5, 255)
        draw.rounded_rectangle(
            (96, 96, 927, 927),
            radius=180,
            fill=color,
            outline=(0, 0, 0, 255),
            width=24,
        )
        source.save(decoded_dir / f"{state.id}.png")


def _seed_decoded_with_chroma_background(
    run_dir: Path, bundle, *, chroma_rgb: tuple[int, int, int] = _TEST_CHROMA_RGB
) -> None:
    """Seed decoded/ with stickers painted on opaque chroma background.

    Mirrors what $imagegen actually returns in production: a flat magenta
    canvas with the silhouette painted on top. Used to verify that the
    multi-size packager's chroma cleanup removes the background instead of
    shipping it through unchanged.
    """

    decoded_dir = run_dir / "decoded"
    decoded_dir.mkdir(parents=True)
    _seed_request(run_dir, chroma_rgb=chroma_rgb)
    chroma_rgba = chroma_rgb + (255,)
    for state in bundle.atlas.states:
        source = Image.new("RGBA", (1024, 1024), chroma_rgba)
        draw = ImageDraw.Draw(source)
        color = (40 + state.row * 11, 90 + state.row * 7, 180 - state.row * 5, 255)
        draw.rounded_rectangle(
            (200, 200, 823, 823),
            radius=180,
            fill=color,
            outline=(0, 0, 0, 255),
            width=24,
        )
        source.save(decoded_dir / f"{state.id}.png")

class SlackStickersBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = load_bundle("slack-stickers")
        cls.bundle = _materialise_dev_pack(cls.template)

    def test_atlas_extract_folder_strategy_registered(self) -> None:
        self.assertIn("atlas-extract-folder", registered_packagers())

    def test_template_atlas_is_dynamic(self) -> None:
        # The shipped atlas template is dynamic; states are supplied at
        # prepare time (or in tests, via materialize_dynamic_atlas).
        self.assertEqual(self.template.atlas.id, "slack-stickers")
        self.assertTrue(self.template.atlas.is_dynamic)
        self.assertEqual(self.template.atlas.dynamic_states.max_states, 12)
        self.assertEqual(self.template.atlas.states, ())

    def test_bundle_shape(self) -> None:
        # After materialising with the dev-pack preset, the bundle matches
        # the previous fixed shape: 12 single-frame stickers in 1xN cells.
        self.assertEqual(self.bundle.id, "slack-stickers")
        self.assertEqual(self.bundle.atlas.id, "slack-stickers")
        self.assertEqual(self.bundle.atlas.geometry.columns, 1)
        self.assertEqual(self.bundle.atlas.geometry.rows, 12)
        self.assertEqual(len(self.bundle.atlas.states), 12)
        for state in self.bundle.atlas.states:
            self.assertEqual(state.frames, 1)
        self.assertEqual(
            [state.id for state in self.bundle.atlas.states],
            [variant.id for variant in _DEV_PACK_VARIANTS],
        )
        self.assertEqual(self.bundle.style.id, "flat-vector")
        self.assertEqual(self.bundle.extractor.strategy, "chroma-key-slots")
        self.assertEqual(self.bundle.packager.strategy, "atlas-extract-folder")

    def test_finalize_produces_multisize_pngs_and_readme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            sprite_home = tmp_root / "icon-forge"
            _seed_sticker_frames(frames_root, self.bundle)
            _seed_decoded_sources(run_dir, self.bundle)

            options = FinalizeOptions(
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="Dev-themed Slack stickers PoC.",
                frames_root=frames_root,
                output_run_dir=run_dir,
                package_overrides={"ICON_FORGE_HOME": str(sprite_home)},
            )
            result = finalize_run(self.bundle, options)

            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["stage"], "package")
            self.assertEqual(result["package"]["sticker_count"], 12)
            self.assertEqual(result["package"]["sizes"], [128, 256, 512, 1024])

            sticker_dir = sprite_home / "stickers" / "dev-pack"
            self.assertTrue(sticker_dir.is_dir())
            for state in self.bundle.atlas.states:
                for size in (128, 256, 512, 1024):
                    target = sticker_dir / state.id / f"{state.id}-{size}.png"
                    self.assertTrue(target.is_file(), msg=f"missing {target}")
                    with Image.open(target) as image:
                        self.assertEqual(image.size, (size, size))
                        self.assertEqual(image.format, "PNG")

            readme_path = sticker_dir / "README.md"
            self.assertTrue(readme_path.is_file())
            readme = readme_path.read_text(encoding="utf-8")
            self.assertIn("Dev Pack sticker pack", readme)
            self.assertIn("Dev-themed Slack stickers PoC.", readme)
            self.assertIn(":shipping-it:", readme)
            self.assertIn(":merge-conflict:", readme)
            self.assertIn("`shipping-it/shipping-it-1024.png`", readme)

    def test_finalize_refuses_to_overwrite_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir_a = tmp_root / "run-a"
            run_dir_b = tmp_root / "run-b"
            sprite_home = tmp_root / "icon-forge"
            _seed_sticker_frames(frames_root, self.bundle)
            _seed_decoded_sources(run_dir_a, self.bundle)
            _seed_decoded_sources(run_dir_b, self.bundle)

            base_kwargs = dict(
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="Dev-themed Slack stickers PoC.",
                frames_root=frames_root,
                package_overrides={"ICON_FORGE_HOME": str(sprite_home)},
            )
            finalize_run(
                self.bundle,
                FinalizeOptions(output_run_dir=run_dir_a, **base_kwargs),
            )

            with self.assertRaises(FileExistsError):
                finalize_run(
                    self.bundle,
                    FinalizeOptions(output_run_dir=run_dir_b, **base_kwargs),
                )

            result = finalize_run(
                self.bundle,
                FinalizeOptions(output_run_dir=run_dir_b, force=True, **base_kwargs),
            )
            self.assertTrue(result["ok"])

    def test_multisize_output_strips_chroma_background(self) -> None:
        """Multi-size sticker outputs must not ship the chroma key through.

        Regression: an earlier version of the multi-size branch read decoded/
        and resized directly, skipping the chroma cleanup that the extract
        step applies to the spritesheet path. Production stickers shipped
        with a magenta background still attached. This test seeds decoded/
        with opaque magenta canvases and asserts the packaged output is
        transparent at the chroma-only corners.
        """

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            sprite_home = tmp_root / "icon-forge"
            _seed_sticker_frames(frames_root, self.bundle)
            _seed_decoded_with_chroma_background(run_dir, self.bundle)

            options = FinalizeOptions(
                entity_id="dev-pack",
                display_name="Dev Pack",
                description="Dev-themed Slack stickers PoC.",
                frames_root=frames_root,
                output_run_dir=run_dir,
                package_overrides={"ICON_FORGE_HOME": str(sprite_home)},
            )
            result = finalize_run(self.bundle, options)
            self.assertTrue(result["ok"], msg=result)

            sticker_dir = sprite_home / "stickers" / "dev-pack"
            sample_state = self.bundle.atlas.states[0]
            for size in (128, 1024):
                target = sticker_dir / sample_state.id / f"{sample_state.id}-{size}.png"
                with Image.open(target) as image:
                    image = image.convert("RGBA")
                    self.assertEqual(image.size, (size, size))
                    corner_pixels = [
                        image.getpixel((0, 0)),
                        image.getpixel((size - 1, 0)),
                        image.getpixel((0, size - 1)),
                        image.getpixel((size - 1, size - 1)),
                    ]
                    for pixel in corner_pixels:
                        self.assertEqual(
                            pixel[3],
                            0,
                            msg=(
                                f"corner pixel {pixel} at size={size} is not transparent — "
                                "chroma cleanup did not run on the multi-size source"
                            ),
                        )
                    centre = image.getpixel((size // 2, size // 2))
                    self.assertGreater(
                        centre[3],
                        0,
                        msg=f"centre pixel {centre} at size={size} is transparent — silhouette lost",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
