"""M5.5.4e.4b | Read-only durable write-attempt journal auditor.

No journal repair, claim deletion, execution approval, writer launch, DB write,
archive write, or checkpoint creation. This is a *local single-machine* audit.
An existing claim fences every retry regardless of recorded result.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STAGES = frozenset({
    "SNAPSHOT_REGISTRATION", "REUSE_LINKING", "CANDIDATE_INGESTION",
    "MANIFEST_REGISTRATION", "FINALIZATION",
})
_JOURNAL_COMPONENTS = ("reports", "m5_write_attempts", "v1")
_MAX_BYTES = 16384


@dataclass(frozen=True)
class RecoveryAudit:
    state: str
    repo_id: str
    target_revision: str
    stage: str
    claim_present: bool
    result_present: bool
    claim_sha256: str | None
    journal_status: str | None
    evidence_status: str | None
    detail: str
    write_authorized: bool = False
    retry_authorized: bool = False
    write_executed: bool = False

    def __post_init__(self):
        if self.write_authorized or self.retry_authorized or self.write_executed:
            raise ValueError("Recovery audit cannot authorize or execute writes")


class InvalidJournal(ValueError):
    pass


def _directory(root: Path, repo_id: str, revision: str) -> Path:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise InvalidJournal("Project root must be a real directory")
    if (not isinstance(repo_id, str) or not repo_id or len(repo_id) > 300
            or any(ch in repo_id for ch in ("\\", "\x00"))):
        raise InvalidJournal("Invalid repo_id")
    if not isinstance(revision, str) or not _SHA40.fullmatch(revision):
        raise InvalidJournal("Invalid pinned SHA40")
    trusted_root = root.resolve(strict=True)
    name = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()
    parent = trusted_root
    for part in (*_JOURNAL_COMPONENTS, name, revision):
        parent = parent / part
        if parent.is_symlink():
            raise InvalidJournal("Symlink in write-attempt journal path")
        if parent.exists() and not parent.is_dir():
            raise InvalidJournal("Non-directory in write-attempt journal path")
    return parent


def _read_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if path.is_symlink():
        raise InvalidJournal("Journal entry symlink prohibited")
    if not path.exists():
        return None, None
    if not path.is_file():
        raise InvalidJournal("Journal entry is not a regular file")
    if path.stat().st_size > _MAX_BYTES:
        raise InvalidJournal("Journal entry exceeds size limit")
    data = path.read_bytes()
    if not data or len(data) > _MAX_BYTES:
        raise InvalidJournal("Empty or oversized journal entry")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidJournal("Unreadable journal entry") from exc
    if not isinstance(payload, dict):
        raise InvalidJournal("Journal entry must be a JSON object")
    return payload, hashlib.sha256(data).hexdigest()


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 80:
        return False
    try:
        ts = datetime.fromisoformat(value)
        return ts.tzinfo is not None and ts.utcoffset() is not None
    except ValueError:
        return False


def _validate_claim(obj: dict, repo_id: str, revision: str, stage: str) -> None:
    expected = {
        "schema", "created_at_utc", "repo_id", "target_revision", "stage",
        "context_sha256", "intent_sha256", "target_snapshot_id",
        "argv_sha256", "status",
    }
    if set(obj) != expected:
        raise InvalidJournal("Claim fields missing or unexpected")
    if (obj["schema"] != "m5-write-attempt-v1"
            or obj["status"] != "RESERVED_OUTCOME_UNKNOWN"
            or obj["repo_id"] != repo_id
            or obj["target_revision"] != revision or obj["stage"] != stage):
        raise InvalidJournal("Claim identity/status mismatch")
    if not _timestamp(obj["created_at_utc"]):
        raise InvalidJournal("Invalid claim timestamp")
    for field in ("context_sha256", "intent_sha256", "argv_sha256"):
        if not isinstance(obj[field], str) or not _SHA256.fullmatch(obj[field]):
            raise InvalidJournal("Invalid claim hash: " + field)
    sid = obj["target_snapshot_id"]
    if sid is not None and (type(sid) is not int or sid <= 0):
        raise InvalidJournal("Invalid target snapshot ID")


def _validate_result(obj: dict, claim_sha: str) -> str:
    expected = {"schema", "claim_sha256", "recorded_at_utc", "status"}
    if set(obj) != expected:
        raise InvalidJournal("Result fields missing or unexpected")
    if (obj["schema"] != "m5-write-attempt-result-v1"
            or obj["claim_sha256"] != claim_sha):
        raise InvalidJournal("Result doesn't bind to claim bytes")
    if not _timestamp(obj["recorded_at_utc"]):
        raise InvalidJournal("Invalid result timestamp")
    if obj["status"] not in {"VERIFIED_AFTER_WRITE", "UNVERIFIED_AFTER_ATTEMPT"}:
        raise InvalidJournal("Unexpected result status")
    return obj["status"]


def audit_attempt(*, root: Path, repo_id: str, target_revision: str,
                  stage: str, verifier: Callable[..., Any] | None = None) -> RecoveryAudit:
    """Inspect local journal, optionally also independent read-only M4 evidence.

    A missing claim only means no *local* claim was observed; it does not
    authorize writes and does not establish the absence of other workers.
    Never removes or resets reservations.
    """
    if stage not in _STAGES:
        raise ValueError("Unknown write stage")
    base = dict(repo_id=repo_id, target_revision=target_revision, stage=stage)
    claim_present = False
    result_present = False
    claim_sha = None
    journal_status = None
    evidence_status = None
    try:
        parent = _directory(root, repo_id, target_revision)
        claim_path = parent / (stage + ".claim.json")
        result_path = parent / (stage + ".result.json")
        claim, claim_sha = _read_json_file(claim_path)
        outcome, _ = _read_json_file(result_path)
        claim_present = claim is not None
        result_present = outcome is not None
        if claim is None and outcome is None:
            return RecoveryAudit(
                "NO_LOCAL_ATTEMPT", **base, claim_present=False,
                result_present=False, claim_sha256=None,
                journal_status=None, evidence_status=None,
                detail="No local attempt record; NOT execution authorization",
            )
        if claim is None:
            raise InvalidJournal("Orphan result without claim")
        _validate_claim(claim, repo_id, target_revision, stage)
        journal_status = "RESERVED_OUTCOME_UNKNOWN"
        if outcome is not None:
            journal_status = _validate_result(outcome, claim_sha)
        if verifier is not None:
            evidence = verifier(repo_id=repo_id, target_revision=target_revision,
                                stage=stage, root=root)
            if (getattr(evidence, "stage", None) != stage
                    or getattr(evidence, "write_authorized", None) is not False
                    or getattr(evidence, "status", None) not in (
                        "VERIFIED", "INCOMPLETE", "BLOCKED")):
                raise InvalidJournal("Invalid external evidence result")
            evidence_status = evidence.status
        if journal_status == "VERIFIED_AFTER_WRITE" and evidence_status == "VERIFIED":
            state = "EVIDENCE_CONSISTENT_MANUAL_REVIEW"
            detail = "Journal and independent stage evidence agree; no retry or stage advancement authorized"
        elif (journal_status == "VERIFIED_AFTER_WRITE"
              and evidence_status in ("INCOMPLETE", "BLOCKED")):
            state = "BLOCKED_CONTRADICTORY_EVIDENCE"
            detail = "Verified journal conflicts with current independent stage evidence"
        elif journal_status == "UNVERIFIED_AFTER_ATTEMPT":
            state = "MANUAL_RECONCILIATION_REQUIRED"
            detail = "Writer outcome was unverified; inspect durable DB/archive evidence before manual recovery"
        elif journal_status == "VERIFIED_AFTER_WRITE":
            state = "RECORDED_VERIFIED_REQUIRES_EVIDENCE"
            detail = "Recorded verifier success is historical; independent current verification not requested"
        else:
            state = "AMBIGUOUS_ATTEMPT_BLOCKED"
            detail = "Claim exists without terminal result; writer may have run; manual review required"
        return RecoveryAudit(
            state, **base, claim_present=claim_present,
            result_present=result_present, claim_sha256=claim_sha,
            journal_status=journal_status, evidence_status=evidence_status,
            detail=detail,
        )
    except (InvalidJournal, OSError, UnicodeError) as exc:
        return RecoveryAudit(
            "BLOCKED_INVALID_JOURNAL", **base, claim_present=claim_present,
            result_present=result_present, claim_sha256=claim_sha,
            journal_status=journal_status, evidence_status=evidence_status,
            detail=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        # Fail-closed even on a misconfigured/inoperable optional evidence reader.
        return RecoveryAudit(
            "BLOCKED_AUDIT_FAILURE", **base, claim_present=claim_present,
            result_present=result_present, claim_sha256=claim_sha,
            journal_status=journal_status, evidence_status=evidence_status,
            detail=f"{type(exc).__name__}: {exc}",
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit", action="store_true", required=True)
    p.add_argument("--repo-id", required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--stage", choices=sorted(_STAGES), required=True)
    p.add_argument("--verify-stage-evidence", action="store_true",
                   help="Also call existing M4 verifier in read-only mode")
    args = p.parse_args(argv)
    checker = None
    if args.verify_stage_evidence:
        from scripts.incremental.incremental_guarded_stage_executor import verify_stage_evidence
        checker = verify_stage_evidence
    from scripts.incremental.incremental_execution_context import ROOT
    result = audit_attempt(
        root=ROOT, repo_id=args.repo_id, target_revision=args.target_revision,
        stage=args.stage, verifier=checker,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    if result.state.startswith("BLOCKED_"):
        return 2
    if result.state == "NO_LOCAL_ATTEMPT":
        return 0
    return 1  # Every existing claim demands review, even with VERIFIED evidence.


if __name__ == "__main__":
    raise SystemExit(main())
