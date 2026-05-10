---
name: icon-forge
description: Generate icon packs, sticker packs, static game tile sheets, and iOS in-app symbol PNG fallbacks through configurable bundle profiles. Use for slack-stickers, app-icons, app-icon-set, ios-button-icons, and game-tiles.
---

# Icon Forge

AI-powered icon and sticker pack pipeline. Same generation engine, multiple entry points: each bundle is a specific design product with its own geometry, prompt style, extraction, and packaging.

| Axis | Controls | Profile path |
|---|---|---|
| Atlas | Cell geometry, state catalog, derivations | `profiles/atlas/<id>.json` |
| Style | Target kind, prompt templates, forbidden artifacts, chroma key candidates | `profiles/style/<id>/profile.json` |
| Extractor | Background removal + frame extraction strategy | `profiles/extractor/<id>.json` |
| Packager | Output layout strategy | `profiles/packager/<id>.json` |
| Bundle | Names one of each above | `profiles/bundles/<id>.json` |

## Bundles shipped

- **`slack-stickers`** — user-defined 1–12 sticker pack, each sticker rendered in a 128x128 cell. Variants are supplied at prepare time via `--variant id:purpose` (one per sticker). Output: one transparent PNG per variant plus a `README.md` with Slack import instructions. Style is `flat-vector` (bold simple shapes, thick outlines, limited palette). The canonical dev-pack preset (`shipping-it`, `tests-passing`, `merge-conflict`, …) lives in README.md as 12 ready-to-paste `--variant` strings.
- **`app-icons`** — one icon design rendered at 8 platform sizes (16, 32, 64, 128, 180, 256, 512, 1024). Output: 8 sized PNGs plus a README mapping each size to its platform use (iOS App Store, Android Play Store, web favicon, apple-touch-icon, Slack workspace icon). Style is `launcher-tile` (full-colour app launcher tile, hardened against feature-glyph leakage, readable from 16x16).
- **`app-icon-set`** — user-defined family of 1–12 distinct icon designs (e.g. main + share-extension + watch + notification), each rendered at all 8 platform sizes. Variants are supplied at prepare time via `--variant id:purpose`; each variant is generated independently by its own subagent, then fan'ed out to subfolders. Output: `<entity>/<variant>/<variant>-<size>.png` plus a family README.
- **`ios-button-icons`** - user-defined family of 1-12 iOS in-app symbols (tab bar, toolbar, button, list-row glyphs), each rendered as monochrome transparent PNG fallbacks at 24pt and 25pt in @1x/@2x/@3x scale variants (24, 48, 72px and 25, 50, 75px). Variants are supplied at prepare time via `--variant id:purpose`. Style is `ios-symbol` (SF Symbols-style proportions, tintable, no launcher tile).
- **`game-tiles`** - user-defined 1-12 static game-world tiles, each rendered as an opaque/full-bleed 256x256 tile. Output: one compact `tilesheet.png`, individual `tiles/<id>.png` files, `manifest.json`, and README. Uses `slot-only` because terrain tiles should preserve edge pixels.

Add more bundles by authoring profile JSONs — no engine code changes required for typical new products.

## When to use this skill

| Goal | Use |
|---|---|
| 1–12 transparent stickers/emojis for Slack, Discord, Mattermost | `slack-stickers` bundle with `--variant id:purpose` per sticker (or paste the dev-pack preset) |
| Single app icon at all platform sizes | `app-icons` bundle |
| Family of distinct app icons (main + alternates, share-ext, watch, notification, light/dark) each at all sizes | `app-icon-set` bundle with `--variant id:purpose` per design |
| iOS in-app symbols for tab bars, buttons, toolbars, list rows | `ios-button-icons` bundle with `--variant id:purpose` per glyph |
| Static game-world tile sheets for terrain, floors, walls, maps | `game-tiles` bundle with `--variant id:purpose` per tile |
| New icon-family product (favicon pack, social avatar set, logo variations) | Author a new bundle with the existing engine |
| Animated sprite sheets or game character atlases | Use a separate sprite/animation-focused skill; icon-forge is for static icon and sticker products |

