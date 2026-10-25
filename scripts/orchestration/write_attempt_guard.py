"""M5.5.4e.3: fail-closed durable, single-attempt fence for each repo/revision/stage.

A claim is reserved via OS-level O_EXCL before the first M4 writer subprocess.
Claims are never deleted or replaced automatically. An incomplete/corrupt claim,
a crash between claim and spawn, a timeout, or an ambiguous partial write all
block another attempt for the same repo/revision/stage until manual review.

This journal is local, durable M5 orchestration metadata, NOT an M4 checkpoint,
DB evidence, write approval, or recovery authorization. Only use on a trusted
filesystem. It does not replace a DB/advisory lock for distributed writers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Sequence

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STAGE = re.compile(r"[A-Z][A-Z0-9_]{1,63}\Z")
_STATUSES = frozenset({"VERIFIED_AFTER_WRITE", "UNVERIFIED_AFTER_ATTEMPT"})


class WriteAttemptBlocked(RuntimeError):
    """Attempt ledger exists, cannot be safely created, or may be ambiguous."""


@dataclass(frozen=True)
class AttemptClaim:
    claim_path: Path
    claim_sha256: str


def _validate_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise WriteAttemptBlocked("Trusted real project root required")
    return root.resolve(strict=True)


def _mkdir_safe(root: Path, components: tuple[str, ...]) -> Path:
    current = root
    for part in components:
        if not part or part in (".", "..") or "/" in part or "\\" in part:
            raise WriteAttemptBlocked("Unsafe journal directory component")
        current = current / part
        if current.is_symlink():
            raise WriteAttemptBlocked("Journal directory symlink is prohibited")
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if not current.is_dir() or current.is_symlink():
                raise WriteAttemptBlocked("Journal path is not a real directory")
        except OSError as exc:
            raise WriteAttemptBlocked("Cannot create attempt journal directory") from exc
        if not current.is_dir() or current.is_symlink():
            raise WriteAttemptBlocked("Journal directory changed unexpectedly")
    return current


def _atomic_exclusive_json(path: Path, obj: dict) -> str:
    payload = (json.dumps(obj, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise WriteAttemptBlocked(
            "Existing write attempt for this repository/revision/stage; "
            "manual recovery review required before any re-execution"
        ) from exc
    except OSError as exc:
        raise WriteAttemptBlocked("Could not exclusively reserve write attempt") from exc
    # NEVER unlink an already created reservation, even if writing it fails.
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        raise WriteAttemptBlocked(
            "Attempt reservation may be partial; manual review required"
        ) from exc
    # POSIX directory durability where supported; on Windows os.open(dir) may
    # be unsupported, but O_EXCL + fsync(file) still fences normal processes.
    if os.name != "nt":
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError as exc:
            raise WriteAttemptBlocked("Journal directory fsync failed; attempt remains reserved") from exc
    return digest


def reserve_write_attempt(*, root: Path, repo_id: str, target_revision: str,
                          stage: str, context_sha256: str, intent_sha256: str,
                          target_snapshot_id: int | None,
                          argv: Sequence[str]) -> AttemptClaim:
    """Atomic reservation keyed by repo/revision/STAGE (not intent/context).

    Keying on stage intentionally prevents bypass by changing the planned
    context or CLI argv for an ambiguous or partially committed attempt.
    """
    trusted = _validate_root(root)
    if not isinstance(repo_id, str) or not repo_id or len(repo_id) > 300:
        raise WriteAttemptBlocked("Invalid repository identity")
    if not isinstance(target_revision, str) or not _SHA40.fullmatch(target_revision):
        raise WriteAttemptBlocked("Invalid lowercase pinned revision")
    if not isinstance(stage, str) or not _STAGE.fullmatch(stage):
        raise WriteAttemptBlocked("Invalid stage")
    if (not isinstance(context_sha256, str) or not _SHA256.fullmatch(context_sha256)
            or not isinstance(intent_sha256, str) or not _SHA256.fullmatch(intent_sha256)):
        raise WriteAttemptBlocked("Context/intent SHA256 required")
    if target_snapshot_id is not None and (
        type(target_snapshot_id) is not int or target_snapshot_id <= 0
    ):
        raise WriteAttemptBlocked("Invalid target snapshot ID")
    if (not isinstance(argv, (tuple, list)) or not argv
            or any(not isinstance(v, str) or not v for v in argv)):
        raise WriteAttemptBlocked("Invalid pinned writer argv")
    argv_blob = json.dumps(tuple(argv), ensure_ascii=True, separators=(",", ":")).encode()
    repo_hash = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()
    parent = _mkdir_safe(trusted, ("reports", "m5_write_attempts", "v1", repo_hash, target_revision))
    path = parent / (stage + ".claim.json")
    blob = {
        "schema": "m5-write-attempt-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_id": repo_id,
        "target_revision": target_revision,
        "stage": stage,
        "context_sha256": context_sha256,
        "intent_sha256": intent_sha256,
        "target_snapshot_id": target_snapshot_id,
        "argv_sha256": hashlib.sha256(argv_blob).hexdigest(),
        "status": "RESERVED_OUTCOME_UNKNOWN",
    }
    return AttemptClaim(path, _atomic_exclusive_json(path, blob))


def record_attempt_result(claim: AttemptClaim, *, status: str) -> Path:
    """Record one immutable result. Claim remains permanent regardless of status."""
    if not isinstance(claim, AttemptClaim) or not _SHA256.fullmatch(claim.claim_sha256):
        raise WriteAttemptBlocked("Invalid reservation token")
    if status not in _STATUSES:
        raise WriteAttemptBlocked("Invalid terminal attempt status")
    claim_path = claim.claim_path
    if not claim_path.is_file() or claim_path.is_symlink():
        raise WriteAttemptBlocked("Reservation disappeared or changed type")
    try:
        blob = claim_path.read_bytes()
    except OSError as exc:
        raise WriteAttemptBlocked("Reservation could not be revalidated") from exc
    if hashlib.sha256(blob).hexdigest() != claim.claim_sha256:
        raise WriteAttemptBlocked("Reservation data changed unexpectedly")
    outcome = claim_path.with_name(claim_path.name.replace(".claim.json", ".result.json"))
    _atomic_exclusive_json(outcome, {
        "schema": "m5-write-attempt-result-v1",
        "claim_sha256": claim.claim_sha256,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
    })
    return outcome
