"""Packager strategy registry + shared utilities.

A packager profile names a strategy plus configuration. Each strategy is a
registered callable that takes (profile, context, atlas, *, force) and returns
a result dict. Generic file-copy packaging uses ``files-and-manifest``;
sticker-style packs use ``atlas-extract-folder``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .profiles import AtlasProfile, PackagerProfile

_ENV_PATTERN = re.compile(r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_BARE_ENV_PATTERN = re.compile(r"\$(?P<var>[A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class PackageContext:
    entity_id: str
    display_name: str
    description: str
    run_dir: Path
    overrides: dict[str, str] | None = None

    def lookup(self, name: str) -> str | None:
        if self.overrides and name in self.overrides:
            return self.overrides[name]
        return os.environ.get(name)


PackagerStrategy = Callable[
    [PackagerProfile, PackageContext, AtlasProfile | None],
    dict[str, Any],
]


_REGISTRY: dict[str, PackagerStrategy] = {}


def register(name: str) -> Callable[[PackagerStrategy], PackagerStrategy]:
    def decorator(strategy: PackagerStrategy) -> PackagerStrategy:
        _REGISTRY[name] = strategy
        return strategy

    return decorator


def get_strategy(name: str) -> PackagerStrategy:
    if name not in _REGISTRY:
        raise KeyError(
            f"no packager strategy registered for {name!r} (have {list(_REGISTRY)})"
        )
    return _REGISTRY[name]


def registered() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# ---------------------------------------------------------------------------
# Shared helpers (used by multiple strategies)
# ---------------------------------------------------------------------------


def _expand_env(template: str, context: PackageContext) -> str:
    home_default = str(Path.home())

    def lookup_or_default(var: str, default: str) -> str:
        value = context.lookup(var)
        if value is not None:
            return value
        return _expand_env(default, context) if default else ""

    def replace_braced(match: re.Match[str]) -> str:
        var = match.group("var")
        default = match.group("default") or ""
        if var == "HOME":
            return context.lookup("HOME") or home_default
        return lookup_or_default(var, default)

    def replace_bare(match: re.Match[str]) -> str:
        var = match.group("var")
        if var == "HOME":
            return context.lookup("HOME") or home_default
        return context.lookup(var) or ""

    expanded = _ENV_PATTERN.sub(replace_braced, template)
    expanded = _BARE_ENV_PATTERN.sub(replace_bare, expanded)
    return expanded


def _format_with_context(template: str, context: PackageContext) -> str:
    return template.format(
        entity_id=context.entity_id,
        display_name=context.display_name,
        description=context.description,
    )


def resolve_output_root(profile: PackagerProfile, context: PackageContext) -> Path:
    expanded = _expand_env(profile.output_root, context)
    formatted = _format_with_context(expanded, context)
    return Path(formatted).expanduser().resolve()


def render_schema(value: Any, context: PackageContext) -> Any:
    if isinstance(value, str):
        return _format_with_context(value, context)
    if isinstance(value, list):
        return [render_schema(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_schema(item, context) for key, item in value.items()}
    return value


def copy_or_convert(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    source_suffix = source.suffix.lower()
    target_suffix = target.suffix.lower()
    if source_suffix == target_suffix:
        shutil.copy2(source, target)
        return
    if {source_suffix, target_suffix} <= {".png", ".webp"}:
        with Image.open(source) as image:
            image.convert("RGBA").save(
                target,
                format="WEBP" if target_suffix == ".webp" else "PNG",
                lossless=True,
                quality=100,
                method=6,
            )
        return
    shutil.copy2(source, target)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def package(
    profile: PackagerProfile,
    context: PackageContext,
    *,
    atlas: AtlasProfile | None = None,
    force: bool = False,
) -> dict[str, Any]:
    strategy = get_strategy(profile.strategy)
    return strategy(profile, context, atlas, force=force)


# ---------------------------------------------------------------------------
# Built-in strategy: files-and-manifest
# ---------------------------------------------------------------------------


@register("files-and-manifest")
def _files_and_manifest(
    profile: PackagerProfile,
    context: PackageContext,
    atlas: AtlasProfile | None,
    *,
    force: bool,
) -> dict[str, Any]:
    if not profile.files or profile.manifest_writer is None:
        raise ValueError(
            f"packager profile {profile.id!r} uses 'files-and-manifest' but lacks "
            "files or manifest_writer"
        )

    output_dir = resolve_output_root(profile, context)
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = [output_dir / mapping.target for mapping in profile.files]
    manifest_path = output_dir / profile.manifest_writer.filename
    if not force and (manifest_path.exists() or any(target.exists() for target in targets)):
        raise FileExistsError(
            f"{output_dir} already contains packaged files; pass force=True to overwrite"
        )

    written: list[str] = []
    for mapping, target in zip(profile.files, targets):
        source = context.run_dir / mapping.source
        copy_or_convert(source, target)
        written.append(str(target))

    manifest = render_schema(profile.manifest_writer.schema, context)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    written.append(str(manifest_path))

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "files": written,
        "manifest": manifest,
    }


# Importing the package's bundled strategies populates the registry.
from .packagers import multi_size_folder  # noqa: E402,F401
from .packagers import sticker_folder  # noqa: E402,F401
from .packagers import tile_sheet_folder  # noqa: E402,F401
