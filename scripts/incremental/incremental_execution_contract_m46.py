#!/usr/bin/env python3
"""M4.6.4d.6c.2b.1 | Strictly READ-ONLY stage execution contract.

This module defines *requirements and CLI --plan previews* for the future
Guarded Stage Executor. It NEVER approves or constructs write-mode commands,
executes subprocesses, reads/writes PostgreSQL, fetches HF data, publishes
checkpoints, or changes cache/archive state.

A Stage Planner READY status is only an advisory read-only review entrypoint.
No checkpoint or RecoveryAssessment alone is authority for a write.

From project root (static contract description, no network/DB):
  python -m scripts.tools.incremental_execution_contract_m46 --list-stages
  python -m scripts.tools.incremental_execution_contract_m46 --describe-stage REUSE_LINKING
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from scripts.tools.incremental_execution_context import (
    PlannedContext, PreparedContext, RegisteredContext, SHA256, checkpoint_digest,
)
from scripts.tools.incremental_guarded_stage_planner import (
    STAGE_ORDER, RECOVERY_NEXT, GuardedStagePlan,
)
from scripts.tools.incremental_runner_contracts import (
    CacheEvidence, _check_evidence, plan_command,
)

NO_WRITE = "NO_WRITE_APPROVAL"
DISPOSITIONS = frozenset(("NO_ACTION", "BLOCKED", "AWAITING_EVIDENCE", "REVIEW_READ_ONLY_PLAN"))
KINDS = frozenset(("ROUTER", "CHECKPOINT", "LOCAL_PUBLISH", "CACHE_DOWNLOAD", "ARCHIVE_PUBLISH", "DATABASE_MUTATION"))
CONTEXT_KINDS = frozenset(("none", "planned", "prepared", "registered"))


@dataclass(frozen=True)
class StageRule:
    stage: str
    kind: str
    context_kind: str
    preview_steps: tuple[str, ...]
    required_evidence: tuple[str, ...]
    post_write_verification: tuple[str, ...]
    write_mode: str | None  # descriptive metadata, NEVER emitted as CLI
    confirm_flag: str | None  # descriptive metadata, NEVER emitted as CLI

    def __post_init__(self) -> None:
        if self.stage not in STAGE_ORDER or self.kind not in KINDS or self.context_kind not in CONTEXT_KINDS:
            raise ValueError("Unknown stage/kind/context")
        if self.stage == "ROUTE" and (self.write_mode is not None or self.confirm_flag is not None):
            raise ValueError("ROUTE cannot be a writer")
        if (self.write_mode is None) != (self.confirm_flag is None):
            raise ValueError("Write mode and confirmation must be paired")
        for flag in (self.write_mode, self.confirm_flag):
            if flag is not None and not (isinstance(flag, str) and flag.startswith("--") and len(flag) > 2):
                raise ValueError("Invalid descriptive write metadata")
        if not self.required_evidence or not self.post_write_verification:
            raise ValueError("Missing stage gate/postcondition")


def _rule(name: str, kind: str, ctx: str, steps: tuple[str, ...],
          pre: tuple[str, ...], post: tuple[str, ...],
          write: str | None = None, confirm: str | None = None) -> StageRule:
    return StageRule(name, kind, ctx, steps, pre, post, write, confirm)


STAGE_RULES: dict[str, StageRule] = {
    "ROUTE": _rule("ROUTE", "ROUTER", "none", (),
        ("Pinned lowercase SHA40; target-state gate; read-only recovery audit",),
        ("Classify target ABSENT/completed/in_progress; never turn recovery into new registration",)),
    "PLANNED": _rule("PLANNED", "CHECKPOINT", "planned", (),
        ("Fresh SyncPlan, pinned baseline/revision/fingerprint, no REMOVED/REVIEW",),
        ("Publish planned.json create-only; reread SHA256 and identity",)),
    "MANIFEST_PREPARATION": _rule("MANIFEST_PREPARATION", "LOCAL_PUBLISH", "planned",
        ("manifest_preparation",),
        ("Verified planned checkpoint; pinned full HF manifest metadata and SHA256",),
        ("Report and immutable manifest objects exist and rehash to the same pinned SHA256",),
        "--publish", "--confirm-manifest-archive-only"),
    "CACHE_DOWNLOAD": _rule("CACHE_DOWNLOAD", "CACHE_DOWNLOAD", "planned",
        ("download",),
        ("Verified planned checkpoint; exact JSON candidate path/size/identity list",),
        ("Revalidate local cache bytes, SHA, JSON parse and strictly positive record totals",),
        "--download", "--confirm-cache-only"),
    "ARCHIVE_PROMOTION": _rule("ARCHIVE_PROMOTION", "ARCHIVE_PUBLISH", "planned",
        ("archive_promotion",),
        ("Verified cache receipts and expected_records; no forged PreparedContext",),
        ("Every candidate immutable archive exists and matches pinned byte-size/SHA256",),
        "--promote", "--confirm-archive-only"),
    "PREPARED": _rule("PREPARED", "CHECKPOINT", "prepared", (),
        ("Two identical immutable full manifests; all candidate archives rehashed",),
        ("Publish prepared.json create-only; verify parent hash, manifest, receipts and counts",)),
    "SNAPSHOT_REGISTRATION": _rule("SNAPSHOT_REGISTRATION", "DATABASE_MUTATION", "prepared",
        ("snapshot_preparation", "snapshot_registration"),
        ("Verified prepared checkpoint; target DB row absent; newest baseline unchanged",),
        ("SELECT exact new snapshot ID with in_progress, counts and NULL manifest fields; "
         "publish registered.json only after independent readback; crash gap BLOCKS",),
        "--register", "--confirm-snapshot-only"),
    "REUSE_LINKING": _rule("REUSE_LINKING", "DATABASE_MUTATION", "registered",
        ("reuse",),
        ("Verified registered checkpoint, in_progress target and pinned baseline observations",),
        ("Read-only reconciliation of exact REUSE paths/file IDs and expected record references",),
        "--link", "--confirm-reuse-only"),
    "CANDIDATE_INGESTION": _rule("CANDIDATE_INGESTION", "DATABASE_MUTATION", "registered",
        ("candidate_planner", "candidate_ingestion"),
        ("Verified REUSE complete; per-candidate archive/record receipts and unprocessed file list",),
        ("Read back candidate observations, raw_record counts and unique locators; "
         "never replay verified INSERTs",),
        "--apply", "--confirm-candidate-write"),
    "MANIFEST_REGISTRATION": _rule("MANIFEST_REGISTRATION", "DATABASE_MUTATION", "registered",
        ("manifest_registration",),
        ("All pinned REUSE and candidates independently verified",),
        ("Read back manifest SHA256 and archive path from target snapshot; no duplicates",),
        "--commit", "--confirm-manifest-only"),
    "FINALIZATION": _rule("FINALIZATION", "DATABASE_MUTATION", "registered",
        ("finalization",),
        ("Full manifest registered; DB/Raw/record/immutable archive final gate passes",),
        ("Read back completed target and immutable manifest identity; rerun returns SKIP",),
        "--commit", "--confirm-complete"),
}
if tuple(STAGE_RULES) != STAGE_ORDER:
    raise RuntimeError("Stage rule table must exactly match Stage Planner order")


@dataclass(frozen=True)
class ExecutionContract:
    repo_id: str
    target_revision: str
    disposition: str
    review_stage: str | None
    context_kind: str
    context_sha256: str | None
    required_evidence: tuple[str, ...]
    post_write_verification: tuple[str, ...]
    read_only_argv: tuple[tuple[str, ...], ...]
    blockers: tuple[str, ...] = ()
    write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.write_authorized or self.disposition not in DISPOSITIONS:
            raise ValueError("Execution contract is never an approval")
        if self.review_stage is not None and self.review_stage not in STAGE_ORDER:
            raise ValueError("Invalid stage")
        if self.context_kind not in CONTEXT_KINDS:
            raise ValueError("Invalid context kind")
        if self.context_sha256 is not None and not SHA256.fullmatch(self.context_sha256):
            raise ValueError("Invalid context digest")
        if self.disposition != "REVIEW_READ_ONLY_PLAN" and self.read_only_argv:
            raise ValueError("Cannot expose preview commands for a non-reviewed stage")
        if self.disposition == "REVIEW_READ_ONLY_PLAN" and self.blockers:
            raise ValueError("A blocked contract cannot be reviewable")
        for argv in self.read_only_argv:
            if (not isinstance(argv, tuple) or len(argv) < 4 or argv[1] != "-m"
                    or not argv[2].startswith("scripts.tools.") or argv[-1] != "--plan"
                    or any(x in argv for x in ("--commit", "--apply", "--download", "--promote",
                                                "--publish", "--register", "--link", "--run-all"))):
                raise ValueError("Unsafe CLI preview")


def _lineage(planned: PlannedContext | None, prepared: PreparedContext | None,
             registered: RegisteredContext | None) -> PlannedContext | None:
    """Ensure optional context chain is coherent; none is ever assumed verified."""
    if planned is not None and not isinstance(planned, PlannedContext):
        raise TypeError("PlannedContext required")
    if prepared is not None and not isinstance(prepared, PreparedContext):
        raise TypeError("PreparedContext required")
    if registered is not None and not isinstance(registered, RegisteredContext):
        raise TypeError("RegisteredContext required")
    if prepared is not None and planned is not None and prepared.planned != planned:
        raise RuntimeError("PreparedContext differs from supplied PlannedContext")
    if registered is not None and prepared is not None and registered.prepared != prepared:
        raise RuntimeError("RegisteredContext differs from supplied PreparedContext")
    p = registered.prepared.planned if registered is not None else (
        prepared.planned if prepared is not None else planned)
    if p is not None and planned is not None and p != planned:
        raise RuntimeError("Context chain drift")
    return p


def build_execution_contract(plan: GuardedStagePlan, *,
                             planned: PlannedContext | None = None,
                             prepared: PreparedContext | None = None,
                             registered: RegisteredContext | None = None,
                             cache_evidence: CacheEvidence | None = None) -> ExecutionContract:
    """Pure advisory contract. It does not re-audit state or authorize execution."""
    if not isinstance(plan, GuardedStagePlan) or plan.write_authorized:
        raise TypeError("Read-only GuardedStagePlan required")
    if cache_evidence is not None and not isinstance(cache_evidence, CacheEvidence):
        raise TypeError("CacheEvidence required")
    p = _lineage(planned, prepared, registered)
    if p is not None and (p.repo_id, p.target_revision) != (plan.repo_id, plan.target_revision):
        raise RuntimeError("Execution Context and Stage Planner identity mismatch")
    if registered is not None and (plan.target_snapshot_id is None
                                    or registered.target_snapshot_id != plan.target_snapshot_id):
        raise RuntimeError("RegisteredContext target snapshot ID absent or mismatched")
    if plan.planned_context_sha256 is not None and p is not None and (
            checkpoint_digest(p) != plan.planned_context_sha256):
        raise RuntimeError("Stage Planner pinned PlannedContext SHA256 mismatch")
    common = dict(repo_id=plan.repo_id, target_revision=plan.target_revision,
                  context_sha256=checkpoint_digest(p) if p is not None else None)
    if plan.decision.startswith("BLOCKED_") or plan.blockers:
        return ExecutionContract(**common, disposition="BLOCKED", review_stage=None,
                                 context_kind="none", required_evidence=(),
                                 post_write_verification=(), read_only_argv=(),
                                 blockers=plan.blockers or ("Stage Planner blocked this target",))
    if plan.decision.startswith("SKIP_"):
        return ExecutionContract(**common, disposition="NO_ACTION", review_stage=None,
                                 context_kind="none", required_evidence=(),
                                 post_write_verification=(), read_only_argv=())
    stage = plan.next_review_stage
    if stage not in STAGE_RULES or stage == "ROUTE":
        raise RuntimeError("No eligible next stage; fail closed")
    if (sum(e.status == "READY" for e in plan.stages) != 1
            or plan.stages[STAGE_ORDER.index(stage)].status != "READY"):
        raise RuntimeError("Invalid stage readiness matrix")
    if plan.decision == "PLAN_NEW_REVISION" and stage != "PLANNED":
        raise RuntimeError("New revision cannot skip planned checkpoint review")
    if plan.decision == "RECOVERY_REVIEW_REQUIRED" and (
            RECOVERY_NEXT.get(plan.recovery_state) != stage):
        raise RuntimeError("Recovery stage does not match the audited state")
    if plan.decision not in ("PLAN_NEW_REVISION", "RECOVERY_REVIEW_REQUIRED"):
        raise RuntimeError("Unrecognized stage decision")
    rule = STAGE_RULES[stage]
    missing = {"planned": planned, "prepared": prepared, "registered": registered,
               "none": True}[rule.context_kind] is None
    if rule.context_kind == "planned" and p is not None and planned is None:
        # For post-registration recovery, permit a verified higher-stage context.
        planned = p
        missing = False
    if rule.context_kind == "prepared" and prepared is None and registered is not None:
        prepared = registered.prepared
        missing = False
    blockers: list[str] = []
    if missing:
        blockers.append(f"Require separately verified {rule.context_kind.title()}Context")
    if stage == "ARCHIVE_PROMOTION" and cache_evidence is None:
        blockers.append("Require fresh CacheEvidence for expected_records")
    if stage == "ARCHIVE_PROMOTION" and cache_evidence is not None and planned is not None:
        _check_evidence(planned, cache_evidence)
    if stage != "ARCHIVE_PROMOTION" and cache_evidence is not None:
        raise ValueError("CacheEvidence is only accepted for archive promotion contract")
    if plan.decision == "PLAN_NEW_REVISION" and plan.planned_context_sha256 is None:
        raise RuntimeError("New revision lacks pinned context hash")
    if blockers:
        return ExecutionContract(**common, disposition="AWAITING_EVIDENCE",
                                 review_stage=stage, context_kind=rule.context_kind,
                                 required_evidence=rule.required_evidence,
                                 post_write_verification=rule.post_write_verification,
                                 read_only_argv=(), blockers=tuple(blockers))
    active = {"planned": planned, "prepared": prepared, "registered": registered}[rule.context_kind]
    previews = tuple(plan_command(step, active, cache_evidence=(cache_evidence if step == "archive_promotion" else None))
                     for step in rule.preview_steps)
    return ExecutionContract(**common, disposition="REVIEW_READ_ONLY_PLAN",
                             review_stage=stage, context_kind=rule.context_kind,
                             required_evidence=rule.required_evidence,
                             post_write_verification=rule.post_write_verification,
                             read_only_argv=previews)


def format_contract(result: ExecutionContract) -> str:
    if not isinstance(result, ExecutionContract):
        raise TypeError("ExecutionContract required")
    lines = ["=== M4.6.4d.6c.2b.1 | READ-ONLY EXECUTION CONTRACT ===",
             f"Target: {result.target_revision}", f"Disposition: {result.disposition}",
             f"Review stage: {result.review_stage or 'NONE'}",
             f"Context kind: {result.context_kind}",
             f"Planned SHA256 (identity only): {result.context_sha256 or 'NONE'}"]
    for item in result.required_evidence:
        lines.append("PRECONDITION: " + item)
    for item in result.post_write_verification:
        lines.append("POSTCONDITION (future executor): " + item)
    for argv in result.read_only_argv:
        lines.append("UNEXECUTED --plan argv: " + repr(argv))
    for item in result.blockers:
        lines.append("BLOCKER: " + item)
    lines.extend(("Write authorized: NO | Generated write argv: NONE | Subprocesses: NONE",
                  "PostgreSQL writes: NONE | Cache/archive/checkpoint writes: NONE",
                  "READ-ONLY CONTRACT: PASS (NOT WRITE APPROVAL)"))
    return "\n".join(lines)


def describe_stage(stage: str) -> str:
    if stage not in STAGE_RULES:
        raise ValueError("Unknown stage")
    rule = STAGE_RULES[stage]
    lines = [f"{rule.stage} | {rule.kind} | requires {rule.context_kind} context",
             "CLI previews (read-only only): " + (", ".join(rule.preview_steps) or "none")]
    lines.extend("PRE: " + x for x in rule.required_evidence)
    lines.extend("VERIFY AFTER STAGE: " + x for x in rule.post_write_verification)
    lines.append("Writer flags (documentation only; NOT runnable): " +
                 (f"{rule.write_mode} + {rule.confirm_flag}" if rule.write_mode else "separate checkpoint publication / none"))
    lines.append("Never approved by this contract")
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--list-stages", action="store_true")
    modes.add_argument("--describe-stage", choices=STAGE_ORDER)
    return parser.parse_args(argv)


def main(argv=None, *, output: Callable[[str], None] = print) -> int:
    args = parse_args(argv)
    output("\n".join(STAGE_ORDER) if args.list_stages else describe_stage(args.describe_stage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
