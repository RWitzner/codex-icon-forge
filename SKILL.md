---
name: icon-forge
description: Use when a user wants a consistent static icon family, Slack or Discord sticker pack, app launcher icon set, favicon set, or browser/PWA brand kit generated and packaged from one visual concept.
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

- **`slack-stickers`** — user-defined 1–12 sticker pack. Variants are supplied at prepare time via `--variant id:purpose` (one per sticker). Output: transparent 128, 256, 512, and 1024 px PNGs at `<sticker>/<sticker>-<size>.png`, plus a `README.md` with Slack import instructions. Style is `flat-vector` (bold simple shapes, thick outlines, limited palette). The canonical dev-pack preset (`shipping-it`, `tests-passing`, `merge-conflict`, …) lives in README.md as 12 ready-to-paste `--variant` strings.
- **`app-icons`** — one icon design rendered at 8 platform sizes (16, 32, 64, 128, 180, 256, 512, 1024). Output: 8 sized PNGs plus a README mapping each size to its platform use (iOS App Store, Android Play Store, web favicon, apple-touch-icon, Slack workspace icon). Style is `launcher-tile` (full-colour app launcher tile, hardened against feature-glyph leakage, readable from 16x16).
- **`app-icon-set`** — user-defined family of 1–12 distinct icon designs (e.g. main + share-extension + watch + notification), each rendered at all 8 platform sizes. Variants are supplied at prepare time via `--variant id:purpose` or, for style-defined semantic roles, `--variant id@role:purpose`; each variant is generated independently by its own subagent, then fan'ed out to subfolders. Output: `<entity>/<variant>/<variant>-<size>.png` plus a family README.
- **`web-brand-kit`** — one browser/PWA brand mark rendered from a 1024x1024 source cell. Output: PNG favicons, `favicon.ico` with 16/32/48 entries, `apple-touch-icon.png`, 192/512 manifest icons, `site.webmanifest`, and a README with HTML usage.

Add more bundles by authoring profile JSONs — no engine code changes required for typical new products.

## When to use this skill

| Goal | Use |
|---|---|
| 1–12 transparent stickers/emojis for Slack, Discord, Mattermost | `slack-stickers` bundle with `--variant id:purpose` per sticker (or paste the dev-pack preset) |
| Single app icon at all platform sizes | `app-icons` bundle |
| Family of distinct app icons (main + alternates, share-ext, watch, notification, light/dark) each at all sizes | `app-icon-set` bundle with `--variant id:purpose` per design, or `id@role:purpose` for style-defined roles |
| Browser/PWA favicon and manifest asset kit | `web-brand-kit` bundle |
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
| (B) in-app symbols | Not shipped yet. Tell the user honestly and suggest either (i) Apple's SF Symbols app, or (ii) flagging the gap so an `ios-button-icons` bundle gets prioritised. **Do not** fake it by running `app-icon-set` with feature-named variants — the launcher-style prompts produce full-colour square tiles, not silhouette glyphs. |

## Default workflow (concept to packaged output)

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/icon-forge"
PYTHON="$SKILL_DIR/.venv/bin/python"
```

If `$PYTHON` does not exist, the skill was installed without its virtual environment — tell the user to run the install block in README.md rather than falling back to a system `python3`, which will not have Pillow.

**Run location rule.** Omit `--output-dir` and the script picks `${PWD}/output/icon-forge/<entity-id>-<UTC-timestamp>` for you — i.e. the current working directory the user invoked you from. Only pass `--output-dir` when the user explicitly names a different path. Never default to `~/Downloads`, `~/Desktop`, `$CODEX_HOME`, or `/tmp`.

1. **Prepare** the run folder.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" prepare \
     --bundle <bundle-id> \
     --entity-id <slug> \
     --display-name "<Display Name>" \
     --description "<one sentence>" \
     --notes "<stable visual description used in every prompt>" \
     --style-notes "<optional user style overrides>" \
     --reference /absolute/path/to/reference.png
   ```

   Omit `--output-dir`; the script returns the chosen `run_dir` in its JSON output. Capture that path and pass it as `--run-dir` to the downstream commands (`status`, `record`, `approve`, `reject`, `resume`, `derive`, `extract`). Pass `--output-dir <path>` only when the user named one explicitly.

   Writes `request.json`, `prompts/`, `references/`, and `imagegen-jobs.json` listing every visual job with dependencies and input images.

   ```bash
   RUN_DIR=$("$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" prepare ... | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["run_dir"])')
   ```

