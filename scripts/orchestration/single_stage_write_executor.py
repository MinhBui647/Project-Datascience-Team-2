"""M5.5.4d | Fail-closed, one-stage M4.CSV write gate.

--plan is read-only and NEVER provides execution authority. --execute-one-stage
requires an explicit stage approval, a pinned ExecutionContext SHA256, a SHA256
of the exact platform-local WriterIntent, and a confirmation flag. A second
M4 stage review plus an independent pre-write evidence check are mandatory.

This runner does not create snapshots, parse files, construct SQL, or write
M4 checkpoints. It does persist a durable M5 attempt reservation BEFORE invoking
exactly ONE existing M4 module via argv with shell=False.
A successful child exit is NOT completion until the independent post-write
verifier returns VERIFIED. If it fails, STOP; some stages may partially commit.
Do not auto-retry or auto-advance to another stage.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from scripts.incremental.incremental_execution_context import ROOT
from scripts.incremental.incremental_guarded_stage_executor import (
    StageReview, review_one_stage, validate_read_only_commands,
    verify_stage_evidence,
)
from scripts.orchestration.csv_write_intent import (
    STAGE_WRITERS, WriterIntent, build_writer_intent,
)
from scripts.orchestration.guarded_write_coordinator import review_stage_writer
from scripts.orchestration.write_attempt_guard import (
    reserve_write_attempt, record_attempt_result, WriteAttemptBlocked,
)

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REPO = "Toibinguz/epl-premier-league-dataset"
MAX_TIMEOUT_SECONDS = 900


class WriteGateError(RuntimeError):
    """Error carrying whether a child write process may have started."""

    def __init__(self, message: str, *, attempted: bool = False):
        super().__init__(message)
        self.attempted = attempted


@dataclass(frozen=True)
class GatePlan:
    state: str
    stage: str
    repo_id: str
    target_revision: str
    context_sha256: str | None
    intent_sha256: str | None = None
    module: str | None = None
    reason: str = ""
    write_authorized: bool = False
    write_executed: bool = False

    def __post_init__(self):
        if self.write_authorized or self.write_executed:
            raise ValueError("A plan never grants or reports a write")


@dataclass(frozen=True)
class ApprovedIntent:
    plan: GatePlan
    intent: WriterIntent


def _read_only_intent(review: StageReview, stage: str, context: str,
                      candidate_cache: str | None) -> ApprovedIntent | GatePlan:
    """Validate upstream M4 contract and construct ONE exact writer argv."""
    screened = review_stage_writer(
        review=review, stage=stage, expected_context_sha256=context,
        expected_candidate_cache=candidate_cache,
    )
    if screened.state != "READY_FOR_SEPARATE_WRITE_GATE":
        return GatePlan(screened.state, stage, screened.repo_id,
                        screened.target_revision, screened.expected_context_sha256,
                        reason=screened.reason)
    commands = validate_read_only_commands(review)
    module = f"scripts.tools.{STAGE_WRITERS[stage][0]}"
    matches = [argv for argv in commands if len(argv) >= 3 and argv[2] == module]
    if len(matches) != 1:
        raise WriteGateError("Exactly one M4 writer CLI preview required")
    intent = build_writer_intent(stage, matches[0])
    if intent.write_authorized or intent.module != module:
        raise WriteGateError("WriterIntent identity/authorization mismatch")
    digest = _intent_digest(review, stage, context, intent)
    return ApprovedIntent(
        GatePlan("READY_FOR_EXPLICIT_APPROVAL", stage, review.plan.repo_id,
                 review.plan.target_revision, context, digest, module,
                 "Read-only review only; not an approval"),
        intent,
    )


def _intent_digest(review: StageReview, stage: str, context: str,
                   intent: WriterIntent) -> str:
    """Hash pinned context, full args and target ID; not just stage name."""
    blob = {
        "schema": "m554d-writer-intent-v1",
        "repo_id": review.plan.repo_id,
        "target_revision": review.plan.target_revision,
        "target_snapshot_id": review.plan.target_snapshot_id,
        "context_sha256": context,
        "stage": stage,
        "argv": intent.argv,
    }
    return hashlib.sha256(json.dumps(blob, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def make_write_plan(*, repo_id: str, target_revision: str, stage: str,
                    context_sha256: str | None = None,
                    baseline_snapshot: int | None = None,
                    candidate_cache: str | None = None,
                    reviewer: Callable[..., StageReview] = review_one_stage,
                    root: Path = ROOT) -> GatePlan:
    _check_inputs(repo_id, target_revision, stage, baseline_snapshot, root)
    review = reviewer(repo_id=repo_id, target_revision=target_revision,
                      baseline_snapshot=baseline_snapshot, root=root, readers=None)
    screened = _read_only_intent(review, stage, context_sha256, candidate_cache)
    return screened.plan if isinstance(screened, ApprovedIntent) else screened


def _check_inputs(repo_id: str, revision: str, stage: str,
                  baseline: int | None, root: Path) -> None:
    if repo_id != REPO or not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise WriteGateError("Pinned repo/target revision mismatch")
    if stage not in STAGE_WRITERS:
        raise WriteGateError("DB-stage mapping missing; other stages unsupported")
    if baseline is not None and (type(baseline) is not int or baseline <= 0):
        raise WriteGateError("Invalid baseline snapshot ID")
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise WriteGateError("Safe project root directory required")


def _evidence_state(evidence: Any, stage: str) -> str:
    if (getattr(evidence, "stage", None) != stage
            or getattr(evidence, "write_authorized", None) is not False):
        raise WriteGateError("Invalid independent M4 verification result")
    state = getattr(evidence, "status", None)
    if state not in ("VERIFIED", "INCOMPLETE", "BLOCKED"):
        raise WriteGateError("Unknown independent evidence state")
    return state


def execute_one_stage(*, repo_id: str, target_revision: str, stage: str,
                      baseline_snapshot: int | None, context_sha256: str,
                      approved_stage: str, expected_intent_sha256: str,
                      candidate_cache: str | None = None,
                      reviewer: Callable[..., StageReview] = review_one_stage,
                      verifier: Callable[..., Any] = verify_stage_evidence,
                      launcher: Callable[..., Any] = subprocess.run,
                      root: Path = ROOT,
                      timeout_seconds: int = MAX_TIMEOUT_SECONDS) -> dict:
    """Single-stage explicit approval. Any failure after spawn may be partial."""
    _check_inputs(repo_id, target_revision, stage, baseline_snapshot, root)
    if (approved_stage != stage or not isinstance(context_sha256, str)
            or not SHA256.fullmatch(context_sha256)
            or not isinstance(expected_intent_sha256, str)
            or not SHA256.fullmatch(expected_intent_sha256)):
        raise WriteGateError("Explicit matching stage/context/intent approval required")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise WriteGateError("Invalid bounded timeout")
    args = dict(repo_id=repo_id, target_revision=target_revision,
                baseline_snapshot=baseline_snapshot, root=root, readers=None)
    first_review = reviewer(**args)
    first = _read_only_intent(first_review, stage, context_sha256, candidate_cache)
    if not isinstance(first, ApprovedIntent):
        raise WriteGateError("Stage is not ready for execution: " + first.state)
    if first.plan.intent_sha256 != expected_intent_sha256:
        raise WriteGateError("Expected writer intent SHA256 does not match first review")
    second_review = reviewer(**args)
    second = _read_only_intent(second_review, stage, context_sha256, candidate_cache)
    if not isinstance(second, ApprovedIntent) or second != first:
        raise WriteGateError("Fresh M4 stage review/command changed; aborting")
    if second.plan.intent_sha256 != expected_intent_sha256:
        raise WriteGateError("Writer intent changed before execution")

    evidence_kw = dict(repo_id=repo_id, target_revision=target_revision,
                       stage=stage, root=root)
    pre = _evidence_state(verifier(**evidence_kw), stage)
    if pre != "INCOMPLETE":
        raise WriteGateError("Pre-write evidence is not INCOMPLETE (" + pre + "); no write")

    # Reserve a durable per-repository/revision/stage fence BEFORE any writer
    # may start. This is local M5 metadata, not an M4 checkpoint or DB write.
    # An ambiguous result (including a crash) permanently blocks automatic
    # retry. Recovery requires explicit manual audit and a separate protocol.
    try:
        claim = reserve_write_attempt(
            root=root, repo_id=repo_id, target_revision=target_revision,
            stage=stage, context_sha256=context_sha256,
            intent_sha256=expected_intent_sha256,
            target_snapshot_id=first_review.plan.target_snapshot_id,
            argv=first.intent.argv,
        )
    except WriteAttemptBlocked as exc:
        raise WriteGateError("Durable write-attempt guard blocked execution: " + str(exc)) from exc

    # Only the trusted/pinned M4 writer subprocess may mutate raw storage.
    try:
        try:
            proc = launcher(first.intent.argv, cwd=str(root), shell=False,
                            capture_output=True, text=True, timeout=timeout_seconds,
                            check=False)
        except subprocess.TimeoutExpired as exc:
            raise WriteGateError(
                "Writer timed out; stage may be partially committed; manual recovery audit required",
                attempted=True,
            ) from exc
        except Exception as exc:
            raise WriteGateError(
                "Writer subprocess failed to launch or finish; manual recovery audit required",
                attempted=True,
            ) from exc
        if type(getattr(proc, "returncode", None)) is not int or proc.returncode != 0:
            raise WriteGateError(
                f"M4 writer exited {getattr(proc, 'returncode', None)!r}; "
                "stage may be partially committed; manual recovery audit required",
                attempted=True,
            )
        try:
            post = _evidence_state(verifier(**evidence_kw), stage)
        except Exception as exc:
            raise WriteGateError("Post-write verification failed; manual recovery audit required",
                                 attempted=True) from exc
        if post != "VERIFIED":
            raise WriteGateError("Post-write evidence is " + post +
                                 "; manual recovery audit required", attempted=True)
    except BaseException:
        # Best effort: the durable reservation alone suffices to block retry,
        # even if recording an outcome fails or process terminates abruptly.
        try:
            record_attempt_result(claim, status="UNVERIFIED_AFTER_ATTEMPT")
        except Exception:
            pass
        raise

    try:
        record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
    except Exception as exc:
        # Real write may already have succeeded. Never advertise success while
        # the local attempt journal's terminal outcome cannot be persisted.
        raise WriteGateError("Write evidence verified but durable result recording failed; "
                             "manual audit required", attempted=True) from exc
    return {
        "state": "VERIFIED_AFTER_WRITE", "stage": stage,
        "repo_id": repo_id, "target_revision": target_revision,
        "intent_sha256": expected_intent_sha256,
        "child_exit_code": 0, "post_evidence_status": post,
        "write_attempted": True, "write_verified": True,
        "next_stage_authorized": False,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--execute-one-stage", action="store_true")
    p.add_argument("--repo-id", default=REPO)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--baseline-snapshot", type=int)
    p.add_argument("--stage", required=True)
    p.add_argument("--expected-context-sha256")
    p.add_argument("--expected-candidate-cache")
    p.add_argument("--approve-stage")
    p.add_argument("--expected-write-intent-sha256")
    p.add_argument("--confirm-single-stage-write", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    a = p.parse_args(argv)
    if a.execute_one_stage:
        if (not a.confirm_single_stage_write or a.approve_stage != a.stage
                or not a.expected_context_sha256 or not a.expected_write_intent_sha256):
            p.error("Write requires explicit --approve-stage, context/intent SHA256, "
                    "and --confirm-single-stage-write")
    elif a.confirm_single_stage_write or a.approve_stage or a.expected_write_intent_sha256:
        p.error("--plan forbids write confirmation and approval arguments")
    try:
        if a.plan:
            outcome = make_write_plan(
                repo_id=a.repo_id, target_revision=a.target_revision,
                stage=a.stage, context_sha256=a.expected_context_sha256,
                baseline_snapshot=a.baseline_snapshot,
                candidate_cache=a.expected_candidate_cache,
            )
            print(json.dumps(asdict(outcome), ensure_ascii=False, indent=2))
            return 0 if outcome.state in ("SKIPPED", "READY_FOR_EXPLICIT_APPROVAL") else 2
        outcome = execute_one_stage(
            repo_id=a.repo_id, target_revision=a.target_revision,
            stage=a.stage, baseline_snapshot=a.baseline_snapshot,
            context_sha256=a.expected_context_sha256,
            approved_stage=a.approve_stage,
            expected_intent_sha256=a.expected_write_intent_sha256,
            candidate_cache=a.expected_candidate_cache,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        print(json.dumps({
            "state": "BLOCKED_OR_FAILED", "stage": a.stage,
            "error_type": type(exc).__name__, "reason": str(exc),
            "write_attempted": bool(getattr(exc, "attempted", False)),
            "write_verified": False, "next_stage_authorized": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
