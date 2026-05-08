"""Tests for the launcher-tile style profile and the purpose_wrapper engine feature.

Covers:
  * StyleProfile.purpose_wrapper loads from prompt_blocks.purpose_wrapper
  * compose_row_prompt applies the wrapper to state.purpose when set
  * compose_row_prompt leaves purpose unchanged when wrapper is unset
  * launcher-tile profile loads cleanly with watch/notification state_requirements
  * launcher-tile row prompts contain anti-glyph guards
  * watch variants fall back to monochrome via state_requirements
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

# Imports below cover the full eventual test scope of this file (Tasks 1, 2, 6).
# Some are unused at Task 1's checkpoint and become live as later tasks land.
from engine.profiles import (  # noqa: E402
    AtlasProfile,
    Geometry,
    LayoutGuides,
    ProfileError,
    StateSpec,
    StyleProfile,
    load_style_profile,
)
from engine.prompts import compose_row_prompt  # noqa: E402


class StyleProfilePurposeWrapperLoadTests(unittest.TestCase):
    def test_clean_app_icon_has_empty_purpose_wrapper(self) -> None:
        style = load_style_profile("clean-app-icon")
        self.assertEqual(style.purpose_wrapper, "")
        self.assertEqual(style.purpose_wrapper_overrides, {})

    def test_loader_rejects_malformed_purpose_wrapper(self) -> None:
        """Malformed format strings must fail at profile-load, not at first prepare."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            style_dir = root / "style" / "broken-style"
            (style_dir / "templates").mkdir(parents=True)
            (style_dir / "templates" / "base.txt").write_text("base", encoding="utf-8")
            (style_dir / "templates" / "row.txt").write_text("row", encoding="utf-8")
            (style_dir / "profile.json").write_text(
                json.dumps({
                    "id": "broken-style",
                    "target_kind": "icon",
                    "templates": {"base": "templates/base.txt", "row_strip": "templates/row.txt"},
                    "prompt_blocks": {
                        "house_style": "x",
                        "purpose_wrapper": "theme {bogus} not {purpose}",
                    },
                    "chroma_key": {
                        "selection": "auto",
                        "candidates": [{"name": "magenta", "hex": "#FF00FF"}],
                    },
                }),
                encoding="utf-8",
            )
            with self.assertRaises(ProfileError):
                load_style_profile("broken-style", root=root)

    def test_loader_rejects_unmatched_brace_in_wrapper(self) -> None:
        """str.format raises ValueError (not KeyError) on syntactically broken
        braces. The probe must catch that too — otherwise a profile with an
        unmatched brace slips past load and fails at first prepare."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            style_dir = root / "style" / "broken-braces"
            (style_dir / "templates").mkdir(parents=True)
            (style_dir / "templates" / "base.txt").write_text("base", encoding="utf-8")
            (style_dir / "templates" / "row.txt").write_text("row", encoding="utf-8")
            (style_dir / "profile.json").write_text(
                json.dumps({
                    "id": "broken-braces",
                    "target_kind": "icon",
                    "templates": {"base": "templates/base.txt", "row_strip": "templates/row.txt"},
                    "prompt_blocks": {
                        "house_style": "x",
                        "purpose_wrapper": "unmatched { brace",
                    },
                    "chroma_key": {
                        "selection": "auto",
                        "candidates": [{"name": "magenta", "hex": "#FF00FF"}],
                    },
                }),
                encoding="utf-8",
            )
            with self.assertRaises(ProfileError):
                load_style_profile("broken-braces", root=root)


def _make_atlas_with_state(state_id: str, purpose: str) -> AtlasProfile:
    state = StateSpec(
        id=state_id,
        row=0,
        frames=1,
        durations_ms=(0,),
        purpose=purpose,
    )
    return AtlasProfile(
        id="test-atlas",
        description="",
        geometry=Geometry(columns=1, rows=1, cell_width=1024, cell_height=1024),
        states=(state,),
        derivations=(),
        layout_guides=LayoutGuides(enabled=False, safe_margin_x=0, safe_margin_y=0),
        requires_base=False,
        dynamic_states=None,
    )


def _clone_with_wrapper(
    base: StyleProfile,
    wrapper: str = "",
    overrides: dict[str, str] | None = None,
) -> StyleProfile:
    return dataclasses.replace(
        base,
        purpose_wrapper=wrapper,
        purpose_wrapper_overrides=dict(overrides) if overrides else {},
    )


class ComposeRowPromptPurposeWrapperTests(unittest.TestCase):
    def test_no_wrapper_leaves_purpose_unchanged(self) -> None:
        style = load_style_profile("clean-app-icon")
        atlas = _make_atlas_with_state("journal", "journal entry icon")
        prompt = compose_row_prompt(
            style,
            atlas,
            atlas.states[0],
            entity_id="wellness-app",
            entity_notes="modern minimalist",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        )
        self.assertIn("Concept: journal entry icon.", prompt)

    def test_wrapper_reframes_purpose(self) -> None:
        wrapped_style = _clone_with_wrapper(
            load_style_profile("clean-app-icon"),
            wrapper='theme "{purpose}" rendered as a launcher tile',
        )
        atlas = _make_atlas_with_state("journal", "journal entry icon")
        prompt = compose_row_prompt(
            wrapped_style,
            atlas,
            atlas.states[0],
            entity_id="wellness-app",
            entity_notes="modern minimalist",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        )
        self.assertIn(
            'theme "journal entry icon" rendered as a launcher tile', prompt
        )
        self.assertNotIn("Concept: journal entry icon.", prompt)

    def test_empty_override_bypasses_wrapper(self) -> None:
        """A state listed in purpose_wrapper_overrides with an empty value
        gets no wrapping at all — even if a default purpose_wrapper is set.
        Used for variants whose state_requirements override the render mode
        (e.g. watch silhouettes that must not also receive launcher framing).
        """
        wrapped_style = _clone_with_wrapper(
            load_style_profile("clean-app-icon"),
            wrapper='theme "{purpose}" rendered as a launcher tile',
            overrides={"watch": ""},
        )
        atlas = _make_atlas_with_state("watch", "1-bit silhouette for watchOS")
        prompt = compose_row_prompt(
            wrapped_style,
            atlas,
            atlas.states[0],
            entity_id="wellness-app",
            entity_notes="modern minimalist",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        )
        self.assertNotIn("rendered as a launcher tile", prompt)
        self.assertIn("Concept: 1-bit silhouette for watchOS.", prompt)


class LauncherTileProfileLoadTests(unittest.TestCase):
    def test_launcher_tile_loads(self) -> None:
        style = load_style_profile("launcher-tile")
        self.assertEqual(style.id, "launcher-tile")
        self.assertEqual(style.target_kind, "app launcher icon")

    def test_launcher_tile_has_purpose_wrapper(self) -> None:
        style = load_style_profile("launcher-tile")
        self.assertNotEqual(style.purpose_wrapper, "")
        self.assertIn("{purpose}", style.purpose_wrapper)
        self.assertIn("launcher tile", style.purpose_wrapper.lower())
        self.assertIn("not", style.purpose_wrapper.lower())

    def test_launcher_tile_bans_glyphs(self) -> None:
        style = load_style_profile("launcher-tile")
        forbidden_blob = " ".join(style.forbidden_artifacts).lower()
        self.assertIn("sf symbols", forbidden_blob)
        self.assertIn("monochrome glyph", forbidden_blob)

    def test_launcher_tile_state_requirements_for_watch(self) -> None:
        style = load_style_profile("launcher-tile")
        self.assertIn("watch", style.state_requirements)
        watch_text = " ".join(style.state_requirements["watch"]).lower()
        self.assertIn("silhouette", watch_text)
        self.assertIn("does not apply", watch_text)

    def test_launcher_tile_state_requirements_for_notification(self) -> None:
        style = load_style_profile("launcher-tile")
        self.assertIn("notification", style.state_requirements)

    def test_launcher_tile_has_wrapper_overrides_for_watch_and_notification(self) -> None:
        """watch and notification must opt out of the launcher-tile wrapper
        because their state_requirements mandate monochrome silhouettes —
        leaving the launcher framing in would produce an internally
        contradictory prompt."""
        style = load_style_profile("launcher-tile")
        self.assertIn("watch", style.purpose_wrapper_overrides)
        self.assertIn("notification", style.purpose_wrapper_overrides)
        self.assertEqual(style.purpose_wrapper_overrides["watch"], "")
        self.assertEqual(style.purpose_wrapper_overrides["notification"], "")


class LauncherTileRowPromptIntegrationTests(unittest.TestCase):
    def _compose(self, state_id: str, purpose: str) -> str:
        style = load_style_profile("launcher-tile")
        atlas = _make_atlas_with_state(state_id, purpose)
        return compose_row_prompt(
            style,
            atlas,
            atlas.states[0],
            entity_id="wellness-app",
            entity_notes="modern minimalist",
            chroma_key_name="magenta",
            chroma_key_hex="#FF00FF",
        )

    def test_feature_named_variant_gets_anti_glyph_framing(self) -> None:
        prompt = self._compose("journal", "journal entry icon")
        self.assertIn("expressing the visual theme of", prompt.lower())
        self.assertIn("not as a ui symbol", prompt.lower())
        self.assertIn('"journal entry icon"', prompt)

    def test_watch_variant_gets_silhouette_override_without_launcher_wrapper(self) -> None:
        """watch state_requirements should fire AND the launcher-tile wrapper
        should be suppressed — otherwise the prompt contradicts itself
        ('full-colour Home Screen tile' + '1-bit monochrome silhouette')."""
        prompt = self._compose("watch", "1-bit silhouette for watchOS")
        self.assertIn("silhouette", prompt.lower())
        self.assertIn("does not apply", prompt.lower())
        # Wrapper is suppressed: the wrapper's unique phrasing is absent.
        # (Note: "not as a UI symbol" appears verbatim in forbidden_artifacts[0]
        # which is global to all row prompts; the wrapper-unique signal is
        # "expressing the visual theme of", which is suppressed via
        # purpose_wrapper_overrides["watch"] = "".)
        self.assertNotIn("expressing the visual theme of", prompt.lower())
        self.assertNotIn("home screen app tile", prompt.lower())

    def test_notification_variant_skips_launcher_wrapper(self) -> None:
        prompt = self._compose("notification", "single-tone status icon")
        self.assertIn("silhouette", prompt.lower())
        # Wrapper-unique phrase, not the global forbidden_artifacts text.
        self.assertNotIn("expressing the visual theme of", prompt.lower())

    def test_main_variant_still_works_normally(self) -> None:
        prompt = self._compose("main", "primary app icon")
        self.assertIn("launcher tile", prompt.lower())
        self.assertIn('"primary app icon"', prompt)

    def test_light_and_dark_variants_get_wrapper_and_surface_hint(self) -> None:
        """light and dark variants are full-colour launcher tiles. They keep the
        launcher-tile wrapper (no override) and pick up their state-specific
        surface hint from state_requirements."""
        light_prompt = self._compose("light", "primary launcher tile")
        self.assertIn("expressing the visual theme of", light_prompt.lower())
        self.assertIn("light-mode launcher tile", light_prompt.lower())
        self.assertIn("light system surfaces", light_prompt.lower())

        dark_prompt = self._compose("dark", "primary launcher tile")
        self.assertIn("expressing the visual theme of", dark_prompt.lower())
        self.assertIn("dark-mode launcher tile", dark_prompt.lower())
        self.assertIn("dark system surfaces", dark_prompt.lower())


class BundleStyleWiringTests(unittest.TestCase):
    def test_app_icon_set_uses_launcher_tile(self) -> None:
        from engine.profiles import load_bundle
        bundle = load_bundle("app-icon-set")
        self.assertEqual(bundle.style.id, "launcher-tile")

    def test_app_icons_uses_launcher_tile(self) -> None:
        from engine.profiles import load_bundle
        bundle = load_bundle("app-icons")
        self.assertEqual(bundle.style.id, "launcher-tile")
