"""Coherence workflow tests for the game-tiles bundle."""

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
from engine.manifest import load_manifest  # noqa: E402
from engine.orchestrate import FinalizeOptions, finalize_run  # noqa: E402
from engine.profiles import Bundle, StateSpec, materialize_dynamic_atlas  # noqa: E402
from engine.run_setup import (  # noqa: E402
    CANONICAL_TILE_SENTINEL,
    CANONICAL_TILE_STYLE_PATH,
    PrepareOptions,
    prepare_run,
    promote_tile_reference,
    record_result,
    record_tile_qa,
)
from engine.tile_contracts import contract_for_state  # noqa: E402
from engine.tile_guides import render_tile_guides  # noqa: E402
from engine.tile_qa import review_tiles, sha256_path  # noqa: E402
from tests.test_game_tiles_bundle import _colour_for_row, _seed_frames  # noqa: E402


def _state(tile_id: str, purpose: str, row: int = 0) -> StateSpec:
    return StateSpec(id=tile_id, row=row, frames=1, durations_ms=(0,), purpose=purpose)


def _materialise(variants: list[VariantSpec]) -> Bundle:
    template = load_bundle("game-tiles")
    atlas = materialize_dynamic_atlas(template.atlas, variants)
    return Bundle(
        id=template.id,
        description=template.description,
        atlas=atlas,
        style=template.style,
        extractor=template.extractor,
        packager=template.packager,
    )


def _seed_decoded_256(run_dir: Path, bundle: Bundle) -> None:
    decoded = run_dir / "decoded"
    decoded.mkdir(parents=True, exist_ok=True)
    for state in bundle.atlas.states:
        Image.new("RGBA", (256, 256), _colour_for_row(state.row)).save(decoded / f"{state.id}.png")


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


