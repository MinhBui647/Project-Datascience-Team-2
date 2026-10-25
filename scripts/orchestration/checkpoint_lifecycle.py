"""M5.5.5c.2: fail-closed M4 checkpoint lifecycle (no DB writes, no HF bodies).

This coordinator ONLY previews or publishes validated M4 checkpoints via M4's
atomic publish_checkpoint. It does not execute M4 preparation stages, register
snapshots, authorize DB writes, or resolve a moving Hugging Face ref.

The default services use M4 read-only routing (PLANNED), immutable archive
verification (PREPARED), and independent read-only DB evidence (REGISTERED).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
ORDER = ("PLANNED", "PREPARED", "REGISTERED")


@dataclass(frozen=True)
class Services:
    read_chain: Callable[[Path, str], tuple[Any, Any, Any]]
    new_plan: Callable[[str, str, int | None], tuple[Any, Any]]
    cache_evidence: Callable[[Any, Path], Any]
    prepared: Callable[[Any, Any, Path, str], Any]
    registered: Callable[[Any], Any]
    digest: Callable[[Any], str]
    publish: Callable[[Path, Any], tuple[str, str]]


def production_services() -> Services:
    """Use canonical M4 modules at call time, never mock in live CLI."""
    from scripts.incremental.incremental_execution_context import (
        checkpoint_digest, publish_checkpoint,
    )
    from scripts.incremental.incremental_guarded_stage_planner import (
        build_stage_plan_from_route,
    )
    from scripts.incremental.incremental_readonly_router import route_read_only
    from scripts.incremental.incremental_recovery_audit import read_checkpoints
    from scripts.incremental.incremental_runner_contracts import (
        audit_cache_evidence, build_prepared_from_archives,
        build_registered_from_db,
    )

    def new_plan(repo_id: str, revision: str, baseline: int | None):
        routed = route_read_only(repo_id=repo_id, target_revision=revision,
                                 baseline_snapshot=baseline)
        stage_plan = build_stage_plan_from_route(
            repo_id=repo_id, target_revision=revision, routed=routed,
        )
        if (routed.decision != "PLAN_NEW_REVISION"
                or stage_plan.decision != "PLAN_NEW_REVISION"
                or stage_plan.next_review_stage != "PLANNED"
                or stage_plan.blockers or stage_plan.write_authorized
                or routed.planner is None or routed.planner.planned is None):
            raise RuntimeError("M4 has not authorized a new PLANNED review")
        return stage_plan, routed.planner.planned

    return Services(
        read_chain=read_checkpoints,
        new_plan=new_plan,
        cache_evidence=lambda planned, root: audit_cache_evidence(planned, root=root),
        prepared=lambda planned, evidence, root, manifest_sha: build_prepared_from_archives(
            planned, evidence, root=root, expected_manifest_sha256=manifest_sha,
        ),
        registered=lambda prepared: build_registered_from_db(prepared),
        digest=checkpoint_digest,
        publish=publish_checkpoint,
    )


@dataclass(frozen=True)
class LifecycleReview:
    state: str
    stage: str
    repo_id: str
    target_revision: str
    context_sha256: str
    expected_manifest_sha256: str | None
    checkpoint_relative: str
    write_authorized: bool = False
    db_write_executed: bool = False
    hf_body_downloaded: bool = False

    def __post_init__(self):
        if self.write_authorized or self.db_write_executed or self.hf_body_downloaded:
            raise ValueError("Checkpoint review cannot authorize or perform external writes")


def _validate(*, root: Path, stage: str, repo_id: str, revision: str,
              baseline_snapshot: int | None, manifest_sha256: str | None) -> None:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("A real non-symlink project root is required")
    if stage not in ORDER:
        raise ValueError("Unsupported checkpoint stage")
    if not isinstance(repo_id, str) or not REPO.fullmatch(repo_id):
        raise ValueError("Invalid repository ID")
    if not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise ValueError("Pinned lowercase SHA40 required")
    if baseline_snapshot is not None and (
        type(baseline_snapshot) is not int or baseline_snapshot <= 0
    ):
        raise ValueError("Invalid baseline snapshot")
    if stage == "PLANNED" and baseline_snapshot is None:
        raise ValueError("New-revision planning requires explicit pinned baseline")
    if stage == "PREPARED":
        if not isinstance(manifest_sha256, str) or not SHA256.fullmatch(manifest_sha256):
            raise ValueError("Prepared checkpoint needs independently pinned manifest SHA256")
    elif manifest_sha256 is not None:
        raise ValueError("Manifest SHA256 applies only to PREPARED")


def _construct(*, root: Path, stage: str, repo_id: str, revision: str,
               baseline_snapshot: int | None, manifest_sha256: str | None,
               services: Services) -> tuple[Any, LifecycleReview]:
    _validate(root=root, stage=stage, repo_id=repo_id, revision=revision,
              baseline_snapshot=baseline_snapshot, manifest_sha256=manifest_sha256)
    chain = services.read_chain(root, revision)
    if not isinstance(chain, tuple) or len(chain) != 3:
        raise RuntimeError("Malformed checkpoint chain")
    planned, prepared, registered = chain
    if stage == "PLANNED":
        if any(item is not None for item in chain):
            raise RuntimeError("Already checkpointed revision; never replan/backfill")
        plan, context = services.new_plan(repo_id, revision, baseline_snapshot)
        if (plan.repo_id != repo_id or plan.target_revision != revision
                or plan.next_review_stage != "PLANNED"
                or plan.decision != "PLAN_NEW_REVISION"
                or plan.write_authorized or plan.blockers):
            raise RuntimeError("M4 new-revision stage plan mismatch")
        expected_digest = plan.planned_context_sha256
    elif stage == "PREPARED":
        if planned is None or prepared is not None or registered is not None:
            raise RuntimeError("PREPARED requires planned only, no later checkpoint")
        context = services.prepared(
            planned, services.cache_evidence(planned, root), root, manifest_sha256,
        )
        if context.planned != planned:
            raise RuntimeError("Prepared checkpoint lineage mismatch")
        expected_digest = None
    else:
        if planned is None or prepared is None or registered is not None:
            raise RuntimeError("REGISTERED requires exact planned + prepared chain")
        if prepared.planned != planned:
            raise RuntimeError("Prepared checkpoint lineage mismatch")
        context = services.registered(prepared)
        if context.prepared != prepared:
            raise RuntimeError("Registered checkpoint lineage mismatch")
        expected_digest = None
    actual_repo = (context.prepared.planned.repo_id if stage == "REGISTERED"
                   else context.planned.repo_id if stage == "PREPARED"
                   else context.repo_id)
    actual_revision = (context.prepared.planned.target_revision if stage == "REGISTERED"
                       else context.planned.target_revision if stage == "PREPARED"
                       else context.target_revision)
    if actual_repo != repo_id or actual_revision != revision:
        raise RuntimeError("Checkpoint context identity drift")
    digest = services.digest(context)
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise RuntimeError("Malformed context SHA256")
    if expected_digest is not None and digest != expected_digest:
        raise RuntimeError("M4 planned digest mismatch")
    relative = f"reports/m4_6_runs/{revision}/{stage.lower()}.json"
    return context, LifecycleReview(
        state="REVIEW_ONLY", stage=stage, repo_id=repo_id, target_revision=revision,
        context_sha256=digest, expected_manifest_sha256=manifest_sha256,
        checkpoint_relative=relative,
    )


def checkpoint_once(*, root: Path, stage: str, repo_id: str,
                    target_revision: str, baseline_snapshot: int | None = None,
                    expected_manifest_sha256: str | None = None,
                    publish: bool = False, approve_stage: str | None = None,
                    expected_context_sha256: str | None = None,
                    services: Services | None = None) -> dict[str, Any]:
    """Preview or publish one immutable checkpoint; never execute a DB Writer.

    Explicit publish needs a fresh second construction and matching context.
    Concurrent checkpoint publication is handled by M4's publish_checkpoint.
    """
    if type(publish) is not bool:
        raise ValueError("Invalid publish switch")
    if publish:
        if (approve_stage != stage or not isinstance(expected_context_sha256, str)
                or not SHA256.fullmatch(expected_context_sha256)):
            raise ValueError("Explicit stage approval + pinned context SHA256 required")
    elif approve_stage is not None or expected_context_sha256 is not None:
        raise ValueError("Preview cannot contain publish approval flags")
    services = services or production_services()
    inputs = dict(root=root, stage=stage, repo_id=repo_id,
                  revision=target_revision, baseline_snapshot=baseline_snapshot,
                  manifest_sha256=expected_manifest_sha256, services=services)
    first_context, first = _construct(**inputs)
    if not publish:
        return asdict(first)
    if first.context_sha256 != expected_context_sha256:
        raise RuntimeError("Approved context digest does not match current evidence")
    second_context, second = _construct(**inputs)
    if first != second or first_context != second_context:
        raise RuntimeError("Checkpoint evidence changed during approval recheck")
    rel, result = services.publish(root, second_context)
    if rel != second.checkpoint_relative or result not in ("CREATED_NEW", "ALREADY_VERIFIED"):
        raise RuntimeError("Unexpected checkpoint publication response; inspect durable state")
    stored = services.read_chain(root, target_revision)
    index = ORDER.index(stage)
    if not isinstance(stored, tuple) or len(stored) != 3 or stored[index] != second_context:
        raise RuntimeError("Published checkpoint failed independent chain verification")
    payload = asdict(second)
    payload.update(state=result, checkpoint_published=True)
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--publish", action="store_true")
    p.add_argument("--stage", choices=ORDER, required=True)
    p.add_argument("--repo-id", required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--baseline-snapshot", type=int)
    p.add_argument("--expected-manifest-sha256")
    p.add_argument("--approve-stage", choices=ORDER)
    p.add_argument("--expected-context-sha256")
    args = p.parse_args(argv)
    if args.publish and (args.approve_stage != args.stage or not args.expected_context_sha256):
        p.error("--publish requires matching --approve-stage and --expected-context-sha256")
    if args.plan and (args.approve_stage or args.expected_context_sha256):
        p.error("--plan forbids approval flags")
    try:
        from scripts.incremental.incremental_execution_context import ROOT
        output = checkpoint_once(
            root=ROOT, stage=args.stage, repo_id=args.repo_id,
            target_revision=args.target_revision,
            baseline_snapshot=args.baseline_snapshot,
            expected_manifest_sha256=args.expected_manifest_sha256,
            publish=args.publish, approve_stage=args.approve_stage,
            expected_context_sha256=args.expected_context_sha256,
        )
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"state": "BLOCKED", "stage": args.stage,
                          "detail": f"{type(exc).__name__}: {exc}",
                          "db_write_executed": False, "write_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
