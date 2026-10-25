#!/usr/bin/env python3
"""M4.6.4d.6c.2b.2b.3 | Guarded Executor + Post-execution Evidence (READ-ONLY).

Safely evaluates one stage and optionally invokes *only* its pinned ``--plan``
CLI children. No mutation flag exists, and StageRule.write_mode is inert metadata.

Use from project root (recommended):
  python -m scripts.tools.incremental_guarded_stage_executor \\
    --target-revision <lowercase_SHA40> --plan

  # Explicit opt-in for *read-only --plan subprocesses*, NOT write approval:
  python -m scripts.tools.incremental_guarded_stage_executor \\
    --target-revision <lowercase_SHA40> --stage REUSE_LINKING \\
    --execute-read-only-plan --approve-stage REUSE_LINKING \\
    --expected-context-sha256 <planned_checkpoint_sha256>

No --commit/--apply/--download/--publish/--register/--link/--run-all mode.
No checkpoint creation, HF body fetch, cache/archive writes or SQL mutations.
The optional evidence inspection invokes the independent read-only verifier.
It does NOT claim the --plan child performed a write or advanced a stage.
No writer dispatch exists. READY/VERIFIED never means write authorization.

Additional read-only modes:
  python -m scripts.tools.incremental_guarded_stage_executor \
    --target-revision <lowercase_SHA40> --stage REUSE_LINKING \
    --verify-stage-evidence

  # Optional extra inspection AFTER successful --plan child runs:
  # append --verify-after-read-only-plan to --execute-read-only-plan command.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from scripts.tools.incremental_execution_context import ROOT, SHA40, SHA256
from scripts.tools.incremental_execution_contract_m46 import (
    ExecutionContract, STAGE_RULES, build_execution_contract,
)
from scripts.tools.incremental_guarded_stage_planner import (
    STAGE_ORDER, GuardedStagePlan, build_stage_plan_from_route,
)
from scripts.tools.incremental_readonly_router import (
    DEFAULT_REPO, RoutingOutcome, route_read_only,
)
from scripts.tools.incremental_recovery_audit import read_checkpoints
from scripts.tools.incremental_runner_contracts import PLAN_MODULES

MAX_PLAN_TIMEOUT = 180
WRITE_TOKENS = frozenset((
    "--commit", "--apply", "--download", "--promote", "--publish", "--register",
    "--link", "--run-all", "--confirm-complete", "--confirm-cache-only",
    "--confirm-manifest-only", "--confirm-archive-only", "--confirm-reuse-only",
    "--confirm-candidate-write", "--confirm-snapshot-only",
    "--confirm-manifest-archive-only",
))


@dataclass(frozen=True)
class StageReview:
    """Immutable read-only view. Not an execution authorization."""
    plan: GuardedStagePlan
    contract: ExecutionContract

    def __post_init__(self) -> None:
        if not isinstance(self.plan, GuardedStagePlan) or not isinstance(self.contract, ExecutionContract):
            raise TypeError("StageReview needs typed read-only plan and contract")
        if self.plan.write_authorized or self.contract.write_authorized:
            raise ValueError("Stage review must never grant write approval")
        if (self.plan.repo_id, self.plan.target_revision) != (
            self.contract.repo_id, self.contract.target_revision
        ):
            raise ValueError("Stage review identity mismatch")
        if self.contract.disposition == "REVIEW_READ_ONLY_PLAN" and (
            self.plan.next_review_stage != self.contract.review_stage
        ):
            raise ValueError("Stage review next-stage mismatch")
        if self.contract.disposition in ("NO_ACTION", "BLOCKED") and self.contract.read_only_argv:
            raise ValueError("Skipped/blocked target cannot expose subprocesses")


@dataclass(frozen=True)
class StageResult:
    decision: str
    stage: str | None
    detail: str
    executed_read_only_commands: int = 0
    context_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.stage is not None and self.stage not in STAGE_ORDER:
            raise ValueError("Unknown reviewed stage")
        if type(self.executed_read_only_commands) is not int or self.executed_read_only_commands < 0:
            raise ValueError("Invalid child count")
        if self.context_sha256 is not None and not SHA256.fullmatch(self.context_sha256):
            raise ValueError("Invalid pinned context hash")


def review_one_stage(*, repo_id: str, target_revision: str,
                     baseline_snapshot: int | None = None,
                     root: Path = ROOT, readers: Any = None,
                     router: Callable[..., RoutingOutcome] = route_read_only,
                     checkpoint_reader: Callable = read_checkpoints) -> StageReview:
    """Exactly one Router pass. Recovery loads *verified* persisted contexts.

    The in-memory PlannedContext is used only for newly absent target revisions.
    No checkpoint or DB records are synthesized in recovery mode.
    """
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository ID")
    if not isinstance(target_revision, str) or SHA40.fullmatch(target_revision) is None:
        raise ValueError("Full pinned lowercase SHA40 required")
    if baseline_snapshot is not None and (type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise ValueError("Invalid baseline snapshot")
    routed = router(repo_id=repo_id, target_revision=target_revision,
                    baseline_snapshot=baseline_snapshot, readers=readers)
    plan = build_stage_plan_from_route(repo_id=repo_id, target_revision=target_revision,
                                       routed=routed)
    planned = prepared = registered = None
    if plan.decision == "PLAN_NEW_REVISION":
        if routed.planner is None or routed.planner.planned is None:
            raise RuntimeError("New plan lacks fresh PlannedContext")
        planned = routed.planner.planned
    elif plan.decision == "RECOVERY_REVIEW_REQUIRED":
        # Recovery path never re-plans an already registered target.
        planned, prepared, registered = checkpoint_reader(root, target_revision)
        if planned is None:
            raise RuntimeError("Recovery needs a verified planned checkpoint")
        if routed.recovery.target_snapshot_id is None and registered is not None:
            raise RuntimeError("Unregistered target has registered checkpoint")
        if routed.recovery.target_snapshot_id is not None and (
            registered is None or registered.target_snapshot_id != routed.recovery.target_snapshot_id
        ):
            raise RuntimeError("Registered checkpoint missing or target ID drifted")
    contract = build_execution_contract(plan, planned=planned, prepared=prepared,
                                        registered=registered)
    return StageReview(plan, contract)


def validate_read_only_commands(review: StageReview) -> tuple[tuple[str, ...], ...]:
    """Fail-closed allowlist for existing *read-only* child CLI previews.

    The ExecutionContract generates argv from typed contexts. This extra gate
    prevents arbitrary child commands, shell wrappers, and write-mode switches.
    """
    if not isinstance(review, StageReview):
        raise TypeError("StageReview required")
    contract = review.contract
    if contract.disposition != "REVIEW_READ_ONLY_PLAN" or contract.blockers:
        raise RuntimeError("Cannot invoke subprocess for blocked/unreviewed contract")
    stage = contract.review_stage
    if stage not in STAGE_RULES or stage != review.plan.next_review_stage:
        raise RuntimeError("Stage drifted from review")
    rule = STAGE_RULES[stage]
    if len(contract.read_only_argv) != len(rule.preview_steps):
        raise RuntimeError("Read-only child count differs from stage contract")
    for step, argv in zip(rule.preview_steps, contract.read_only_argv):
        module = PLAN_MODULES.get(step)
        if not module or not isinstance(argv, tuple) or len(argv) < 4:
            raise RuntimeError("Unexpected stage preview shape")
        if (argv[:3] != (sys.executable, "-m", f"scripts.tools.{module}")
                or argv[-1] != "--plan"
                or any(not isinstance(a, str) for a in argv)
                or any(a in WRITE_TOKENS for a in argv)
                or argv.count("--plan") != 1):
            raise RuntimeError("Unsafe or unpinned read-only command")
        # Each flag must be a known key with exactly one value; no positional
        # shell fragments or Python interpreter options may enter the argv.
        keys = {
            "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
            "--repo-id", "--expected-files", "--expected-bytes",
            "--expected-records", "--expected-baseline-files", "--expected-full-files",
            "--expected-raw-files", "--expected-reuse", "--target-snapshot-id",
            "--expected-baseline-records", "--expected-reused-records",
            "--manifest-report", "--manifest-sha256", "--cache-dir",
        }
        opts = argv[3:-1]
        if len(opts) % 2 or len(set(opts[::2])) != len(opts) // 2:
            raise RuntimeError("Duplicate or malformed read-only flags")
        if not all(flag in keys for flag in opts[::2]):
            raise RuntimeError("Unexpected CLI flag in preview")
        if any(not val or val.startswith("--") for val in opts[1::2]):
            raise RuntimeError("Invalid read-only CLI flag value")
        required = ("--repo-id", "--target-revision", "--baseline-snapshot",
                    "--expected-fingerprint")
        if any(k not in opts[::2] for k in required):
            raise RuntimeError("Unpinned preview command")
        expected = {
            "--repo-id": review.plan.repo_id,
            "--target-revision": review.plan.target_revision,
        }
        provided = dict(zip(opts[::2], opts[1::2]))
        if any(provided[k] != value for k, value in expected.items()):
            raise RuntimeError("Preview identity drift")
        if (SHA256.fullmatch(provided["--expected-fingerprint"]) is None
                or not provided["--baseline-snapshot"].isdigit()
                or int(provided["--baseline-snapshot"]) <= 0):
            raise RuntimeError("Invalid baseline or fingerprint in pinned plan preview")
    return contract.read_only_argv


def _check_approval(*, review: StageReview, stage: str,
                    approve_stage: str | None,
                    expected_context_sha256: str | None) -> None:
    if stage not in STAGE_ORDER[1:]:
        raise RuntimeError("Unknown or non-executable stage")
    if stage != approve_stage or stage != review.plan.next_review_stage:
        raise RuntimeError("Single-stage approval or next stage mismatch")
    if review.contract.disposition != "REVIEW_READ_ONLY_PLAN" or review.contract.review_stage != stage:
        raise RuntimeError("Stage is blocked, skipped or missing evidence")
    if (expected_context_sha256 is None or SHA256.fullmatch(expected_context_sha256) is None
            or review.contract.context_sha256 != expected_context_sha256):
        raise RuntimeError("Explicit context SHA256 missing or mismatched")


def execute_read_only_plan(*, repo_id: str, target_revision: str,
                           stage: str, approve_stage: str,
                           expected_context_sha256: str,
                           baseline_snapshot: int | None = None,
                           root: Path = ROOT, readers: Any = None,
                           reviewer: Callable[..., StageReview] = review_one_stage,
                           launcher: Callable[..., Any] = subprocess.run,
                           timeout_seconds: int = MAX_PLAN_TIMEOUT) -> StageResult:
    """Opt-in *plan-only* child execution for exactly one stage.

    Re-evaluates state twice before invoking the first child. After the last
    child it returns a read-only result, NEVER proof of stage completion.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_PLAN_TIMEOUT:
        raise ValueError("Invalid read-only subprocess timeout")
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Real project root directory required")
    kw = dict(repo_id=repo_id, target_revision=target_revision,
              baseline_snapshot=baseline_snapshot, root=root, readers=readers)
    first = reviewer(**kw)
    _check_approval(review=first, stage=stage, approve_stage=approve_stage,
                    expected_context_sha256=expected_context_sha256)
    argv = validate_read_only_commands(first)
    if not argv:
        raise RuntimeError("No independent read-only CLI command exists for this stage")
    second = reviewer(**kw)  # Fresh read-only Router/DB/HF/checkpoint audit.
    _check_approval(review=second, stage=stage, approve_stage=approve_stage,
                    expected_context_sha256=expected_context_sha256)
    if second != first or validate_read_only_commands(second) != argv:
        raise RuntimeError("Target, checkpoint, or stage evidence drifted on re-audit")
    count = 0
    for command in argv:
        try:
            result = launcher(command, cwd=str(root), shell=False, capture_output=True,
                              text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Read-only plan child timeout; re-audit before retry") from exc
        if type(getattr(result, "returncode", None)) is not int or result.returncode != 0:
            raise RuntimeError("Read-only plan child failed; re-audit before retry")
        count += 1
    return StageResult("READ_ONLY_PLANS_RAN", stage,
                       "Child exit codes checked; no writes and no verified stage advancement",
                       count, expected_context_sha256)


def verify_stage_evidence(*, repo_id: str, target_revision: str, stage: str,
                          root: Path = ROOT, verifier: Callable[..., Any] | None = None):
    """Independently inspect existing durable evidence, with NO subprocesses/writes.

    This is evidence about repository/DB state, NOT proof that a preceding
    --plan-only child created that state. It never grants stage advancement.
    Injection is for read-only tests; results must be a typed VerificationResult.
    """
    from scripts.tools.incremental_post_execution_verifier import (
        VerificationResult, verify_read_only,
    )

    if stage not in STAGE_ORDER[1:]:
        raise ValueError("Evidence inspection requires a named non-ROUTE stage")
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository ID")
    if not isinstance(target_revision, str) or SHA40.fullmatch(target_revision) is None:
        raise ValueError("Pinned lowercase SHA40 required")
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Real project root directory required")
    try:
        result = (verifier or verify_read_only)(stage=stage, repo_id=repo_id,
                                                 target_revision=target_revision, root=root)
        if not isinstance(result, VerificationResult):
            raise TypeError("Evidence reader returned non-VerificationResult")
        if result.stage != stage or result.write_authorized:
            raise RuntimeError("Evidence reader returned a different stage or write approval")
        return result
    except Exception as exc:
        # No stale/malformed verifier output is permitted to imply VERIFIED.
        return VerificationResult(stage, "BLOCKED",
                                  blockers=(f"Verifier error: {type(exc).__name__}: {exc}",))


def format_evidence_inspection(result: Any, *, preceding_plan_children: int = 0) -> str:
    """Display independent evidence separately from read-only child exit codes."""
    from scripts.tools.incremental_post_execution_verifier import VerificationResult

    if not isinstance(result, VerificationResult):
        raise TypeError("Expected typed VerificationResult")
    if type(preceding_plan_children) is not int or preceding_plan_children < 0:
        raise ValueError("Invalid preceding child count")
    lines = [
        "=== M4.6.4d.6c.2b.2b.3 | READ-ONLY EXECUTOR x VERIFIER ===",
        f"Evidence stage: {result.stage} | Evidence status: {result.status}",
        f"Preceding --plan children: {preceding_plan_children}",
        "Verification is independent of --plan subprocess exit codes.",
    ]
    lines.extend("EVIDENCE: " + item for item in result.evidence)
    lines.extend("BLOCKER: " + item for item in result.blockers)
    lines.extend([
        "Verification writes: NONE | Additional subprocesses: NONE",
        "Write authorization: NO | Stage advancement: NOT AUTHORIZED",
        "VERIFIED is evidence of existing state, NOT evidence of a write by this executor.",
    ])
    return "\n".join(lines)


def evidence_exit_code(status: str) -> int:
    """Evidence read outcomes are distinct from core read-only plan outcomes."""
    return {"VERIFIED": 0, "INCOMPLETE": 1, "BLOCKED": 2}[status]


def describe_review(review: StageReview, *, stage: str | None = None) -> StageResult:
    """Pure advisory planning, without a subprocess."""
    if not isinstance(review, StageReview):
        raise TypeError("StageReview required")
    current = review.plan.next_review_stage
    if stage is not None and stage not in STAGE_ORDER[1:]:
        raise ValueError("Unknown selected stage")
    if review.plan.decision.startswith("SKIP_"):
        return StageResult("SKIPPED", stage, "Completed/no-change target; no stage runs")
    if review.plan.decision.startswith("BLOCKED_") or review.contract.disposition == "BLOCKED":
        return StageResult("BLOCKED", stage, "; ".join(review.contract.blockers) or "Stage blocked")
    if stage is not None and stage != current:
        return StageResult("BLOCKED", stage, "Requested stage is not current review stage")
    if review.contract.disposition == "AWAITING_EVIDENCE":
        return StageResult("AWAITING_EVIDENCE", current,
                           "; ".join(review.contract.blockers), 0,
                           review.contract.context_sha256)
    if review.contract.disposition == "REVIEW_READ_ONLY_PLAN":
        return StageResult("REVIEW_ONLY", current,
                           "Advisory review only; --plan children not invoked; writers disabled",
                           0, review.contract.context_sha256)
    return StageResult("BLOCKED", stage, "No valid read-only review disposition")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="Print advisory next stage; no child process")
    mode.add_argument("--execute-read-only-plan", action="store_true",
                      help="Explicitly run only allowlisted --plan children for ONE stage")
    mode.add_argument("--verify-stage-evidence", action="store_true",
                      help="Inspect existing evidence; no child subprocess or write")
    p.add_argument("--target-revision", required=True)
    p.add_argument("--repo-id", default=DEFAULT_REPO)
    p.add_argument("--baseline-snapshot", type=int, default=None)
    p.add_argument("--stage", choices=STAGE_ORDER[1:])
    p.add_argument("--approve-stage", choices=STAGE_ORDER[1:])
    p.add_argument("--expected-context-sha256")
    p.add_argument("--verify-after-read-only-plan", action="store_true",
                   help="After successful --plan children, independently inspect existing evidence")
    a = p.parse_args(argv)
    if SHA40.fullmatch(a.target_revision) is None:
        p.error("Target revision must be pinned lowercase SHA40")
    if not a.repo_id.strip() or len(a.repo_id) > 300:
        p.error("Invalid repository ID")
    if a.baseline_snapshot is not None and a.baseline_snapshot <= 0:
        p.error("Invalid baseline snapshot")
    if a.verify_after_read_only_plan and not a.execute_read_only_plan:
        p.error("--verify-after-read-only-plan requires --execute-read-only-plan")
    if a.verify_stage_evidence:
        if a.stage is None:
            p.error("--verify-stage-evidence requires --stage")
        if (a.approve_stage is not None or a.expected_context_sha256 is not None
                or a.baseline_snapshot is not None):
            p.error("Evidence-only mode cannot contain execution approval/baseline flags")
    elif a.execute_read_only_plan:
        if a.stage is None or a.approve_stage is None or a.expected_context_sha256 is None:
            p.error("Read-only child execution needs --stage, --approve-stage, and --expected-context-sha256")
        if a.stage != a.approve_stage or SHA256.fullmatch(a.expected_context_sha256) is None:
            p.error("Stage approvals or context SHA256 differ/invalid")
    elif a.approve_stage is not None or a.expected_context_sha256 is not None:
        p.error("--plan cannot include approval flags")
    return a


