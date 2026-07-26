"""Tests for the multi-size-folder packager's ``targets`` and ``emit_files``.

``naming`` can only substitute ``{size}``, so it cannot express a layout whose
filenames are not a pure function of pixel size — which is every platform
except the two originally shipped. ``targets`` states the size→path mapping
outright and ``emit_files`` writes the companion manifests those platforms
need, so new products land as profile JSON instead of engine code.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine.packager import PackageContext, package  # noqa: E402
from engine.profiles import (  # noqa: E402
    AtlasProfile,
    Geometry,
    LayoutGuides,
    PackagerProfile,
    StateSpec,
)

CELL = 64


def _atlas(states: tuple[str, ...] = ("icon",)) -> AtlasProfile:
    return AtlasProfile(
        id="test",
        description="",
        geometry=Geometry(
            columns=1, rows=len(states), cell_width=CELL, cell_height=CELL
        ),
        states=tuple(
            StateSpec(
                id=state_id,
                row=index,
                frames=1,
                durations_ms=(0,),
                purpose=f"{state_id} purpose",
            )
            for index, state_id in enumerate(states)
        ),
        derivations=(),
        layout_guides=LayoutGuides(enabled=False, safe_margin_x=0, safe_margin_y=0),
        requires_base=False,
    )


class TargetsAndEmitFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = self.root / "run"
        (self.run_dir / "final").mkdir(parents=True)
        self.out = self.root / "out"

    def _write_atlas_image(self, rows: int = 1) -> None:
        image = Image.new("RGBA", (CELL, CELL * rows), (0, 0, 0, 0))
        for row in range(rows):
            block = Image.new("RGBA", (CELL - 8, CELL - 8), (10 + row * 40, 120, 200, 255))
            image.alpha_composite(block, (4, row * CELL + 4))
        image.save(self.run_dir / "final" / "spritesheet.png")

    def _profile(self, **params: object) -> PackagerProfile:
        return PackagerProfile(
            id="test-packager",
            description="",
            output_root=str(self.out),
            strategy="multi-size-folder",
            params=params,
        )

    def _context(self) -> PackageContext:
        return PackageContext(
            entity_id="myapp",
            display_name="MyApp",
            description="a test",
            run_dir=self.run_dir,
        )

    def test_targets_write_platform_filenames(self) -> None:
        self._write_atlas_image()
        result = package(
            self._profile(
                targets=[
                    {"px": 16, "path": "icons/icon16.png"},
                    {"px": 48, "path": "icons/icon48.png"},
                ]
            ),
            self._context(),
            atlas=_atlas(),
            force=True,
        )
        self.assertTrue(result["ok"])
        for size in (16, 48):
            target = self.out / "icons" / f"icon{size}.png"
            self.assertTrue(target.is_file(), msg=f"missing {target}")
            with Image.open(target) as image:
                self.assertEqual(image.size, (size, size))

    def test_one_pixel_size_can_feed_several_paths(self) -> None:
        """The .iconset case: icon_16x16.png and icon_8x8@2x.png are both 16px."""

        self._write_atlas_image()
        package(
            self._profile(
                targets=[
                    {"px": 16, "path": "MyApp.iconset/icon_16x16.png"},
                    {"px": 16, "path": "MyApp.iconset/icon_8x8@2x.png"},
                    {"px": 32, "path": "MyApp.iconset/icon_16x16@2x.png"},
                ]
            ),
            self._context(),
            atlas=_atlas(),
            force=True,
        )
        iconset = self.out / "MyApp.iconset"
        first = (iconset / "icon_16x16.png").read_bytes()
        second = (iconset / "icon_8x8@2x.png").read_bytes()
        self.assertEqual(first, second, "same pixel size should reuse one render")
        with Image.open(iconset / "icon_16x16@2x.png") as image:
            self.assertEqual(image.size, (32, 32))

    def test_emit_files_json_survives_literal_braces(self) -> None:
        """JSON goes through render_schema, never str.format.

        A literal {"icons": ...} passed to str.format raises KeyError on its
        own braces — the failure mode this repo has already hit once with a
        README template.
        """

        self._write_atlas_image()
        package(
            self._profile(
                targets=[{"px": 16, "path": "icons/icon16.png"}],
                emit_files=[
                    {
                        "path": "manifest.icons.json",
                        "json": {
                            "icons": {"16": "icons/icon16.png"},
                            "name": "{display_name}",
                        },
                    }
                ],
            ),
            self._context(),
            atlas=_atlas(),
            force=True,
        )
        payload = json.loads((self.out / "manifest.icons.json").read_text())
        self.assertEqual(payload["icons"], {"16": "icons/icon16.png"})
        # render_schema substitutes context values inside string leaves.
        self.assertEqual(payload["name"], "MyApp")

    def test_emit_files_rejects_a_path_escaping_the_output_root(self) -> None:
        self._write_atlas_image()
        with self.assertRaises(ValueError) as caught:
            package(
                self._profile(
                    targets=[{"px": 16, "path": "icons/icon16.png"}],
                    emit_files=[{"path": "../escaped.json", "json": {"a": 1}}],
                ),
                self._context(),
                atlas=_atlas(),
                force=True,
            )
        self.assertIn("escapes", str(caught.exception))
        self.assertFalse((self.root / "escaped.json").exists())

    def test_target_path_escaping_the_output_root_is_rejected(self) -> None:
        self._write_atlas_image()
        with self.assertRaises(ValueError):
            package(
                self._profile(targets=[{"px": 16, "path": "../evil.png"}]),
                self._context(),
                atlas=_atlas(),
                force=True,
            )

    def test_emit_files_requires_exactly_one_of_json_or_template(self) -> None:
        self._write_atlas_image()
        for entry in ({"path": "x.json"}, {"path": "x.json", "json": {}, "template": "t"}):
            with self.assertRaises(ValueError):
                package(
                    self._profile(
                        targets=[{"px": 16, "path": "icons/icon16.png"}],
                        emit_files=[entry],
                    ),
                    self._context(),
                    atlas=_atlas(),
                    force=True,
                )

    def test_emit_files_will_not_clobber_without_force(self) -> None:
        """The data-loss case: manifest.icons.json is merged into by hand."""

        self._write_atlas_image()
        self.out.mkdir(parents=True)
        hand_edited = '{"USER_HAND_EDITED": true}\n'
        (self.out / "manifest.icons.json").write_text(hand_edited)

        with self.assertRaises(FileExistsError):
            package(
                self._profile(
                    targets=[{"px": 16, "path": "icons/icon16.png"}],
                    emit_files=[{"path": "manifest.icons.json", "json": {"a": 1}}],
                ),
                self._context(),
                atlas=_atlas(),
                force=False,
            )
        self.assertEqual(
            (self.out / "manifest.icons.json").read_text(), hand_edited
        )

    def test_two_outputs_mapping_to_one_path_is_refused(self) -> None:
        self._write_atlas_image()
        with self.assertRaises(ValueError) as caught:
            package(
                self._profile(
                    targets=[{"px": 16, "path": "same.png"}],
                    emit_files=[{"path": "same.png", "json": {}}],
                ),
                self._context(),
                atlas=_atlas(),
                force=True,
            )
        self.assertIn("same path", str(caught.exception))

    def test_template_error_names_the_field_and_the_placeholders(self) -> None:
        """A bare KeyError from inside a packager tells an author nothing."""

        self._write_atlas_image()
        with self.assertRaises(ValueError) as caught:
            package(
                self._profile(
                    targets=[{"px": 16, "path": "icons/icon16.png"}],
                    emit_files=[{"path": "x.json", "template": "body { margin: 0 }"}],
                ),
                self._context(),
                atlas=_atlas(),
                force=True,
            )
        message = str(caught.exception)
        self.assertIn("emit_files[0].template", message)
        self.assertIn("Available placeholders", message)
        self.assertIn("doubled", message)

    def test_a_failing_entry_leaves_no_partial_output(self) -> None:
        self._write_atlas_image()
        with self.assertRaises(ValueError):
            package(
                self._profile(
                    targets=[{"px": 16, "path": "icons/icon16.png"}],
                    emit_files=[
                        {"path": "good.json", "json": {"ok": 1}},
                        {"path": "bad.json", "template": "{nope}"},
                    ],
                ),
                self._context(),
                atlas=_atlas(),
                force=True,
            )
        self.assertFalse((self.out / "good.json").exists())
        self.assertFalse((self.out / "icons" / "icon16.png").exists())

    def test_readme_file_count_includes_emitted_files(self) -> None:
        self._write_atlas_image()
        result = package(
            self._profile(
                targets=[{"px": 16, "path": "icons/icon16.png"}],
                emit_files=[{"path": "manifest.icons.json", "json": {"a": 1}}],
                readme_filename="README.md",
                readme_template="files: {file_count}\n",
            ),
            self._context(),
            atlas=_atlas(),
            force=True,
        )
        on_disk = sum(1 for path in self.out.rglob("*") if path.is_file())
        self.assertEqual((self.out / "README.md").read_text().strip(), f"files: {on_disk}")
        self.assertEqual(result["file_count"], on_disk)

    def test_sizes_and_naming_still_work_unchanged(self) -> None:
        self._write_atlas_image(rows=2)
        result = package(
            self._profile(sizes=[16, 32], naming="{state}/{state}-{size}.png"),
            self._context(),
            atlas=_atlas(("icon", "alt")),
            force=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["sizes"], [16, 32])
        for state in ("icon", "alt"):
            for size in (16, 32):
                self.assertTrue(
                    (self.out / state / f"{state}-{size}.png").is_file(),
                    msg=f"missing {state}-{size}.png",
                )

    def test_a_profile_with_neither_sizes_nor_targets_is_rejected(self) -> None:
        self._write_atlas_image()
        with self.assertRaises(ValueError) as caught:
            package(self._profile(), self._context(), atlas=_atlas(), force=True)
        self.assertIn("targets", str(caught.exception))


class ShippedBrowserExtensionBundleTests(unittest.TestCase):
    """The bundle added purely as profile JSON, with no engine code of its own."""

    def test_bundle_loads_and_declares_the_platform_sizes(self) -> None:
        from engine.profiles import load_bundle

        bundle = load_bundle("browser-extension-icons")
        self.assertEqual(bundle.packager.strategy, "multi-size-folder")
        pixel_sizes = sorted(
            int(entry["px"]) for entry in bundle.packager.params["targets"]
        )
        self.assertEqual(pixel_sizes, [16, 32, 48, 96, 128])

        emitted = bundle.packager.params["emit_files"]
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["path"], "manifest.icons.json")
        # Chrome MV3 requires these four keys under "icons".
        self.assertEqual(
            sorted(emitted[0]["json"]["icons"]), ["128", "16", "32", "48"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
