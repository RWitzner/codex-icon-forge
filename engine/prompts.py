"""Prompt composition from style + atlas profiles.

Style profiles own the prompt templates; this module substitutes runtime
values and applies profile-level purpose wrapping for row prompts.
"""

from __future__ import annotations

from .profiles import AtlasProfile, StateSpec, StyleProfile


def compose_style_notes(style: StyleProfile, user_style_notes: str = "") -> str:
    user_style_notes = user_style_notes.strip()
    if not user_style_notes:
        return style.house_style
    suffix = style.user_style_notes_join.format(user_style_notes=user_style_notes)
    return style.house_style + suffix


def _state_requirement_text(style: StyleProfile, state_id: str) -> str:
    requirements = style.state_requirements.get(state_id, ())
    if not requirements:
        return ""
    bullets = "\n".join(f"- {requirement}" for requirement in requirements)
    return "\n\nState-specific requirements:\n" + bullets


def _transparency_artifact_text(style: StyleProfile) -> str:
    return "\n".join(f"- {item}" for item in style.forbidden_artifacts)


def compose_base_prompt(
    style: StyleProfile,
    atlas: AtlasProfile,
    *,
    display_name: str,
    entity_notes: str,
    chroma_key_name: str,
    chroma_key_hex: str,
    user_style_notes: str = "",
) -> str:
    style_notes = compose_style_notes(style, user_style_notes)
    return style.base_template.format(
        target_kind=style.target_kind,
        display_name=display_name,
        entity_notes=entity_notes,
        style_notes=style_notes,
        chroma_key_name=chroma_key_name,
        chroma_key_hex=chroma_key_hex,
        cell_width=atlas.geometry.cell_width,
        cell_height=atlas.geometry.cell_height,
    )


def _resolve_purpose(style: StyleProfile, state: StateSpec) -> str:
    """Apply style.purpose_wrapper to state.purpose, honouring per-state overrides.

    If state.id is listed in style.purpose_wrapper_overrides, that override
    wins (whether empty or non-empty). An empty override disables wrapping
    entirely for that state — used when state_requirements mandates a
    fundamentally different render than the default wrapper assumes.
    """
    if state.id in style.purpose_wrapper_overrides:
        override = style.purpose_wrapper_overrides[state.id]
        if not override:
            return state.purpose
        return override.format(purpose=state.purpose)
    if style.purpose_wrapper:
        return style.purpose_wrapper.format(purpose=state.purpose)
    return state.purpose


def compose_row_prompt(
    style: StyleProfile,
    atlas: AtlasProfile,
    state: StateSpec,
    *,
    entity_id: str,
    entity_notes: str,
    chroma_key_name: str,
    chroma_key_hex: str,
    user_style_notes: str = "",
    extra_requirement_text: str = "",
) -> str:
    style_notes = compose_style_notes(style, user_style_notes)
    return style.row_strip_template.format(
        target_kind=style.target_kind,
        entity_id=entity_id,
        state=state.id,
        frames=state.frames,
        purpose=_resolve_purpose(style, state),
        entity_notes=entity_notes,
        style_notes=style_notes,
        state_requirement_text=_state_requirement_text(style, state.id),
        extra_requirement_text=extra_requirement_text,
        transparency_artifact_text=_transparency_artifact_text(style),
        chroma_key_name=chroma_key_name,
        chroma_key_hex=chroma_key_hex,
        cell_width=atlas.geometry.cell_width,
        cell_height=atlas.geometry.cell_height,
    )
