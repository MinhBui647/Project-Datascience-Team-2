#!/usr/bin/env python3
"""M4.6.4d.6c.2b.2b.2 - hardened fail-closed READ-ONLY post-execution verifier.

This first milestone uses existing checkpoint and recovery auditors, rather than
trusting a child process exit status. It deliberately leaves stages that lack
an independent completed-stage audit INCOMPLETE (never falsely VERIFIED).
Does NOT execute stage commands, grant write authority, or mutate anything.

Run (from the project root):
    python -m scripts.tools.incremental_post_execution_verifier \
        --target-revision <lowercase_sha40> --stage REUSE_LINKING --plan
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

STAGES = (
    "ROUTE", "PLANNED", "MANIFEST_PREPARATION", "CACHE_DOWNLOAD",
    "ARCHIVE_PROMOTION", "PREPARED", "SNAPSHOT_REGISTRATION",
    "REUSE_LINKING", "CANDIDATE_INGESTION", "MANIFEST_REGISTRATION",
    "FINALIZATION",
)
STATUSES = frozenset(("VERIFIED", "INCOMPLETE", "BLOCKED"))


@dataclass(frozen=True)
class VerificationResult:
    stage: str
    status: str
    evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.stage not in STAGES or self.status not in STATUSES:
            raise ValueError("Invalid stage or verification status")
        if self.write_authorized:
            raise ValueError("Post-execution verification never grants writes")
        if self.status == "VERIFIED" and self.blockers:
            raise ValueError("VERIFIED cannot contain blockers")


def _audit_consistency_error(state: Any) -> str | None:
    """Reject contradictory RecoveryAssessment state/count pairs, even in fixtures.

    Real audit_recovery() already enforces these invariants. Rechecking here
    prevents an inconsistent/stale injected object from becoming VERIFIED.
    This routine is pure and does not grant permission to advance a stage.
    """
    r = state.recovery_state
    fields = (
        state.expected_reuse, state.verified_reuse,
        state.expected_candidates, state.verified_candidates,
    )
    if any(type(n) is not int or n < 0 for n in fields):
        return "Invalid RecoveryAssessment observation counts"
    er, vr, ec, vc = fields
    if vr > er or vc > ec:
        return "Verified observation counts exceed expected counts"
    if r == "REGISTERED_PENDING_REUSE":
        ok = vr < er and vc == 0
    elif r == "REUSE_COMPLETE":
        ok = vr == er and vc == 0
    elif r == "CANDIDATES_PARTIAL":
        ok = vr == er and 0 < vc < ec
    elif r == "CANDIDATES_COMPLETE":
        ok = vr == er and vc == ec and ec > 0
    elif r == "MANIFEST_REGISTERED":
        ok = vr == er and vc == ec
    else:
        return None  # A separate disposition handles pre-registration/completed/blocked.
    if not ok:
        return "Recovery state contradicts audited observation counts"
    return None


def classify_db_stage(stage: str, state: Any, *, target_id: int | None) -> VerificationResult:
    """Pure, conservative classification of independent Recovery Audit evidence.

    Must only receive an assessment produced by audit_recovery(), not CLI stdout.
    audit_recovery short-circuits completed targets, therefore FINALIZATION is
    never VERIFIED here until a separate completed-snapshot audit is integrated.
    """
    if stage not in ("SNAPSHOT_REGISTRATION", "REUSE_LINKING",
                     "CANDIDATE_INGESTION", "MANIFEST_REGISTRATION",
                     "FINALIZATION"):
        raise ValueError("Not a DB stage")
    if (state is None or getattr(state, "write_authorized", True) is not False
            or not isinstance(getattr(state, "blockers", None), tuple)):
        return VerificationResult(stage, "BLOCKED", blockers=("Invalid recovery evidence",))
    if any(not isinstance(v, str) or not v for v in state.blockers):
        return VerificationResult(stage, "BLOCKED", blockers=("Malformed recovery audit blocker",))
    recovery = getattr(state, "recovery_state", None)
    if recovery == "BLOCKED_REVIEW" or state.blockers:
        return VerificationResult(stage, "BLOCKED", blockers=state.blockers or ("Recovery audit blocked",))
    if (type(target_id) is not int or target_id <= 0
            or type(getattr(state, "target_snapshot_id", None)) is not int
            or state.target_snapshot_id != target_id):
        return VerificationResult(stage, "INCOMPLETE", blockers=(
            "Target snapshot ID not independently confirmed",))
    if recovery == "COMPLETED":
        return VerificationResult(stage, "INCOMPLETE", blockers=(
            "Completed target requires independent post-finalization reconciliation",))

    valid = {
        "REGISTERED_PENDING_REUSE", "REUSE_COMPLETE", "CANDIDATES_PARTIAL",
        "CANDIDATES_COMPLETE", "MANIFEST_REGISTERED",
    }
    if recovery not in valid:
        return VerificationResult(stage, "INCOMPLETE", blockers=(
            "Stage not established by recovery audit",))
    try:
        inconsistency = _audit_consistency_error(state)
    except (AttributeError, TypeError, ValueError) as exc:
        return VerificationResult(stage, "BLOCKED", blockers=(
            f"Malformed recovery evidence: {type(exc).__name__}",))
    if inconsistency:
        return VerificationResult(stage, "BLOCKED", blockers=(inconsistency,))
    if stage == "SNAPSHOT_REGISTRATION":
        return VerificationResult(stage, "VERIFIED", (
            "Registered checkpoint and in_progress DB state independently audited",))
    exp_r, seen_r = state.expected_reuse, state.verified_reuse
    exp_c, seen_c = state.expected_candidates, state.verified_candidates
    if stage == "REUSE_LINKING":
        ok = seen_r == exp_r
        note = f"REUSE observations {seen_r}/{exp_r}"
    elif stage == "CANDIDATE_INGESTION":
        ok = seen_r == exp_r and seen_c == exp_c
        note = f"REUSE {seen_r}/{exp_r}; candidates {seen_c}/{exp_c}"
    elif stage == "MANIFEST_REGISTRATION":
        ok = recovery == "MANIFEST_REGISTERED" and seen_r == exp_r and seen_c == exp_c
        note = f"Manifest registered; REUSE {seen_r}/{exp_r}; candidates {seen_c}/{exp_c}"
    else:
        return VerificationResult(stage, "INCOMPLETE", blockers=(
            "FINALIZATION requires completed-snapshot DB/manifest/record reconciliation",))
    return (VerificationResult(stage, "VERIFIED", (note,)) if ok
            else VerificationResult(stage, "INCOMPLETE", blockers=(note,)))


def verify_read_only(*, stage: str, repo_id: str, target_revision: str,
                     root: Path | None = None,
                     checkpoint_reader: Callable | None = None,
                     recovery_reader: Callable | None = None,
                     cache_reader: Callable | None = None) -> VerificationResult:
    """Reads only; fail closed on invalid or contradictory evidence."""
    from scripts.tools.incremental_execution_context import ROOT, SHA40
    from scripts.tools.incremental_recovery_audit import (
        read_checkpoints, verified_manifest, verify_candidate_archives,
        audit_recovery,
    )
    from scripts.tools.incremental_runner_contracts import audit_cache_evidence, _check_evidence

    if stage not in STAGES:
        raise ValueError("Unsupported stage")
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository ID")
    if not isinstance(target_revision, str) or SHA40.fullmatch(target_revision) is None:
        raise ValueError("Target revision must be a pinned lowercase SHA40")
    actual_root = ROOT if root is None else Path(root)
    result = lambda status, *ev: VerificationResult(stage, status, tuple(ev))
    if stage == "ROUTE":
        return VerificationResult(stage, "INCOMPLETE", blockers=(
            "ROUTE is advisory; there is no independent post-execution write to verify",))
    try:
        planned, prepared, registered = (checkpoint_reader or read_checkpoints)(actual_root, target_revision)
        if planned is None:
            return VerificationResult(stage, "INCOMPLETE", blockers=("No planned checkpoint",))
        if (planned.repo_id, planned.target_revision) != (repo_id, target_revision):
            raise RuntimeError("Planned checkpoint differs from pinned repository/revision")
        if prepared is not None and prepared.planned != planned:
            raise RuntimeError("Prepared context has wrong parent")
        if registered is not None and (prepared is None or registered.prepared != prepared):
            raise RuntimeError("Registered context has wrong parent")

        if stage == "PLANNED":
            return result("VERIFIED", "Planned checkpoint independently read and validated")
        if stage == "CACHE_DOWNLOAD":
            try:
                evidence = (cache_reader or audit_cache_evidence)(planned, root=actual_root)
                # audit_cache_evidence() performs independent read-only checks;
                # also enforce its pinned-plan accounting on the returned object.
                _check_evidence(planned, evidence)
                if not evidence.receipts:
                    return VerificationResult(stage, "INCOMPLETE", blockers=("Empty cache receipts",))
                return result("VERIFIED", "Fresh cache bodies and pinned receipts audited")
            except (FileNotFoundError, NotADirectoryError) as exc:
                return VerificationResult(stage, "INCOMPLETE", blockers=(str(exc),))
        if stage in ("MANIFEST_PREPARATION", "ARCHIVE_PROMOTION", "PREPARED"):
            if prepared is None:
                return VerificationResult(stage, "INCOMPLETE", blockers=(
                    "PreparedContext absent; intermediate-stage evidence requires a separate audit",))
            manifest = verified_manifest(actual_root, prepared)
            if manifest is None:
                return VerificationResult(stage, "INCOMPLETE", blockers=("Immutable manifest not present",))
            if stage == "MANIFEST_PREPARATION":
                return result("VERIFIED", "Identical pinned manifest report and archive rehashed")
            missing = verify_candidate_archives(actual_root, prepared)
            if missing:
                return VerificationResult(stage, "INCOMPLETE", blockers=(
                    "Missing candidate archives: " + ", ".join(missing[:3]),))
            return result("VERIFIED", "Manifest and candidate archives independently rehashed")
        if registered is None:
            return VerificationResult(stage, "INCOMPLETE", blockers=("No registered checkpoint",))
        if (registered.prepared.planned.repo_id, registered.prepared.planned.target_revision) != (repo_id, target_revision):
            raise RuntimeError("Registered checkpoint identity mismatch")
        audit = (recovery_reader or audit_recovery)(repo_id=repo_id,
                                target_revision=target_revision, root=actual_root)

        # Completed snapshots need full independent post-finalization evidence.
        # Recovery state alone is NEVER sufficient to claim VERIFIED.
        if stage == "FINALIZATION" and getattr(audit, "recovery_state", None) == "COMPLETED":
            if (getattr(audit, "write_authorized", True) is not False
                    or not isinstance(getattr(audit, "blockers", None), tuple)
                    or audit.blockers):
                return VerificationResult(stage, "BLOCKED", blockers=(
                    "Invalid completed recovery evidence",))

            if (type(getattr(audit, "target_snapshot_id", None)) is not int
                    or audit.target_snapshot_id != registered.target_snapshot_id):
                return VerificationResult(stage, "INCOMPLETE", blockers=(
                    "Completed snapshot ID differs from registered checkpoint",))

            try:
                from scripts.tools.incremental_completed_snapshot_audit import (
                    audit_completed_snapshot,
                )
                evidence = audit_completed_snapshot(
                    registered, root=actual_root
                )
            except Exception as exc:
                return VerificationResult(stage, "BLOCKED", blockers=(
                    f"Completed snapshot audit failed: {type(exc).__name__}: {exc}",))

            if (not isinstance(evidence, str)
                    or not evidence.startswith(
                        f"Completed snapshot #{registered.target_snapshot_id}; "
                    )):
                return VerificationResult(stage, "BLOCKED", blockers=(
                    "Malformed completed snapshot audit evidence",))

            return result("VERIFIED", evidence)

        return classify_db_stage(stage, audit, target_id=registered.target_snapshot_id)
    except (RuntimeError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
        return VerificationResult(stage, "BLOCKED", blockers=(f"{type(exc).__name__}: {exc}",))


def main(argv: list[str] | None = None) -> int:
    from scripts.tools.incremental_recovery_audit import DEFAULT_REPO
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-id", default=DEFAULT_REPO)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--stage", choices=STAGES, required=True)
    p.add_argument("--plan", action="store_true", required=True,
                   help="Mandatory read-only mode; no apply/write mode exists")
    a = p.parse_args(argv)
    try:
        verdict = verify_read_only(stage=a.stage, repo_id=a.repo_id,
                                   target_revision=a.target_revision)
    except (ValueError, TypeError) as exc:
        p.error(str(exc))
    print(f"=== M4.6.4d.6c.2b.2b.2 | READ-ONLY POST-EXECUTION VERIFICATION ===")
    print(f"Stage: {verdict.stage} | Status: {verdict.status}")
    for line in verdict.evidence:
        print("EVIDENCE: " + line)
    for line in verdict.blockers:
        print("BLOCKER: " + line)
    print("Write authorized: NO | Child subprocesses: NONE | DB writes: NONE")
    print("Checkpoint/cache/archive writes: NONE | VERIFIED != write approval")
    return 0 if verdict.status == "VERIFIED" else 1 if verdict.status == "INCOMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