## STOP — before `prepare`: disambiguate iOS icon intent

"iOS icons" is ambiguous. Before picking a bundle, decide which of two products the user means:

- **Launcher icons** — the square Home Screen tile users tap to open the app. One per app, plus optional alternate launchers, share-extension icon, watchOS variant, notification silhouette, light/dark pairs. Square geometry, 1024×1024 master, full colour, opaque background allowed.
- **In-app symbols** — monochrome SF-Symbols-style glyphs used inside the app on tab bars, buttons, list rows, navigation chrome. Many per app, transparent, point-based sizes (24/32/48/60), silhouette-friendly.

**Trigger to ask:** the user says "iOS app icons", "icons for my app", "icon set", or names variants that sound like in-app features (`journal`, `breathing`, `support`, `home`, `settings`, `profile`, `search`, etc.) rather than launcher purposes.

**Skip the question** when the user has explicitly said "launcher icon", "Home Screen icon", "alternate app icons", or supplied variant IDs that match launcher-purpose patterns (`main`, `alternate-1`, `share-ext`, `watch`, `notification`, `light`, `dark`).

**Question to ask:**

> Quick check before I run: do you want **(A) launcher icons** — the Home Screen tiles users tap to open the app — or **(B) in-app symbols** — monochrome glyphs used on tab bars, buttons, and list rows inside the app?

**Routing on the answer:**

| Answer | Route |
|---|---|
| (A) launcher, single design | `app-icons` bundle |
| (A) launcher, family (main + alternates / share-ext / watch / notification / light/dark) | `app-icon-set` bundle |
| (B) in-app symbols | `ios-button-icons` bundle with `--variant id:purpose` per glyph. **Do not** fake it by running `app-icon-set` with feature-named variants - the launcher-style prompts produce full-colour square tiles, not silhouette glyphs. |

## STOP - before `prepare`: disambiguate tiles

"Tiles" can mean different products. If the user asks for game-world terrain/floor/wall/map tiles, use `game-tiles`. If they ask for app launcher tiles, use `app-icons` or `app-icon-set`. If they ask for stickers or emoji tiles, use `slack-stickers`. If they ask for animated character sheets, do not use `game-tiles`; use a sprite/animation-focused skill.

