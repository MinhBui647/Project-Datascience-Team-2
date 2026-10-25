"""Pin an HF revision and delegate all data decisions to M4.

No stage execution, downloads, checkpoint writes or DB writes.
"""

from dataclasses import dataclass, asdict
from typing import Any, Callable
import re

from .controller import RunSummary, plan_once, validate_inputs

SHA40 = re.compile(r"^[0-9a-f]{40}$")
HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
REF = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")

SKIP_DECISIONS = frozenset({
    "SKIP", "SKIPPED", "NO_CHANGE", "UNCHANGED",
})

BLOCKED_RECOVERY = frozenset({
    "BLOCKED", "FAILED", "ERROR", "RECOVERY_REQUIRED",
})


@dataclass(frozen=True)
class WatchOutcome:
    state: str
    requested_ref: str
    pinned_revision: str
    resolution_performed: bool
    controller: RunSummary


def validate_ref(value: str) -> None:
    if not isinstance(value, str) or not REF.fullmatch(value):
        raise ValueError("Invalid Hugging Face revision ref")

    if value.startswith(("/", "-", ".")):
        raise ValueError("Unsafe revision ref")

    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Unsafe revision path components")

    if HEX40.fullmatch(value) and not SHA40.fullmatch(value):
        raise ValueError("Commit SHA40 must be lowercase")


def watch_once(
    *,
    repo_id: str,
    revision_ref: str,
    resolver: Callable[[str, str], str],
    planner: Callable[..., Any],
    baseline_snapshot: int | None = None,
) -> WatchOutcome:
    """Resolve at most once; delegate the pinned commit to M4."""

    # Validate everything possible before accessing HF or PostgreSQL.
    validate_ref(revision_ref)
    validate_inputs(repo_id, "0" * 40, baseline_snapshot)

    if SHA40.fullmatch(revision_ref):
        pinned = revision_ref
        resolved = False
    else:
        pinned = resolver(repo_id, revision_ref)
        resolved = True

    validate_inputs(repo_id, pinned, baseline_snapshot)

    summary = plan_once(
        repo_id=repo_id,
        target_revision=pinned,
        baseline_snapshot=baseline_snapshot,
        planner=planner,
    )

    recovery = summary.recovery_state.upper()

    if summary.status == "BLOCKED" or recovery in BLOCKED_RECOVERY:
        state = "BLOCKED"

    elif summary.status == "MANUAL_REVIEW_REQUIRED":
        state = "REVIEW_REQUIRED"

    elif (
        summary.status == "NO_STAGE_REPORTED"
        and summary.m4_decision.upper() == "SKIP_ALREADY_CURRENT"
        and recovery == "COMPLETED"
        and summary.next_review_stage is None
        and not summary.blockers
        and not summary.m4_write_authorized
        and not summary.m5_write_executed
    ):
        state = "SKIPPED_BY_M4"

    elif (
        summary.status == "NO_STAGE_REPORTED"
        and summary.m4_decision.upper() in SKIP_DECISIONS
        and not summary.m4_write_authorized
    ):
        state = "SKIPPED_BY_M4"

    else:
        # Never guess that a run has completed successfully.
        state = "UNRESOLVED"

    return WatchOutcome(
        state=state,
        requested_ref=revision_ref,
        pinned_revision=pinned,
        resolution_performed=resolved,
        controller=summary,
    )


def report_dict(outcome: WatchOutcome) -> dict[str, Any]:
    return asdict(outcome)
