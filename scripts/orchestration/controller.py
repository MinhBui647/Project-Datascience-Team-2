"""M5 read-only orchestration controller.

Consumes M4's guarded stage plan. Never executes a stage,
publishes checkpoints, downloads files, or writes database rows.
"""

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable


SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPO_ID = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?$")


@dataclass(frozen=True)
class RunSummary:
    status: str
    repo_id: str
    target_revision: str
    m4_decision: str
    recovery_state: str
    next_review_stage: str | None
    blockers: tuple[str, ...]
    stage_count: int
    read_only_preview_count: int
    planned_context_sha256: str | None
    m4_write_authorized: bool
    m5_write_executed: bool = False


def validate_inputs(
    repo_id: str,
    target_revision: str,
    baseline_snapshot: int | None,
) -> None:
    if not isinstance(repo_id, str) or not REPO_ID.fullmatch(repo_id):
        raise ValueError("Invalid Hugging Face repository ID")

    if not isinstance(target_revision, str) or not SHA40.fullmatch(
        target_revision
    ):
        raise ValueError("Target revision must be pinned lowercase SHA40")

    if baseline_snapshot is not None:
        if (
            type(baseline_snapshot) is not int
            or baseline_snapshot <= 0
        ):
            raise ValueError("Baseline snapshot must be a positive integer")


def plan_once(
    *,
    repo_id: str,
    target_revision: str,
    baseline_snapshot: int | None = None,
    planner: Callable[..., Any],
) -> RunSummary:
    """Call one injected M4 read-only planner and summarize its result."""
    validate_inputs(repo_id, target_revision, baseline_snapshot)

    try:
        result = planner(
            repo_id=repo_id,
            target_revision=target_revision,
            baseline_snapshot=baseline_snapshot,
        )
    except RuntimeError as exc:
        detail = str(exc)

        # Handle only the known M4 incremental JSON-only boundary.
        # Preserve the full error; never silently drop CSV candidates.
        if not (
            detail.startswith("Candidate validation blocked: ")
            and "Incremental phase 1 accepts JSON files only" in detail
        ):
            raise

        return RunSummary(
            status="BLOCKED",
            repo_id=repo_id,
            target_revision=target_revision,
            m4_decision="NOT_PRODUCED",
            recovery_state="NOT_EVALUATED",
            next_review_stage=None,
            blockers=(detail,),
            stage_count=0,
            read_only_preview_count=0,
            planned_context_sha256=None,
            m4_write_authorized=False,
        )

    required = (
        "repo_id",
        "target_revision",
        "decision",
        "recovery_state",
        "next_review_stage",
        "blockers",
        "stages",
        "read_only_previews",
        "planned_context_sha256",
        "write_authorized",
    )

    for field in required:
        if not hasattr(result, field):
            raise ValueError("M4 plan missing required field: " + field)

    if result.repo_id != repo_id:
        raise ValueError("M4 planner returned a different repository")

    if result.target_revision != target_revision:
        raise ValueError("M4 planner returned a different target revision")

    blockers = tuple(result.blockers)
    decision = str(result.decision)
    next_stage = result.next_review_stage

    blocked_decisions = {
        "BLOCKED",
        "FAILED",
        "ERROR",
        "RECOVERY_REQUIRED",
    }

    if blockers or decision.upper() in blocked_decisions:
        status = "BLOCKED"
    elif next_stage is not None:
        status = "MANUAL_REVIEW_REQUIRED"
    else:
        status = "NO_STAGE_REPORTED"

    return RunSummary(
        status=status,
        repo_id=repo_id,
        target_revision=target_revision,
        m4_decision=decision,
        recovery_state=str(result.recovery_state),
        next_review_stage=next_stage,
        blockers=blockers,
        stage_count=len(tuple(result.stages)),
        read_only_preview_count=len(tuple(result.read_only_previews)),
        planned_context_sha256=result.planned_context_sha256,
        m4_write_authorized=bool(result.write_authorized),
    )


def report_dict(summary: RunSummary) -> dict[str, Any]:
    return asdict(summary)