## Default workflow (concept to packaged output)

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/icon-forge"
```

**Run location rule.** Omit `--output-dir` and the script picks `${PWD}/output/icon-forge/<entity-id>-<UTC-timestamp>` for you — i.e. the current working directory the user invoked you from. Only pass `--output-dir` when the user explicitly names a different path. Never default to `~/Downloads`, `~/Desktop`, `$CODEX_HOME`, or `/tmp`.

1. **Prepare** the run folder.

   ```bash
   python "$SKILL_DIR/scripts/icon_forge.py" prepare \
     --bundle <bundle-id> \
     --entity-id <slug> \
     --display-name "<Display Name>" \
     --description "<one sentence>" \
     --notes "<stable visual description used in every prompt>" \
     --style-notes "<optional user style overrides>" \
     --reference /absolute/path/to/reference.png
   ```

   Omit `--output-dir`; the script returns the chosen `run_dir` in its JSON output. Capture that path and pass it as `--run-dir` to the downstream commands (`status`, `record`, `derive`, `extract`). Pass `--output-dir <path>` only when the user named one explicitly.

   Writes `request.json`, `prompts/`, `references/`, and `imagegen-jobs.json` listing every visual job with dependencies and input images.

   ```bash
   RUN_DIR=$(python "$SKILL_DIR/scripts/icon_forge.py" prepare ... | python -c 'import json,sys; print(json.load(sys.stdin)["run_dir"])')
   ```

2. **Inspect** ready jobs.

   ```bash
   python "$SKILL_DIR/scripts/icon_forge.py" status --run-dir "$RUN_DIR"
   ```

3. **Generate** each ready job through `$imagegen`. For atlases with `requires_base: true`, the base job runs first as the canonical identity reference. For all atlases (whether they declare a base or not), the remaining state jobs run **in sequence**: generate the first state job, pause at the first-result approval gate (below), then fan out the rest. Each row job must attach its listed input images.

   **First-result approval gate (multi-job bundles only).** After recording the first job in a multi-job bundle (e.g. the first variant in `app-icon-set`, or the first sticker in `slack-stickers`), **stop and show the user the `decoded_path` returned by `record`** (e.g. `decoded/main.png`). Wait for an explicit `approve` before generating remaining jobs. Catching style or intent errors at job 1 of N is far cheaper than discovering them after job N of N. Single-job bundles like `app-icons` are no-ops. On `regenerate`, re-run the same job and re-record with `--force`. On `abort`, leave the run dir as-is for inspection.

4. **Record** each completed generation.

   ```bash
   python "$SKILL_DIR/scripts/icon_forge.py" record \
     --run-dir "$RUN_DIR" \
     --job-id <id> \
     --source /absolute/path/to/$CODEX_HOME/generated_images/.../ig_*.png
   ```

   For base jobs (atlases that declare `requires_base: true`; none of the three shipped bundles do, but external bundles may) this also writes `references/canonical-base.png` so subsequent row jobs use it as identity reference. The record step is concurrency-safe: a sibling lock file serialises parallel calls so no manifest update is dropped.

5. **Derive** any mirror states (rare for icon bundles; common for animated sprites).

   ```bash
   python "$SKILL_DIR/scripts/icon_forge.py" derive \
     --run-dir "$RUN_DIR" \
     --target <state-id> \
     --decision-note "<why mirroring preserves identity>"
   ```

6. **Extract** decoded strips into per-state frame directories.

   ```bash
   python "$SKILL_DIR/scripts/icon_forge.py" extract \
     --run-dir "$RUN_DIR" \
     --states all
   ```

7. **Finalize** — compose, validate, package.

   ```bash
   python "$SKILL_DIR/scripts/icon_forge.py" finalize \
     --bundle <bundle-id> \
     --frames "$RUN_DIR/frames" \
     --entity-id <slug> \
     --display-name "<Display Name>" \
     --description "<one sentence>" \
     --output-run-dir "$RUN_DIR" \
     --icon-forge-home "${ICON_FORGE_HOME:-$HOME/icon-forge}"
   ```

   Output goes to `${ICON_FORGE_HOME:-$HOME/icon-forge}/<bundle-output>/<slug>/`. Each bundle decides its own subpath (sticker bundles write to `stickers/<slug>/`, app-icon bundles write to `app-icons/<slug>/`, iOS symbol bundles write to `ios-button-icons/<slug>/`, game tile bundles write to `game-tiles/<slug>/`).

## Subagent row generation

For bundles with many parallel-eligible jobs (e.g. `slack-stickers` with 1-12 user-defined stickers, `app-icon-set` with multiple variants, `ios-button-icons` with multiple symbols, or `game-tiles` with multiple tiles), fan out generation to subagents. The parent agent owns the manifest and recording; subagents only produce candidate images.

Default flow:

1. Parent runs `prepare`, then runs `status` to see ready jobs.
2. For atlases with `requires_base: true`, parent generates and records the base job first.
3. **First-result human approval** (multi-job bundles only). After recording the first job's decoded output, before spawning row subagents for the rest, present its path to the user:

   > ```
   > First job recorded: <absolute path to decoded/<state-id>.png>
   > Open it and confirm the style and intent are correct before I fan out the remaining N-1 jobs.
   > Reply: `approve` to continue · `regenerate` to retry this job · `abort` to stop.
   > ```

   Only proceed after `approve`. On `regenerate`, re-record the job with `--force`. On `abort`, leave the run dir as-is for inspection. Single-job bundles like `app-icons` skip this step.
4. Parent spawns subagents for the remaining ready jobs (the N-1 jobs left after the first-result approval).
5. Each subagent generates one image with `$imagegen` and returns only the selected source path.
6. Parent runs `record` for each returned source. The lock file makes parallel record calls safe.
7. Parent runs `derive`, `extract`, and `finalize`.

**MANDATORY parallelism question for 8+ parallel-eligible jobs.** When *any* bundle runs with 8 or more parallel-eligible jobs (e.g. `slack-stickers` with 8+ stickers, `app-icon-set` with 8+ variants, `ios-button-icons` with 8+ symbols, or `game-tiles` with 8+ tiles), **after the first-result approval and before generating job 2 of N, halt and ask the user explicitly** whether to fan out to 2 subagents or run sequentially. Do not autonomously decide - neither default to fan-out nor fall back to sequential without an explicit answer.

**Question to ask (verbatim):**

> ```
> I have N-1 jobs left to generate. For an 8+ run I need an explicit choice before I continue:
> - `parallel`   — split across 2 subagents (≈half the wall time; per-image quality independent across jobs)
> - `sequential` — run them one-by-one in this agent
> ```

Only proceed after the answer. If the user replies `parallel` but subagent spawning is unavailable in this environment, **surface that constraint and ask** whether to (a) run sequentially anyway or (b) pause so they can grant delegated agent permissions and resume. **Do not** announce a constraint and continue sequentially as a fallback — that's the failure mode this gate exists to prevent.

Batching is allowed: for an 8-job run, two subagents × 4 jobs; for 12, two × 6. Per-image quality is independent across jobs, so batching carries no quality cost; the manifest lock guarantees parallel record safety; sequential generation is NOT required for provenance. Smaller multi-job runs (any bundle with <8 parallel-eligible jobs) may run sequentially without flagging.

Subagent write boundary: subagents must not edit `imagegen-jobs.json`, copy files into `decoded/`, run `record`, run `derive`, run `extract`, or run `finalize`. This avoids manifest races and keeps provenance checks centralised.

Provenance enforcement: `record` rejects any source path that is not `$CODEX_HOME/generated_images/.../ig_*.png`, and any path that lives inside the run directory itself. Locally drawn or post-processed images cannot be ingested as visual job outputs. The hidden `--allow-synthetic-test-source` flag bypasses the check for unit tests only — never use it in real runs.

Overwrite guard: `record` refuses to replace a job's existing decoded output unless `--force` is passed. This prevents a stale subagent result, a double-record bug, or a parallel race from silently overwriting an already-approved image. Re-recording after an explicit regenerate is a one-flag operation.

Package overwrite semantics: when `finalize` is run with `--force`, folder-style packagers remove the existing entity output directory before writing new files. This prevents stale files from previous package runs from remaining in public output.

Subagent handoff template (drop in the row id, prompt path, and input image list from `imagegen-jobs.json`):

```text
Generate the `<row-id>` job for this icon-forge run.

