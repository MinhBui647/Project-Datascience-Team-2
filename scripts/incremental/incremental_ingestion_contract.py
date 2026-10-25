"""M4.5.4b | Pure transactional contract for future incremental Raw ingestion.

NO PostgreSQL connection, Hugging Face access, downloads, archive access,
file writes, or ``--commit`` in this module. This is intentionally an
in-memory specification and negative-test harness, NOT a database writer.

Key rules to preserve in the real writer:
  * one immutable file version per (repo_id, relative_path, content_sha256)
  * each file's DB inserts/observations are committed in ONE transaction
  * failures never leave partial file records or a partial observation
  * reused unchanged file IDs must not reinsert existing Raw records
  * a retry may only relink the SAME verified file version
  * target snapshot cannot be marked completed by the ingestion step
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Literal

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")


class ContractViolation(RuntimeError):
    """Fail closed before mutating any simulated state."""


def _safe_raw_path(path: str) -> bool:
    if not isinstance(path, str) or not path.startswith(RAW_PREFIXES):
        return False
    parts = path.split("/")
    return not (path.startswith("/") or "\\" in path or "//" in path or
                any(part in ("", ".", "..") for part in parts) or
                any(ord(ch) < 32 for ch in path))


@dataclass(frozen=True)
class VerifiedCandidate:
    """Content that passed metadata/content checks for ONE pinned revision.

    The real writer must REHASH the file bytes immediately before archiving
    and writing; this dataclass is not itself cryptographic evidence.
    """
    repo_id: str
    path: str
    target_revision: str
    category: Literal["NEW", "CHANGED"]
    sha256: str
    byte_size: int
    record_count: int
    source_code: str
    file_format: str = "json"

    def __post_init__(self) -> None:
        if not self.repo_id or not self.repo_id.strip():
            raise ContractViolation("Missing repo_id")
        if not _safe_raw_path(self.path):
            raise ContractViolation("Unsafe or out-of-scope Raw path")
        if not SHA40_RE.fullmatch(self.target_revision):
            raise ContractViolation("Target revision must be a pinned SHA40")
        if self.category not in ("NEW", "CHANGED"):
            raise ContractViolation("Only NEW/CHANGED candidates can be ingested")
        if not SHA256_RE.fullmatch(self.sha256):
            raise ContractViolation("Invalid content SHA256")
        if type(self.byte_size) is not int or self.byte_size <= 0:
            raise ContractViolation("Invalid byte size")
        if type(self.record_count) is not int or self.record_count < 0:
            raise ContractViolation("Invalid parser record count")
        if self.file_format != "json":
            raise ContractViolation("M4.5.4b contract intentionally covers JSON candidates only")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", self.source_code):
            raise ContractViolation("Invalid source_code")


@dataclass(frozen=True)
class StoredVersion:
    file_id: int
    repo_id: str
    path: str
    sha256: str
    byte_size: int
    record_count: int
    archive_state: str = "verified"
    parse_state: str = "success"


@dataclass
class Snapshot:
    repo_id: str
    target_revision: str
    expected_paths: frozenset[str]
    status: str = "in_progress"
    observations: dict[str, int] = field(default_factory=dict)
    manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.repo_id or not SHA40_RE.fullmatch(self.target_revision):
            raise ContractViolation("Invalid snapshot identity")
        if not self.expected_paths or any(not _safe_raw_path(p) for p in self.expected_paths):
            raise ContractViolation("Invalid target Raw scope")
        if self.status != "in_progress":
            raise ContractViolation("Test contract only begins with in_progress snapshot")


@dataclass
class MemoryDB:
    """Small independent simulation; never persists/contacts a real database."""
    versions: dict[tuple[str, str, str], StoredVersion] = field(default_factory=dict)
    raw_record_counts: dict[int, int] = field(default_factory=dict)
    snapshot: Snapshot | None = None
    next_file_id: int = 1

    def register_existing(self, version: StoredVersion, records: int | None = None) -> None:
        if (type(version.file_id) is not int or version.file_id <= 0 or
                not SHA256_RE.fullmatch(version.sha256) or
                not _safe_raw_path(version.path)):
            raise ContractViolation("Invalid existing version")
        key = (version.repo_id, version.path, version.sha256)
        if key in self.versions or version.file_id in self.raw_record_counts:
            raise ContractViolation("Duplicate existing file identity")
        self.versions[key] = version
        self.raw_record_counts[version.file_id] = version.record_count if records is None else records
        self.next_file_id = max(self.next_file_id, version.file_id + 1)

    def start_snapshot(self, repo_id: str, target_revision: str,
                       expected_paths: set[str] | frozenset[str]) -> None:
        if self.snapshot is not None:
            raise ContractViolation("Existing target snapshot requires explicit inspection/resume")
        self.snapshot = Snapshot(repo_id, target_revision, frozenset(expected_paths))

    def _ensure_snapshot(self, repo: str, revision: str, path: str) -> Snapshot:
        snap = self.snapshot
        if (snap is None or snap.status != "in_progress" or
                snap.repo_id != repo or snap.target_revision != revision or
                path not in snap.expected_paths):
            raise ContractViolation("Snapshot, revision or path mismatch")
        return snap

    def _atomic(self, operation, fail_at: str | None) -> tuple[int, str]:
        """Model SQL commit by applying changes to a copy, swapping only on success."""
        working = copy.deepcopy(self)
        result = operation(working, fail_at)
        self.versions = working.versions
        self.raw_record_counts = working.raw_record_counts
        self.snapshot = working.snapshot
        self.next_file_id = working.next_file_id
        return result

    def link_unchanged(self, repo_id: str, revision: str, path: str,
                       file_id: int, *, fail_at: str | None = None) -> tuple[int, str]:
        """Link existing SUCCESS version; NEVER insert or alter its raw records."""
        def operation(db: MemoryDB, fault: str | None) -> tuple[int, str]:
            snap = db._ensure_snapshot(repo_id, revision, path)
            candidates = [v for v in db.versions.values()
                          if v.file_id == file_id and v.repo_id == repo_id and v.path == path]
            if len(candidates) != 1:
                raise ContractViolation("REUSE file_id is absent or does not belong to path")
            version = candidates[0]
            _check_version_integrity(version, db)
            before = snap.observations.get(path)
            if before is not None and before != file_id:
                raise ContractViolation("Conflicting target observation")
            snap.observations[path] = file_id
            if fault == "after_observation":
                raise ContractViolation("Simulated interruption after observation insert")
            return file_id, "REUSED" if before is None else "ALREADY_LINKED"
        return self._atomic(operation, fail_at)

    def ingest_verified_json(self, candidate: VerifiedCandidate, *,
                             verified_sha256: str, verified_byte_size: int,
                             parsed_record_count: int,
                             fail_at: str | None = None) -> tuple[int, str]:
        """Atomic simulation of archive-verified file insert + records + observation.

        `verified_*` are independent expected values supplied by the caller.
        A real writer must calculate these from bytes immediately before writes.
        """
        if (verified_sha256, verified_byte_size, parsed_record_count) != (
            candidate.sha256, candidate.byte_size, candidate.record_count
        ):
            raise ContractViolation("Cached bytes/parser records do not match validated candidate")

        def operation(db: MemoryDB, fault: str | None) -> tuple[int, str]:
            snap = db._ensure_snapshot(candidate.repo_id, candidate.target_revision,
                                       candidate.path)
            key = (candidate.repo_id, candidate.path, candidate.sha256)
            version = db.versions.get(key)
            if version is None:
                file_id = db.next_file_id
                db.next_file_id += 1
                version = StoredVersion(file_id=file_id, repo_id=candidate.repo_id,
                                        path=candidate.path, sha256=candidate.sha256,
                                        byte_size=candidate.byte_size,
                                        record_count=candidate.record_count)
                db.versions[key] = version
                if fault == "after_version_insert":
                    raise ContractViolation("Simulated interruption after version insert")
                db.raw_record_counts[file_id] = candidate.record_count
                if fault == "after_record_insert":
                    raise ContractViolation("Simulated interruption after record insert")
                action = "INSERTED_NEW_VERSION"
            else:
                if (version.byte_size != candidate.byte_size or
                        version.record_count != candidate.record_count):
                    raise ContractViolation("Same SHA version has conflicting size/count")
                _check_version_integrity(version, db)
                file_id, action = version.file_id, "REUSED_EXISTING_VERSION"

            linked = snap.observations.get(candidate.path)
            if linked is not None and linked != file_id:
                raise ContractViolation("Conflicting target observation/version")
            snap.observations[candidate.path] = file_id
            if fault == "after_observation":
                raise ContractViolation("Simulated interruption after observation insert")
            if linked == file_id:
                action = "ALREADY_LINKED"
            return file_id, action
        return self._atomic(operation, fail_at)

    def finalize(self, *, manifest_verified: bool, manifest_sha256: str | None) -> None:
        """Simulate separate finalization gate; ingestion NEVER calls this."""
        snap = self.snapshot
        if snap is None or snap.status != "in_progress":
            raise ContractViolation("No in-progress snapshot")
        if set(snap.observations) != set(snap.expected_paths):
            raise ContractViolation("Cannot complete: missing or extra Raw observations")
        if not manifest_verified or not isinstance(manifest_sha256, str) or not SHA256_RE.fullmatch(manifest_sha256):
            raise ContractViolation("Cannot complete: manifest proof missing")
        for path, file_id in snap.observations.items():
            versions = [v for v in self.versions.values()
                        if v.file_id == file_id and v.path == path and v.repo_id == snap.repo_id]
            if len(versions) != 1:
                raise ContractViolation("Cannot complete: invalid observation")
            _check_version_integrity(versions[0], self)
        snap.manifest_sha256 = manifest_sha256
        snap.status = "completed"


def _check_version_integrity(version: StoredVersion, db: MemoryDB) -> None:
    if version.archive_state != "verified" or version.parse_state != "success":
        raise ContractViolation("Cannot link unverified/incomplete Raw file version")
    if (type(version.record_count) is not int or version.record_count < 0 or
            db.raw_record_counts.get(version.file_id) != version.record_count):
        raise ContractViolation("Physical record count does not match file metadata")
