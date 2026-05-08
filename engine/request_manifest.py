"""Request-manifest read/write helpers.

New icon-forge runs write ``request.json``. Readers also accept the previous
request filename so existing local run folders remain inspectable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUEST_FILENAME = "request.json"
LEGACY_REQUEST_FILENAME = "pet_request.json"


def find_request_path(run_dir: Path) -> Path:
    request_path = run_dir / REQUEST_FILENAME
    if request_path.is_file():
        return request_path

    legacy_path = run_dir / LEGACY_REQUEST_FILENAME
    if legacy_path.is_file():
        return legacy_path

    raise FileNotFoundError(
        f"no request manifest under {run_dir}; looked for "
        f"{REQUEST_FILENAME} and legacy {LEGACY_REQUEST_FILENAME}"
    )


def read_request(run_dir: Path) -> dict[str, Any]:
    path = find_request_path(run_dir)
    return json.loads(path.read_text(encoding="utf-8"))


def write_request(run_dir: Path, request: dict[str, Any]) -> Path:
    path = run_dir / REQUEST_FILENAME
    path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return path
