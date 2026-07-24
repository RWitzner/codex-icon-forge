# Icon Forge v0.2 Functional Expansion Plan

> **Execution contract:** Implement each task test-first, keep the full suite green after every task, and land the five tasks as five separate commits in the order below.

**Goal:** Make Icon Forge resumable and reviewable, improve output QA and prompt control, add a production-ready web brand export, and allow private profiles to extend the public engine without copying private assets into this repository.

**Architecture:** Keep the current profile-driven engine and JSON run artifacts. Extend the manifest as the durable workflow state, add a QA module that reads recorded outputs, enrich style profiles with versioned semantic roles, add one focused packager strategy for browser/PWA assets, and introduce ordered profile discovery with bundled profiles as the final fallback.

**Compatibility:** Existing bundles, profile files, prepared run manifests, CLI syntax, and tests must continue to work. New manifest/profile fields receive explicit defaults when older files are loaded.

**Verification command:**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -t . -v
```

---

## Commit 1: Persist review, approval, and resume state

**Commit:** `feat(workflow): persist review, approval and resume state`

**Files:**

- Modify: `engine/manifest.py`
- Modify: `engine/run_setup.py`
- Modify: `scripts/icon_forge.py`
- Modify: `engine/__init__.py`
- Modify: `tests/test_run_setup.py`
- Add: `tests/test_workflow_state.py`
- Modify: `README.md`
- Modify: `SKILL.md`
- Add: `docs/superpowers/plans/2026-07-24-icon-forge-v0.2.md`

### Behavior

1. Bump the image-generation manifest schema while keeping schema-v2 files readable.
2. Persist per-job review state using these values:
   - `not-recorded`
   - `pending`
   - `approved`
   - `rejected`
3. Persist `reviewed_at` and `review_note` when a decision is made.
4. Persist one `approval_gate_job_id` on newly prepared multi-job runs. The first generatable non-base job is the gate.
5. Recording or deriving an output marks its review state `pending`.
6. Until the gate job is approved, `ready_jobs()` exposes only the gate job. Once approved, normal dependency-based fan-out resumes.
7. Rejection moves the job back to generation-ready `pending` status while retaining its prior provenance and review note for audit. Completed direct/transitive dependents are invalidated, and rejecting the approval gate invalidates all completed fan-out jobs, so stale outputs cannot later be extracted.
8. Add locked engine operations:
   - `approve_results(run_dir, job_ids=None, approve_all=False, note="")`
   - `reject_result(run_dir, job_id, note)`
   - `resume_run(run_dir)`
9. Add CLI commands:
   - `approve --run-dir … --job-id … [--job-id …] --note …`
   - `approve --run-dir … --all --note …`
   - `reject --run-dir … --job-id … --note …`
   - `resume --run-dir …`
10. `resume` returns a machine-readable next action and grouped job IDs:
    - `generate`
    - `review`
    - `regenerate`
    - `extract`
11. `extract` rejects unknown state IDs and refuses outputs that have not been approved. This makes the persisted review decision enforceable rather than informational.
12. Single-job runs still allow the initial job to be generated immediately; after recording, approval is required before extraction.
13. Older completed jobs that have no review field load as approved, preserving existing prepared runs.

### Test-first sequence

1. Add failing tests for schema migration defaults.
2. Add failing tests for first-result gate, approval fan-out, rejection/regeneration, and resume summaries.
3. Add failing CLI tests for approve/reject/resume.
4. Add failing extraction tests for unapproved and unknown states.
5. Implement the smallest manifest and run-setup changes that satisfy them.
6. Update user-facing workflow docs.
7. Run targeted tests, then the full suite.

---

## Commit 2: Add visual review sheets and output validation

**Commit:** `feat(qa): add visual review sheets and output validation`

**Files:**

- Add: `engine/review.py`
- Modify: `engine/__init__.py`
- Modify: `scripts/icon_forge.py`
- Add: `tests/test_review.py`
- Modify: `README.md`
- Modify: `SKILL.md`

### Behavior

1. Add `review_outputs(bundle, run_dir, force=False)` that reads every visual job/decoded result without altering source images.
2. Write:
   - `qa/review-sheet.png`
   - `qa/review.json`
3. The sheet uses deterministic Pillow rendering and includes, for each recorded job:
   - job/state label
   - source image on a light checkerboard
   - source image on a dark checkerboard
   - status marker for validation/review state
4. `review.json` contains a versioned schema, creation timestamp, logical expected dimensions, actual source dimensions/mode/format, cleaned alpha bounding box, validation errors/warnings, and overall `ok`.
5. Validate recorded strips against their state contract:
   - expected future jobs with `status=pending` and `review_status=not-recorded` are included as skipped warning placeholders and do not fail the overall review when at least one completed visual output validates
   - at least one completed visual output exists; otherwise fail with a top-level error
   - manifest output paths are relative paths under `decoded/`, with no parent components, absolute paths, or symlink escapes
   - file exists and opens for completed/rejected/stale or otherwise non-skipped jobs
   - PNG or WebP
   - actual decoded size does not exceed the documented 16,777,216 pixel budget
   - expected logical strip size is `cell_width * frames` by `cell_height`
   - actual decoded size is proportionally compatible with the logical strip size and at least as large as that logical size; high-resolution decoded masters such as a 1024x1024 single-frame Slack sticker for a 128x128 logical cell are valid
   - non-empty visible content after applying the run's persisted chroma key and extractor cleanup where applicable
   - no extra/missing recorded state
6. Missing outputs remain visible as errors in the JSON and placeholder cells in the sheet.
7. Add CLI command:
   - `review --run-dir … [--force]`
8. The command always writes the artifacts when possible and exits non-zero when validation errors exist.
9. The sheet must be regenerated explicitly unless `--force` is passed, preventing accidental overwrite of a reviewed artifact.

### Test-first sequence

1. Add failing unit tests for valid, missing, corrupt, and wrong-sized decoded results.
2. Add a failing deterministic sheet layout test using known images.
3. Add failing CLI exit-code tests.
4. Implement validation and rendering.
5. Document `review -> approve -> extract`.
6. Run targeted tests, then the full suite.

---

## Commit 3: Introduce versioned role-based prompt profiles

**Commit:** `refactor(prompts): introduce versioned role-based prompt profiles`

**Files:**

- Modify: `engine/profiles.py`
- Modify: `engine/prompts.py`
- Modify: `engine/run_setup.py`
- Modify: `engine/manifest.py`
- Modify: `engine/__init__.py`
- Modify: `scripts/icon_forge.py`
- Modify: `profiles/style/launcher-tile/profile.json`
- Modify: `profiles/style/flat-vector/profile.json`
- Modify: `profiles/style/clean-app-icon/profile.json`
- Modify: `profiles/atlas/app-icons.json`
- Modify: `profiles/atlas/app-icon-set.json`
- Modify: `profiles/atlas/slack-stickers.json`
- Add: `tests/test_prompt_roles.py`
- Modify: `tests/test_launcher_tile_style.py`
- Modify: `tests/test_run_setup.py`
- Modify: `README.md`
- Modify: `SKILL.md`

### Data model

1. Add `role: str = "default"` to `StateSpec` and `VariantSpec`.
2. Add `PromptRole` with optional overrides for:
   - `target_kind`
   - `purpose_wrapper`
   - `requirements`
   - `forbidden_artifacts`
3. Add to `StyleProfile`:
   - `prompt_profile_version`
   - `roles`
4. Style JSON accepts:

```json
{
  "prompt_profile_version": "1.0",
  "roles": {
    "default": {},
    "launcher": {
      "purpose_wrapper": "Design a platform launcher icon that communicates: {purpose}",
      "requirements": ["Remain legible at favicon size."]
    }
  }
}
```

5. Missing version/roles load as version `legacy` with a synthesized `default` role.
6. Static atlas states may declare `"role": "launcher"`.
7. Dynamic CLI variants keep `id:purpose` as default-role syntax and add unambiguous `id@role:purpose`.

### Prompt resolution

1. Resolve prompt settings in this order:
   - role-level override
   - exact state-level legacy override
   - style-level default
2. Combine role requirements with exact state requirements without duplication.
3. Reject an atlas state that names an unknown role when prompts are prepared.
4. Include stable prompt metadata in generated request/job data:
   - style profile ID
   - prompt profile version
   - semantic role
5. Migrate bundled profiles to explicit version `1.0` and roles while preserving current prompt meaning.

### Test-first sequence

1. Add failing loader tests for legacy defaults, valid roles, invalid role data, and malformed wrappers.
2. Add failing prompt composition tests for precedence and requirement merging.
3. Add failing variant parser/materialization tests for `id@role:purpose`.
4. Add failing request persistence tests.
5. Implement the model, loader, composition, and CLI parsing.
6. Migrate built-in profiles and update docs.
7. Run targeted tests, then the full suite.

---

## Commit 4: Add the web-brand-kit bundle

**Commit:** `feat(bundles): add web-brand-kit`

**Files:**

- Add: `profiles/atlas/web-brand-kit.json`
- Add: `profiles/bundles/web-brand-kit.json`
- Add: `profiles/packager/web-brand-kit.json`
- Modify: `profiles/style/launcher-tile/profile.json`
- Add: `engine/packagers/web_brand_kit.py`
- Modify: `engine/packager.py`
- Add: `tests/test_web_brand_kit.py`
- Modify: `tests/test_run_setup.py`
- Modify: `README.md`
- Modify: `SKILL.md`

### Behavior

1. Add a single-design bundle using:
   - a 1024x1024 source cell
   - the launcher-tile style’s `web-brand` role
   - chroma-key extraction
   - a dedicated `web-brand-kit` packager strategy
2. Package the canonical browser/PWA output:
   - `favicon-16x16.png`
   - `favicon-32x32.png`
   - `favicon-48x48.png`
   - `favicon.ico` containing 16, 32, and 48 pixel entries
   - `apple-touch-icon.png` at 180x180
   - `icon-192.png`
   - `icon-512.png`
   - `site.webmanifest`
   - `README.md`
3. Default output root:
   - `${ICON_FORGE_HOME:-$HOME/icon-forge}/web-brand-kits/{entity_id}`
4. `site.webmanifest` includes name, short name, icons, MIME types, sizes, and purpose.
5. Preserve RGBA PNG output and use high-quality LANCZOS resizing from the 1024 source.
6. Refuse overwrite unless `force=True`.
7. Return written files and sizes in the standard package result.

### Test-first sequence

1. Add failing bundle-resolution tests.
2. Add failing packager tests for filenames, exact dimensions, ICO frames, web manifest schema, and overwrite protection.
3. Implement/register the packager.
4. Add the profile files and documentation.
5. Run targeted tests, then the full suite.

---

## Commit 5: Support external private profile directories

**Commit:** `feat(extensions): support external private profile directories`

**Files:**

- Modify: `engine/profiles.py`
- Modify: `engine/run_setup.py`
- Modify: `scripts/icon_forge.py`
- Modify: `engine/__init__.py`
- Add: `tests/test_profile_discovery.py`
- Modify: `tests/test_run_setup.py`
- Modify: `README.md`
- Modify: `SKILL.md`

### Behavior

1. Support ordered profile roots from:
   - repeatable CLI `--profile-dir`
   - `ICON_FORGE_PROFILE_PATH`, split by `os.pathsep`
   - bundled `profiles/` as the final fallback
2. First matching profile ID wins.
3. Bundle and component lookup happen independently across all roots, so a private bundle can reuse public atlas/style/extractor/packager profiles.
4. `bundles` returns the union of all visible bundle IDs, deduplicated by precedence.
5. Preserve existing loader calls that pass one `Path`.
6. Validate configured roots and report actionable errors for missing directories or missing component files.
7. Persist external profile roots in `request.json` at prepare time so `review`, `extract`, and `finalize` can resume the same private bundle without repeating flags.
8. Do not copy profile content into the run directory or public repository.
9. Document a private extension layout and precedence example.

### Test-first sequence

1. Add failing tests for environment roots, explicit roots, precedence, deduplication, and hybrid private/public bundles.
2. Add failing round-trip test for persisted roots on a prepared run.
3. Refactor lookup through a small ordered-root resolver while keeping single-root compatibility.
4. Wire all CLI commands through the resolver.
5. Update docs.
6. Run targeted tests, then the full suite.

---

## Completion and release checks

1. Verify exactly five implementation commits exist after the branch point and that their subjects match the requested subjects.
2. Run the complete unittest suite with bytecode disabled.
3. Run CLI smoke checks for:
   - `bundles`
   - `show web-brand-kit`
   - `prepare`
   - `status`
   - `resume`
4. Review the final diff for secrets, private paths/content, generated images, and accidental Unity/BRIK material.
5. Confirm the working tree is clean.
6. Push `codex/icon-forge-v0.2` to `origin` and report the exact remote branch and commit hashes.
