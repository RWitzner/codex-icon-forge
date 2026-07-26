"""External private profile root discovery and prepared-run continuity tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from engine import (  # noqa: E402
    PROFILES_ROOT,
    list_bundle_ids,
    load_bundle,
    load_bundle_for_run,
    resolve_profile_roots,
)
from engine.profiles import ProfileError  # noqa: E402
from engine.review import review_outputs  # noqa: E402
from engine.run_setup import (  # noqa: E402
    PrepareOptions,
    approve_results,
    derive_mirror,
    prepare_run,
    record_result,
)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _copy_profile_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _write_bundle(
    root: Path,
    bundle_id: str,
    *,
    atlas: str = "app-icons",
    style: str = "launcher-tile",
    extractor: str = "chroma-key-slots",
    packager: str = "app-icons-multisize",
    description: str = "private bundle",
) -> None:
    _write_json(
        root / "bundles" / f"{bundle_id}.json",
        {
            "id": bundle_id,
            "description": description,
            "atlas": atlas,
            "style": style,
            "extractor": extractor,
            "packager": packager,
        },
    )


def _write_atlas(root: Path, atlas_id: str, *, cell_width: int = 32) -> None:
    _write_json(
        root / "atlas" / f"{atlas_id}.json",
        {
            "id": atlas_id,
            "description": f"{atlas_id} atlas",
            "requires_base": False,
            "geometry": {
                "columns": 1,
                "rows": 1,
                "cell_width": cell_width,
                "cell_height": cell_width,
            },
            "states": [
                {
                    "id": "icon",
                    "row": 0,
                    "frames": 1,
                    "durations_ms": [0],
                    "purpose": "private icon",
                    "role": "launcher",
                }
            ],
            "derivations": [],
            "layout_guides": {"enabled": False, "safe_margin_x": 0, "safe_margin_y": 0},
        },
    )


def _write_mirror_atlas(root: Path, atlas_id: str) -> None:
    _write_json(
        root / "atlas" / f"{atlas_id}.json",
        {
            "id": atlas_id,
            "description": f"{atlas_id} atlas",
            "requires_base": False,
            "geometry": {
                "columns": 1,
                "rows": 2,
                "cell_width": 32,
                "cell_height": 32,
            },
            "states": [
                {
                    "id": "left",
                    "row": 0,
                    "frames": 1,
                    "durations_ms": [0],
                    "purpose": "left-facing icon",
                    "role": "launcher",
                },
                {
                    "id": "right",
                    "row": 1,
                    "frames": 1,
                    "durations_ms": [0],
                    "purpose": "right-facing icon",
                    "role": "launcher",
                },
            ],
            "derivations": [
                {
                    "target": "right",
                    "source": "left",
                    "method": "horizontal-mirror",
                    "requires_explicit_approval": True,
                }
            ],
            "layout_guides": {"enabled": False, "safe_margin_x": 0, "safe_margin_y": 0},
        },
    )


def _write_style(root: Path, style_id: str, marker: str) -> None:
    style_dir = root / "style" / style_id
    (style_dir / "templates").mkdir(parents=True, exist_ok=True)
    (style_dir / "templates" / "base.txt").write_text(
        f"base template {marker} {{display_name}} {{target_kind}} {{entity_notes}} "
        "{style_notes} {chroma_key_name} {chroma_key_hex} {cell_width} {cell_height}\n",
        encoding="utf-8",
    )
    (style_dir / "templates" / "row.txt").write_text(
        f"row template {marker} {{entity_id}} {{state}} {{purpose}} {{target_kind}} "
        "{entity_notes} {style_notes} {chroma_key_name} {chroma_key_hex} "
        "{cell_width} {cell_height}{state_requirement_text}{transparency_artifact_text}\n",
        encoding="utf-8",
    )
    _write_json(
        style_dir / "profile.json",
        {
            "id": style_id,
            "description": f"{style_id} style",
            "target_kind": f"{marker} icon",
            "prompt_profile_version": "1.0",
            "roles": {"default": {}, "launcher": {}},
            "templates": {"base": "templates/base.txt", "row_strip": "templates/row.txt"},
            "prompt_blocks": {
                "house_style": f"private style {marker}",
                "user_style_notes_join": " {user_style_notes}",
            },
            "forbidden_artifacts": [f"forbid {marker}"],
            "state_requirements": {},
            "chroma_key": {
                "selection": "auto",
                "candidates": [{"name": "magenta", "hex": "#FF00FF"}],
            },
        },
    )


def _seed_icon_frame(frames_root: Path, state_id: str = "icon") -> None:
    state_dir = frames_root / state_id
    state_dir.mkdir(parents=True, exist_ok=True)
    icon = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.ellipse((180, 180, 844, 844), fill=(210, 80, 40, 255))
    icon.save(state_dir / "00.png")


def _seed_record_source(path: Path, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((size // 4, size // 4, size * 3 // 4, size * 3 // 4), fill=(80, 160, 220, 255))
    image.save(path)


class ProfileResolverTests(unittest.TestCase):
    def test_cli_env_bundled_precedence_and_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cli_root = tmp_root / "cli"
            env_root = tmp_root / "env"
            cli_root.mkdir()
            env_root.mkdir()
            with mock.patch.dict(
                os.environ,
                {"ICON_FORGE_PROFILE_PATH": os.pathsep.join([str(env_root), str(cli_root), ""])},
            ):
                roots = resolve_profile_roots([cli_root, cli_root])
            self.assertEqual(roots, (cli_root.resolve(), env_root.resolve(), PROFILES_ROOT.resolve()))

    def test_invalid_roots_name_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with self.assertRaisesRegex(ProfileError, "cli --profile-dir.*missing"):
                resolve_profile_roots([missing])

    def test_invalid_env_root_names_icon_forge_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with mock.patch.dict(os.environ, {"ICON_FORGE_PROFILE_PATH": str(missing)}):
                with self.assertRaisesRegex(ProfileError, "ICON_FORGE_PROFILE_PATH.*missing"):
                    resolve_profile_roots([])

    def test_env_pathsep_and_empty_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            root_a.mkdir()
            root_b.mkdir()
            with mock.patch.dict(
                os.environ,
                {"ICON_FORGE_PROFILE_PATH": os.pathsep.join(["", str(root_a), "", str(root_b)])},
            ):
                roots = resolve_profile_roots([])
            self.assertEqual(roots[:2], (root_a.resolve(), root_b.resolve()))

    def test_rejects_traversal_profile_ids(self) -> None:
        with self.assertRaisesRegex(ProfileError, "invalid profile id"):
            load_bundle("../app-icons")
        with self.assertRaisesRegex(ProfileError, "invalid profile id"):
            load_bundle("/tmp/app-icons")


class ProfileLoaderTests(unittest.TestCase):
    def test_single_path_compatibility_and_first_match_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            for root, width in ((first, 40), (second, 80)):
                _write_bundle(root, "collision", atlas="private-atlas")
                _write_atlas(root, "private-atlas", cell_width=width)
                _copy_profile_file(
                    PROFILES_ROOT / "style" / "launcher-tile" / "profile.json",
                    root / "style" / "launcher-tile" / "profile.json",
                )
                for template in (PROFILES_ROOT / "style" / "launcher-tile" / "templates").iterdir():
                    _copy_profile_file(template, root / "style" / "launcher-tile" / "templates" / template.name)
                _copy_profile_file(
                    PROFILES_ROOT / "extractor" / "chroma-key-slots.json",
                    root / "extractor" / "chroma-key-slots.json",
                )
                _copy_profile_file(
                    PROFILES_ROOT / "packager" / "app-icons-multisize.json",
                    root / "packager" / "app-icons-multisize.json",
                )

            self.assertEqual(load_bundle("collision", root=second).atlas.geometry.cell_width, 80)
            self.assertEqual(load_bundle("collision", root=[first, second]).atlas.geometry.cell_width, 40)

    def test_hybrid_private_bundle_uses_bundled_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_root = Path(tmp) / "private"
            _write_bundle(private_root, "private-app")
            bundle = load_bundle("private-app", root=[private_root, PROFILES_ROOT])
            self.assertEqual(bundle.id, "private-app")
            self.assertEqual(bundle.atlas.id, "app-icons")
            self.assertEqual(bundle.style.id, "launcher-tile")

    def test_private_style_precedes_bundled_and_uses_relative_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_root = Path(tmp) / "private"
            _write_bundle(private_root, "private-style-app", style="private-style")
            _write_style(private_root, "private-style", "private-marker")
            bundle = load_bundle("private-style-app", root=[private_root, PROFILES_ROOT])
            self.assertEqual(bundle.style.id, "private-style")
            self.assertEqual(bundle.style.target_kind, "private-marker icon")
            self.assertIn("base template private-marker", bundle.style.base_template)
            self.assertIn("row template private-marker", bundle.style.row_strip_template)

    def test_style_template_parent_traversal_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_root = Path(tmp) / "private"
            outside = Path(tmp) / "outside-template.txt"
            outside.write_text("outside secret template", encoding="utf-8")
            _write_bundle(private_root, "private-style-app", style="private-style")
            _write_style(private_root, "private-style", "private-marker")
            profile_path = private_root / "style" / "private-style" / "profile.json"
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            data["templates"]["base"] = "../../../outside-template.txt"
            _write_json(profile_path, data)

            with self.assertRaisesRegex(
                ProfileError, "base template.*must stay inside style profile directory"
            ):
                load_bundle("private-style-app", root=[private_root, PROFILES_ROOT])

    def test_style_template_symlink_escape_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_root = Path(tmp) / "private"
            outside = Path(tmp) / "outside-template.txt"
            outside.write_text("outside secret template", encoding="utf-8")
            _write_bundle(private_root, "private-style-app", style="private-style")
            _write_style(private_root, "private-style", "private-marker")
            link = private_root / "style" / "private-style" / "templates" / "outside-link.txt"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            profile_path = private_root / "style" / "private-style" / "profile.json"
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            data["templates"]["row_strip"] = "templates/outside-link.txt"
            _write_json(profile_path, data)

            with self.assertRaisesRegex(
                ProfileError, "row_strip template.*must stay inside style profile directory"
            ):
                load_bundle("private-style-app", root=[private_root, PROFILES_ROOT])

    def test_list_bundle_ids_returns_visible_union_with_first_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_root = Path(tmp) / "private"
            _write_bundle(private_root, "private-app")
            _write_bundle(private_root, "app-icons", description="private collision")
            bundle_ids = list_bundle_ids([private_root, PROFILES_ROOT])
            self.assertEqual(bundle_ids.count("app-icons"), 1)
            self.assertIn("private-app", bundle_ids)
            self.assertLess(bundle_ids.index("private-app"), len(bundle_ids))

    def test_missing_profile_error_lists_searched_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_root = Path(tmp) / "private"
            private_root.mkdir()
            with self.assertRaisesRegex(ProfileError, "searched.*private.*profiles"):
                load_bundle("does-not-exist", root=[private_root, PROFILES_ROOT])


class ProfileCliAndRunContinuityTests(unittest.TestCase):
    def _run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            [sys.executable, "scripts/icon_forge.py", *args],
            cwd=str(SKILL_DIR),
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_bundles_show_prepare_with_profile_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            run_dir = Path(tmp) / "run"
            _write_bundle(root, "private-app")

            bundles_proc = self._run_cli("bundles", "--profile-dir", str(root))
            self.assertEqual(bundles_proc.returncode, 0, msg=bundles_proc.stderr)
            self.assertIn("private-app", json.loads(bundles_proc.stdout)["bundles"][0]["id"])

            show_proc = self._run_cli("show", "private-app", "--profile-dir", str(root))
            self.assertEqual(show_proc.returncode, 0, msg=show_proc.stderr)
            self.assertEqual(json.loads(show_proc.stdout)["id"], "private-app")

            prepare_proc = self._run_cli(
                "prepare",
                "--profile-dir",
                str(root),
                "--bundle",
                "private-app",
                "--entity-id",
                "solar",
                "--description",
                "private prepare",
                "--output-dir",
                str(run_dir),
                "--force",
            )
            self.assertEqual(prepare_proc.returncode, 0, msg=prepare_proc.stderr)
            request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["profile_roots"], [str(root.resolve())])

    def test_env_only_prepare_persists_absolute_external_roots_without_copying_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            run_dir = Path(tmp) / "run"
            _write_bundle(root, "private-app")
            proc = self._run_cli(
                "prepare",
                "--bundle",
                "private-app",
                "--entity-id",
                "solar",
                "--description",
                "env prepare",
                "--output-dir",
                str(run_dir),
                "--force",
                env={"ICON_FORGE_PROFILE_PATH": str(root)},
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["profile_roots"], [str(root.resolve())])
            self.assertFalse((run_dir / "profiles").exists())
            self.assertFalse((run_dir / "bundles").exists())

    def test_prepare_validates_profile_roots_before_creating_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing"
            run_dir = Path(tmp) / "run"
            bundle = load_bundle("app-icons")
            with self.assertRaisesRegex(ProfileError, "PrepareOptions.profile_roots.*missing"):
                prepare_run(
                    PrepareOptions(
                        bundle=bundle,
                        profile_roots=[root],
                        entity_id="solar",
                        display_name="Solar",
                        description="bad roots",
                        entity_notes="",
                        style_notes="",
                        output_dir=run_dir,
                        force=True,
                    )
                )
            self.assertFalse(run_dir.exists())

    def test_load_bundle_for_run_rehydrates_without_flags_or_env_and_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            run_dir = Path(tmp) / "run"
            legacy_run = Path(tmp) / "legacy"
            _write_bundle(root, "private-app")
            bundle = load_bundle("private-app", root=[root, PROFILES_ROOT])
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    profile_roots=[root.resolve()],
                    entity_id="solar",
                    display_name="Solar",
                    description="rehydrate",
                    entity_notes="",
                    style_notes="",
                    output_dir=run_dir,
                    force=True,
                )
            )
            with mock.patch.dict(os.environ, {"ICON_FORGE_PROFILE_PATH": ""}):
                self.assertEqual(load_bundle_for_run(run_dir).id, "private-app")

            legacy_run.mkdir()
            _write_json(legacy_run / "request.json", {"bundle": "app-icons", "variants": []})
            self.assertEqual(load_bundle_for_run(legacy_run).id, "app-icons")

    def test_bad_request_profile_roots_shape_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            _write_json(
                run_dir / "request.json",
                {"bundle": "app-icons", "profile_roots": "/tmp/private", "variants": []},
            )
            with self.assertRaisesRegex(ProfileError, "profile_roots.*array"):
                load_bundle_for_run(run_dir)

            _write_json(
                run_dir / "request.json",
                {"bundle": "app-icons", "profile_roots": [123], "variants": []},
            )
            with self.assertRaisesRegex(ProfileError, r"profile_roots\[0\].*non-empty string"):
                load_bundle_for_run(run_dir)

    def test_relative_request_profile_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            _write_json(
                run_dir / "request.json",
                {"bundle": "app-icons", "profile_roots": ["relative-root"], "variants": []},
            )
            with mock.patch("os.getcwd", return_value="/tmp/other-cwd"):
                with self.assertRaisesRegex(ProfileError, r"profile_roots\[0\].*absolute"):
                    load_bundle_for_run(run_dir)

    def test_persisted_roots_win_over_later_colliding_env_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persisted_root = Path(tmp) / "persisted"
            colliding_env_root = Path(tmp) / "env"
            run_dir = Path(tmp) / "run"
            _write_bundle(persisted_root, "private-app", atlas="private-atlas")
            _write_atlas(persisted_root, "private-atlas", cell_width=32)
            _write_bundle(colliding_env_root, "private-app", atlas="private-atlas")
            _write_atlas(colliding_env_root, "private-atlas", cell_width=64)
            bundle = load_bundle("private-app", root=[persisted_root, PROFILES_ROOT])
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    profile_roots=[persisted_root],
                    entity_id="solar",
                    display_name="Solar",
                    description="source stable",
                    entity_notes="",
                    style_notes="",
                    output_dir=run_dir,
                    force=True,
                )
            )
            with mock.patch.dict(os.environ, {"ICON_FORGE_PROFILE_PATH": str(colliding_env_root)}):
                rehydrated = load_bundle_for_run(run_dir)
            self.assertEqual(rehydrated.atlas.geometry.cell_width, 32)

    def test_cli_review_and_extract_rehydrate_persisted_private_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            run_dir = Path(tmp) / "run"
            source = Path(tmp) / "source.png"
            _write_bundle(root, "private-app")
            bundle = load_bundle("private-app", root=[root, PROFILES_ROOT])
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    profile_roots=[root],
                    entity_id="solar",
                    display_name="Solar",
                    description="review extract",
                    entity_notes="",
                    style_notes="",
                    output_dir=run_dir,
                    force=True,
                )
            )
            _seed_record_source(source)
            record_result(run_dir, "icon", source, allow_synthetic_test_source=True)

            review_proc = self._run_cli(
                "review",
                "--run-dir",
                str(run_dir),
                env={"ICON_FORGE_PROFILE_PATH": ""},
            )
            self.assertEqual(review_proc.returncode, 0, msg=review_proc.stderr + review_proc.stdout)

            approve_results(run_dir, job_ids=["icon"], note="approved")
            extract_proc = self._run_cli(
                "extract",
                "--run-dir",
                str(run_dir),
                env={"ICON_FORGE_PROFILE_PATH": ""},
            )
            self.assertEqual(extract_proc.returncode, 0, msg=extract_proc.stderr + extract_proc.stdout)
            self.assertTrue((run_dir / "frames" / "icon" / "00.png").is_file())

    def test_derive_mirror_rehydrates_private_derivation_from_persisted_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            run_dir = Path(tmp) / "run"
            source = Path(tmp) / "left.png"
            _write_bundle(root, "private-derive", atlas="mirror-atlas")
            _write_mirror_atlas(root, "mirror-atlas")
            bundle = load_bundle("private-derive", root=[root, PROFILES_ROOT])
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    profile_roots=[root],
                    entity_id="solar",
                    display_name="Solar",
                    description="derive",
                    entity_notes="",
                    style_notes="",
                    output_dir=run_dir,
                    force=True,
                )
            )
            _seed_record_source(source, size=32)
            record_result(run_dir, "left", source, allow_synthetic_test_source=True)
            approve_results(run_dir, job_ids=["left"], note="source approved")

            with mock.patch.dict(os.environ, {"ICON_FORGE_PROFILE_PATH": ""}):
                result = derive_mirror(run_dir, "right", "mirror accepted")
            self.assertTrue(result["ok"])
            self.assertTrue((run_dir / "decoded" / "right.png").is_file())

    def test_static_finalize_uses_persisted_private_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            run_dir = Path(tmp) / "run"
            frames_root = Path(tmp) / "frames"
            icon_home = Path(tmp) / "icon-home"
            _write_bundle(root, "private-app")
            bundle = load_bundle("private-app", root=[root, PROFILES_ROOT])
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    profile_roots=[root.resolve()],
                    entity_id="solar",
                    display_name="Solar",
                    description="static finalize",
                    entity_notes="",
                    style_notes="",
                    output_dir=run_dir,
                    force=True,
                )
            )
            _seed_icon_frame(frames_root)
            proc = self._run_cli(
                "finalize",
                "--bundle",
                "private-app",
                "--frames",
                str(frames_root),
                "--entity-id",
                "solar",
                "--description",
                "static finalize",
                "--output-run-dir",
                str(run_dir),
                "--icon-forge-home",
                str(icon_home),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertTrue((icon_home / "app-icons" / "solar" / "solar-1024.png").is_file())


class ReviewHonoursProfileDirOverrideTests(unittest.TestCase):
    """`review --profile-dir` used to be discarded before reaching the loader.

    Every other run-dir command (`extract`, `derive`, `finalize`) threads the
    caller's root chain through. `review` loaded the bundle with the override
    and then reloaded it without, so a run whose private profile root had moved
    could be extracted but not reviewed.
    """

    def test_review_loads_the_bundle_from_the_relocated_profile_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_profiles = root / "profiles-v1"
            _write_bundle(original_profiles, "private-icons", atlas="private-atlas")
            _write_atlas(original_profiles, "private-atlas")

            run_dir = root / "run"
            bundle = load_bundle("private-icons", root=[original_profiles, PROFILES_ROOT])
            prepare_run(
                PrepareOptions(
                    bundle=bundle,
                    entity_id="private",
                    display_name="Private",
                    description="profile-dir override test",
                    entity_notes="a simple mark",
                    style_notes="",
                    references=[],
                    output_dir=run_dir,
                    chroma_key="#FF00FF",
                    force=True,
                    profile_roots=[original_profiles],
                )
            )

            source = root / "icon.png"
            image = Image.new("RGBA", (32, 32), (255, 0, 255, 255))
            ImageDraw.Draw(image).ellipse((6, 6, 25, 25), fill=(20, 90, 200, 255))
            image.save(source)
            record_result(
                run_dir, "icon", source, allow_synthetic_test_source=True
            )

            # The user reorganises their private profiles after preparing.
            relocated = root / "profiles-v2"
            original_profiles.rename(relocated)

            # The persisted root no longer exists, so review must use the
            # override the caller supplied — exactly as extract does.
            with self.assertRaises(ProfileError):
                review_outputs(bundle, run_dir, force=True)

            result = review_outputs(
                bundle,
                run_dir,
                force=True,
                root=[relocated, PROFILES_ROOT],
            )
            self.assertEqual(result["bundle"], "private-icons")
            self.assertTrue((run_dir / "qa" / "review.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