def format_result(result: StageResult) -> str:
    lines = ["=== M4.6.4d.6c.2b.2a | GUARDED SINGLE-STAGE EXECUTOR CORE ===",
             f"Decision: {result.decision}", f"Stage: {result.stage or 'NONE'}",
             f"Detail: {result.detail}",
             f"Read-only child commands run: {result.executed_read_only_commands}",
             f"Pinned PlannedContext SHA256: {result.context_sha256 or 'NONE'}",
             "PostgreSQL writes: NONE | Archive/cache writes: NONE | Checkpoint writes: NONE",
             "Write authorization: NO | Stage advancement: NOT VERIFIED",
             "M4.6.4d.6c.2b.2a CORE: " + ("BLOCKED (NO WRITES)" if result.decision == "BLOCKED"
                                           else "PASS (READ-ONLY, NOT WRITE APPROVAL)")]
    return "\n".join(lines)


def main(argv=None, *, reviewer: Callable[..., StageReview] = review_one_stage,
         launcher: Callable[..., Any] = subprocess.run,
         output: Callable[[str], None] = print,
         verifier: Callable[..., Any] | None = None) -> int:
    a = parse_args(argv)
    try:
        if a.verify_stage_evidence:
            evidence = verify_stage_evidence(
                repo_id=a.repo_id, target_revision=a.target_revision,
                stage=a.stage, root=ROOT, verifier=verifier)
            output(format_evidence_inspection(evidence))
            return evidence_exit_code(evidence.status)
        if a.plan:
            review = reviewer(repo_id=a.repo_id, target_revision=a.target_revision,
                              baseline_snapshot=a.baseline_snapshot, root=ROOT, readers=None)
            outcome = describe_review(review, stage=a.stage)
        else:
            outcome = execute_read_only_plan(
                repo_id=a.repo_id, target_revision=a.target_revision,
                stage=a.stage, approve_stage=a.approve_stage,
                expected_context_sha256=a.expected_context_sha256,
                baseline_snapshot=a.baseline_snapshot, root=ROOT,
                reviewer=reviewer, launcher=launcher)
        output(format_result(outcome))
        if a.execute_read_only_plan and a.verify_after_read_only_plan:
            if outcome.decision != "READ_ONLY_PLANS_RAN":
                raise RuntimeError("Cannot inspect after unsuccessful read-only child execution")
            evidence = verify_stage_evidence(
                repo_id=a.repo_id, target_revision=a.target_revision,
                stage=a.stage, root=ROOT, verifier=verifier)
            output(format_evidence_inspection(
                evidence, preceding_plan_children=outcome.executed_read_only_commands))
            return evidence_exit_code(evidence.status)
        return 2 if outcome.decision in ("BLOCKED", "AWAITING_EVIDENCE") else 0
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        output(format_result(StageResult("BLOCKED", a.stage,
                      f"{type(exc).__name__}: {exc}")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