Run dir: <absolute run dir>
Bundle: <bundle-id>
Prompt file: <absolute prompt file>
Input images:
- <absolute path> — <role>
- <absolute path> — <role>

Read and follow the row prompt exactly, including its style and transparency rules. Use `$imagegen` only; do not use local scripts to draw, tile, edit, or synthesize images.

Before returning, visually check:
- exact requested frame count
- consistent style with the bundle's style profile
- clean flat chroma-key background matching `request.json`
- complete, separated, unclipped design
- no forbidden artifacts described in the prompt

Do not edit manifests, copy into decoded, record results, derive states, extract frames, or finalize. Return only:
selected_source=/absolute/path/to/$CODEX_HOME/generated_images/.../ig_*.png
qa_note=<one sentence>
```

## Dynamic Slack sticker packs (`slack-stickers`)

`slack-stickers` produces 1–12 independent Slack emoji/sticker motifs in the `flat-vector` style on a chroma-key background. Always supply one `--variant id:purpose` per sticker. The `id` becomes both the Slack emoji shortcode and the output PNG filename; the `purpose` is the visual concept the prompt template uses for that sticker.

**Generating variants from a user theme.** When the user describes a theme (e.g. "stickers for our book club", "stand-up daily mood pack", "5 cat reactions"), derive a coherent set of short slug IDs and concrete visual purposes from that theme:

- `id` matches `^[a-z0-9][a-z0-9-]{0,30}$` — slug-style, lowercase, hyphenated, ≤31 chars
- `purpose` must be a **concrete visual concept**, not a mood label. `book-club:cosy hardcover with steaming mug` is good; `book-club:bookish vibes` is not. The image model needs a single iconic shape it can draw at 128×128.
- Keep every purpose readable at thumbnail size: one subject, simple pose, no text, no scenery, no multi-panel jokes
- IDs must be unique within the run

**How many.** If the user gives an explicit count, use exactly that. If the user names individual motifs, count them and use that. Otherwise default to **8**. Vague qualifiers like "lots", "a full set", or "as many as possible" do not justify scaling above 8 — ask the user for a count instead. Only the explicit dev-pack shortcut (below) selects 12.

**Disambiguation prompt.** When the user just says "make me a sticker pack" with no theme, ask once:

> What should the stickers be about? Give me a theme (e.g. "developer workflow", "daily standup moods", "coffee shop reactions") or paste a list of motifs. I'll default to 8 stickers unless you say otherwise.

Do NOT silently fall back to the dev-pack — the result will surprise non-developer users. Only use the dev-pack preset on explicit request (see "Dev-pack shortcut" below).

**Dev-pack shortcut.** If the user asks for "the dev-pack", "developer workflow stickers", "the example Slack sticker pack", or "the canonical icon-forge Slack stickers", use the documented preset verbatim from README.md ("Recommended preset: dev-pack" section). Twelve variants: `shipping-it`, `tests-passing`, `merge-conflict`, `ci-failed`, `deploy`, `hotfix`, `retry`, `lgtm`, `wip`, `debug`, `refactor`, `ship`.

**Prepare example:**

```bash
python "${SKILL_DIR}/scripts/icon_forge.py" prepare \
  --bundle slack-stickers \
  --entity-id book-club \
  --display-name "Book Club" \
  --description "Stickers for our monthly book club Slack." \
  --notes "warm cosy library aesthetic, autumn palette" \
  --variant "first-chapter:open hardcover with crisp pages, eager energy" \
  --variant "plot-twist:exclamation-shaped bookmark snapping into place" \
  --variant "bad-ending:closed book with one wilted flower on cover" \
  --variant "tea-break:steaming mug next to a paperback" \
  --variant "lurking:single eye peeking over a tall book stack" \
  --variant "must-read:book wrapped in glowing star ribbon" \
  --variant "unread-pile:tall teetering tower of unopened books" \
  --variant "next-meeting:calendar page with a single bookmark"
