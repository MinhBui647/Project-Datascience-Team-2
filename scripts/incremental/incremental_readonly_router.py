#!/usr/bin/env python3
r"""M4.6.4d.5a | Integrated READ-ONLY incremental routing (no execution).

From the repository root on Windows:
  .\.venv_v2\Scripts\python.exe -m scripts.tools.incremental_readonly_router \
      --target-revision <FULL_LOWERCASE_SHA40> --plan

First uses Recovery Audit (including checkpoint inspection) BEFORE any new-revision
HF metadata request. Routes complete targets to SKIP, in_progress targets to
Recovery Audit, locally prepared but unregistered targets to preparation review,
and truly absent targets to the existing new-revision planner. No writes, downloads,
child processes, checkpoint creation, or authorization to resume ingestion.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable

from scripts.tools.incremental_execution_context import SHA40
from scripts.tools.plan_incremental_orchestrator import (
    DEFAULT_REPO, ReadOnlyOutcome, default_readers, format_report, plan_read_only,
)
from scripts.tools.incremental_recovery_audit import RecoveryAssessment, audit_recovery


@dataclass(frozen=True)
class RoutingOutcome:
    decision: str
    target_revision: str
    recovery: RecoveryAssessment
    planner: ReadOnlyOutcome | None = None
    explanation: str = ""
    write_authorized: bool = False

    def __post_init__(self):
        if self.write_authorized:
            raise ValueError("Read-only routing cannot authorize writes")
        if self.planner is not None and self.recovery.recovery_state != "NO_REGISTERED_SNAPSHOT":
            raise ValueError("Planner is forbidden for a recovered or completed target")
        if self.decision == "PLAN_NEW_REVISION":
            if self.planner is None or self.planner.planned is None:
                raise ValueError("New plan requires a verified in-memory PlannedContext")
        elif self.planner is not None and self.planner.planned is not None:
            raise ValueError("Blocked/skipped route must not expose a new PlannedContext")


def route_read_only(*, repo_id: str, target_revision: str,
                    baseline_snapshot: int | None = None,
                    readers: Any = None,
                    recovery_auditor: Callable[..., RecoveryAssessment] = audit_recovery,
                    ) -> RoutingOutcome:
    """Read-only dispatch: target audit first; new planning second if safe.

    Independent target-state checks guard races. A newly registered target after
    the absence check is NOT automatically resumed, and never re-planned here.
    """
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository ID")
    if not isinstance(target_revision, str) or SHA40.fullmatch(target_revision) is None:
        raise ValueError("Target revision must be pinned lowercase SHA40")
    if baseline_snapshot is not None and (type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise ValueError("Invalid explicit baseline snapshot ID")
    r = readers if readers is not None else default_readers()

    # The audit checks target state and local immutable checkpoints. It does not
    # request HF metadata. Its own PostgreSQL evidence reader is READ ONLY.
    recovery = recovery_auditor(
        repo_id=repo_id, target_revision=target_revision, state_reader=r.read_target_state,
    )
    if not isinstance(recovery, RecoveryAssessment) or recovery.write_authorized:
        raise RuntimeError("Recovery Audit returned an invalid assessment")

    state = recovery.recovery_state
    if state == "COMPLETED":
        sid = recovery.target_snapshot_id
        if type(sid) is not int or sid <= 0:
            raise RuntimeError("Completed target did not provide a snapshot ID")
        # Completed is an immutable/no-work outcome. Distinguish current vs
        # historical by SELECTing the newest completed baseline (no HF).
        latest = r.select_baseline(repo_id)
        latest_id = latest.snapshot_id
        if type(latest_id) is not int or latest_id <= 0:
            raise RuntimeError("Invalid latest completed snapshot ID")
        return RoutingOutcome(
            "SKIP_ALREADY_CURRENT" if sid == latest_id else "SKIP_ALREADY_PROCESSED",
            target_revision, recovery,
        )

    # In-progress or locally staged work is never sent through a fresh planner.
    if state != "NO_REGISTERED_SNAPSHOT":
        if baseline_snapshot is not None:
            return RoutingOutcome("BLOCKED_EXPLICIT_BASELINE_RECOVERY", target_revision,
                                  recovery, explanation=(
                                      "Explicit baseline cannot override the immutable recovery checkpoint; "
                                      "rerun without --baseline-snapshot."))
        if state == "BLOCKED_REVIEW":
            return RoutingOutcome("BLOCKED_RECOVERY_REVIEW", target_revision, recovery)
        allowed = {
            "PREPARATION_PARTIAL", "PREPARED_READY", "REGISTERED_PENDING_REUSE",
            "REUSE_COMPLETE", "CANDIDATES_PARTIAL", "CANDIDATES_COMPLETE",
            "MANIFEST_REGISTERED",
        }
        if state not in allowed:
            return RoutingOutcome("BLOCKED_UNKNOWN_RECOVERY_STATE", target_revision,
                                  recovery, explanation="Unknown Recovery Audit state")
        return RoutingOutcome("RECOVERY_REVIEW_REQUIRED", target_revision, recovery)

    if recovery.target_snapshot_id is not None:
        raise RuntimeError("Absent snapshot assessment returned an unexpected target ID")
    if recovery.blockers:
        return RoutingOutcome("BLOCKED_ABSENCE_WITH_BLOCKERS", target_revision, recovery,
                              explanation="Unexpected blockers in absent-target assessment")

    # Safe to enter the existing planner ONLY when no target or checkpoint
    # evidence exists. The planner independently rechecks target state before HF.
    planned = plan_read_only(repo_id=repo_id, target_revision=target_revision,
                             baseline_snapshot=baseline_snapshot, readers=r)
    if planned.decision == "PLAN_NEW_REVISION":
        # A concurrent snapshot registration after the planner's first gate
        # invalidates the preview; discard the plan rather than expose stale steps.
        if r.read_target_state(repo_id, target_revision) is not None:
            return RoutingOutcome("BLOCKED_TARGET_STATE_CHANGED", target_revision, recovery,
                                  explanation="Target appeared while building the read-only plan; rerun audit")
        return RoutingOutcome("PLAN_NEW_REVISION", target_revision, recovery, planned)
    if planned.decision.startswith("SKIP_"):
        return RoutingOutcome(planned.decision, target_revision, recovery, planned)
    # Includes RECOVERY_REQUIRED on a race: do not recover without new audit.
    return RoutingOutcome("BLOCKED_PLANNING_GATE", target_revision, recovery,
                          explanation=("Planner returned " + planned.decision +
                                       "; rerun read-only audit after checking state"))


def format_routing_report(result: RoutingOutcome) -> str:
    """No runnable write instructions are emitted by any branch."""
    if not isinstance(result, RoutingOutcome):
        raise TypeError("RoutingOutcome required")
    a = result.recovery
    lines = [
        "=== M4.6.4d.5a | INTEGRATED READ-ONLY INCREMENTAL ROUTER ===",
        f"Target revision: {result.target_revision}",
        f"Decision: {result.decision}",
        f"Recovery state: {a.recovery_state}",
        f"Target snapshot: {'ABSENT' if a.target_snapshot_id is None else '#' + str(a.target_snapshot_id)}",
        f"REUSE verified: {a.verified_reuse}/{a.expected_reuse}",
        f"Candidates verified: {a.verified_candidates}/{a.expected_candidates}",
        f"Next step (manual review): {a.next_step}",
    ]
    if a.recovery_state == "COMPLETED":
        lines.append("Completed target: skipped; observation counts are not re-audited here.")
    for blocker in a.blockers:
        lines.append("BLOCKER: " + blocker)
    if result.explanation:
        lines.append("NOTE: " + result.explanation)
    if result.planner is not None and result.decision == "PLAN_NEW_REVISION":
        lines.extend(("", format_report(result.planner)))
    lines.extend((
        "HF file-body downloads: NONE | Subprocesses: NONE",
        "PostgreSQL writes: NONE | Cache/archive writes: NONE | Checkpoints written: NONE",
        "Write authorized: NO | Recovery continuation: MANUAL REVIEW ONLY",
        "M4.6.4d.5a READ-ONLY ROUTING: " + (
            "BLOCKED (NO WRITES)" if result.decision.startswith("BLOCKED_")
            else "PASS (NOT WRITE APPROVAL)"),
    ))
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-revision", required=True, help="Pinned lowercase SHA40")
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--baseline-snapshot", type=int, default=None,
                        help="Allowed only for an absent target without recovery checkpoints")
    parser.add_argument("--plan", required=True, action="store_true", help="Read only; no write mode exists")
    args = parser.parse_args(argv)
    if not isinstance(args.repo_id, str) or not args.repo_id.strip():
        parser.error("Repository cannot be blank")
    if SHA40.fullmatch(args.target_revision) is None:
        parser.error("Target revision must be pinned lowercase SHA40")
    if args.baseline_snapshot is not None and args.baseline_snapshot <= 0:
        parser.error("Baseline snapshot must be positive")
    return args


def main(argv=None, *, readers=None, recovery_auditor=audit_recovery,
         output: Callable[[str], None] = print) -> int:
    args = parse_args(argv)
    result = route_read_only(repo_id=args.repo_id, target_revision=args.target_revision,
                             baseline_snapshot=args.baseline_snapshot,
                             readers=readers, recovery_auditor=recovery_auditor)
    output(format_routing_report(result))
    return 2 if result.decision.startswith("BLOCKED_") else 0


if __name__ == "__main__":
    raise SystemExit(main())
