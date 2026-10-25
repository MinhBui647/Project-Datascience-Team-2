"""M5.5.5b | READ-ONLY end-to-end orchestration preview.

Real entry point delegates to the existing M5 Watcher -> Controller -> M4
Stage Planner. After planning, it checks *local* immutable checkpoints and
(for a DB-write stage only) the durable M5 attempt journal. No subprocess,
checkpoint publication, cache/archive mutations, SQL writes or stage advancement.

This module is deliberately NOT an unattended pipeline executor. A review state
is never a write approval; the CLI has no execution switches.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable

DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")

STAGE_ORDER = (
    "ROUTE", "PLANNED", "MANIFEST_PREPARATION", "CACHE_DOWNLOAD",
    "ARCHIVE_PROMOTION", "PREPARED", "SNAPSHOT_REGISTRATION",
    "REUSE_LINKING", "CANDIDATE_INGESTION", "MANIFEST_REGISTRATION",
    "FINALIZATION",
)
WRITE_STAGES = frozenset(STAGE_ORDER[6:])
PREPARATION_STAGES = frozenset(STAGE_ORDER[2:5])
CHECKPOINT_STAGES = frozenset(("PLANNED", "PREPARED"))
# A stage may be reviewed only if the *predecessor* checkpoint chain exists.
REQUIRED_CHECKPOINTS = {
    "ROUTE": (),
    "PLANNED": (),  # PlannedContext is still in memory for a new revision.
    "MANIFEST_PREPARATION": ("planned",),
    "CACHE_DOWNLOAD": ("planned",),
    "ARCHIVE_PROMOTION": ("planned",),
    "PREPARED": ("planned",),
    "SNAPSHOT_REGISTRATION": ("planned", "prepared"),
    "REUSE_LINKING": ("planned", "prepared", "registered"),
    "CANDIDATE_INGESTION": ("planned", "prepared", "registered"),
    "MANIFEST_REGISTRATION": ("planned", "prepared", "registered"),
    "FINALIZATION": ("planned", "prepared", "registered"),
}


@dataclass(frozen=True)
class PipelinePreview:
    state: str
    repo_id: str
    target_revision: str
    requested_ref: str
    watcher_state: str
    m4_decision: str
    recovery_state: str
    next_review_stage: str | None
    stage_kind: str | None
    next_action: str
    checkpoints: dict[str, str]
    journal_state: str | None
    stages: tuple[dict[str, str], ...]
    blockers: tuple[str, ...]
    write_authorized: bool = False
    write_executed: bool = False
    retry_authorized: bool = False
    stage_advanced: bool = False
    # Evidence-derived stage is advisory and NEVER overrides next_review_stage.
    preparation_resolution: str | None = None
    evidence_next_stage: str | None = None
    verified_preparation_stages: tuple[str, ...] = ()
    preparation_evidence_statuses: dict[str, str] | None = None
    preparation_journal_statuses: dict[str, str] | None = None

    def __post_init__(self):
        if (self.write_authorized or self.write_executed
                or self.retry_authorized or self.stage_advanced):
            raise ValueError("Read-only orchestration cannot authorize or perform writes")


def _stage_kind(stage: str) -> str:
    if stage in WRITE_STAGES:
        return "DB_WRITE"
    if stage in PREPARATION_STAGES:
        return "PREPARATION"
    if stage in CHECKPOINT_STAGES:
        return "CHECKPOINT"
    if stage == "ROUTE":
        return "CONTROL"
    raise ValueError("Unknown M4 stage")


def _stage_matrix(planned: Any) -> tuple[dict[str, str], ...]:
    if planned is None:
        return ()
    values = tuple(getattr(planned, "stages"))
    if tuple(getattr(item, "name", None) for item in values) != STAGE_ORDER:
        raise ValueError("M4 stage matrix missing, reordered, or invalid")
    return tuple({"name": item.name, "status": str(item.status)} for item in values)


def review_end_to_end(*, repo_id: str, revision_ref: str,
                      baseline_snapshot: int | None,
                      root: Path,
                      resolver: Callable[[str, str], str],
                      planner: Callable[..., Any],
                      watcher: Callable[..., Any],
                      checkpoint_reader: Callable[[Path, str], tuple[Any, Any, Any]],
                      journal_auditor: Callable[..., Any],
                      preparation_resolver: Callable[..., Any] | None = None) -> PipelinePreview:
    """One Watcher/Controller/M4 planning pass; never dispatch a writer.

    Injecting services allows offline tests. The live CLI uses the exact
    pre-existing M4/M5 services. Journal data never authorizes retry.
    """
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Real project root required")
    if type(baseline_snapshot) not in (int, type(None)) or (
        baseline_snapshot is not None and baseline_snapshot <= 0
    ):
        raise ValueError("Invalid baseline snapshot")
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository")
    if not isinstance(revision_ref, str) or not revision_ref.strip():
        raise ValueError("Invalid revision ref")

    capture: list[Any] = []
    def capture_planner(**kwargs):
        if capture:
            raise RuntimeError("More than one M4 planning pass prohibited")
        planned = planner(**kwargs)
        capture.append(planned)
        return planned

    watch = watcher(
        repo_id=repo_id, revision_ref=revision_ref,
        baseline_snapshot=baseline_snapshot,
        resolver=resolver, planner=capture_planner,
    )
    summary = watch.controller
    revision = watch.pinned_revision
    if not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise ValueError("Watcher did not pin lowercase SHA40")
    if (summary.repo_id != repo_id or summary.target_revision != revision
            or bool(summary.m5_write_executed)):
        raise ValueError("Watcher/Controller identity or write-state mismatch")
    stages = _stage_matrix(capture[0] if capture else None)
    if stages and summary.stage_count != len(stages):
        raise ValueError("M4 stage count drift")
    stage = summary.next_review_stage
    if stage is not None and stage not in STAGE_ORDER[1:]:
        raise ValueError("M4 reports unsupported review stage")
    blockers = list(summary.blockers)
    common = dict(repo_id=repo_id, target_revision=revision,
                  requested_ref=revision_ref, watcher_state=watch.state,
                  m4_decision=summary.m4_decision,
                  recovery_state=summary.recovery_state,
                  next_review_stage=stage,
                  stage_kind=_stage_kind(stage) if stage else None,
                  stages=stages)

    def outcome(state: str, action: str, *, checkpoints=None, journal_state=None,
                resolution=None):
        evidence_fields = {}
        if resolution is not None:
            evidence_fields = dict(
                preparation_resolution=resolution.state,
                evidence_next_stage=resolution.evidence_next_stage,
                verified_preparation_stages=tuple(resolution.verified_stages),
                preparation_evidence_statuses=dict(resolution.evidence_statuses),
                preparation_journal_statuses=dict(resolution.journal_statuses),
            )
        return PipelinePreview(
            state=state, next_action=action,
            checkpoints=checkpoints if checkpoints is not None else {},
            journal_state=journal_state, blockers=tuple(blockers),
            **evidence_fields, **common,
        )

    # Fail closed: never treat a merely reported SKIP as complete without
    # the explicit matching M4 completed/current decision and recovery state.
    if watch.state == "SKIPPED_BY_M4":
        if (summary.m4_decision != "SKIP_ALREADY_CURRENT"
                or summary.recovery_state != "COMPLETED"
                or stage is not None or blockers or summary.m4_write_authorized):
            blockers.append("Inconsistent completed/current skip evidence")
            return outcome("BLOCKED", "Inspect conflicting M4 completed state; no actions")
        return outcome("SKIPPED_CURRENT", "No work; immutable completed snapshot")

    if (summary.status == "BLOCKED" or watch.state == "BLOCKED"
            or blockers or summary.m4_write_authorized):
        if summary.m4_write_authorized:
            blockers.append("M4 unexpectedly granted write authority")
        return outcome("BLOCKED", "Investigate M4 plan or recovery blockers")
    if (watch.state != "REVIEW_REQUIRED"
            or summary.status != "MANUAL_REVIEW_REQUIRED" or stage is None):
        blockers.append("Watcher/Controller has no verified next review stage")
        return outcome("BLOCKED", "Re-audit pinned M4 decision; do not advance")
    if not stages or len(stages) != len(STAGE_ORDER):
        blockers.append("Missing complete typed M4 stage matrix")
        return outcome("BLOCKED", "Inspect M4 stage plan integrity")
    row = stages[STAGE_ORDER.index(stage)]
    if row["status"] != "READY":
        blockers.append("M4 next stage is not READY")
        return outcome("BLOCKED", "Repair inconsistent stage plan")

    # Read only on REVIEW_REQUIRED paths. Historical completed snapshots (#367)
    # may legitimately lack M4 checkpoints; NEVER synthesize them here.
    try:
        chain = checkpoint_reader(root, revision)
        if not isinstance(chain, tuple) or len(chain) != 3:
            raise ValueError("Invalid checkpoint reader result")
        checkpoints = dict(zip(("planned", "prepared", "registered"),
                               ("PRESENT" if item is not None else "ABSENT"
                                for item in chain)))
    except Exception as exc:
        blockers.append(f"Checkpoint reader failed: {type(exc).__name__}: {exc}")
        return outcome("BLOCKED", "Inspect checkpoint chain read failure")

    missing = [name for name in REQUIRED_CHECKPOINTS[stage]
               if checkpoints[name] != "PRESENT"]
    if missing:
        blockers.append("Missing prerequisite checkpoint(s): " + ", ".join(missing))
        return outcome("BLOCKED", "Review missing durable predecessor evidence",
                       checkpoints=checkpoints)

    if stage in WRITE_STAGES:
        try:
            audited = journal_auditor(root=root, repo_id=repo_id,
                                      target_revision=revision, stage=stage)
            journal_state = str(audited.state)
            if (audited.write_authorized or audited.retry_authorized
                    or audited.write_executed):
                raise ValueError("Recovery audit unexpectedly authorized writes")
        except Exception as exc:
            blockers.append(f"Journal auditor failed: {type(exc).__name__}: {exc}")
            return outcome("BLOCKED", "Inspect local durable journal",
                           checkpoints=checkpoints)
        if journal_state != "NO_LOCAL_ATTEMPT":
            blockers.append("Existing or invalid durable write-attempt state: " + journal_state)
            return outcome("BLOCKED_ATTEMPT_REVIEW", "Manual journal/evidence reconciliation only",
                           checkpoints=checkpoints, journal_state=journal_state)
        return outcome("REVIEW_DB_WRITE", "Review M4 CLI contract, independent evidence, and explicit single-stage approval",
                       checkpoints=checkpoints, journal_state=journal_state)

    if stage in PREPARATION_STAGES or stage == "PREPARED":
        if preparation_resolver is not None:
            # Fresh M4 review, independent stage evidence, and immutable journal.
            # Differences are reported, never converted into stage authorization.
            try:
                from scripts.orchestration.preparation_evidence_resolver import StageResolution
                resolution = preparation_resolver(
                    root=root, repo_id=repo_id, target_revision=revision,
                    baseline_snapshot=baseline_snapshot,
                )
                if not isinstance(resolution, StageResolution):
                    raise TypeError("Preparation resolver returned untyped outcome")
                if (resolution.repo_id != repo_id or
                    resolution.target_revision != revision or
                    resolution.m4_next_stage != stage or
                    resolution.write_authorized or
                    resolution.artifact_write_executed or
                    resolution.db_write_executed or
                    resolution.retry_authorized or
                    resolution.stage_advanced):
                    raise ValueError("Preparation resolver identity/M4 stage/authorization drift")
                if resolution.evidence_next_stage is not None and (
                    resolution.evidence_next_stage not in PREPARATION_STAGES
                    and resolution.evidence_next_stage not in CHECKPOINT_STAGES
                ):
                    raise ValueError("Unknown evidence-derived preparation stage")
            except Exception as exc:
                blockers.append("Preparation evidence resolution failed: " +
                                f"{type(exc).__name__}: {exc}")
                return outcome("BLOCKED", "Review M4 preparation evidence resolver failure",
                               checkpoints=checkpoints)
            state = resolution.state
            if state == "REVIEW_STAGE_READ_ONLY" and resolution.evidence_next_stage == stage:
                return outcome("REVIEW_PREPARATION", "M4 and evidence agree; manual single-stage review only",
                               checkpoints=checkpoints, resolution=resolution)
            if state == "REVIEW_PREPARED" and stage == "PREPARED":
                return outcome("REVIEW_CHECKPOINT", "Review PREPARED publication independently; no writes authorized",
                               checkpoints=checkpoints, resolution=resolution)
            # Even if evidence is VERIFIED, historical M4 publication without an
            # M5 journal cannot be silently treated as an automatic advancement.
            if state in ("ROUTER_ALIGNMENT_REQUIRED", "MANUAL_RECONCILIATION_REQUIRED",
                         "BLOCKED_ATTEMPT_REVIEW", "BLOCKED_OUT_OF_ORDER_ATTEMPT",
                         "BLOCKED", "PREPARATION_ALREADY_CLOSED"):
                blockers.append("Preparation evidence review: " + state + ": " + resolution.detail)
                output_state = state if state != "PREPARATION_ALREADY_CLOSED" else "BLOCKED_PREPARATION"
                return outcome(output_state, "Reconcile M4 entrypoint with verified artifacts and durable journal; do not dispatch",
                               checkpoints=checkpoints, resolution=resolution)
            blockers.append("Unexpected preparation evidence resolver state: " + state)
            return outcome("BLOCKED", "Inspect inconsistent M4/evidence stage classification",
                           checkpoints=checkpoints, resolution=resolution)
        # Compatibility for previously created offline callers. The live CLI
        # ALWAYS supplies the evidence resolver; this path never authorizes writes.
        if stage == "PREPARED":
            return outcome("REVIEW_CHECKPOINT", "Evidence resolver not supplied; checkpoint review only",
                           checkpoints=checkpoints)
        return outcome("REVIEW_PREPARATION", "Evidence resolver not supplied; manual preparation review only",
                       checkpoints=checkpoints)

    if stage in CHECKPOINT_STAGES:
        return outcome("REVIEW_CHECKPOINT", "Review and publish verified immutable M4 checkpoint via separate controlled step",
                       checkpoints=checkpoints)
    raise ValueError("Unexpected stage after validation")


def preview_exit_code(state: str) -> int:
    """Only explicitly safe review/skip states exit successfully."""
    return 0 if state in {"SKIPPED_CURRENT", "REVIEW_CHECKPOINT",
                          "REVIEW_DB_WRITE", "REVIEW_PREPARATION"} else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True,
                        help="Read-only end-to-end preview; NO execution mode")
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--revision-ref", default="main")
    parser.add_argument("--baseline-snapshot", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        from scripts.incremental.incremental_execution_context import ROOT
        from scripts.incremental.check_raw_updates import resolve_target_revision
        from scripts.incremental.incremental_guarded_stage_planner import plan_stages_read_only
        from scripts.incremental.incremental_recovery_audit import read_checkpoints
        from scripts.orchestration.commit_watcher import watch_once
        from scripts.orchestration.durable_attempt_recovery_audit import audit_attempt
        from scripts.orchestration.preparation_evidence_resolver import resolve_preparation
        result = review_end_to_end(
            repo_id=args.repo_id, revision_ref=args.revision_ref,
            baseline_snapshot=args.baseline_snapshot, root=ROOT,
            resolver=resolve_target_revision,
            planner=plan_stages_read_only,
            watcher=watch_once,
            checkpoint_reader=read_checkpoints,
            journal_auditor=audit_attempt,
            preparation_resolver=resolve_preparation,
        )
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return preview_exit_code(result.state)
    except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
        print(json.dumps({
            "state": "BLOCKED", "reason": f"{type(exc).__name__}: {exc}",
            "write_authorized": False, "write_executed": False,
            "retry_authorized": False, "stage_advanced": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