```

The rest of the workflow (`status`, `record`, `extract`, `finalize`) is identical to the other bundles. Variant IDs are persisted in `request.json`, so downstream commands reload the materialised atlas automatically.

## Dynamic icon families (`app-icon-set`)

Some products need several distinct icon designs that all ship together — main app icon plus alternate icons, a share-extension icon, a watchOS variant, a notification silhouette, light/dark pairs. The `app-icon-set` bundle handles that with up to 12 user-defined variants per run, each generated by its own subagent and fan'ed out to all 8 platform sizes.

> **Before you run `app-icon-set`:**
> - If the user's variant IDs sound like in-app features (tabs, buttons, sections) rather than launcher purposes → see "STOP — before `prepare`: disambiguate iOS icon intent" above. `app-icon-set` produces full-colour square Home Screen tiles, not in-app symbols.
> - The `launcher-tile` style profile re-frames every variant purpose declaratively (via `purpose_wrapper`) so feature-named variants cannot dominate the prompt. You do **not** need to re-write `--variant id:purpose` strings yourself; pass them through verbatim.
> - `watch`, `notification`, `light`, and `dark` variant IDs receive automatic per-variant overrides (monochrome silhouette for watch/notification; full-colour tile with explicit light/dark intent for light/dark). Use those exact IDs to opt in.
> - After the first variant is recorded → pause for human approval before fanning out to the remaining variants. See step 3 of "Default workflow" and step 3 of "Subagent row generation".

Prepare with one `--variant id:purpose` per icon design:

```bash
python "${SKILL_DIR}/scripts/icon_forge.py" prepare \
  --bundle app-icon-set \
  --entity-id myapp \
  --display-name "MyApp" \
  --description "MyApp icon family" \
  --notes "modern minimalist, bold silhouette" \
  --output-dir "${RUN_DIR}" \
  --variant "main:primary app icon" \
  --variant "share-ext:share extension, simpler version" \
  --variant "watch:1-bit silhouette for watchOS" \
  --force
