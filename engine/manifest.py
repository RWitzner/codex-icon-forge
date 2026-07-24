"""imagegen-jobs.json manifest read/write.

Each run directory holds an ``imagegen-jobs.json`` listing every visual job
with its prompt, input images, dependencies, and current status. This module
owns the schema and the read/write semantics; CLI commands and the run-setup
helper produce and consume it via these primitives.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "imagegen-jobs.json"
LOCK_FILENAME = "imagegen-jobs.json.lock"
SCHEMA_VERSION = 4
REVIEW_STATUSES = frozenset(
    {"not-recorded", "pending", "approved", "rejected"}
)


def acquire_manifest_lock(run_dir: Path, timeout: float = 30.0):
    """Cross-process exclusive lock around the imagegen-jobs.json manifest.

    Returns an opaque handle that must be passed back to
    ``release_manifest_lock``. Implemented with O_CREAT|O_EXCL on a sibling
    ``.lock`` file so two parallel record/derive calls cannot interleave a
    read-modify-write cycle and drop each other's status updates.
    """
    import time

    lock_path = run_dir / LOCK_FILENAME
    end = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            return fd, lock_path
        except FileExistsError:
            if time.monotonic() >= end:
                raise RuntimeError(
                    f"could not acquire {lock_path} within {timeout}s; "
                    "another writer may be stuck — delete the lock file if confirmed"
                )
            time.sleep(0.01)


def release_manifest_lock(handle) -> None:
    fd, lock_path = handle
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


@dataclass
class JobInput:
    path: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "role": self.role}


@dataclass
class Job:
    id: str
    kind: str
    status: str
    prompt_file: str
    input_images: list[JobInput]
    output_path: str
    depends_on: list[str] = field(default_factory=list)
    generation_skill: str = "$imagegen"
    requires_grounded_generation: bool = False
    allow_prompt_only_generation: bool = True
    identity_reference_paths: list[str] = field(default_factory=list)
    parallelizable_after: list[str] = field(default_factory=list)
    mirror_policy: dict[str, Any] = field(default_factory=dict)
    recording_owner: str = "parent"
    source: str | None = None
    recorded_at: str | None = None
    review_status: str = "not-recorded"
    reviewed_at: str | None = None
    review_note: str | None = None
    prompt_profile: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "prompt_file": self.prompt_file,
            "input_images": [item.to_dict() for item in self.input_images],
            "output_path": self.output_path,
            "depends_on": list(self.depends_on),
            "generation_skill": self.generation_skill,
            "requires_grounded_generation": self.requires_grounded_generation,
            "allow_prompt_only_generation": self.allow_prompt_only_generation,
            "identity_reference_paths": list(self.identity_reference_paths),
            "parallelizable_after": list(self.parallelizable_after),
            "mirror_policy": dict(self.mirror_policy),
            "recording_owner": self.recording_owner,
            "review_status": self.review_status,
        }
        if self.prompt_profile:
            data["prompt_profile"] = dict(self.prompt_profile)
        if self.source is not None:
            data["source"] = self.source
        if self.recorded_at is not None:
            data["recorded_at"] = self.recorded_at
        if self.reviewed_at is not None:
            data["reviewed_at"] = self.reviewed_at
        if self.review_note is not None:
            data["review_note"] = self.review_note
        return data


@dataclass
class ImagegenManifest:
    bundle: str
    run_dir: str
    jobs: list[Job]
    created_at: str
    schema_version: int = SCHEMA_VERSION
    primary_generation_skill: str = "$imagegen"
    approval_gate_job_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "bundle": self.bundle,
            "run_dir": self.run_dir,
            "primary_generation_skill": self.primary_generation_skill,
            "created_at": self.created_at,
            "jobs": [job.to_dict() for job in self.jobs],
        }
        if self.approval_gate_job_id is not None:
            data["approval_gate_job_id"] = self.approval_gate_job_id
        return data

    def job(self, job_id: str) -> Job:
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise KeyError(job_id)

    def _gate_generation_ids(self) -> set[str] | None:
        gate_id = self.approval_gate_job_id
        if gate_id is None:
            return None
        gate = self.job(gate_id)
        if gate.status == "complete" and gate.review_status == "approved":
            return None

        prerequisite_ids: set[str] = set()
        pending = list(gate.depends_on)
        while pending:
            dependency_id = pending.pop()
            if dependency_id in prerequisite_ids:
                continue
            prerequisite_ids.add(dependency_id)
            pending.extend(self.job(dependency_id).depends_on)
        return prerequisite_ids | {gate_id}

    def generation_allowed(self, job: Job) -> bool:
        """Whether workflow gates and dependencies allow generating ``job``."""

        approved = {
            item.id
            for item in self.jobs
            if item.status == "complete" and item.review_status == "approved"
        }
        if not all(dependency in approved for dependency in job.depends_on):
            return False
        gate_generation_ids = self._gate_generation_ids()
        return gate_generation_ids is None or job.id in gate_generation_ids

    def ready_jobs(self) -> list[Job]:
        return [
            job
            for job in self.jobs
            if job.status == "pending" and self.generation_allowed(job)
        ]

    def save(self, run_dir: Path) -> Path:
        path = run_dir / MANIFEST_FILENAME
        # Unique tmp filename per call so concurrent writers do not race on
        # the same ``.tmp`` path. ``os.replace`` itself is atomic on POSIX
        # and Windows once the source file is closed.
        tmp_path = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
        tmp_path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp_path, path)
        return path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(run_dir: Path) -> ImagegenManifest:
    path = run_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"no manifest at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    source_schema_version = int(data.get("schema_version", 1))
    if source_schema_version > SCHEMA_VERSION:
        raise ValueError(
            f"manifest schema {source_schema_version} is newer than supported "
            f"schema {SCHEMA_VERSION}"
        )

    jobs: list[Job] = []
    for job_data in data.get("jobs", []):
        inputs = [
            JobInput(path=str(item["path"]), role=str(item["role"]))
            for item in job_data.get("input_images", [])
        ]
        status = str(job_data["status"])
        review_status = str(
            job_data.get(
                "review_status",
                "approved" if status == "complete" else "not-recorded",
            )
        )
        if review_status not in REVIEW_STATUSES:
            raise ValueError(
                f"job {job_data.get('id')!r} has invalid review_status "
                f"{review_status!r}"
            )
        jobs.append(
            Job(
                id=str(job_data["id"]),
                kind=str(job_data["kind"]),
                status=status,
                prompt_file=str(job_data["prompt_file"]),
                input_images=inputs,
                output_path=str(job_data["output_path"]),
                depends_on=list(job_data.get("depends_on", [])),
                generation_skill=str(job_data.get("generation_skill", "$imagegen")),
                requires_grounded_generation=bool(
                    job_data.get("requires_grounded_generation", False)
                ),
                allow_prompt_only_generation=bool(
                    job_data.get("allow_prompt_only_generation", True)
                ),
                identity_reference_paths=list(
                    job_data.get("identity_reference_paths", [])
                ),
                parallelizable_after=list(job_data.get("parallelizable_after", [])),
                mirror_policy=dict(job_data.get("mirror_policy", {})),
                recording_owner=str(job_data.get("recording_owner", "parent")),
                source=job_data.get("source"),
                recorded_at=job_data.get("recorded_at"),
                review_status=review_status,
                reviewed_at=job_data.get("reviewed_at"),
                review_note=job_data.get("review_note"),
                prompt_profile=dict(job_data.get("prompt_profile", {})),
            )
        )
    return ImagegenManifest(
        bundle=str(data.get("bundle", "")),
        run_dir=str(data.get("run_dir", str(run_dir))),
        jobs=jobs,
        created_at=str(data.get("created_at", now_iso())),
        schema_version=SCHEMA_VERSION,
        primary_generation_skill=str(data.get("primary_generation_skill", "$imagegen")),
        approval_gate_job_id=data.get("approval_gate_job_id"),
    )