2. **Inspect** ready jobs.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" status --run-dir "$RUN_DIR"
   ```

3. **Generate** only the jobs returned by `status` through `$imagegen`. For atlases with `requires_base: true`, the base job runs first as the canonical identity reference and must be recorded and approved before its dependent state can become ready. For all atlases, generate the first state job, record it, approve it, then fan out the remaining ready states. Each row job must attach its listed input images.

   **First-result approval gate (multi-job bundles only).** After recording the first state in a multi-job bundle (e.g. the first variant in `app-icon-set`, or the first sticker in `slack-stickers`), **stop and show the user the `decoded_path` returned by `record`** (e.g. `decoded/main.png`). Persist the user's decision with `approve` or `reject`; the remaining jobs cannot be recorded until the gate is approved. Catching style or intent errors at job 1 of N is far cheaper than discovering them after job N of N. Single-job bundles do not block a fan-out, but their result still requires approval before extraction. On rejection, regenerate the now-ready job and record it again. On abort, leave the run dir as-is for inspection.

4. **Record** each completed generation.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" record \
     --run-dir "$RUN_DIR" \
     --job-id <id> \
     --source /absolute/path/to/$CODEX_HOME/generated_images/.../ig_*.png
   ```

   For base jobs (atlases that declare `requires_base: true`; none of the shipped bundles do, but external bundles may) this also writes `references/canonical-base.png` so subsequent row jobs use it as identity reference. The record step is concurrency-safe: a sibling lock file serialises parallel calls so no manifest update is dropped.

5. **Render QA artifacts and persist review decisions.** Every recorded or derived result enters `pending` review. Run `review` before approving: it writes `qa/review-sheet.png` and `qa/review.json`, validates decoded output format, proportional strip geometry, useful size, safe `decoded/` paths, the 4096x4096 decoded pixel budget, and non-blank visible content after chroma cleanup. Not-yet-recorded future jobs are shown as skipped placeholders; at least one completed visual output must exist. Review artifacts are protected from accidental overwrite; pass `--force` to regenerate them after recording more jobs.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" review \
     --run-dir "$RUN_DIR"
   ```

   Approve the first gate result before generation fans out, then approve or reject each later result before extraction.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" approve \
     --run-dir "$RUN_DIR" \
     --job-id <id> \
     --note "<review decision>"

   # Or approve all currently recorded results after reviewing the set:
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" approve \
     --run-dir "$RUN_DIR" \
     --all \
     --note "<review decision>"

   # Rejection preserves note/provenance and reopens the job. Recorded
   # dependents (or all fanout jobs when rejecting the gate) are invalidated:
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" reject \
     --run-dir "$RUN_DIR" \
     --job-id <id> \
     --note "<specific correction needed>"
   ```

   Use the persisted state instead of reconstructing progress from memory:

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" resume --run-dir "$RUN_DIR"
   ```

   `next_action` is one of `generate`, `review`, `regenerate`, or `extract`, with grouped job IDs.

6. **Derive** any mirror states (rare for icon bundles; common for animated sprites).

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" derive \
     --run-dir "$RUN_DIR" \
     --target <state-id> \
     --decision-note "<why mirroring preserves identity>"
   ```

7. **Extract** approved decoded strips into per-state frame directories. Unknown state IDs and unapproved outputs are rejected.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" extract \
     --run-dir "$RUN_DIR" \
     --states all
   ```

8. **Finalize** — compose, validate, package.

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" finalize \
     --bundle <bundle-id> \
     --frames "$RUN_DIR/frames" \
     --entity-id <slug> \
     --display-name "<Display Name>" \
     --description "<one sentence>" \
     --output-run-dir "$RUN_DIR" \
     --icon-forge-home "${ICON_FORGE_HOME:-$HOME/icon-forge}"
   ```

   Output goes to `${ICON_FORGE_HOME:-$HOME/icon-forge}/<bundle-output>/<slug>/`. Each bundle decides its own subpath (sticker bundles write to `stickers/<slug>/`, app-icon bundles write to `app-icons/<slug>/`).

## Subagent row generation

For bundles with many parallel-eligible jobs (e.g. `slack-stickers` with 1–12 user-defined stickers, or `app-icon-set` with multiple variants), fan out generation to subagents. The parent agent owns the manifest and recording; subagents only produce candidate images.

Default flow:

1. Parent runs `prepare`, then runs `status` to see ready jobs.
2. For atlases with `requires_base: true`, parent generates and records the base job first.
3. **First-result human approval** (multi-job bundles only). After recording the first job's decoded output, before spawning row subagents for the rest, present its path to the user:

   > ```
   > First job recorded: <absolute path to decoded/<state-id>.png>
   > Open it and confirm the style and intent are correct before I fan out the remaining N-1 jobs.
   > Reply: `approve` to continue · `regenerate` to retry this job · `abort` to stop.
   > ```

   Persist `approve` with:

   ```bash
   "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" approve \
     --run-dir "$RUN_DIR" --job-id <state-id> \
     --note "<why it is approved>"
   ```

   Persist `regenerate` with `reject --job-id <state-id> --note "<correction>"`, then regenerate and record that now-ready job. On `abort`, leave the run dir as-is for inspection. Single-job bundles skip the fan-out gate but still require approval before extraction.
