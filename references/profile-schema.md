# Profile schema reference

The icon-forge engine loads five profile types from the `profiles/` tree.
All profiles are JSON. Loaders live in `engine/profiles.py`; the dataclasses
on this page are the canonical contract.

## Layout on disk

```
profiles/
├── atlas/<id>.json
├── style/<id>/
│   ├── profile.json
│   └── templates/
│       ├── base-*.txt
│       └── row-*.txt
├── extractor/<id>.json
├── packager/<id>.json
└── bundles/<id>.json
```

## Atlas profile

`profiles/atlas/<id>.json` — drives geometry, the state catalog, derivation rules, and layout-guide rendering.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Must match filename |
| `description` | string | Free text |
| `geometry.columns` | int | Atlas grid width in cells |
| `geometry.rows` | int | Atlas grid height in cells |
| `geometry.cell_width` | int | Pixels per cell |
| `geometry.cell_height` | int | Pixels per cell |
| `states[]` | array | One entry per animation state. Each state owns one whole row |
| `states[].id` | string | Unique within profile |
| `states[].row` | int | Unique within profile, in `[0, geometry.rows)` |
| `states[].frames` | int | In `[1, geometry.columns]` |
| `states[].durations_ms` | array of int | Length must equal `frames` |
| `states[].purpose` | string | Used in row-prompt substitution |
| `states[].is_reduced_motion_first_frame` | bool, optional | Defaults to `false` |
| `derivations[]` | array, optional | Source→target mirror rules |
| `derivations[].target` | string | Existing state id |
| `derivations[].source` | string | Existing state id |
| `derivations[].method` | string | `horizontal-mirror` or `vertical-mirror` |
| `derivations[].requires_explicit_approval` | bool | Defaults to `true` |
| `derivations[].fallback_strategy` | string | `full-generation` or `error` |
| `layout_guides.enabled` | bool | Whether to render layout-guide images |
| `layout_guides.safe_margin_x` | int | Inner safe-area inset in pixels |
| `layout_guides.safe_margin_y` | int | Same |
| `layout_guides.guide_style` | object | Color hex strings: `background`, `cell_border`, `safe_border`, `center_dashes` |

## Style profile

`profiles/style/<id>/profile.json` — drives prompt strings and visual rules.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Must match dirname |
| `description` | string | Free text |
| `extends` | string or null | Reserved for future template inheritance; not yet implemented |
| `target_kind` | string | Substituted into prompt templates as `{target_kind}` |
| `templates.base` | string | Path relative to the style dir, points at the base prompt template `.txt` file |
| `templates.row_strip` | string | Same, for the row prompt template |
| `prompt_blocks.house_style` | string | Substituted as `{style_notes}` when no user style notes are given |
| `prompt_blocks.user_style_notes_join` | string | Suffix template; substitutes `{user_style_notes}` |
| `forbidden_artifacts[]` | array of string | Joined as `- bullet` lines into `{transparency_artifact_text}` |
| `state_requirements{}` | object | Map of `state_id -> array of string` bullets. Empty for stickers. |
| `chroma_key.selection` | string | `auto` or `manual` |
| `chroma_key.candidates[]` | array | `{name, hex}` entries with hex like `#RRGGBB` |

### Template substitution variables

Available to both base and row templates:

| Variable | Source |
|---|---|
| `{target_kind}` | Style profile |
| `{display_name}` | Runtime input |
| `{entity_notes}` | Runtime input (free-text description) |
| `{style_notes}` | Computed: `house_style` plus optional user-style-notes suffix |
| `{chroma_key_name}`, `{chroma_key_hex}` | Computed from chroma-key candidate |
| `{cell_width}`, `{cell_height}` | Atlas geometry |

Row template only:

| Variable | Source |
|---|---|
| `{entity_id}` | Runtime input (slug) |
| `{state}` | Atlas state id |
| `{frames}` | Atlas state frame count |
| `{purpose}` | Atlas state purpose |
| `{state_requirement_text}` | Computed bullet list, empty if state has no requirements |
| `{transparency_artifact_text}` | Computed bullet list of `forbidden_artifacts` |

## Extractor profile

`profiles/extractor/<id>.json` — names a registered extraction strategy plus its parameters.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Must match filename |
| `description` | string | Free text |
| `strategy` | string | Registered name: `chroma-key-components` or `slot-only` |
| `params` | object | Strategy-specific parameters (see below) |

### Strategy: `chroma-key-components`

Removes a flat chroma-key background, finds connected visual components, and groups them into N frame slots. Falls back to equal slot crops if components cannot be cleanly separated.

| Param | Default | Notes |
|---|---|---|
| `key_threshold` | `96.0` | Euclidean RGB distance for chroma matching |
| `component_seed_min_area` | `120` | Min pixel count for a component to be considered a frame seed |
| `component_seed_relative_threshold` | `0.20` | Or this fraction of the largest component's area |
| `noise_relative_threshold` | `0.002` | Components below this fraction of the largest area are dropped |
| `component_padding` | `4` | Pixel padding around extracted components |
| `min_used_pixels` | `50` | Validator: min non-transparent pixel count for a "used" cell |
| `near_opaque_threshold` | `0.95` | Validator: cells above this fraction of opaque pixels trigger a warning/error |
| `fallback_method` | `slots` | What to do if components extraction fails |

### Strategy: `slot-only`

Equal-width slot crops with no chroma-key removal. Suitable for styles where the model emits transparent PNG output directly.

Same `min_used_pixels` and `near_opaque_threshold` validator parameters as above; for sticker packs that fill their cell, set `near_opaque_threshold` above `1.0` to disable the check.

## Packager profile

`profiles/packager/<id>.json` — names a registered packaging strategy plus its configuration.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Must match filename |
| `description` | string | Free text |
| `strategy` | string | Registered name: `files-and-manifest` or `atlas-extract-folder` |
| `output_root` | string | Output directory template — supports `${VAR:-default}` env-style expansion plus `{entity_id}` / `{display_name}` / `{description}` |
| `files[]` | array, optional | For `files-and-manifest`: source/target relative paths |
| `manifest_writer` | object, optional | For `files-and-manifest`: `kind`, `filename`, `schema` (templated dict) |
| `params` | object, optional | Strategy-specific parameters |

### Strategy: `files-and-manifest`

Copies (or converts between PNG/WebP) files from the run directory to the output directory, then writes a JSON manifest from a templated schema.

Required fields: `files`, `manifest_writer`.

### Strategy: `atlas-extract-folder`

Reads the composed atlas from `run_dir/final/spritesheet.{webp,png}`, crops each cell using the atlas profile's state catalog, and writes one image per state into the output directory. Optionally renders a README from a template.

| Param | Default | Notes |
|---|---|---|
| `image_format` | `PNG` | Output format per cell — `PNG` or `WEBP` |
| `readme_filename` | unset | If set, render a README into this filename |
| `readme_template` | unset | Template string with `{display_name}`, `{description}`, `{state_list}`, `{sticker_count}`, `{bundle_id}`, `{entity_id}` substitutions |

The `{state_list}` substitution renders one `- :state-id: → state-id.ext (purpose)` line per state.

## Bundle

`profiles/bundles/<id>.json` — names one of each profile type.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Must match filename |
| `description` | string | Free text |
| `atlas` | string | Atlas profile id |
| `style` | string | Style profile id |
| `extractor` | string | Extractor profile id |
| `packager` | string | Packager profile id |
