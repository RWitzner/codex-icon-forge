"""End-to-end tests for the game-tiles bundle.

Run from the skill root:
    python -m unittest tests.test_game_tiles_bundle -v
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
from engine.extractor import get as get_extractor  # noqa: E402
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402
from engine.packager import PackageContext, package  # noqa: E402
from engine.packager import registered as registered_packagers  # noqa: E402
from engine.profiles import Bundle, materialize_dynamic_atlas  # noqa: E402
from engine.tile_qa import sha256_path  # noqa: E402


_TILE_VARIANTS = [
    VariantSpec(id="grass", purpose="seamless mossy grass floor tile"),
    VariantSpec(id="stone", purpose="cracked stone floor tile"),
    VariantSpec(id="water", purpose="shallow blue water tile"),
    VariantSpec(id="sand", purpose="dry sand path tile"),
    VariantSpec(id="mud", purpose="dark muddy ground tile"),
]


def _materialise(template, variants: list[VariantSpec]) -> Bundle:
    atlas = materialize_dynamic_atlas(template.atlas, variants)
    return Bundle(
        id=template.id,
        description=template.description,
        atlas=atlas,
        style=template.style,
        extractor=template.extractor,
        packager=template.packager,
    )


def _seed_frames(frames_root: Path, bundle: Bundle) -> None:
    for state in bundle.atlas.states:
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (256, 256), _colour_for_row(state.row))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 255, 255), outline=(10, 10, 10, 255), width=4)
        image.save(state_dir / "00.png")


def _seed_decoded(run_dir: Path, bundle: Bundle) -> None:
    decoded = run_dir / "decoded"
    decoded.mkdir(parents=True, exist_ok=True)
    for state in bundle.atlas.states:
        image = Image.new("RGBA", (512, 512), _colour_for_row(state.row))
        draw = ImageDraw.Draw(image)
        draw.line(
            (0, state.row * 20 % 512, 511, state.row * 20 % 512),
            fill=(0, 0, 0, 255),
            width=16,
        )
        image.save(decoded / f"{state.id}.png")


def _seed_passing_review(run_dir: Path, bundle: Bundle) -> None:
    qa = run_dir / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    contact_sheet = qa / "contact-sheet.png"
    Image.new("RGBA", (256, 256), (24, 24, 24, 255)).save(contact_sheet)
    (qa / "review.json").write_text(
        json.dumps(
            {
                "ok": True,
                "approved": True,
                "state_ids": [state.id for state in bundle.atlas.states],
                "contact_sheet": str(contact_sheet),
                "tiles": [
                    {
                        "id": state.id,
                        "decoded_sha256": sha256_path(run_dir / "decoded" / f"{state.id}.png"),
                        "errors": [],
                        "warnings": [],
                    }
                    for state in bundle.atlas.states
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_sparse_frames(frames_root: Path, bundle: Bundle) -> None:
    for state in bundle.atlas.states:
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((96, 96, 160, 160), fill=_colour_for_row(state.row))
        image.save(state_dir / "00.png")


def _colour_for_row(row: int) -> tuple[int, int, int, int]:
    palette = [
        (60, 140, 70, 255),
        (120, 120, 130, 255),
        (60, 120, 220, 255),
        (220, 190, 120, 255),
        (90, 70, 50, 255),
        (160, 90, 60, 255),
        (80, 150, 150, 255),
        (150, 80, 150, 255),
        (190, 180, 80, 255),
        (70, 90, 130, 255),
        (120, 170, 90, 255),
        (180, 110, 90, 255),
    ]
    return palette[row % len(palette)]


class GameTilesBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = load_bundle("game-tiles")

    def test_tile_sheet_folder_strategy_registered(self) -> None:
        self.assertIn("tile-sheet-folder", registered_packagers())

    def test_template_atlas_is_dynamic(self) -> None:
        self.assertEqual(self.template.id, "game-tiles")
        self.assertEqual(self.template.atlas.id, "game-tiles")
        self.assertTrue(self.template.atlas.is_dynamic)
        self.assertEqual(self.template.atlas.geometry.columns, 1)
        self.assertEqual(self.template.atlas.geometry.rows, 1)
        self.assertEqual(self.template.atlas.geometry.cell_width, 256)
        self.assertEqual(self.template.atlas.geometry.cell_height, 256)
        self.assertEqual(self.template.atlas.dynamic_states.max_states, 12)
        self.assertEqual(self.template.atlas.states, ())
        self.assertEqual(self.template.extractor.strategy, "slot-only")
        self.assertTrue(self.template.extractor.params["allow_opaque"])
        self.assertTrue(self.template.extractor.params["allow_near_opaque_used_cells"])
        self.assertTrue(self.template.extractor.params["preserve_full_bleed"])
        self.assertEqual(self.template.packager.strategy, "tile-sheet-folder")

    def test_finalize_writes_tilesheet_tiles_manifest_and_readme(self) -> None:
        bundle = _materialise(self.template, _TILE_VARIANTS)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_forge_home = tmp_root / "icon-forge"
            _seed_frames(frames_root, bundle)
            _seed_decoded(run_dir, bundle)
            _seed_passing_review(run_dir, bundle)

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="forest-ruins",
                    display_name="Forest Ruins",
                    description="Terrain tiles for a mossy ruin map.",
                    frames_root=frames_root,
                    output_run_dir=run_dir,
                    package_overrides={"ICON_FORGE_HOME": str(icon_forge_home)},
                ),
            )

            self.assertTrue(result["ok"], msg=result)
            package = result["package"]
            self.assertEqual(package["tile_count"], 5)
            self.assertEqual(package["tile_size"], 256)
            self.assertEqual(package["columns"], 4)
            self.assertEqual(package["rows"], 2)

            output_dir = icon_forge_home / "game-tiles" / "forest-ruins"
            self.assertTrue((output_dir / "tilesheet.png").is_file())
            self.assertTrue((output_dir / "manifest.json").is_file())
            self.assertTrue((output_dir / "README.md").is_file())

            with Image.open(output_dir / "tilesheet.png") as sheet:
                sheet = sheet.convert("RGBA")
                self.assertEqual(sheet.size, (1024, 512))
                self.assertEqual(sheet.getpixel((900, 300)), (0, 0, 0, 0))

            for variant in _TILE_VARIANTS:
                with Image.open(output_dir / "tiles" / f"{variant.id}.png") as tile:
                    self.assertEqual(tile.size, (256, 256))
                    self.assertEqual(tile.convert("RGBA").getpixel((0, 0))[3], 255)

            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["bundle"], "game-tiles")
            self.assertEqual(manifest["entity_id"], "forest-ruins")
            self.assertEqual(manifest["tile_size"], 256)
            self.assertEqual(manifest["columns"], 4)
            self.assertEqual(manifest["rows"], 2)
            self.assertEqual(manifest["tilesheet"], "tilesheet.png")
            self.assertEqual(
                [tile["id"] for tile in manifest["tiles"]],
                [v.id for v in _TILE_VARIANTS],
            )
            self.assertEqual(
                [
                    (tile["row"], tile["column"], tile["x"], tile["y"])
                    for tile in manifest["tiles"]
                ],
                [
                    (0, 0, 0, 0),
                    (0, 1, 256, 0),
                    (0, 2, 512, 0),
                    (0, 3, 768, 0),
                    (1, 0, 0, 256),
                ],
            )

            readme = (output_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("Forest Ruins game tile sheet", readme)
            self.assertIn("256x256", readme)
            self.assertIn("tilesheet.png", readme)
            self.assertIn("tiles/grass.png", readme)

    def test_layout_dimensions_for_supported_counts(self) -> None:
        cases = {
            1: (256, 256, 1, 1),
            4: (1024, 256, 4, 1),
            8: (1024, 512, 4, 2),
            12: (1024, 768, 4, 3),
        }
        for count, (width, height, columns, rows) in cases.items():
            variants = [
                VariantSpec(id=f"tile-{index}", purpose=f"opaque terrain tile {index}")
                for index in range(count)
            ]
            bundle = _materialise(self.template, variants)
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                frames_root = tmp_root / "frames"
                run_dir = tmp_root / "run"
                icon_forge_home = tmp_root / "icon-forge"
                _seed_frames(frames_root, bundle)
                _seed_decoded(run_dir, bundle)
                _seed_passing_review(run_dir, bundle)
                result = finalize_run(
                    bundle,
                    FinalizeOptions(
                        entity_id=f"tiles-{count}",
                        display_name=f"Tiles {count}",
                        description="Layout test.",
                        frames_root=frames_root,
                        output_run_dir=run_dir,
                        package_overrides={"ICON_FORGE_HOME": str(icon_forge_home)},
                    ),
                )
                self.assertTrue(result["ok"], msg=result)
                self.assertEqual(result["package"]["columns"], columns)
                self.assertEqual(result["package"]["rows"], rows)
                with Image.open(
                    icon_forge_home / "game-tiles" / f"tiles-{count}" / "tilesheet.png"
                ) as sheet:
                    self.assertEqual(sheet.size, (width, height))

    def test_finalize_refuses_to_overwrite_existing_pack(self) -> None:
        bundle = _materialise(self.template, _TILE_VARIANTS[:2])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            icon_forge_home = tmp_root / "icon-forge"

            for run_name in ("run-a", "run-b"):
                _seed_frames(tmp_root / f"frames-{run_name}", bundle)
                _seed_decoded(tmp_root / run_name, bundle)
                _seed_passing_review(tmp_root / run_name, bundle)

            kwargs = dict(
                entity_id="dupe",
                display_name="Dupe",
                description="Overwrite check.",
                package_overrides={"ICON_FORGE_HOME": str(icon_forge_home)},
            )
            finalize_run(
                bundle,
                FinalizeOptions(
                    frames_root=tmp_root / "frames-run-a",
                    output_run_dir=tmp_root / "run-a",
                    **kwargs,
                ),
            )
            with self.assertRaises(FileExistsError):
                finalize_run(
                    bundle,
                    FinalizeOptions(
                        frames_root=tmp_root / "frames-run-b",
                        output_run_dir=tmp_root / "run-b",
                        **kwargs,
                    ),
                )

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    frames_root=tmp_root / "frames-run-b",
                    output_run_dir=tmp_root / "run-b",
                    force=True,
                    **kwargs,
                ),
            )
            self.assertTrue(result["ok"])

    def test_force_removes_stale_tile_outputs(self) -> None:
        first_bundle = _materialise(self.template, _TILE_VARIANTS[:2])
        second_bundle = _materialise(self.template, _TILE_VARIANTS[:1])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            icon_forge_home = tmp_root / "icon-forge"

            for run_name, bundle in (("run-a", first_bundle), ("run-b", second_bundle)):
                _seed_frames(tmp_root / f"frames-{run_name}", bundle)
                _seed_decoded(tmp_root / run_name, bundle)
                _seed_passing_review(tmp_root / run_name, bundle)

            kwargs = dict(
                entity_id="force-clean",
                display_name="Force Clean",
                description="Force cleanup check.",
                package_overrides={"ICON_FORGE_HOME": str(icon_forge_home)},
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
            tiles_dir = icon_forge_home / "game-tiles" / "force-clean" / "tiles"
            self.assertEqual(
                sorted(path.name for path in tiles_dir.glob("*.png")),
                ["grass.png"],
            )

    def test_force_missing_atlas_does_not_delete_existing_output(self) -> None:
        bundle = _materialise(self.template, _TILE_VARIANTS[:1])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            output_root = tmp_root / "icon-forge"
            existing = output_root / "game-tiles" / "missing-atlas" / "tiles"
            existing.mkdir(parents=True)
            sentinel = existing / "old.png"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                package(
                    bundle.packager,
                    PackageContext(
                        entity_id="missing-atlas",
                        display_name="Missing Atlas",
                        description="Missing atlas direct package call.",
                        run_dir=tmp_root / "run-without-final",
                        overrides={"ICON_FORGE_HOME": str(output_root)},
                    ),
                    atlas=bundle.atlas,
                    force=True,
                )

            self.assertTrue(sentinel.exists())

    def test_sparse_transparent_tiles_fail_full_bleed_validation(self) -> None:
        bundle = _materialise(self.template, _TILE_VARIANTS[:1])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_forge_home = tmp_root / "icon-forge"
            _seed_sparse_frames(frames_root, bundle)

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="sparse",
                    display_name="Sparse",
                    description="Sparse tile should fail.",
                    frames_root=frames_root,
                    output_run_dir=run_dir,
                    package_overrides={"ICON_FORGE_HOME": str(icon_forge_home)},
                ),
            )

            self.assertFalse(result["ok"], msg=result)
            self.assertEqual(result["stage"], "validate")
            self.assertTrue(
                any("full-bleed" in error for error in result["errors"]),
                result["errors"],
            )

    def test_slot_only_preserve_full_bleed_avoids_padding(self) -> None:
        bundle = _materialise(self.template, _TILE_VARIANTS[:1])
        extractor = get_extractor("slot-only")
        strip = Image.new("RGBA", (512, 512), (40, 120, 70, 255))

        frames, method = extractor(
            strip,
            bundle.atlas.states[0],
            bundle.atlas,
            bundle.extractor,
            chroma_key=(255, 0, 255),
        )

        self.assertEqual(method, "slots")
        self.assertEqual(len(frames), 1)
        frame = frames[0].convert("RGBA")
        self.assertEqual(frame.size, (256, 256))
        corners = [
            frame.getpixel((0, 0)),
            frame.getpixel((255, 0)),
            frame.getpixel((0, 255)),
            frame.getpixel((255, 255)),
        ]
        self.assertTrue(all(pixel[3] == 255 for pixel in corners), corners)

    def test_packager_falls_back_to_composed_atlas_when_decoded_missing(self) -> None:
        bundle = _materialise(self.template, _TILE_VARIANTS[:1])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            frames_root = tmp_root / "frames"
            run_dir = tmp_root / "run"
            icon_forge_home = tmp_root / "icon-forge"
            _seed_frames(frames_root, bundle)
            _seed_decoded(run_dir, bundle)
            _seed_passing_review(run_dir, bundle)

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="atlas-fallback",
                    display_name="Atlas Fallback",
                    description="Fallback source test.",
                    frames_root=frames_root,
                    output_run_dir=run_dir,
                    package_overrides={"ICON_FORGE_HOME": str(icon_forge_home)},
                ),
            )

            self.assertTrue(result["ok"], msg=result)
            target = (
                icon_forge_home
                / "game-tiles"
                / "atlas-fallback"
                / "tiles"
                / "grass.png"
            )
            with Image.open(target) as tile:
                self.assertEqual(tile.size, (256, 256))
                self.assertGreater(tile.convert("RGBA").getpixel((128, 128))[3], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