```

Variant ID rules (validated at prepare time):

- 1–12 variants per run; pass `--variant` repeatedly
- ID matches `^[a-z0-9][a-z0-9-]{0,30}$` (slug-style)
- IDs must be unique within the run
- Purpose is required and at most 200 chars

The rest of the workflow is identical to other bundles: parent generates each variant via `$imagegen` (or fans them out to subagents), parent records each result with `record`, then `extract` and `finalize`. The packager writes `${ICON_FORGE_HOME}/app-icon-sets/<entity-id>/<variant>/<variant>-<size>.png` for every (variant, size) pair plus a family README.

For a single icon at all sizes, use the simpler `app-icons` bundle — `app-icon-set` is overkill for one design.

## Static game tile sheets (`game-tiles`)

Use `game-tiles` for static terrain, floor, wall, and world tiles for games. The first version is optimized for opaque/full-bleed 256x256 tiles and uses `slot-only`, not chroma-key cleanup.

Example:

```bash
python "${SKILL_DIR}/scripts/icon_forge.py" prepare \
  --bundle game-tiles \
  --entity-id forest-ruins \
  --display-name "Forest Ruins" \
  --description "Mossy top-down terrain tiles for a ruined forest map." \
  --variant "grass:seamless mossy grass floor tile" \
  --variant "stone:cracked stone floor tile" \
  --variant "water:shallow blue water tile" \
  --variant "sand:dry sand path tile"
```

Each variant becomes one tile. Keep purposes concrete and tile-shaped: `seamless mossy grass floor tile`, `cracked stone floor tile`, `shallow blue water tile`. Avoid character poses, icon metaphors, UI labels, scene descriptions, and animated states.

Tile QA:

- matches the variant purpose
- readable and useful at 256x256
- visually consistent with the first approved tile
- no text, labels, UI, visible grid, borders, or watermarks
- no scene frame, launcher-icon treatment, sticker treatment, or transparent padding
- full-bleed opaque output is preserved
- if the purpose says seamless or tileable, opposite edges should not obviously break the pattern

For multi-tile `game-tiles` runs, the first approved tile becomes the canonical style reference. After recording and user approval of the first tile, run:

```bash
python "$SKILL_DIR/scripts/icon_forge.py" promote-reference \
  --run-dir "$RUN_DIR" \
  --job-id <first-approved-tile-id>
