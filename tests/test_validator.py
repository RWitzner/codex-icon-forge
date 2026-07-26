"""Tests for ``engine.validator.validate_atlas``.

``validate_atlas`` is the last automated gate before packaging: it is the only
thing standing between a botched chroma removal and a shipped icon pack. It had
no test coverage at all, and its near-opaque check was measured against the raw
cell area while ``fit_to_cell`` guarantees a padded frame can never reach that —
so the check could not fire for any shipped bundle. These tests pin each failure
branch and, crucially, that the near-opaque limit is *reachable*.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from engine.profiles import (
    AtlasProfile,
    ExtractorProfile,
    Geometry,
    LayoutGuides,
    StateSpec,
)
from engine.validator import validate_atlas

CELL = 64
PADDING = 10


def _atlas_profile(*, columns: int = 2, rows: int = 1, frames: int = 2) -> AtlasProfile:
    return AtlasProfile(
        id="test-atlas",
        description="",
        geometry=Geometry(
            columns=columns, rows=rows, cell_width=CELL, cell_height=CELL
        ),
        states=(
            StateSpec(
                id="idle",
                row=0,
                frames=frames,
                durations_ms=tuple(0 for _ in range(frames)),
                purpose="test state",
            ),
        ),
        derivations=(),
        layout_guides=LayoutGuides(enabled=False, safe_margin_x=0, safe_margin_y=0),
        requires_base=False,
    )


def _extractor_profile(**params: object) -> ExtractorProfile:
    merged: dict[str, object] = {
        "min_used_pixels": 50,
        "near_opaque_threshold": 0.98,
        "cell_padding_px": PADDING,
    }
    merged.update(params)
    return ExtractorProfile(
        id="test-extractor", description="", strategy="chroma-key-slots", params=merged
    )


def _fill_cell(image: Image.Image, column: int, inset: int) -> None:
    """Paint an opaque square into one cell, ``inset`` px in from each edge."""

    left = column * CELL + inset
    top = inset
    block = Image.new(
        "RGBA", (CELL - 2 * inset, CELL - 2 * inset), (10, 120, 200, 255)
    )
    image.alpha_composite(block, (left, top))


class ValidateAtlasTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _save(self, image: Image.Image, name: str = "atlas.png") -> Path:
        path = self.tmp / name
        image.save(path)
        return path

    def _healthy_atlas(self) -> Image.Image:
        image = Image.new("RGBA", (CELL * 2, CELL), (0, 0, 0, 0))
        _fill_cell(image, 0, inset=8)
        _fill_cell(image, 1, inset=8)
        return image

    # -- happy path ------------------------------------------------------

    def test_healthy_atlas_validates(self) -> None:
        path = self._save(self._healthy_atlas())
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertTrue(result.ok, msg=result.errors)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.cells), 2)

    # -- failure branches ------------------------------------------------

    def test_unopenable_file_reports_error(self) -> None:
        path = self.tmp / "not-an-image.png"
        path.write_text("definitely not a PNG", encoding="utf-8")
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertFalse(result.ok)
        self.assertTrue(
            any("could not open atlas" in error for error in result.errors),
            msg=result.errors,
        )

    def test_wrong_dimensions_report_error(self) -> None:
        path = self._save(Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0)))
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertFalse(result.ok)
        self.assertTrue(
            any("expected 128x64" in error for error in result.errors),
            msg=result.errors,
        )

    def test_non_png_format_reports_error(self) -> None:
        path = self.tmp / "atlas.tiff"
        self._healthy_atlas().save(path, format="TIFF")
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertFalse(result.ok)
        self.assertTrue(
            any("expected PNG or WebP" in error for error in result.errors),
            msg=result.errors,
        )

    def test_missing_alpha_channel_reports_error(self) -> None:
        path = self._save(Image.new("RGB", (CELL * 2, CELL), (10, 120, 200)))
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertFalse(result.ok)
        self.assertTrue(
            any("does not have an alpha channel" in error for error in result.errors),
            msg=result.errors,
        )

    def test_sparse_used_cell_reports_error(self) -> None:
        image = self._healthy_atlas()
        # Wipe the second frame so it falls under min_used_pixels.
        image.paste((0, 0, 0, 0), (CELL, 0, CELL * 2, CELL))
        path = self._save(image)
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertFalse(result.ok)
        self.assertTrue(
            any("empty or too sparse" in error for error in result.errors),
            msg=result.errors,
        )

    def test_unused_column_must_be_transparent(self) -> None:
        image = self._healthy_atlas()
        path = self._save(image)
        # Declare only one frame, so column 1 is unused but still painted.
        result = validate_atlas(
            path, _atlas_profile(frames=1), _extractor_profile()
        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any("unused column" in error for error in result.errors),
            msg=result.errors,
        )

    # -- the check that was unreachable ----------------------------------

    def test_near_opaque_limit_is_reachable(self) -> None:
        """A cell that kept its background must be flagged.

        This is the regression guard: before the fix the limit was
        ``cell_width * cell_height * threshold``, which a padded frame can
        never exceed, so this branch was dead code for every shipped bundle.
        """

        image = Image.new("RGBA", (CELL * 2, CELL), (255, 0, 255, 255))
        path = self._save(image)
        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertFalse(result.ok)
        self.assertTrue(
            any("nearly opaque used cells" in error for error in result.errors),
            msg=result.errors,
        )

    def test_legitimate_full_bleed_tile_is_not_flagged(self) -> None:
        """A rounded launcher tile fills most of its cell and must still pass.

        Guards the other direction: the limit has to sit above what real
        full-colour tile art reaches, or every app-icon run fails. The shipped
        monoline-suite masters sit at ~0.92 of the attainable area; the rounded
        corners are what keep a legitimate tile below the limit, while a cell
        that kept its rectangular background lands above it.
        """

        image = Image.new("RGBA", (CELL * 2, CELL), (0, 0, 0, 0))
        inset = PADDING // 2
        draw = ImageDraw.Draw(image)
        for column in range(2):
            left = column * CELL + inset
            draw.rounded_rectangle(
                (left, inset, left + CELL - 2 * inset - 1, CELL - inset - 1),
                radius=14,
                fill=(10, 120, 200, 255),
            )
        path = self._save(image)

        attainable = (CELL - PADDING) ** 2
        filled = sum(
            image.crop((0, 0, CELL, CELL)).getchannel("A").histogram()[1:]
        )
        self.assertLess(
            filled / attainable,
            0.98,
            "test fixture no longer represents realistic tile art",
        )

        result = validate_atlas(path, _atlas_profile(), _extractor_profile())
        self.assertTrue(result.ok, msg=result.errors)

    def test_near_opaque_can_be_downgraded_to_warning(self) -> None:
        image = Image.new("RGBA", (CELL * 2, CELL), (255, 0, 255, 255))
        path = self._save(image)
        result = validate_atlas(
            path,
            _atlas_profile(),
            _extractor_profile(),
            allow_opaque=True,
            allow_near_opaque_used_cells=True,
        )
        self.assertTrue(result.ok, msg=result.errors)
        self.assertTrue(
            any("nearly opaque used cells" in warning for warning in result.warnings),
            msg=result.warnings,
        )

    def test_threshold_above_one_disables_the_check(self) -> None:
        """Documented escape hatch in references/profile-schema.md."""

        image = Image.new("RGBA", (CELL * 2, CELL), (255, 0, 255, 255))
        path = self._save(image)
        result = validate_atlas(
            path,
            _atlas_profile(),
            _extractor_profile(near_opaque_threshold=1.5),
            allow_opaque=True,
        )
        self.assertEqual(
            [error for error in result.errors if "nearly opaque" in error], []
        )

    def test_fully_opaque_atlas_reports_error(self) -> None:
        image = Image.new("RGBA", (CELL * 2, CELL), (10, 120, 200, 255))
        path = self._save(image)
        result = validate_atlas(
            path,
            _atlas_profile(),
            # Disable the per-cell check so only the whole-atlas one can fire.
            _extractor_profile(near_opaque_threshold=1.5),
        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any("fully opaque" in error for error in result.errors),
            msg=result.errors,
        )


class FinalizeStopsOnValidationFailureTests(unittest.TestCase):
    """`finalize_run`'s validate-failure branch had never executed in CI.

    It is the gate that stops a pack whose chroma background survived from
    reaching the packager. If it regresses, a broken pack ships silently with
    `ok: true`.
    """

    def test_finalize_refuses_to_package_an_atlas_that_kept_its_background(
        self,
    ) -> None:
        from engine.orchestrate import FinalizeOptions, finalize_run
        from engine.profiles import Bundle, PackagerProfile, StyleProfile
        from engine.profiles import ChromaKeyCandidate, ChromaKeyConfig

        atlas = _atlas_profile(columns=1, frames=1)
        bundle = Bundle(
            id="test-bundle",
            description="",
            atlas=atlas,
            style=StyleProfile(
                id="test-style",
                description="",
                target_kind="icon",
                house_style="",
                user_style_notes_join="",
                base_template="",
                row_strip_template="",
                forbidden_artifacts=(),
                state_requirements={},
                chroma_key=ChromaKeyConfig(
                    selection="auto",
                    candidates=(ChromaKeyCandidate(name="magenta", hex="#FF00FF"),),
                ),
            ),
            extractor=_extractor_profile(),
            packager=PackagerProfile(
                id="test-packager",
                description="",
                output_root="/should/never/be/created",
                strategy="multi-size-folder",
                params={"sizes": [16]},
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames_root = root / "frames"
            state_dir = frames_root / "idle"
            state_dir.mkdir(parents=True)
            # A frame that still carries its chroma background end to end.
            Image.new("RGBA", (CELL, CELL), (255, 0, 255, 255)).save(
                state_dir / "00.png"
            )

            result = finalize_run(
                bundle,
                FinalizeOptions(
                    entity_id="testapp",
                    display_name="TestApp",
                    description="",
                    frames_root=frames_root,
                    output_run_dir=root / "run",
                    force=True,
                ),
            )

        self.assertFalse(result["ok"], msg=result)
        self.assertEqual(result["stage"], "validate")
        self.assertTrue(result["errors"], msg=result)
        self.assertFalse(
            Path("/should/never/be/created").exists(),
            "packager ran despite validation failing",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
