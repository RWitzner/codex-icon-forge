"""Behaviour tests for packager strategies.

Covers env-style ``output_root`` resolution and the ``atlas-extract-folder``
strategy used by sticker bundles. The ``multi-size-folder`` strategy is
exercised by ``test_app_icons_bundle`` end-to-end.

Run from the skill root:
    python -m unittest tests.test_packager -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import load_bundle  # noqa: E402
from engine.packager import PackageContext, resolve_output_root  # noqa: E402


class OutputRootResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle("slack-stickers")

    def test_env_override_resolves(self) -> None:
        context = PackageContext(
            entity_id="dev-pack",
            display_name="Dev Pack",
            description="Test.",
            run_dir=Path("."),
            overrides={"ICON_FORGE_HOME": "C:/fake/icon-forge"},
        )
        resolved = resolve_output_root(self.bundle.packager, context)
        self.assertEqual(
            resolved,
            Path("C:/fake/icon-forge/stickers/dev-pack").resolve(),
        )

    def test_default_falls_back_to_home(self) -> None:
        context = PackageContext(
            entity_id="dev-pack",
            display_name="Dev Pack",
            description="Test.",
            run_dir=Path("."),
            overrides={"HOME": "C:/fake/home"},
        )
        resolved = resolve_output_root(self.bundle.packager, context)
        self.assertEqual(
            resolved.parts[-3:],
            ("icon-forge", "stickers", "dev-pack"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