```

Before this promotion step, only the first tile should appear in `status.ready_jobs`; non-seed jobs are blocked by the canonical-reference sentinel. Do not generate remaining tiles until this promotion step has updated `imagegen-jobs.json` and removed the sentinel dependency.

After all tile jobs are recorded and before finalize, run:

```bash
python "$SKILL_DIR/scripts/icon_forge.py" review-tiles --run-dir "$RUN_DIR"
```

For every generated tile, persist the subagent note and parent decision:

```bash
python "$SKILL_DIR/scripts/icon_forge.py" record-qa \
  --run-dir "$RUN_DIR" \
  --job-id <tile-id> \
  --selected-source <generated-image-path> \
  --subagent-note "<subagent qa_note>" \
  --parent-decision accepted \
  --parent-note "<why this tile is accepted>"
```

Review `qa/contact-sheet.png` and `qa/review.json`. Treat `qa/review.json` errors as blockers. Warnings require visual review. When the contact sheet is acceptable, explicitly approve the review:

```bash
python "$SKILL_DIR/scripts/icon_forge.py" approve-review \
  --run-dir "$RUN_DIR" \
  --note "cross-tile style, palette, and path geometry accepted"
```

Do not call the run done unless `qa/review.json` covers every tile, has matching decoded hashes, has `approved: true`, and cross-tile style, palette, and path geometry are coherent.

## Dynamic iOS in-app symbols (`ios-button-icons`)

Use `ios-button-icons` when the user wants custom glyphs inside an iOS app: tab bars, toolbar buttons, list rows, settings rows, empty-state actions, or navigation controls. These are monochrome, transparent, tintable UI symbols. They are not Home Screen launcher icons.

Prepare with one `--variant id:purpose` per glyph:

```bash
python "${SKILL_DIR}/scripts/icon_forge.py" prepare \
  --bundle ios-button-icons \
  --entity-id myapp \
  --display-name "MyApp" \
  --description "MyApp in-app symbol family" \
  --notes "calm productivity app, rounded monoline glyph language" \
  --variant "search:magnifying glass for search tab" \
  --variant "settings:simple gear for settings button" \
  --variant "journal:open notebook for journal tab"
```

Variant rules are the same as other dynamic bundles: 1-12 variants per run, IDs match `^[a-z0-9][a-z0-9-]{0,30}$`, IDs are unique, and purpose is required. Keep each purpose to one simple symbol concept; do not ask for full scenes, coloured app tiles, or multi-object compositions.

The packager writes `${ICON_FORGE_HOME:-$HOME/icon-forge}/ios-button-icons/<entity-id>/<variant>/<variant>-<size>.png` for 24pt and 25pt @1x/@2x/@3x raster fallbacks (24, 48, 72px and 25, 50, 75px) plus a family README.

## Authoring a new bundle

A new icon product is normally five JSON files plus two prompt templates, no engine code change.

1. **Atlas** (`profiles/atlas/<id>.json`) — geometry, state catalog. For most icon products, one or N states with `frames: 1`.
2. **Style** (`profiles/style/<id>/profile.json` plus `templates/`) — `target_kind`, prompt templates (`base` and `row_strip`), `forbidden_artifacts`, `chroma_key.candidates`.
3. **Extractor** (`profiles/extractor/<id>.json`) — typically `chroma-key-slots` for images on chroma-key backgrounds, `slot-only` if the model emits transparent PNG directly.
4. **Packager** (`profiles/packager/<id>.json`) — pick a registered strategy:
   - `atlas-extract-folder` for sticker-style packs (one PNG per state plus a README)
   - `multi-size-folder` for icon packs that need the same design at multiple sizes
   - Author a new strategy under `engine/packagers/<name>.py` if neither fits
5. **Bundle** (`profiles/bundles/<id>.json`) — names the four profile IDs.

See `references/profile-schema.md` for full field-by-field documentation.

## Profile schema reference

See `references/profile-schema.md`.

## Tests

```bash
python -m unittest discover tests -v
```

The test suite drives both bundles end-to-end with synthetic imagegen outputs, verifies concurrency safety of `record`, and asserts that profile loading produces sane results.
