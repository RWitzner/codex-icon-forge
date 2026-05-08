# Contributing

icon-forge is a Codex skill for generating icon and sticker packs through
profile-driven bundles. Contributions are welcome when they keep the skill
usable as a drop-in folder under `~/.codex/skills/icon-forge`.

## Development setup

```bash
REPO_URL="https://github.com/your-org/icon-forge.git"
git clone "$REPO_URL"
cd icon-forge
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest discover tests -v
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest discover tests -v
```

## Pull requests

Before opening a PR:

1. Run `python -m unittest discover tests -v`.
2. Keep generated run folders, local prompts, and imagegen outputs out of git.
3. Add or update tests when changing profile loading, extraction, packaging,
   prompt composition, or CLI behavior.
4. Update `README.md`, `SKILL.md`, or `references/profile-schema.md` when a
   user-facing command, bundle profile, or authoring contract changes.

## Profile changes

Most new icon products should be implemented as profiles, not engine code:

- `profiles/atlas/<id>.json`
- `profiles/style/<id>/profile.json` and templates
- `profiles/extractor/<id>.json`
- `profiles/packager/<id>.json`
- `profiles/bundles/<id>.json`

Only add a new Python strategy when the existing extractors or packagers cannot
describe the output shape.

## Licensing of contributions

By submitting a pull request, you agree that your contribution is licensed under
the same Apache-2.0 license as this repository, unless you explicitly mark it as
"Not a Contribution" before submission.
