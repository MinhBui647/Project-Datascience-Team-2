#!/usr/bin/env python3
"""M4.6.4d.6c.2a | Read-only guarded stage planner for EPL Raw Storage V2.

Use from the project root:
  python -m scripts.tools.incremental_guarded_stage_planner \
      --target-revision <PINNED_LOWERCASE_SHA40> --plan

This module ONLY routes through the existing read-only Router/Recovery Audit and
prints an advisory stage readiness matrix. READY means *ready for read-only
manual stage review*, NEVER permission to execute a writer. Completed snapshots
are not re-audited. No subprocess invocation, checkpoint publication, HF body
fetching, cache/archive writes or SQL mutations. It deliberately does not
provide --stage / --commit / --apply / --run-all options.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Any

from scripts.tools.incremental_execution_context import SHA40, checkpoint_digest
from scripts.tools.incremental_readonly_router import (
    DEFAULT_REPO, RoutingOutcome, route_read_only,
)
from scripts.tools.incremental_runner_contracts import plan_command

STAGE_ORDER = (
    "ROUTE",
    "PLANNED",
    "MANIFEST_PREPARATION",
    "CACHE_DOWNLOAD",
    "ARCHIVE_PROMOTION",
    "PREPARED",
    "SNAPSHOT_REGISTRATION",
    "REUSE_LINKING",
    "CANDIDATE_INGESTION",
    "MANIFEST_REGISTRATION",
    "FINALIZATION",
)
STAGE_STATUS = frozenset(("READY", "WAITING", "COMPLETED", "BLOCKED", "SKIPPED"))

# First stage needing a read-only manual review, by the Recovery Audit state.
# The audit does not provide per-file manifest/cache readiness for preparation
# partials, so MANIFEST_PREPARATION is only an audit entrypoint, not an assertion
# that a manifest needs republishing.
RECOVERY_NEXT = {
    "PREPARATION_PARTIAL": "MANIFEST_PREPARATION",
    "PREPARED_READY": "SNAPSHOT_REGISTRATION",
    "REGISTERED_PENDING_REUSE": "REUSE_LINKING",
    "REUSE_COMPLETE": "CANDIDATE_INGESTION",
    "CANDIDATES_PARTIAL": "CANDIDATE_INGESTION",
    "CANDIDATES_COMPLETE": "MANIFEST_REGISTRATION",
    "MANIFEST_REGISTERED": "FINALIZATION",
}


@dataclass(frozen=True)
class StageEntry:
    name: str
    status: str
    explanation: str = ""

    def __post_init__(self):
        if self.name not in STAGE_ORDER or self.status not in STAGE_STATUS:
            raise ValueError("Invalid guarded stage entry")


@dataclass(frozen=True)
class GuardedStagePlan:
    repo_id: str
    target_revision: str
    decision: str
    recovery_state: str
    target_snapshot_id: int | None
    stages: tuple[StageEntry, ...]
    next_review_stage: str | None
    planned_context_sha256: str | None = None
    read_only_previews: tuple[tuple[str, ...], ...] = ()
    blockers: tuple[str, ...] = ()
    write_authorized: bool = False

    def __post_init__(self):
        if self.write_authorized:
            raise ValueError("Stage planner must never authorize writes")
        if not isinstance(self.repo_id, str) or not self.repo_id.strip():
            raise ValueError("Invalid repository ID")
        if not isinstance(self.target_revision, str) or not SHA40.fullmatch(self.target_revision):
            raise ValueError("Invalid pinned revision")
        if tuple(entry.name for entry in self.stages) != STAGE_ORDER:
            raise ValueError("Incomplete or unordered stage plan")
        if self.next_review_stage is not None and (
            self.next_review_stage not in STAGE_ORDER
            or self.stages[STAGE_ORDER.index(self.next_review_stage)].status != "READY"
        ):
            raise ValueError("Next stage is not marked READY")
        if len({entry.name for entry in self.stages}) != len(STAGE_ORDER):
            raise ValueError("Duplicate stage in plan")
        if self.decision != "PLAN_NEW_REVISION" and self.read_only_previews:
            raise ValueError("Only a freshly planned revision can expose CLI previews")
        for argv in self.read_only_previews:
            if (not isinstance(argv, tuple) or len(argv) < 4 or "-m" not in argv
                    or argv[-1] != "--plan"
                    or any(arg in argv for arg in (
                        "--apply", "--commit", "--download", "--promote",
                        "--register", "--link", "--publish", "--run-all"))):
                raise ValueError("Unsafe command preview")


def _matrix(*, route_status: str, ready: str | None = None,
            completed_through: str | None = None,
            rest_status: str = "WAITING", note: str = "") -> tuple[StageEntry, ...]:
    if route_status not in STAGE_STATUS or rest_status not in STAGE_STATUS:
        raise ValueError("Invalid stage status")
    if ready is not None and ready not in STAGE_ORDER[1:]:
        raise ValueError("Invalid next review stage")
    if completed_through is not None and completed_through not in STAGE_ORDER:
        raise ValueError("Invalid completed-through stage")
    done = STAGE_ORDER.index(completed_through) if completed_through is not None else -1
    result = []
    for index, name in enumerate(STAGE_ORDER):
        if name == "ROUTE":
            status, detail = route_status, "Target-state routing reviewed"
        elif index <= done:
            status, detail = "COMPLETED", "Supported by recovery audit evidence"
        elif name == ready:
            status, detail = "READY", note or "Read-only stage review is possible"
        else:
            status, detail = rest_status, "No evidence of stage readiness yet"
        result.append(StageEntry(name, status, detail))
    return tuple(result)


def _valid_recovery_counts(route: RoutingOutcome) -> None:
    """Reject contradictory RecoveryAssessment counts, not a second DB audit."""
    a = route.recovery
    for name in ("expected_reuse", "verified_reuse", "expected_candidates", "verified_candidates"):
        number = getattr(a, name)
        if type(number) is not int or number < 0:
            raise RuntimeError("Recovery returned invalid counts")
    if (a.verified_reuse > a.expected_reuse
            or a.verified_candidates > a.expected_candidates):
        raise RuntimeError("Recovery has more verified than expected observations")
    if a.recovery_state in RECOVERY_NEXT:
        if a.expected_candidates < 1 or a.expected_reuse < 0:
            raise RuntimeError("Recovery missing candidate accounting")
        before_registration = a.recovery_state in ("PREPARATION_PARTIAL", "PREPARED_READY")
        if before_registration != (a.target_snapshot_id is None):
            raise RuntimeError("Recovery snapshot presence conflicts with stage")
        if not before_registration and (
            type(a.target_snapshot_id) is not int or a.target_snapshot_id <= 0
        ):
            raise RuntimeError("Recovery has invalid registered snapshot ID")
        if before_registration and (a.verified_reuse or a.verified_candidates):
            raise RuntimeError("Unregistered recovery reports DB observations")
        if a.recovery_state == "REGISTERED_PENDING_REUSE":
            if a.verified_reuse >= a.expected_reuse or a.verified_candidates:
                raise RuntimeError("Pending REUSE state has contradictory progress")
        if a.recovery_state in ("REUSE_COMPLETE", "CANDIDATES_PARTIAL",
                                "CANDIDATES_COMPLETE", "MANIFEST_REGISTERED"):
            if a.verified_reuse != a.expected_reuse:
                raise RuntimeError("Recovery requires complete verified REUSE")
        if a.recovery_state == "REUSE_COMPLETE" and a.verified_candidates:
            raise RuntimeError("REUSE_COMPLETE cannot include candidate observations")
        if a.recovery_state == "CANDIDATES_PARTIAL" and not (
            0 < a.verified_candidates < a.expected_candidates
        ):
            raise RuntimeError("CANDIDATES_PARTIAL has contradictory counts")
        if a.recovery_state in ("CANDIDATES_COMPLETE", "MANIFEST_REGISTERED"):
            if a.verified_candidates != a.expected_candidates:
                raise RuntimeError("Completed candidates not fully verified")


def build_stage_plan_from_route(*, repo_id: str, target_revision: str,
                                routed: RoutingOutcome) -> GuardedStagePlan:
    """Pure, fail-closed classification from independently obtained Router result."""
    if not isinstance(routed, RoutingOutcome) or routed.write_authorized:
        raise TypeError("Read-only RoutingOutcome required")
    if (routed.target_revision != target_revision
            or not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300
            or not isinstance(target_revision, str) or not SHA40.fullmatch(target_revision)):
        raise RuntimeError("Router identity differs from requested pinned target")
    a = routed.recovery
    if a.write_authorized:
        raise RuntimeError("Recovery Audit must not authorize writes")
    _valid_recovery_counts(routed)

    common = dict(repo_id=repo_id, target_revision=target_revision,
                  decision=routed.decision, recovery_state=a.recovery_state,
                  target_snapshot_id=a.target_snapshot_id)
    if routed.decision.startswith("BLOCKED_"):
        return GuardedStagePlan(**common,
            stages=_matrix(route_status="BLOCKED", rest_status="BLOCKED"),
            next_review_stage=None,
            blockers=tuple(a.blockers) + ((routed.explanation,) if routed.explanation else ()))

    if routed.decision == "PLAN_NEW_REVISION":
        if a.recovery_state != "NO_REGISTERED_SNAPSHOT" or a.target_snapshot_id is not None:
            raise RuntimeError("New-revision plan came from non-absent target")
        if routed.planner is None or routed.planner.planned is None:
            raise RuntimeError("New revision lacks PlannedContext")
        p = routed.planner.planned
        if p.repo_id != repo_id or p.target_revision != target_revision:
            raise RuntimeError("New PlannedContext identity mismatch")
        if a.blockers:
            raise RuntimeError("New revision has unresolved recovery blockers")
        previews = (plan_command("manifest_preparation", p), plan_command("download", p))
        return GuardedStagePlan(**common, stages=_matrix(
            route_status="COMPLETED", ready="PLANNED", note=(
                "PlannedContext only in memory; checkpoint not written. Manual checkpoint review required")),
            next_review_stage="PLANNED", planned_context_sha256=checkpoint_digest(p),
            read_only_previews=previews)

    if routed.decision.startswith("SKIP_"):
        if a.blockers:
            raise RuntimeError("Cannot skip when Recovery Audit has blockers")
        if a.recovery_state == "COMPLETED":
            if routed.decision not in ("SKIP_ALREADY_CURRENT", "SKIP_ALREADY_PROCESSED"):
                raise RuntimeError("Completed recovery and skip reason conflict")
            if type(a.target_snapshot_id) is not int or a.target_snapshot_id <= 0:
                raise RuntimeError("Completed target lacks valid snapshot ID")
        elif a.recovery_state == "NO_REGISTERED_SNAPSHOT":
            if routed.decision != "SKIP_NO_RAW_CHANGES" or a.target_snapshot_id is not None:
                raise RuntimeError("Unregistered target has invalid skip decision")
        else:
            raise RuntimeError("Recovery state does not justify skipping")
        return GuardedStagePlan(**common, stages=_matrix(
            route_status="COMPLETED", rest_status="SKIPPED"),
            next_review_stage=None)

    if routed.decision == "RECOVERY_REVIEW_REQUIRED":
        if a.blockers or a.recovery_state not in RECOVERY_NEXT:
            raise RuntimeError("Recovery review has an unsupported or blocked state")
        nxt = RECOVERY_NEXT[a.recovery_state]
        predecessor = STAGE_ORDER[STAGE_ORDER.index(nxt) - 1]
        note = ("Preparation partially observed; re-audit MANIFEST/CACHE/ARCHIVE independently"
                if a.recovery_state == "PREPARATION_PARTIAL" else
                "Candidate partial progress; verify missing files before any retry"
                if a.recovery_state == "CANDIDATES_PARTIAL" else
                "Recovery Audit complete; read-only stage review, not execution approval")
        return GuardedStagePlan(**common,
            stages=_matrix(route_status="COMPLETED", completed_through=predecessor,
                           ready=nxt, note=note),
            next_review_stage=nxt)

    raise RuntimeError("Unrecognized router decision; fail-closed: " + routed.decision)


def plan_stages_read_only(*, repo_id: str, target_revision: str,
                          baseline_snapshot: int | None = None,
                          readers: Any = None,
                          router: Callable[..., RoutingOutcome] = route_read_only,
                          ) -> GuardedStagePlan:
    """Exactly one read-only route; no new DB/HF calls beyond existing Router."""
    if (not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300
            or not isinstance(target_revision, str) or not SHA40.fullmatch(target_revision)):
        raise ValueError("Invalid repository or pinned target revision")
    if baseline_snapshot is not None and (type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise ValueError("Baseline snapshot must be a positive integer")
    routed = router(repo_id=repo_id, target_revision=target_revision,
                    baseline_snapshot=baseline_snapshot, readers=readers)
    return build_stage_plan_from_route(repo_id=repo_id, target_revision=target_revision,
                                       routed=routed)


def format_stage_report(plan: GuardedStagePlan) -> str:
    if not isinstance(plan, GuardedStagePlan):
        raise TypeError("GuardedStagePlan required")
    lines = [
        "=== M4.6.4d.6c.2a | READ-ONLY GUARDED STAGE PLANNER ===",
        f"Target revision: {plan.target_revision}",
        f"Router decision: {plan.decision}",
        f"Recovery state: {plan.recovery_state}",
        f"Target snapshot: {'ABSENT' if plan.target_snapshot_id is None else '#' + str(plan.target_snapshot_id)}",
        "STAGES (READY = FOR MANUAL READ-ONLY REVIEW, NOT WRITE APPROVAL):",
    ]
    for stage in plan.stages:
        lines.append(f"  {stage.name:<24} {stage.status:<10} {stage.explanation}")
    lines.append("Next stage for manual review: " + (plan.next_review_stage or "NONE"))
    if plan.planned_context_sha256 is not None:
        lines.append(f"PlannedContext SHA256 (in memory, NOT WRITTEN): {plan.planned_context_sha256}")
    if plan.read_only_previews:
        lines.append("UNEXECUTED read-only argv previews (NOT automatically run):")
        for argv in plan.read_only_previews:
            lines.append("  " + repr(argv))
    for blocker in plan.blockers:
        lines.append("BLOCKER: " + blocker)
    if plan.recovery_state == "COMPLETED":
        lines.append("Completed target: downstream stages SKIPPED, not re-audited by this planner.")
    lines.extend([
        "Write authorized: NO | Subprocesses: NONE | HF file-body downloads: NONE",
        "PostgreSQL writes: NONE | Cache/archive writes: NONE | Checkpoints written: NONE",
        "M4.6.4d.6c.2a STAGE PLANNER: " + (
            "BLOCKED (NO WRITES)" if plan.decision.startswith("BLOCKED_")
            else "PASS (NOT WRITE APPROVAL)"),
    ])
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-revision", required=True, help="Pinned lowercase SHA40; never main")
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--baseline-snapshot", type=int, default=None)
    parser.add_argument("--plan", action="store_true", required=True,
                        help="Read only; no write/executor mode is available")
    args = parser.parse_args(argv)
    if not args.repo_id.strip() or len(args.repo_id) > 300:
        parser.error("Repository ID must be nonempty")
    if SHA40.fullmatch(args.target_revision) is None:
        parser.error("Revision must be a pinned lowercase SHA40")
    if args.baseline_snapshot is not None and args.baseline_snapshot <= 0:
        parser.error("Baseline snapshot must be positive")
    return args


def main(argv=None, *, readers=None, router=route_read_only,
         output: Callable[[str], None] = print) -> int:
    args = parse_args(argv)
    try:
        result = plan_stages_read_only(repo_id=args.repo_id,
                target_revision=args.target_revision,
                baseline_snapshot=args.baseline_snapshot,
                readers=readers, router=router)
        output(format_stage_report(result))
        return 2 if result.decision.startswith("BLOCKED_") else 0
    except (RuntimeError, ValueError, TypeError) as exc:
        output("M4.6.4d.6c.2a STAGE PLANNER: BLOCKED (NO WRITES) | " +
               f"{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