class GameTileCoherenceTests(unittest.TestCase):
    def test_dirt_cross_gets_centered_path_contract(self) -> None:
        state = _state("dirt-cross", "rounded dirt path crossroads tile connecting all four directions over grass")

        contract = contract_for_state(state)

        self.assertEqual(contract.kind, "path_cross")
        self.assertEqual(contract.exits, ("top", "right", "bottom", "left"))
        self.assertEqual(contract.path_width_px, 64)
        self.assertEqual(contract.path_center_px, 128)
        self.assertIn("top edge midpoint", contract.prompt_text)
        self.assertIn("right edge midpoint", contract.prompt_text)
        self.assertIn("bottom edge midpoint", contract.prompt_text)
        self.assertIn("left edge midpoint", contract.prompt_text)

    def test_rotated_corner_and_t_junction_parse_explicit_directions(self) -> None:
        corner = contract_for_state(_state("dirt-corner-ne", "rounded dirt path corner curving from north to east"))
        tee = contract_for_state(_state("dirt-t-nwe", "rounded dirt path T-junction connecting north west and east"))

        self.assertEqual(corner.kind, "path_corner")
        self.assertEqual(corner.exits, ("top", "right"))
        self.assertEqual(tee.kind, "path_t")
        self.assertEqual(tee.exits, ("top", "right", "left"))

    def test_grass_base_gets_style_contract_without_path_geometry(self) -> None:
        contract = contract_for_state(_state("grass", "seamless lush green grass floor tile with subtle clover patches"))

        self.assertEqual(contract.kind, "base_terrain")
        self.assertIsNone(contract.path_width_px)
        self.assertEqual(contract.exits, ())
        self.assertIn("Match the canonical style reference", contract.prompt_text)

    def test_soft_edges_does_not_parse_as_transition(self) -> None:
        contract = contract_for_state(_state("grass", "seamless grass tile with soft edges"))

        self.assertEqual(contract.kind, "base_terrain")

    def test_path_cross_guide_is_written(self) -> None:
        states = [
            _state(
                "dirt-cross",
                "rounded dirt path crossroads tile connecting all four directions over grass",
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guides = render_tile_guides(root, states)

            self.assertEqual(len(guides), 1)
            self.assertEqual(guides[0].path, "references/tile-guides/dirt-cross.png")
            with Image.open(root / guides[0].path) as image:
                self.assertEqual(image.size, (256, 256))
                self.assertGreater(image.convert("RGBA").getpixel((128, 0))[3], 0)

    def test_game_tile_path_prompt_contains_geometry_contract(self) -> None:
        bundle = load_bundle("game-tiles")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="terrain",
                    display_name="Terrain",
                    description="Terrain tiles.",
                    entity_notes="top-down RPG terrain",
                    style_notes="HD vector-style",
                    output_dir=run_dir,
                    variants=[
                        VariantSpec(
                            id="dirt-cross",
                            purpose="rounded dirt path crossroads tile connecting all four directions over grass",
                        )
                    ],
                )
            )

            prompt = (run_dir / "prompts" / "rows" / "dirt-cross.md").read_text(encoding="utf-8")

        self.assertIn("Tile coherence contract:", prompt)
        self.assertIn("64px wide", prompt)
        self.assertIn("pixel 128", prompt)
        self.assertIn("crisp HD top-down game terrain art", prompt)
        self.assertNotIn("HD vector-style", prompt)

    def test_prepare_blocks_non_seed_jobs_until_promotion(self) -> None:
        bundle = load_bundle("game-tiles")
        variants = [
            VariantSpec(id="grass", purpose="seamless lush green grass floor tile"),
            VariantSpec(id="dirt-cross", purpose="rounded dirt path crossroads tile connecting all four directions over grass"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="terrain",
                    display_name="Terrain",
                    description="Terrain tiles.",
                    entity_notes="terrain",
                    style_notes="",
                    output_dir=run_dir,
                    variants=variants,
                )
            )
            self.assertEqual(result["ready_jobs"], ["grass"])
            manifest = load_manifest(run_dir)
            blocked = manifest.job("dirt-cross")
            self.assertIn(CANONICAL_TILE_SENTINEL, blocked.depends_on)
            self.assertIn(CANONICAL_TILE_STYLE_PATH, [item.path for item in blocked.input_images])
            self.assertIn("references/tile-guides/dirt-cross.png", [item.path for item in blocked.input_images])

            decoded = run_dir / "decoded" / "grass.png"
            Image.new("RGBA", (256, 256), (60, 140, 70, 255)).save(decoded)
            manifest.job("grass").status = "complete"
            manifest.save(run_dir)

            promoted = promote_tile_reference(run_dir, "grass")
            self.assertTrue(promoted["ok"])
            self.assertTrue((run_dir / CANONICAL_TILE_STYLE_PATH).is_file())
            self.assertEqual(promoted["next_ready_jobs"], ["dirt-cross"])
            self.assertNotIn(CANONICAL_TILE_SENTINEL, load_manifest(run_dir).job("dirt-cross").depends_on)

    def test_prepare_rejects_path_seed_when_base_terrain_comes_later(self) -> None:
        bundle = load_bundle("game-tiles")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                prepare_run(
                    PrepareOptions(
                        bundle=bundle,
                        entity_id="terrain",
                        display_name="Terrain",
                        description="Terrain tiles.",
                        entity_notes="terrain",
                        style_notes="",
                        output_dir=Path(tmp) / "run",
                        variants=[
                            VariantSpec(
                                id="dirt-cross",
                                purpose="rounded dirt path crossroads tile connecting all four directions over grass",
                            ),
                            VariantSpec(id="grass", purpose="seamless lush green grass floor tile"),
                        ],
                    )
                )

    def test_promote_reference_requires_first_seed_job(self) -> None:
        bundle = load_bundle("game-tiles")
        variants = [
            VariantSpec(id="grass", purpose="seamless lush green grass floor tile"),
            VariantSpec(id="dirt-v", purpose="rounded dirt path north to south over grass"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="terrain",
                    display_name="Terrain",
                    description="Terrain tiles.",
                    entity_notes="terrain",
                    style_notes="",
                    output_dir=run_dir,
                    variants=variants,
                )
            )
            decoded = run_dir / "decoded" / "dirt-v.png"
            Image.new("RGBA", (256, 256), (160, 110, 60, 255)).save(decoded)
            manifest = load_manifest(run_dir)
            manifest.job("dirt-v").status = "complete"
            manifest.save(run_dir)

            with self.assertRaises(ValueError):
                promote_tile_reference(run_dir, "dirt-v")

    def test_record_refuses_non_seed_before_canonical_promotion(self) -> None:
        bundle = load_bundle("game-tiles")
        variants = [
            VariantSpec(id="grass", purpose="seamless lush green grass floor tile"),
            VariantSpec(id="stone-road", purpose="stone road north to south over grass"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="terrain",
                    display_name="Terrain",
                    description="Terrain tiles.",
                    entity_notes="terrain",
                    style_notes="",
                    output_dir=run_dir,
                    variants=variants,
                )
            )
            source = root / "stone-road.png"
            Image.new("RGBA", (256, 256), (120, 120, 120, 255)).save(source)
            stale_reference = run_dir / CANONICAL_TILE_STYLE_PATH
            stale_reference.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (256, 256), (60, 140, 70, 255)).save(stale_reference)

            with self.assertRaises(ValueError):
                record_result(
                    run_dir,
                    "stone-road",
                    source,
                    allow_synthetic_test_source=True,
                )

    def test_record_qa_persists_parent_and_subagent_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            selected = Path(tmp) / "ig_tile.png"
            run_dir.mkdir()
            Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(selected)

            result = record_tile_qa(
                run_dir,
                "grass",
                selected,
                subagent_note="matches reference",
                parent_decision="accepted",
                parent_note="style accepted",
            )

            self.assertTrue(result["ok"])
            payload = json.loads((run_dir / "qa" / "jobs" / "grass.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["subagent_note"], "matches reference")
            self.assertEqual(payload["parent_decision"], "accepted")

    def test_record_qa_rejects_invalid_parent_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            selected = Path(tmp) / "ig_tile.png"
            run_dir.mkdir()
            Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(selected)

            with self.assertRaises(ValueError):
                record_tile_qa(
                    run_dir,
                    "grass",
                    selected,
                    subagent_note="matches reference",
                    parent_decision="yolo",
                    parent_note="invalid",
                )

    def test_review_tiles_writes_contact_sheet_and_errors_for_bad_dirt_path(self) -> None:
        states = [_state("dirt-cross", "rounded dirt path crossroads tile connecting all four directions over grass")]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decoded = run_dir / "decoded"
            decoded.mkdir()
            image = Image.new("RGBA", (256, 256), (60, 140, 70, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((120, 0, 136, 255), fill=(160, 110, 60, 255))
            draw.rectangle((0, 120, 255, 136), fill=(160, 110, 60, 255))
            image.save(decoded / "dirt-cross.png")

            result = review_tiles(run_dir, states)

            self.assertFalse(result["ok"])
            self.assertFalse(result["approved"])
            self.assertTrue((run_dir / "qa" / "contact-sheet.png").is_file())
            self.assertTrue((run_dir / "qa" / "review.json").is_file())
            self.assertTrue(result["tiles"][0]["errors"])

    def test_review_tiles_errors_for_bad_non_dirt_path_geometry(self) -> None:
        states = [_state("stone-road", "stone road north to south over grass")]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decoded = run_dir / "decoded"
            decoded.mkdir()
            Image.new("RGBA", (256, 256), (120, 120, 120, 255)).save(decoded / "stone-road.png")

            result = review_tiles(run_dir, states)

            self.assertFalse(result["ok"])
            self.assertTrue(result["tiles"][0]["errors"])

    def test_game_tiles_finalize_blocks_when_review_has_errors(self) -> None:
        bundle = _materialise([VariantSpec(id="grass", purpose="seamless lush green grass floor tile")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            run = root / "run"
            _seed_frames(frames, bundle)
            _seed_decoded_256(run, bundle)
            qa = run / "qa"
            qa.mkdir(parents=True)
            contact_sheet = qa / "contact-sheet.png"
            Image.new("RGBA", (256, 256), (24, 24, 24, 255)).save(contact_sheet)
            (qa / "review.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "approved": False,
                        "state_ids": ["grass"],
                        "contact_sheet": str(contact_sheet),
                        "tiles": [
                            {
                                "id": "grass",
                                "decoded_sha256": sha256_path(run / "decoded" / "grass.png"),
                                "errors": ["style drift"],
                                "warnings": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="blocked",
                    display_name="Blocked",
                    description="Blocked by QA.",
                    frames_root=frames,
                    output_run_dir=run,
                    package_overrides={"ICON_FORGE_HOME": str(root / "out")},
                ),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "tile-qa")

    def test_game_tiles_finalize_blocks_empty_or_unapproved_review(self) -> None:
        bundle = _materialise([VariantSpec(id="grass", purpose="seamless lush green grass floor tile")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            run = root / "run"
            _seed_frames(frames, bundle)
            _seed_decoded_256(run, bundle)
            qa = run / "qa"
            qa.mkdir(parents=True)
            (qa / "review.json").write_text(json.dumps({"ok": True, "tiles": []}), encoding="utf-8")

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="blocked-empty",
                    display_name="Blocked Empty",
                    description="Blocked by incomplete QA.",
                    frames_root=frames,
                    output_run_dir=run,
                    package_overrides={"ICON_FORGE_HOME": str(root / "out")},
                ),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "tile-qa")

    def test_game_tiles_finalize_blocks_duplicate_review_entries(self) -> None:
        bundle = _materialise(
            [
                VariantSpec(id="grass", purpose="seamless lush green grass floor tile"),
                VariantSpec(id="stone", purpose="cracked stone floor tile"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            run = root / "run"
            _seed_frames(frames, bundle)
            _seed_decoded_256(run, bundle)
            qa = run / "qa"
            qa.mkdir(parents=True)
            contact_sheet = qa / "contact-sheet.png"
            Image.new("RGBA", (256, 256), (24, 24, 24, 255)).save(contact_sheet)
            grass_hash = sha256_path(run / "decoded" / "grass.png")
            (qa / "review.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "approved": True,
                        "state_ids": ["grass", "stone"],
                        "contact_sheet": str(contact_sheet),
                        "tiles": [
                            {"id": "grass", "decoded_sha256": grass_hash, "errors": [], "warnings": []},
                            {"id": "grass", "decoded_sha256": grass_hash, "errors": [], "warnings": []},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="duplicate-review",
                    display_name="Duplicate Review",
                    description="Blocked by incomplete QA.",
                    frames_root=frames,
                    output_run_dir=run,
                    package_overrides={"ICON_FORGE_HOME": str(root / "out")},
                ),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "tile-qa")
        self.assertTrue(any("duplicate" in error for error in result["errors"]))

    def test_game_tiles_finalize_succeeds_with_approved_fresh_review(self) -> None:
        bundle = _materialise([VariantSpec(id="grass", purpose="seamless lush green grass floor tile")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            run = root / "run"
            _seed_frames(frames, bundle)
            _seed_decoded_256(run, bundle)
            _seed_passing_review(run, bundle)

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="approved",
                    display_name="Approved",
                    description="Approved QA.",
                    frames_root=frames,
                    output_run_dir=run,
                    package_overrides={"ICON_FORGE_HOME": str(root / "out")},
                ),
            )

        self.assertTrue(result["ok"], msg=result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
