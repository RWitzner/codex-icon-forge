"""Regression tests for the public GitHub and skill documentation contracts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class PublicDocumentationTests(unittest.TestCase):
    def test_installation_and_invocation_match_current_codex(self) -> None:
        readme = _read("README.md")
        skill = _read("SKILL.md")
        contributing = _read("CONTRIBUTING.md")
        metadata = _read("agents/openai.yaml")

        for text in (readme, skill, contributing):
            self.assertNotIn("~/.codex/skills", text)
            self.assertNotIn("${CODEX_HOME:-$HOME/.codex}/skills", text)

        self.assertIn('$HOME/.agents/skills/icon-forge', readme)
        self.assertIn('$HOME/.agents/skills/icon-forge', skill)
        self.assertIn('PYTHON="$SKILL_DIR/.venv/bin/python"', readme)
        self.assertIn('PYTHON="$SKILL_DIR/.venv/bin/python"', skill)
        self.assertIn("$icon-forge", readme)
        self.assertNotIn("`/icon-forge`", readme)
        self.assertIn("$icon-forge", metadata)

        unqualified_python = re.compile(r"(?m)^(?:[A-Z_]+=\$\()?python(?:\s|$)")
        self.assertIsNone(unqualified_python.search(readme))
        self.assertIsNone(unqualified_python.search(skill))

    def test_slack_output_contract_matches_multisize_packager(self) -> None:
        readme = _read("README.md")
        skill = _read("SKILL.md")
        bundle = _read("profiles/bundles/slack-stickers.json")
        packager = _read("profiles/packager/sticker-folder.json")

        for text in (readme, skill, bundle):
            for size in ("128", "256", "512", "1024"):
                self.assertIn(size, text)

        self.assertIn("<sticker>/<sticker>-<size>.png", readme)
        self.assertIn("<sticker>/<sticker>-<size>.png", skill)
        self.assertNotIn("one PNG per sticker", readme)
        self.assertNotIn("one transparent PNG per variant", skill)
        self.assertIn('"sizes": [128, 256, 512, 1024]', packager)

    def test_readme_presents_current_workflow_and_all_four_bundles(self) -> None:
        readme = _read("README.md")
        product_section = readme.split(" What it makes\n", 1)[1].split(
            " Requirements\n", 1
        )[0]

        self.assertNotIn("Five stages", readme)
        self.assertIn("```mermaid", readme)
        for command in ("review", "approve", "reject", "resume"):
            self.assertIn(command, readme)
        for bundle_id in (
            "slack-stickers",
            "app-icons",
            "app-icon-set",
            "web-brand-kit",
        ):
            self.assertIn(f"<code>{bundle_id}</code>", product_section)
        self.assertNotIn("Every sticker was generated", readme)


if __name__ == "__main__":
    unittest.main()
