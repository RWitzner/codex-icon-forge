"""Profile loaders for icon-forge.

A run is parameterised by four orthogonal profiles plus a bundle that names
which profile of each kind to use:

    profiles/atlas/<id>.json
    profiles/style/<id>/profile.json  (+ templates/*.txt)
    profiles/extractor/<id>.json
    profiles/packager/<id>.json
    profiles/bundles/<id>.json

Loaders here are pure data: they read JSON, validate structure, and return
frozen-ish dataclasses. No image processing, no CLI, no IO beyond reading
the profile files themselves.

Zero runtime dependencies beyond the standard library.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .request_manifest import read_request

PROFILES_ROOT = Path(__file__).resolve().parent.parent / "profiles"
PROFILE_PATH_ENV = "ICON_FORGE_PROFILE_PATH"

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ROLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_VALID_DERIVATION_METHODS = {"horizontal-mirror", "vertical-mirror"}
_VALID_FALLBACK_STRATEGIES = {"full-generation", "error"}


class ProfileError(ValueError):
    """Raised when a profile file is missing required structure."""


RootSpec = Path | os.PathLike[str] | str
RootInput = RootSpec | Sequence[RootSpec]


# ---------------------------------------------------------------------------
# Atlas profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Geometry:
    columns: int
    rows: int
    cell_width: int
    cell_height: int

    @property
    def width(self) -> int:
        return self.columns * self.cell_width

    @property
    def height(self) -> int:
        return self.rows * self.cell_height


@dataclass(frozen=True)
class StateSpec:
    id: str
    row: int
    frames: int
    durations_ms: tuple[int, ...]
    purpose: str
    role: str = "default"
    is_reduced_motion_first_frame: bool = False


@dataclass(frozen=True)
class Derivation:
    target: str
    source: str
    method: str
    requires_explicit_approval: bool
    fallback_strategy: str


@dataclass(frozen=True)
class LayoutGuides:
    enabled: bool
    safe_margin_x: int
    safe_margin_y: int
    guide_style: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DynamicStatesConfig:
    """Bounded opt-in capability: states are supplied at prepare time.

    When ``enabled`` is True, the atlas template ships with empty states and
    geometry.rows is materialised from the variant count later. Only bundles
    that explicitly declare this block can use dynamic states; everything
    else stays static.
    """

    enabled: bool
    source: str = "prepare_variants"
    max_states: int = 12


_VARIANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_VARIANT_PURPOSE_MAX_LEN = 200


@dataclass(frozen=True)
class VariantSpec:
    """User-supplied state for a dynamic atlas.

    ``id`` is a slug used as the state id and as the per-variant subfolder
    name in packager output. ``purpose`` is the one-sentence description
    the prompt template will use for that variant.
    """

    id: str
    purpose: str
    role: str = "default"


@dataclass(frozen=True)
class AtlasProfile:
    id: str
    description: str
    geometry: Geometry
    states: tuple[StateSpec, ...]
    derivations: tuple[Derivation, ...]
    layout_guides: LayoutGuides
    requires_base: bool = True
    dynamic_states: DynamicStatesConfig | None = None

    def state(self, state_id: str) -> StateSpec:
        for state in self.states:
            if state.id == state_id:
                return state
        raise KeyError(state_id)

    @property
    def state_ids(self) -> tuple[str, ...]:
        return tuple(state.id for state in self.states)

    def derivation_for(self, target_id: str) -> Derivation | None:
        for derivation in self.derivations:
            if derivation.target == target_id:
                return derivation
        return None

    @property
    def is_dynamic(self) -> bool:
        return self.dynamic_states is not None and self.dynamic_states.enabled


def materialize_dynamic_atlas(
    atlas: AtlasProfile, variants: list[VariantSpec]
) -> AtlasProfile:
    """Materialise a dynamic atlas template into a concrete one.

    Validates the variant list, then returns a NEW frozen AtlasProfile with
    geometry.rows = len(variants), one StateSpec per variant in order, and
    each state.row equal to its index. Columns and cell dimensions are
    preserved from the template.
    """

    if not atlas.is_dynamic:
        raise ProfileError(
            f"atlas {atlas.id!r} is not dynamic; cannot materialise variants"
        )
    config = atlas.dynamic_states
    assert config is not None  # narrowed by is_dynamic

    if not variants:
        raise ProfileError(
            f"atlas {atlas.id!r} requires at least one variant; pass --variant id:purpose"
        )
    if len(variants) > config.max_states:
        raise ProfileError(
            f"atlas {atlas.id!r} accepts at most {config.max_states} variants, "
            f"got {len(variants)}"
        )

    seen: set[str] = set()
    states: list[StateSpec] = []
    for index, variant in enumerate(variants):
        if not isinstance(variant.id, str) or not _VARIANT_ID_PATTERN.match(
            variant.id
        ):
            raise ProfileError(
                f"variant id {variant.id!r} is invalid; must match "
                "[a-z0-9][a-z0-9-]{0,30}"
            )
        if not isinstance(variant.role, str) or not _ROLE_ID_PATTERN.match(
            variant.role
        ):
            raise ProfileError(
                f"variant {variant.id!r} role {variant.role!r} is invalid; must match "
                "[a-z0-9][a-z0-9-]{0,30}"
            )
        if variant.id in seen:
            raise ProfileError(f"duplicate variant id {variant.id!r}")
        seen.add(variant.id)
        purpose = (variant.purpose or "").strip()
        if not purpose:
            raise ProfileError(f"variant {variant.id!r} has empty purpose")
        if len(purpose) > _VARIANT_PURPOSE_MAX_LEN:
            raise ProfileError(
                f"variant {variant.id!r} purpose exceeds {_VARIANT_PURPOSE_MAX_LEN} chars"
            )
        states.append(
            StateSpec(
                id=variant.id,
                row=index,
                frames=1,
                durations_ms=(0,),
                purpose=purpose,
                role=variant.role,
            )
        )

    new_geometry = Geometry(
        columns=atlas.geometry.columns,
        rows=len(states),
        cell_width=atlas.geometry.cell_width,
        cell_height=atlas.geometry.cell_height,
    )
    return AtlasProfile(
        id=atlas.id,
        description=atlas.description,
        geometry=new_geometry,
        states=tuple(states),
        derivations=atlas.derivations,
        layout_guides=atlas.layout_guides,
        requires_base=atlas.requires_base,
        dynamic_states=atlas.dynamic_states,
    )


# ---------------------------------------------------------------------------
# Style profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptRole:
    target_kind: str | None = None
    purpose_wrapper: str | None = None
    requirements: tuple[str, ...] = ()
    forbidden_artifacts: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ChromaKeyCandidate:
    name: str
    hex: str


@dataclass(frozen=True)
class ChromaKeyConfig:
    selection: str
    candidates: tuple[ChromaKeyCandidate, ...]


@dataclass(frozen=True)
class StyleProfile:
    id: str
    description: str
    target_kind: str
    house_style: str
    user_style_notes_join: str
    base_template: str
    row_strip_template: str
    forbidden_artifacts: tuple[str, ...]
    state_requirements: dict[str, tuple[str, ...]]
    chroma_key: ChromaKeyConfig
    extends: str | None = None
    purpose_wrapper: str = ""
    purpose_wrapper_overrides: dict[str, str] = field(default_factory=dict)
    prompt_profile_version: str = "legacy"
    roles: dict[str, PromptRole] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extractor profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractorProfile:
    id: str
    description: str
    strategy: str
    params: dict[str, Any]


# ---------------------------------------------------------------------------
# Packager profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileMapping:
    source: str
    target: str


@dataclass(frozen=True)
class ManifestWriter:
    kind: str
    filename: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class PackagerProfile:
    id: str
    description: str
    output_root: str
    strategy: str = "files-and-manifest"
    files: tuple[FileMapping, ...] = ()
    manifest_writer: ManifestWriter | None = None
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bundle:
    id: str
    description: str
    atlas: AtlasProfile
    style: StyleProfile
    extractor: ExtractorProfile
    packager: PackagerProfile


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ProfileError(f"profile file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"invalid JSON in {path}: {exc}") from exc


def _root_source_label(source: str, root: Path) -> str:
    if source == "cli":
        return f"cli --profile-dir {root}"
    if source == "env":
        return f"{PROFILE_PATH_ENV} {root}"
    if source == "explicit":
        return f"explicit profile root {root}"
    if source == "prepare":
        return f"PrepareOptions.profile_roots {root}"
    if source == "persisted":
        return f"request profile_roots {root}"
    return f"bundled profiles root {root}"


def _normalize_existing_root(root: RootSpec, source: str) -> Path:
    try:
        path = Path(root).expanduser().resolve()
    except TypeError as exc:
        raise ProfileError(
            f"profile root from {source} must be a path-like string, got {root!r}"
        ) from exc
    if not path.is_dir():
        raise ProfileError(
            f"profile root from {_root_source_label(source, path)} is not an existing directory"
        )
    return path


def _dedupe_roots(items: Iterable[tuple[Path, str]]) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for root, _source in items:
        if root in seen:
            continue
        seen.add(root)
        roots.append(root)
    return tuple(roots)


def normalize_profile_roots(
    roots: Sequence[RootSpec],
    *,
    source: str = "explicit",
    allow_empty: bool = True,
) -> tuple[Path, ...]:
    if isinstance(roots, (str, os.PathLike)):
        raise ProfileError(
            f"{_root_source_label(source, Path(roots).expanduser())} must be provided "
            "as a list of profile root paths, not a single scalar"
        )
    normalized = [
        (_normalize_existing_root(raw_root, source), source)
        for raw_root in roots
    ]
    if not normalized and not allow_empty:
        raise ProfileError("at least one profile root is required")
    return _dedupe_roots(normalized)


def _coerce_roots(
    root: RootInput | None = PROFILES_ROOT, *, source: str = "explicit"
) -> tuple[Path, ...]:
    if root is None:
        root = PROFILES_ROOT
    if isinstance(root, (str, os.PathLike)):
        raw_roots: Sequence[RootSpec] = [root]
    else:
        raw_roots = root
    return normalize_profile_roots(raw_roots, source=source, allow_empty=False)


def resolve_profile_roots(
    profile_dirs: Sequence[RootSpec] | None = None,
    *,
    include_bundled: bool = True,
    environ: dict[str, str] | None = None,
) -> tuple[Path, ...]:
    """Resolve CLI/env/bundled profile roots in precedence order.

    Library loaders intentionally do not call this by default, keeping normal
    imports deterministic and bundled-only. The CLI uses it at process edges.
    """

    env = os.environ if environ is None else environ
    ordered: list[tuple[Path, str]] = []
    for root in profile_dirs or ():
        ordered.append((_normalize_existing_root(root, "cli"), "cli"))
    for segment in env.get(PROFILE_PATH_ENV, "").split(os.pathsep):
        if not segment:
            continue
        ordered.append((_normalize_existing_root(segment, "env"), "env"))
    if include_bundled:
        ordered.append((_normalize_existing_root(PROFILES_ROOT, "bundled"), "bundled"))
    return _dedupe_roots(ordered)


def _validate_profile_id(profile_id: str) -> str:
    if not isinstance(profile_id, str) or not profile_id:
        raise ProfileError(f"invalid profile id {profile_id!r}: expected non-empty string")
    if (
        profile_id in {".", ".."}
        or Path(profile_id).is_absolute()
        or "/" in profile_id
        or "\\" in profile_id
    ):
        raise ProfileError(
            f"invalid profile id {profile_id!r}: ids must not be absolute, "
            "traverse directories, or contain path separators"
        )
    return profile_id


def _profile_path(root: Path, kind: str, profile_id: str) -> Path:
    if kind == "style":
        return root / "style" / profile_id / "profile.json"
    return root / kind / f"{profile_id}.json"


def _find_profile_file(kind: str, profile_id: str, roots: tuple[Path, ...]) -> Path:
    profile_id = _validate_profile_id(profile_id)
    searched = [_profile_path(root, kind, profile_id) for root in roots]
    for path in searched:
        if path.is_file():
            return path
    raise ProfileError(
        f"{kind} profile {profile_id!r} not found; searched: "
        + ", ".join(str(path) for path in searched)
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_style_template(
    *,
    style_dir: Path,
    rel_path: str,
    label: str,
    context: str,
) -> Path:
    candidate = (style_dir / rel_path).resolve()
    style_root = style_dir.resolve()
    if not _is_relative_to(candidate, style_root):
        raise ProfileError(
            f"{context}: {label} template must stay inside style profile directory "
            f"{style_root}; got {candidate}"
        )
    if not candidate.is_file():
        raise ProfileError(f"{context}: {label} template not found: {candidate}")
    return candidate


def list_bundle_ids(root: RootInput = PROFILES_ROOT) -> list[str]:
    """Return visible bundle ids across roots, first root winning collisions."""

    roots = _coerce_roots(root)
    bundle_ids: list[str] = []
    seen: set[str] = set()
    for profiles_root in roots:
        bundles_dir = profiles_root / "bundles"
        if not bundles_dir.is_dir():
            continue
        for path in sorted(bundles_dir.glob("*.json")):
            bundle_id = path.stem
            if bundle_id in seen:
                continue
            seen.add(bundle_id)
            bundle_ids.append(bundle_id)
    return bundle_ids


def _require(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise ProfileError(f"{context}: missing required key '{key}'")
    return data[key]


def _require_hex(value: str, context: str) -> str:
    if not isinstance(value, str) or not _HEX_COLOR.match(value):
        raise ProfileError(f"{context}: expected #RRGGBB hex color, got {value!r}")
    return "#" + value[1:].upper()


def _require_slug(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _ROLE_ID_PATTERN.match(value):
        raise ProfileError(
            f"{context}: expected stable slug matching [a-z0-9][a-z0-9-]{{0,30}}, "
            f"got {value!r}"
        )
    return value


def _string_tuple(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ProfileError(f"{context}: expected list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ProfileError(f"{context}[{index}]: expected string")
        items.append(item)
    return tuple(items)


def _probe_purpose_wrapper(wrapper: str, where: str) -> None:
    if not wrapper:
        return
    try:
        wrapper.format(purpose="probe")
    except (KeyError, IndexError, ValueError) as exc:
        raise ProfileError(
            f"{where}: malformed format string ({exc}); only the {{purpose}} "
            "placeholder is supported"
        ) from exc


def load_atlas_profile(
    profile_id: str, root: RootInput = PROFILES_ROOT
) -> AtlasProfile:
    path = _find_profile_file("atlas", profile_id, _coerce_roots(root))
    data = _read_json(path)
    context = f"atlas profile {profile_id}"

    geometry_data = _require(data, "geometry", context)
    geometry = Geometry(
        columns=int(_require(geometry_data, "columns", f"{context}.geometry")),
        rows=int(_require(geometry_data, "rows", f"{context}.geometry")),
        cell_width=int(_require(geometry_data, "cell_width", f"{context}.geometry")),
        cell_height=int(_require(geometry_data, "cell_height", f"{context}.geometry")),
    )

    dynamic_states_data = data.get("dynamic_states") or None
    dynamic_states: DynamicStatesConfig | None = None
    if dynamic_states_data is not None:
        if not isinstance(dynamic_states_data, dict):
            raise ProfileError(f"{context}.dynamic_states must be an object")
        dynamic_states = DynamicStatesConfig(
            enabled=bool(dynamic_states_data.get("enabled", False)),
            source=str(dynamic_states_data.get("source", "prepare_variants")),
            max_states=int(dynamic_states_data.get("max_states", 12)),
        )
        if dynamic_states.max_states < 1:
            raise ProfileError(
                f"{context}.dynamic_states.max_states must be >= 1"
            )

    states_data = data.get("states", [])
    is_dynamic_template = dynamic_states is not None and dynamic_states.enabled
    if not isinstance(states_data, list):
        raise ProfileError(f"{context}: 'states' must be a list")
    if not states_data and not is_dynamic_template:
        raise ProfileError(f"{context}: 'states' must be a non-empty list")
    if states_data and is_dynamic_template:
        raise ProfileError(
            f"{context}: dynamic atlases must not pre-declare 'states'; "
            "they are materialised from prepare-time variants"
        )

    states: list[StateSpec] = []
    seen_ids: set[str] = set()
    seen_rows: set[int] = set()
    for index, state in enumerate(states_data):
        state_context = f"{context}.states[{index}]"
        state_id = str(_require(state, "id", state_context))
        if state_id in seen_ids:
            raise ProfileError(f"{state_context}: duplicate state id {state_id!r}")
        row = int(_require(state, "row", state_context))
        if row in seen_rows:
            raise ProfileError(f"{state_context}: duplicate row {row}")
        if not (0 <= row < geometry.rows):
            raise ProfileError(
                f"{state_context}: row {row} outside geometry ({geometry.rows} rows)"
            )
        frames = int(_require(state, "frames", state_context))
        if not (1 <= frames <= geometry.columns):
            raise ProfileError(
                f"{state_context}: frames {frames} outside [1, {geometry.columns}]"
            )
        durations = tuple(int(value) for value in _require(state, "durations_ms", state_context))
        if len(durations) != frames:
            raise ProfileError(
                f"{state_context}: durations_ms has {len(durations)} entries but frames={frames}"
            )
        states.append(
            StateSpec(
                id=state_id,
                row=row,
                frames=frames,
                durations_ms=durations,
                purpose=str(_require(state, "purpose", state_context)),
                role=_require_slug(
                    state.get("role", "default"), f"{state_context}.role"
                ),
                is_reduced_motion_first_frame=bool(
                    state.get("is_reduced_motion_first_frame", False)
                ),
            )
        )
        seen_ids.add(state_id)
        seen_rows.add(row)

    derivations_data = data.get("derivations", []) or []
    derivations: list[Derivation] = []
    for index, derivation in enumerate(derivations_data):
        derivation_context = f"{context}.derivations[{index}]"
        target = str(_require(derivation, "target", derivation_context))
        source = str(_require(derivation, "source", derivation_context))
        if target not in seen_ids:
            raise ProfileError(f"{derivation_context}: target {target!r} is not a known state")
        if source not in seen_ids:
            raise ProfileError(f"{derivation_context}: source {source!r} is not a known state")
        method = str(_require(derivation, "method", derivation_context))
        if method not in _VALID_DERIVATION_METHODS:
            raise ProfileError(
                f"{derivation_context}: method {method!r} not in {_VALID_DERIVATION_METHODS}"
            )
        fallback_strategy = str(derivation.get("fallback_strategy", "full-generation"))
        if fallback_strategy not in _VALID_FALLBACK_STRATEGIES:
            raise ProfileError(
                f"{derivation_context}: fallback_strategy {fallback_strategy!r} "
                f"not in {_VALID_FALLBACK_STRATEGIES}"
            )
        derivations.append(
            Derivation(
                target=target,
                source=source,
                method=method,
                requires_explicit_approval=bool(
                    derivation.get("requires_explicit_approval", True)
                ),
                fallback_strategy=fallback_strategy,
            )
        )

    layout_guides_data = data.get("layout_guides") or {}
    layout_guides = LayoutGuides(
        enabled=bool(layout_guides_data.get("enabled", False)),
        safe_margin_x=int(layout_guides_data.get("safe_margin_x", 0)),
        safe_margin_y=int(layout_guides_data.get("safe_margin_y", 0)),
        guide_style=dict(layout_guides_data.get("guide_style", {})),
    )

    return AtlasProfile(
        id=str(_require(data, "id", context)),
        description=str(data.get("description", "")),
        geometry=geometry,
        states=tuple(states),
        derivations=tuple(derivations),
        layout_guides=layout_guides,
        requires_base=bool(data.get("requires_base", True)),
        dynamic_states=dynamic_states,
    )


def load_style_profile(
    profile_id: str, root: RootInput = PROFILES_ROOT
) -> StyleProfile:
    path = _find_profile_file("style", profile_id, _coerce_roots(root))
    style_dir = path.parent
    data = _read_json(path)
    context = f"style profile {profile_id}"

    templates_data = _require(data, "templates", context)
    base_template_rel = str(_require(templates_data, "base", f"{context}.templates"))
    row_template_rel = str(_require(templates_data, "row_strip", f"{context}.templates"))
    base_template_path = _resolve_style_template(
        style_dir=style_dir,
        rel_path=base_template_rel,
        label="base",
        context=context,
    )
    row_template_path = _resolve_style_template(
        style_dir=style_dir,
        rel_path=row_template_rel,
        label="row_strip",
        context=context,
    )
    base_template = base_template_path.read_text(encoding="utf-8")
    row_template = row_template_path.read_text(encoding="utf-8")

    blocks = _require(data, "prompt_blocks", context)
    house_style = str(_require(blocks, "house_style", f"{context}.prompt_blocks"))
    user_join = str(blocks.get("user_style_notes_join", " {user_style_notes}"))

    purpose_wrapper = str(blocks.get("purpose_wrapper", ""))

    overrides_raw = blocks.get("purpose_wrapper_overrides", {})
    if not isinstance(overrides_raw, dict):
        raise ProfileError(
            f"{context}.prompt_blocks.purpose_wrapper_overrides must be an object"
        )
    purpose_wrapper_overrides: dict[str, str] = {
        str(state_id): str(wrapper) for state_id, wrapper in overrides_raw.items()
    }

    _probe_purpose_wrapper(purpose_wrapper, f"{context}.prompt_blocks.purpose_wrapper")
    for state_id, wrapper in purpose_wrapper_overrides.items():
        _probe_purpose_wrapper(
            wrapper,
            f"{context}.prompt_blocks.purpose_wrapper_overrides[{state_id!r}]",
        )

    forbidden = _string_tuple(
        data.get("forbidden_artifacts", []), f"{context}.forbidden_artifacts"
    )

    state_requirements_raw = data.get("state_requirements", {}) or {}
    state_requirements: dict[str, tuple[str, ...]] = {}
    for state_id, requirements in state_requirements_raw.items():
        state_requirements[str(state_id)] = _string_tuple(
            requirements, f"{context}.state_requirements[{state_id!r}]"
        )

    prompt_profile_version = data.get("prompt_profile_version", "legacy")
    if (
        not isinstance(prompt_profile_version, str)
        or not prompt_profile_version.strip()
    ):
        raise ProfileError(f"{context}.prompt_profile_version must be non-empty")
    roles_raw = data.get("roles")
    roles: dict[str, PromptRole] = {}
    if roles_raw is None:
        roles["default"] = PromptRole()
    else:
        if not isinstance(roles_raw, dict):
            raise ProfileError(f"{context}.roles must be an object")
        for raw_role_id, role_data in roles_raw.items():
            role_id = _require_slug(str(raw_role_id), f"{context}.roles key")
            if not isinstance(role_data, dict):
                raise ProfileError(f"{context}.roles[{role_id!r}] must be an object")

            target_kind = role_data.get("target_kind")
            if target_kind is not None and not isinstance(target_kind, str):
                raise ProfileError(
                    f"{context}.roles[{role_id!r}].target_kind must be a string"
                )
            if isinstance(target_kind, str) and not target_kind.strip():
                raise ProfileError(
                    f"{context}.roles[{role_id!r}].target_kind must be non-empty"
                )

            purpose_wrapper_override = role_data.get("purpose_wrapper")
            if purpose_wrapper_override is not None:
                if not isinstance(purpose_wrapper_override, str):
                    raise ProfileError(
                        f"{context}.roles[{role_id!r}].purpose_wrapper must be a string"
                    )
                _probe_purpose_wrapper(
                    purpose_wrapper_override,
                    f"{context}.roles[{role_id!r}].purpose_wrapper",
                )

            requirements = role_data.get("requirements", [])
            requirements_tuple = _string_tuple(
                requirements, f"{context}.roles[{role_id!r}].requirements"
            )

            forbidden_override = role_data.get("forbidden_artifacts")
            if forbidden_override is None:
                forbidden_tuple: tuple[str, ...] | None = None
            else:
                forbidden_tuple = _string_tuple(
                    forbidden_override,
                    f"{context}.roles[{role_id!r}].forbidden_artifacts",
                )

            roles[role_id] = PromptRole(
                target_kind=target_kind,
                purpose_wrapper=purpose_wrapper_override,
                requirements=requirements_tuple,
                forbidden_artifacts=forbidden_tuple,
            )
        if "default" not in roles:
            roles = {"default": PromptRole(), **roles}

    chroma_data = _require(data, "chroma_key", context)
    candidates_data = _require(chroma_data, "candidates", f"{context}.chroma_key")
    if not isinstance(candidates_data, list) or not candidates_data:
        raise ProfileError(f"{context}.chroma_key.candidates must be a non-empty list")
    candidates: list[ChromaKeyCandidate] = []
    for index, candidate in enumerate(candidates_data):
        candidate_context = f"{context}.chroma_key.candidates[{index}]"
        candidates.append(
            ChromaKeyCandidate(
                name=str(_require(candidate, "name", candidate_context)),
                hex=_require_hex(str(_require(candidate, "hex", candidate_context)), candidate_context),
            )
        )
    chroma_key = ChromaKeyConfig(
        selection=str(chroma_data.get("selection", "auto")),
        candidates=tuple(candidates),
    )

    return StyleProfile(
        id=str(_require(data, "id", context)),
        description=str(data.get("description", "")),
        target_kind=str(_require(data, "target_kind", context)),
        house_style=house_style,
        user_style_notes_join=user_join,
        base_template=base_template,
        row_strip_template=row_template,
        forbidden_artifacts=forbidden,
        state_requirements=state_requirements,
        chroma_key=chroma_key,
        extends=data.get("extends") or None,
        purpose_wrapper=purpose_wrapper,
        purpose_wrapper_overrides=purpose_wrapper_overrides,
        prompt_profile_version=prompt_profile_version,
        roles=roles,
    )


def load_extractor_profile(
    profile_id: str, root: RootInput = PROFILES_ROOT
) -> ExtractorProfile:
    path = _find_profile_file("extractor", profile_id, _coerce_roots(root))
    data = _read_json(path)
    context = f"extractor profile {profile_id}"
    return ExtractorProfile(
        id=str(_require(data, "id", context)),
        description=str(data.get("description", "")),
        strategy=str(_require(data, "strategy", context)),
        params=dict(data.get("params", {})),
    )


def load_packager_profile(
    profile_id: str, root: RootInput = PROFILES_ROOT
) -> PackagerProfile:
    path = _find_profile_file("packager", profile_id, _coerce_roots(root))
    data = _read_json(path)
    context = f"packager profile {profile_id}"

    strategy = str(data.get("strategy", "files-and-manifest"))

    files_data = data.get("files", []) or []
    files: list[FileMapping] = []
    for index, mapping in enumerate(files_data):
        mapping_context = f"{context}.files[{index}]"
        files.append(
            FileMapping(
                source=str(_require(mapping, "source", mapping_context)),
                target=str(_require(mapping, "target", mapping_context)),
            )
        )

    manifest_data = data.get("manifest_writer")
    manifest_writer: ManifestWriter | None = None
    if manifest_data is not None:
        manifest_context = f"{context}.manifest_writer"
        manifest_writer = ManifestWriter(
            kind=str(_require(manifest_data, "kind", manifest_context)),
            filename=str(_require(manifest_data, "filename", manifest_context)),
            schema=dict(_require(manifest_data, "schema", manifest_context)),
        )

    if strategy == "files-and-manifest":
        if not files:
            raise ProfileError(
                f"{context}: 'files-and-manifest' strategy requires non-empty 'files'"
            )
        if manifest_writer is None:
            raise ProfileError(
                f"{context}: 'files-and-manifest' strategy requires 'manifest_writer'"
            )

    return PackagerProfile(
        id=str(_require(data, "id", context)),
        description=str(data.get("description", "")),
        output_root=str(_require(data, "output_root", context)),
        strategy=strategy,
        files=tuple(files),
        manifest_writer=manifest_writer,
        params=dict(data.get("params", {})),
    )


def load_bundle(profile_id: str, root: RootInput = PROFILES_ROOT) -> Bundle:
    roots = _coerce_roots(root)
    path = _find_profile_file("bundles", profile_id, roots)
    data = _read_json(path)
    context = f"bundle {profile_id}"
    return Bundle(
        id=str(_require(data, "id", context)),
        description=str(data.get("description", "")),
        atlas=load_atlas_profile(str(_require(data, "atlas", context)), roots),
        style=load_style_profile(str(_require(data, "style", context)), roots),
        extractor=load_extractor_profile(str(_require(data, "extractor", context)), roots),
        packager=load_packager_profile(str(_require(data, "packager", context)), roots),
    )


def load_bundle_for_run(
    run_dir: Path, root: RootInput | None = None
) -> Bundle:
    """Reload a bundle for a prepared run, re-materialising dynamic atlases.

    Downstream commands (extract, finalize) need the same materialised atlas
    that ``prepare_run`` produced. The variants are persisted in the request
    manifest; this helper reads them back and reapplies materialisation.
    Static atlases are returned unchanged.
    """

    request = read_request(run_dir)
    if root is None:
        raw_profile_roots = request.get("profile_roots", [])
        if not isinstance(raw_profile_roots, list):
            raise ProfileError(
                f"request profile_roots in {run_dir} must be an array of root path strings"
            )
        persisted_roots = []
        for index, item in enumerate(raw_profile_roots):
            if not isinstance(item, str) or not item:
                raise ProfileError(
                    f"request profile_roots[{index}] in {run_dir} must be a non-empty string"
                )
            item_path = Path(item)
            if not item_path.is_absolute():
                raise ProfileError(
                    f"request profile_roots[{index}] in {run_dir} must be an absolute path"
                )
            persisted_roots.append(item_path)
        roots = (
            [*normalize_profile_roots(persisted_roots, source="persisted"), PROFILES_ROOT]
            if persisted_roots
            else PROFILES_ROOT
        )
    else:
        roots = root
    bundle = load_bundle(str(request["bundle"]), root=roots)
    if not bundle.atlas.is_dynamic:
        return bundle
    raw_variants = request.get("variants") or []
    variants = [
        VariantSpec(
            id=str(item["id"]),
            purpose=str(item["purpose"]),
            role=item.get("role", "default"),
        )
        for item in raw_variants
    ]
    materialised = materialize_dynamic_atlas(bundle.atlas, variants)
    return Bundle(
        id=bundle.id,
        description=bundle.description,
        atlas=materialised,
        style=bundle.style,
        extractor=bundle.extractor,
        packager=bundle.packager,
    )


def validate_prompt_roles(atlas: AtlasProfile, style: StyleProfile) -> None:
    """Validate that every materialized state names a role the style supports."""

    unknown = sorted(
        {state.role for state in atlas.states if state.role not in style.roles}
    )
    if unknown:
        raise ProfileError(
            f"atlas {atlas.id!r} uses prompt role(s) not defined by style "
            f"{style.id!r}: {', '.join(unknown)}"
        )