4. Parent spawns subagents for the remaining ready jobs (the N-1 jobs left after the first-result approval).
5. Each subagent generates one image with `$imagegen` and returns only the selected source path.
6. Parent runs `record` for each returned source. The lock file makes parallel record calls safe.
7. Parent runs `review --force`, presents `qa/review-sheet.png` plus validation status, persists final decisions with `approve --all` or per-job `approve`/`reject`, then runs `derive`, `extract`, and `finalize`.

**MANDATORY parallelism question for 8+ parallel-eligible jobs.** When *any* bundle runs with 8 or more parallel-eligible jobs (e.g. `slack-stickers` with 8+ stickers, or `app-icon-set` with 8+ variants), **after the first-result approval and before generating job 2 of N, halt and ask the user explicitly** whether to fan out to 2 subagents or run sequentially. Do not autonomously decide — neither default to fan-out nor fall back to sequential without an explicit answer.

**Question to ask (verbatim):**

> ```
> I have N-1 jobs left to generate. For an 8+ run I need an explicit choice before I continue:
> - `parallel`   — split across 2 subagents (≈half the wall time; per-image quality independent across jobs)
> - `sequential` — run them one-by-one in this agent
> ```

Only proceed after the answer. If the user replies `parallel` but subagent spawning is unavailable in this environment, **surface that constraint and ask** whether to (a) run sequentially anyway or (b) pause so they can grant delegated agent permissions and resume. **Do not** announce a constraint and continue sequentially as a fallback — that's the failure mode this gate exists to prevent.

Batching is allowed: for an 8-job run, two subagents × 4 jobs; for 12, two × 6. Per-image quality is independent across jobs, so batching carries no quality cost; the manifest lock guarantees parallel record safety; sequential generation is NOT required for provenance. Smaller multi-job runs (any bundle with <8 parallel-eligible jobs) may run sequentially without flagging.

Subagent write boundary: subagents must not edit `imagegen-jobs.json`, copy files into `decoded/`, run `record`, run `approve`, run `reject`, run `derive`, run `extract`, or run `finalize`. This avoids manifest races and keeps provenance checks centralised.

Provenance enforcement: `record` rejects any source path that is not `$CODEX_HOME/generated_images/.../ig_*.png`, and any path that lives inside the run directory itself. Locally drawn or post-processed images cannot be ingested as visual job outputs. The hidden `--allow-synthetic-test-source` flag bypasses the check for unit tests only — never use it in real runs.

Workflow enforcement: `record` accepts only workflow-eligible jobs. Dependencies must be approved, the first multi-job result gates fan-out, and `extract` accepts only approved states. `--force` can replace an eligible completed job's file, but never bypasses a rejected gate or dependency.

Overwrite guard: `record` refuses to replace a job's existing decoded output unless `--force` is passed or the job was explicitly rejected. This prevents a stale subagent result, a double-record bug, or a parallel race from silently overwriting an approved image.

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

`slack-stickers` produces 1–12 independent Slack emoji/sticker motifs in the `flat-vector` style on a chroma-key background. Always supply one `--variant id:purpose` per sticker. The `id` becomes both the Slack emoji shortcode and the output filename stem; final packaging writes transparent 128, 256, 512, and 1024 px PNGs at `<sticker>/<sticker>-<size>.png`. The `purpose` is the visual concept the prompt template uses for that sticker.

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
"$PYTHON" "${SKILL_DIR}/scripts/icon_forge.py" prepare \
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

The rest of the workflow (`status`, `record`, `approve`, `extract`, `finalize`) is identical to the other bundles. Variant IDs are persisted in `request.json`, so downstream commands reload the materialised atlas automatically.

## Dynamic icon families (`app-icon-set`)

Some products need several distinct icon designs that all ship together — main app icon plus alternate icons, a share-extension icon, a watchOS variant, a notification silhouette, light/dark pairs. The `app-icon-set` bundle handles that with up to 12 user-defined variants per run, each generated by its own subagent and fan'ed out to all 8 platform sizes.

> **Before you run `app-icon-set`:**
> - If the user's variant IDs sound like in-app features (tabs, buttons, sections) rather than launcher purposes → see "STOP — before `prepare`: disambiguate iOS icon intent" above. `app-icon-set` produces full-colour square Home Screen tiles, not in-app symbols.
> - The `launcher-tile` style profile re-frames every variant purpose declaratively (via `purpose_wrapper`) so feature-named variants cannot dominate the prompt. You do **not** need to re-write `--variant id:purpose` strings yourself; pass them through verbatim.
> - `watch`, `notification`, `light`, and `dark` variant IDs receive automatic per-variant overrides (monochrome silhouette for watch/notification; full-colour tile with explicit light/dark intent for light/dark). Use those exact IDs to opt in. The explicit role syntax `watch@watch:...` and `notification@notification:...` is also supported by the bundled launcher style and persists through resume/finalize.
> - After the first variant is recorded → pause for human approval before fanning out to the remaining variants. See step 3 of "Default workflow" and step 3 of "Subagent row generation".

