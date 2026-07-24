"""Versioned semantic prompt-role tests.

These tests lock the Commit 3 contract before the profile engine is changed:
legacy styles synthesize a default role, new styles can define typed roles,
prompt composition resolves role overrides before exact state overrides, and
run/job metadata persists the selected prompt role.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine.manifest import Job, JobInput, load_manifest  # noqa: E402
from engine.profiles import (  # noqa: E402
    AtlasProfile,
    Bundle,
    DynamicStatesConfig,
    ExtractorProfile,
    Geometry,
    LayoutGuides,
    PackagerProfile,
    ProfileError,
    StateSpec,
    VariantSpec,
    load_atlas_profile,
    load_style_profile,
    materialize_dynamic_atlas,
)
from engine.prompts import compose_row_prompt  # noqa: E402
from engine.run_setup import PrepareOptions, prepare_run  # noqa: E402


def _write_style(root: Path, profile_id: str, extra: dict) -> None:
    style_dir = root / "style" / profile_id
    (style_dir / "templates").mkdir(parents=True)
    (style_dir / "templates" / "base.txt").write_text(
        "Base {target_kind} {display_name} {style_notes}", encoding="utf-8"
    )
    (style_dir / "templates" / "row.txt").write_text(
        "\n".join(
            [
                "Kind: {target_kind}",
                "Concept: {purpose}.",
                "Style: {style_notes}",
                "Requirements:{state_requirement_text}",
                "Forbidden:",
                "{transparency_artifact_text}",
            ]
        ),
        encoding="utf-8",
    )
    data = {
        "id": profile_id,
        "description": "test style",
        "target_kind": "default target",
        "templates": {"base": "templates/base.txt", "row_strip": "templates/row.txt"},
        "prompt_blocks": {
            "house_style": "house",
            "user_style_notes_join": " plus {user_style_notes}",
        },
        "forbidden_artifacts": ["global forbidden"],
        "state_requirements": {},
        "chroma_key": {
            "selection": "auto",
            "candidates": [{"name": "magenta", "hex": "#FF00FF"}],
        },
    }
    data.update(extra)
    (style_dir / "profile.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def _atlas(states: tuple[StateSpec, ...]) -> AtlasProfile:
    return AtlasProfile(
        id="test-atlas",
        description="",
        geometry=Geometry(columns=1, rows=max(1, len(states)), cell_width=64, cell_height=64),
        states=states,
        derivations=(),
        layout_guides=LayoutGuides(enabled=False, safe_margin_x=0, safe_margin_y=0),
        requires_base=False,
    )


def _state(state_id: str = "main", role: str = "default") -> StateSpec:
    return StateSpec(
        id=state_id,
        row=0,
        frames=1,
        durations_ms=(0,),
        purpose="original purpose",
        role=role,
    )


class PromptRoleLoaderTests(unittest.TestCase):
    def test_legacy_style_synthesizes_default_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_style(root, "legacy-style", {})
            style = load_style_profile("legacy-style", root=root)
            self.assertEqual(style.prompt_profile_version, "legacy")
            self.assertEqual(tuple(style.roles), ("default",))
            self.assertIsNone(style.roles["default"].target_kind)
            self.assertIsNone(style.roles["default"].forbidden_artifacts)

    def test_valid_roles_load_with_default_role_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_style(
                root,
                "role-style",
                {
                    "prompt_profile_version": "1.0",
                    "roles": {
                        "launcher": {
                            "target_kind": "launcher target",
                            "purpose_wrapper": "wrapped {purpose}",
                            "requirements": ["role req"],
                            "forbidden_artifacts": ["role forbidden"],
                        }
                    },
                },
            )
            style = load_style_profile("role-style", root=root)
            self.assertEqual(style.prompt_profile_version, "1.0")
            self.assertEqual(tuple(style.roles), ("default", "launcher"))
            self.assertEqual(style.roles["launcher"].target_kind, "launcher target")
            self.assertEqual(style.roles["launcher"].requirements, ("role req",))

    def test_invalid_role_shapes_are_rejected(self) -> None:
        cases = [
            ("bad-slug", {"Bad": {}}),
            ("bad-object", {"default": []}),
            ("bad-target-kind", {"default": {"target_kind": ""}}),
            ("bad-wrapper", {"default": {"purpose_wrapper": "{missing}"}}),
            ("bad-requirements", {"default": {"requirements": "not-list"}}),
            ("bad-forbidden", {"default": {"forbidden_artifacts": [1]}}),
        ]
        for profile_id, roles in cases:
            with self.subTest(profile_id=profile_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_style(
                    root,
                    profile_id,
                    {"prompt_profile_version": "1.0", "roles": roles},
                )
                with self.assertRaises(ProfileError):
                    load_style_profile(profile_id, root=root)

    def test_empty_prompt_profile_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_style(
                root,
                "bad-version",
                {"prompt_profile_version": "", "roles": {"default": {}}},
            )
            with self.assertRaises(ProfileError):
                load_style_profile("bad-version", root=root)

    def test_non_string_prompt_profile_version_is_rejected(self) -> None:
        for value in (1, None):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_style(
                    root,
                    "bad-version",
                    {"prompt_profile_version": value, "roles": {"default": {}}},
                )
                with self.assertRaises(ProfileError):
                    load_style_profile("bad-version", root=root)

    def test_non_string_atlas_state_role_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atlas_dir = root / "atlas"
            atlas_dir.mkdir()
            (atlas_dir / "bad-atlas.json").write_text(
                json.dumps(
                    {
                        "id": "bad-atlas",
                        "geometry": {
                            "columns": 1,
                            "rows": 1,
                            "cell_width": 64,
                            "cell_height": 64,
                        },
                        "states": [
                            {
                                "id": "main",
                                "row": 0,
                                "frames": 1,
                                "durations_ms": [0],
                                "purpose": "main",
                                "role": 7,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ProfileError):
                load_atlas_profile("bad-atlas", root=root)


class PromptRoleCompositionTests(unittest.TestCase):
    def test_role_precedence_and_requirement_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_style(
                root,
                "role-style",
                {
                    "prompt_blocks": {
                        "house_style": "house",
                        "purpose_wrapper": "style {purpose}",
                    },
                    "state_requirements": {
                        "main": ["role req", "state req", "role req"]
                    },
                    "prompt_profile_version": "1.0",
                    "roles": {
                        "launcher": {
                            "target_kind": "launcher target",
                            "purpose_wrapper": "role {purpose}",
                            "requirements": ["role req", "extra req"],
                        }
                    },
                },
            )
            style = load_style_profile("role-style", root=root)
            atlas = _atlas((_state(role="launcher"),))
            prompt = compose_row_prompt(
                style,
                atlas,
                atlas.states[0],
                entity_id="entity",
                entity_notes="notes",
                chroma_key_name="magenta",
                chroma_key_hex="#FF00FF",
            )
            self.assertIn("Kind: launcher target", prompt)
            self.assertIn("Concept: role original purpose.", prompt)
            self.assertNotIn("style original purpose", prompt)
            req_lines = [line.strip() for line in prompt.splitlines() if line.startswith("- ")]
            self.assertEqual(req_lines[:3], ["- role req", "- extra req", "- state req"])
            self.assertEqual(req_lines.count("- role req"), 1)

    def test_empty_role_wrapper_disables_exact_state_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_style(
                root,
                "role-style",
                {
                    "prompt_blocks": {
                        "house_style": "house",
                        "purpose_wrapper_overrides": {"watch": "state {purpose}"},
                    },
                    "prompt_profile_version": "1.0",
                    "roles": {"watch": {"purpose_wrapper": ""}},
                },
            )
            style = load_style_profile("role-style", root=root)
            atlas = _atlas((_state("watch", role="watch"),))
            prompt = compose_row_prompt(
                style,
                atlas,
                atlas.states[0],
                entity_id="entity",
                entity_notes="notes",
                chroma_key_name="magenta",
                chroma_key_hex="#FF00FF",
            )
            self.assertIn("Concept: original purpose.", prompt)
            self.assertNotIn("state original purpose", prompt)

    def test_role_resolves_forbidden_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_style(
                root,
                "role-style",
                {
                    "prompt_profile_version": "1.0",
                    "roles": {
                        "launcher": {"forbidden_artifacts": ["role forbidden"]}
                    },
                },
            )
            style = load_style_profile("role-style", root=root)
            atlas = _atlas((_state(role="launcher"),))
            prompt = compose_row_prompt(
                style,
                atlas,
                atlas.states[0],
                entity_id="entity",
                entity_notes="notes",
                chroma_key_name="magenta",
                chroma_key_hex="#FF00FF",
            )
            self.assertIn("role forbidden", prompt)
            self.assertNotIn("global forbidden", prompt)

    def test_unknown_role_fails_prompt_composition(self) -> None:
        style = load_style_profile("clean-app-icon")
        atlas = _atlas((_state(role="missing"),))
        with self.assertRaises(ProfileError):
            compose_row_prompt(
                style,
                atlas,
                atlas.states[0],
                entity_id="entity",
                entity_notes="notes",
                chroma_key_name="magenta",
                chroma_key_hex="#FF00FF",
            )


class VariantRoleTests(unittest.TestCase):
    def test_dynamic_materialization_preserves_variant_roles(self) -> None:
        atlas = AtlasProfile(
            id="dynamic",
            description="",
            geometry=Geometry(columns=1, rows=1, cell_width=64, cell_height=64),
            states=(),
            derivations=(),
            layout_guides=LayoutGuides(enabled=False, safe_margin_x=0, safe_margin_y=0),
            requires_base=False,
            dynamic_states=DynamicStatesConfig(enabled=True, max_states=12),
        )
        materialized = materialize_dynamic_atlas(
            atlas,
            [VariantSpec(id="watch", role="watch", purpose="watch icon")],
        )
        self.assertEqual(materialized.states[0].role, "watch")

    def test_cli_variant_parser_accepts_role_syntax(self) -> None:
        from scripts.icon_forge import _parse_variant_arg

        parsed = _parse_variant_arg("watch@watch:watchOS silhouette")
        self.assertEqual(parsed.id, "watch")
        self.assertEqual(parsed.role, "watch")
        self.assertEqual(parsed.purpose, "watchOS silhouette")

    def test_cli_variant_parser_keeps_legacy_default_role(self) -> None:
        from scripts.icon_forge import _parse_variant_arg

        parsed = _parse_variant_arg("main:primary icon")
        self.assertEqual(parsed.id, "main")
        self.assertEqual(parsed.role, "default")

    def test_cli_variant_parser_rejects_malformed_roles(self) -> None:
        from scripts.icon_forge import _parse_variant_arg

        for raw in ("main@:purpose", "@role:purpose", "main@one@two:purpose", "main@bad_role:purpose"):
            with self.subTest(raw=raw):
                with self.assertRaises(Exception):
                    _parse_variant_arg(raw)


class PromptMetadataPersistenceTests(unittest.TestCase):
    def test_request_and_jobs_include_prompt_profile_metadata(self) -> None:
        bundle = Bundle(
            id="test-bundle",
            description="",
            atlas=_atlas((_state(role="default"),)),
            style=load_style_profile("clean-app-icon"),
            extractor=ExtractorProfile(id="slot-only", description="", strategy="slot-only", params={}),
            packager=PackagerProfile(
                id="pack",
                description="",
                output_root="out",
                strategy="multi-size-folder",
                params={"sizes": [64]},
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="entity",
                    display_name="Entity",
                    description="metadata test",
                    entity_notes="notes",
                    style_notes="",
                    references=[],
                    output_dir=run_dir,
                    chroma_key="auto",
                    force=True,
                )
            )
            request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
            self.assertEqual(
                request["states"][0]["prompt_profile"],
                {
                    "style": "clean-app-icon",
                    "version": "1.0",
                    "role": "default",
                },
            )

            manifest = load_manifest(run_dir)
            self.assertEqual(
                manifest.job("main").prompt_profile,
                {
                    "style": "clean-app-icon",
                    "version": "1.0",
                    "role": "default",
                },
            )

    def test_manifest_schema_backwards_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "imagegen-jobs.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "bundle": "legacy",
                        "run_dir": str(run_dir),
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "jobs": [
                            {
                                "id": "icon",
                                "kind": "single-frame",
                                "status": "pending",
                                "prompt_file": "prompts/rows/icon.md",
                                "input_images": [],
                                "output_path": "decoded/icon.png",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest = load_manifest(run_dir)
            self.assertEqual(manifest.job("icon").prompt_profile, {})

    def test_job_serializes_prompt_profile(self) -> None:
        job = Job(
            id="icon",
            kind="single-frame",
            status="pending",
            prompt_file="prompts/rows/icon.md",
            input_images=[JobInput(path="ref.png", role="reference")],
            output_path="decoded/icon.png",
            prompt_profile={"style": "s", "version": "1.0", "role": "launcher"},
        )
        self.assertEqual(job.to_dict()["prompt_profile"]["role"], "launcher")


class BuiltInRoleRegressionTests(unittest.TestCase):
    def test_built_in_styles_have_explicit_roles(self) -> None:
        for style_id in ("launcher-tile", "flat-vector", "clean-app-icon"):
            with self.subTest(style_id=style_id):
                style = load_style_profile(style_id)
                self.assertEqual(style.prompt_profile_version, "1.0")
                self.assertIn("default", style.roles)

    def test_app_icons_static_state_uses_launcher_role(self) -> None:
        from engine.profiles import load_bundle

        bundle = load_bundle("app-icons")
        self.assertEqual(bundle.atlas.states[0].role, "launcher")

    def test_show_exposes_prompt_roles(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/icon_forge.py", "show", "app-icons"],
            cwd=str(SKILL_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["atlas"]["states"][0]["role"], "launcher")
        self.assertEqual(payload["style"]["prompt_profile_version"], "1.0")
        self.assertIn("launcher", payload["style"]["roles"])
