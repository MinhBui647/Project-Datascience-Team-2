"""Append-only recovery evidence bound to the original permanent M5 claim.

No claim/result is deleted or rewritten. A missing terminal process result is
never interpreted as permission to retry. Only the automation dispatcher uses
these receipts after independent PostgreSQL evidence checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


class RecoveryJournalError(RuntimeError):
    pass


def _timestamp(value) -> bool:
    if not isinstance(value, str) or len(value) > 80:
        return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _read(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 16384:
        raise RecoveryJournalError("Missing, unsafe or oversized recovery receipt")
    data = path.read_bytes()
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise RecoveryJournalError("Malformed recovery receipt") from exc
    if not isinstance(value, dict):
        raise RecoveryJournalError("Recovery receipt must be an object")
    return value, hashlib.sha256(data).hexdigest()


def _create(path: Path, value: dict) -> str:
    payload = (json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RecoveryAttempt:
    number: int
    claim_sha256: str
    result_sha256: str | None
    outcome: str | None
    verified: bool
    category: str | None = None


class StageJournal:
    def __init__(self, *, root: Path, repo_id: str, revision: str, stage: str,
                 original_claim_sha256: str, context_sha256: str, argv: tuple[str, ...]):
        from scripts.orchestration.auto_ingest import DB_STAGES
        if (not isinstance(root, Path) or not root.is_dir() or root.is_symlink() or stage not in DB_STAGES
                or not isinstance(repo_id, str) or not 0 < len(repo_id) <= 300
                or any(ord(character) < 32 for character in repo_id)
                or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in (original_claim_sha256, context_sha256))
                or not isinstance(argv, tuple) or not argv
                or any(not isinstance(value, str) or not value or "\0" in value for value in argv)):
            raise RecoveryJournalError("Invalid recovery journal identity")
        repo_hash = hashlib.sha256(repo_id.encode()).hexdigest()
        self.directory = root
        for part in ("reports", "auto_ingest", "recovery", repo_hash, revision, stage):
            self.directory /= part
            if self.directory.is_symlink() or (self.directory.exists() and not self.directory.is_dir()):
                raise RecoveryJournalError("Unsafe recovery journal directory")
        self.identity = dict(repo_id=repo_id, target_revision=revision, stage=stage,
                             original_claim_sha256=original_claim_sha256,
                             context_sha256=context_sha256, argv_sha256=digest_json(argv))

    def history(self) -> tuple[RecoveryAttempt, ...]:
        if not self.directory.exists():
            return ()
        names = {path.name for path in self.directory.iterdir()}
        allowed = {f"{number:04d}.{kind}.json" for number in range(1, 6)
                   for kind in ("claim", "result", "verified")}
        if names - allowed:
            raise RecoveryJournalError("Unexpected file or attempt count in recovery journal")
        result = []
        previous = None
        for number in range(1, 6):
            claim_name = f"{number:04d}.claim.json"
            if claim_name not in names:
                if any(name >= f"{number:04d}" for name in names):
                    raise RecoveryJournalError("Recovery attempt chain has a gap or orphan result")
                break
            claim, claim_sha = _read(self.directory / claim_name)
            expected = dict(schema="auto-recovery-attempt-v1", attempt=number,
                            previous_result_sha256=previous, **self.identity)
            if (set(claim) != set(expected) | {"created_at_utc"}
                    or not _timestamp(claim["created_at_utc"])
                    or {key: value for key, value in claim.items() if key != "created_at_utc"} != expected):
                raise RecoveryJournalError("Recovery claim identity/context/command drift")
            outcome, outcome_sha, verified = None, None, False
            category = None
            result_path = self.directory / f"{number:04d}.result.json"
            marker_path = self.directory / f"{number:04d}.verified.json"
            if result_path.name in names:
                payload, outcome_sha = _read(result_path)
                if (set(payload) != {"schema", "claim_sha256", "outcome", "category", "exit_code", "created_at_utc"}
                        or payload["schema"] != "auto-recovery-process-result-v1"
                        or not _timestamp(payload["created_at_utc"])
                        or payload["claim_sha256"] != claim_sha
                        or payload["outcome"] not in ("CHILD_SUCCEEDED", "TRANSIENT_FAILURE", "OUTCOME_UNKNOWN")
                        or payload["category"] not in (None, "network", "http", "database", "unknown")
                        or (payload["exit_code"] is not None and type(payload["exit_code"]) is not int)):
                    raise RecoveryJournalError("Invalid recovery process result")
                outcome, category = payload["outcome"], payload["category"]
                if outcome == "CHILD_SUCCEEDED" and payload["exit_code"] != 0:
                    raise RecoveryJournalError("Successful process has a conflicting exit code")
                if outcome == "TRANSIENT_FAILURE" and (
                        category is None or type(payload["exit_code"]) is not int or payload["exit_code"] == 0):
                    raise RecoveryJournalError("Transient process failure lacks terminal evidence")
                if marker_path.name in names:
                    marker, _ = _read(marker_path)
                    if (set(marker) != {"schema", "result_sha256", "created_at_utc"}
                            or marker["schema"] != "auto-recovery-stage-verified-v1"
                            or not _timestamp(marker["created_at_utc"])
                            or marker["result_sha256"] != outcome_sha or outcome == "OUTCOME_UNKNOWN"):
                        raise RecoveryJournalError("Invalid independent verification marker")
                    verified = True
            elif marker_path.name in names:
                raise RecoveryJournalError("Verification marker has no process result")
            if result and (result[-1].outcome != "TRANSIENT_FAILURE" or result[-1].verified):
                raise RecoveryJournalError("Later attempt bypasses an ambiguous/successful prior process")
            result.append(RecoveryAttempt(number, claim_sha, outcome_sha, outcome, verified, category))
            previous = outcome_sha
        return tuple(result)

    def begin(self) -> RecoveryAttempt:
        history = self.history()
        if len(history) >= 5 or (history and (history[-1].outcome != "TRANSIENT_FAILURE" or history[-1].verified)):
            raise RecoveryJournalError("Recovery writer attempt is not permitted by previous terminal evidence")
        self.directory.mkdir(parents=True, exist_ok=True)
        number = len(history) + 1
        value = dict(schema="auto-recovery-attempt-v1", attempt=number,
                     previous_result_sha256=history[-1].result_sha256 if history else None,
                     created_at_utc=datetime.now(timezone.utc).isoformat(), **self.identity)
        sha = _create(self.directory / f"{number:04d}.claim.json", value)
        return RecoveryAttempt(number, sha, None, None, False)

    def finish(self, attempt: RecoveryAttempt, *, outcome: str,
               category: str | None = None, exit_code: int | None = None) -> None:
        if (outcome not in ("CHILD_SUCCEEDED", "TRANSIENT_FAILURE", "OUTCOME_UNKNOWN")
                or category not in (None, "network", "http", "database", "unknown")
                or (exit_code is not None and type(exit_code) is not int)
                or (outcome == "CHILD_SUCCEEDED" and exit_code != 0)
                or (outcome == "TRANSIENT_FAILURE" and
                    (category is None or type(exit_code) is not int or exit_code == 0))):
            raise RecoveryJournalError("Invalid recovery process result fields")
        history = self.history()
        if not history:
            raise RecoveryJournalError("No recovery attempt to finish")
        current = history[-1]
        if current != attempt or current.outcome is not None:
            raise RecoveryJournalError("Recovery attempt changed before recording process result")
        _create(self.directory / f"{attempt.number:04d}.result.json", dict(
            schema="auto-recovery-process-result-v1", claim_sha256=attempt.claim_sha256,
            outcome=outcome, category=category, exit_code=exit_code,
            created_at_utc=datetime.now(timezone.utc).isoformat()))
        self.history()  # Validate even caller-supplied result fields.

    def mark_verified(self) -> None:
        history = self.history()
        if not history:
            raise RecoveryJournalError("No recovery attempt to verify")
        latest = history[-1]
        if latest.outcome not in ("CHILD_SUCCEEDED", "TRANSIENT_FAILURE"):
            raise RecoveryJournalError("Unknown process outcome cannot be auto-reconciled")
        if not latest.verified:
            _create(self.directory / f"{latest.number:04d}.verified.json", dict(
                schema="auto-recovery-stage-verified-v1", result_sha256=latest.result_sha256,
                created_at_utc=datetime.now(timezone.utc).isoformat()))
