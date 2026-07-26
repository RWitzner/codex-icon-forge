"""Guard: the public repository must never ship private profiles or content.

Private products (game-specific bundles, client work, unreleased branding) are
expected to live outside this repository and be loaded via ``--profile-dir`` or
``ICON_FORGE_PROFILE_PATH``. This test fails the build if any of that material
is ever copied into the repo by accident.

Two independent checks, because the failure modes differ:

* ``test_shipped_profiles_match_allowlist`` catches a private *profile* being
  dropped into ``profiles/``. Adding a genuinely public profile means adding it
  to the allowlist in the same commit, which makes the decision explicit.
* ``test_no_private_markers_in_tracked_files`` catches private *names or paths*
  leaking into docs, plans, comments, or test fixtures.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every profile id this repository is allowed to ship, per axis.
PUBLIC_PROFILES = {
    "bundles": {"app-icon-set", "app-icons", "slack-stickers", "web-brand-kit"},
    "atlas": {"app-icon-set", "app-icons", "slack-stickers", "web-brand-kit"},
    "style": {"clean-app-icon", "flat-vector", "launcher-tile"},
    "extractor": {"chroma-key-slots", "slot-only"},
    "packager": {
        "app-icon-set-multisize",
        "app-icons-multisize",
        "sticker-folder",
        "web-brand-kit",
    },
}

# Case-insensitive markers that must never appear in tracked text files.
# Keep this list in sync with whatever private work shares this machine.
PRIVATE_MARKERS = (
    r"\bbrik\b",
    r"\bunity\b",
    r"/Users/[a-z0-9._-]+/",
    r"[a-z0-9._%-]+@(?:gmail|hotmail|outlook)\.[a-z]{2,}",
)

# Binary and vendored paths that are checked for filename only, not content.
_SKIP_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".ico", ".pyc"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / name for name in out.split("\0") if name]


class NoPrivateContentTest(unittest.TestCase):
    def test_shipped_profiles_match_allowlist(self) -> None:
        profiles_root = REPO_ROOT / "profiles"
        for axis, allowed in PUBLIC_PROFILES.items():
            axis_dir = profiles_root / axis
            self.assertTrue(axis_dir.is_dir(), f"missing profile axis dir: {axis_dir}")
            if axis == "style":
                found = {p.name for p in axis_dir.iterdir() if p.is_dir()}
            else:
                found = {p.stem for p in axis_dir.glob("*.json")}
            unexpected = sorted(found - allowed)
            self.assertEqual(
                [],
                unexpected,
                f"profiles/{axis}/ contains ids that are not on the public "
                f"allowlist: {unexpected}. Private profiles belong outside this "
                f"repo (use --profile-dir / ICON_FORGE_PROFILE_PATH). If these "
                f"are genuinely public, add them to PUBLIC_PROFILES.",
            )

    def test_no_private_markers_in_tracked_files(self) -> None:
        patterns = [re.compile(marker, re.IGNORECASE) for marker in PRIVATE_MARKERS]
        offenders: list[str] = []
        for path in _tracked_files():
            rel = path.relative_to(REPO_ROOT)
            if str(rel) == f"tests/{Path(__file__).name}":
                continue  # this file defines the markers
            for pattern in patterns:
                if pattern.search(str(rel)):
                    offenders.append(f"{rel}: filename matches {pattern.pattern}")
            if path.suffix.lower() in _SKIP_SUFFIXES or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for pattern in patterns:
                    if pattern.search(line):
                        offenders.append(
                            f"{rel}:{lineno}: matches {pattern.pattern}"
                        )
        self.assertEqual(
            [],
            offenders,
            "private content found in tracked files:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