Prepare with one `--variant id:purpose` per icon design. Use `id@role:purpose` when you need a style-defined semantic role:

```bash
"$PYTHON" "${SKILL_DIR}/scripts/icon_forge.py" prepare \
  --bundle app-icon-set \
  --entity-id myapp \
  --display-name "MyApp" \
  --description "MyApp icon family" \
  --notes "modern minimalist, bold silhouette" \
  --output-dir "${RUN_DIR}" \
  --variant "main:primary app icon" \
  --variant "share-ext:share extension, simpler version" \
  --variant "watch@watch:1-bit silhouette for watchOS" \
  --force
```

Variant ID rules (validated at prepare time):

- 1–12 variants per run; pass `--variant` repeatedly
- ID matches `^[a-z0-9][a-z0-9-]{0,30}$` (slug-style)
- Role ID, when supplied with `id@role:purpose`, uses the same slug-style shape and must be defined by the style profile
- IDs must be unique within the run
- Purpose is required and at most 200 chars

The rest of the workflow is identical to other bundles: parent generates each variant via `$imagegen` (or fans them out to subagents), records each result with `record`, persists review decisions with `approve`/`reject`, then runs `extract` and `finalize`. The packager writes `${ICON_FORGE_HOME}/app-icon-sets/<entity-id>/<variant>/<variant>-<size>.png` for every (variant, size) pair plus a family README.

For a single icon at all sizes, use the simpler `app-icons` bundle — `app-icon-set` is overkill for one design.

## Authoring a new bundle

A new icon product is normally five JSON files plus two prompt templates, no engine code change.

1. **Atlas** (`profiles/atlas/<id>.json`) — geometry, state catalog. For most icon products, one or N states with `frames: 1`.
2. **Style** (`profiles/style/<id>/profile.json` plus `templates/`) — `target_kind`, prompt templates (`base` and `row_strip`), `forbidden_artifacts`, optional versioned semantic `roles`, `chroma_key.candidates`.
3. **Extractor** (`profiles/extractor/<id>.json`) — typically `chroma-key-slots` for images on chroma-key backgrounds, `slot-only` if the model emits transparent PNG directly.
4. **Packager** (`profiles/packager/<id>.json`) — pick a registered strategy:
   - `atlas-extract-folder` for sticker-style packs (single-size or configured multi-size state folders plus a README)
   - `multi-size-folder` for icon packs that need the same design at multiple sizes
   - `web-brand-kit` for canonical browser/PWA assets from one brand mark
   - Author a new strategy under `engine/packagers/<name>.py` if neither fits
5. **Bundle** (`profiles/bundles/<id>.json`) — names the four profile IDs.

See `references/profile-schema.md` for full field-by-field documentation.

### Private profile roots

Private extensions can live outside the installed skill in any directory that mirrors the bundled `profiles/` layout:

```text
private-profiles/
├── bundles/<bundle-id>.json
├── atlas/<atlas-id>.json
├── style/<style-id>/profile.json
├── style/<style-id>/templates/
├── extractor/<extractor-id>.json
└── packager/<packager-id>.json
```

Search precedence is repeatable CLI `--profile-dir` entries, then `ICON_FORGE_PROFILE_PATH` entries split by `os.pathsep`, then bundled profiles. First match wins. Bundle components resolve independently across the whole chain, so a private bundle can reference bundled components.

```bash
"$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" show my-private-bundle \
  --profile-dir "$HOME/icon-forge-profiles"

ICON_FORGE_PROFILE_PATH="$HOME/icon-forge-profiles:$HOME/team-profiles" \
  "$PYTHON" "$SKILL_DIR/scripts/icon_forge.py" prepare \
    --bundle my-private-bundle \
    --entity-id sample \
    --description "Private profile smoke test"
```

Prepared runs persist absolute external roots in `request.json` as `profile_roots` and never copy private profile JSON or templates into the run directory. Downstream `review`, `extract`, `derive`, and `finalize` use those persisted roots by default; pass `--profile-dir` only for an intentional override.

## Profile schema reference

See `references/profile-schema.md`.

## Tests

```bash
"$PYTHON" -m unittest discover -s tests -p 'test_*.py' -t . -v
```

The test suite drives all shipped bundles end-to-end with synthetic imagegen outputs, verifies concurrency safety and review gates, and asserts that profile loading and public documentation contracts remain consistent.
