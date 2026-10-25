#!/usr/bin/env python3
"""M4.6.4d.6c.2b.2b.4c.3 | Guarded PLANNED checkpoint publisher.

The ONLY mutation performed by --publish is a create-only planned.json via the
existing execution_context.publish_checkpoint(). No DB updates, file-body
fetches, archive/cache publication, ingestion, writer dispatch, or stage advances.

The plan is recomputed independently twice with the current *latest completed*
DB baseline and a fixed SHA40 Hugging Face target, then compared with explicit
operator pins. Stage Planner READY alone NEVER authorizes this publication.

From project root:
  python -m scripts.tools.incremental_guarded_planned_publisher \\
    --target-revision <sha40> --expected-baseline-snapshot <id> \\
    --expected-plan-fingerprint <sha256> --expected-context-sha256 <sha256> --plan

After unit tests and manual review, explicitly opt in to the single checkpoint:
  ... same pins ... --publish --confirm-planned-only
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.tools.incremental_execution_context import (
    MAX_CHECKPOINT_BYTES, ROOT, SHA40, SHA256, PlannedContext,
    _canonical, _checkpoint_rel, _guard_path, checkpoint_digest,
    checkpoint_envelope, publish_checkpoint, read_checkpoint,
)

DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"


@dataclass(frozen=True)
class PlannedPublicationResult:
    status: str
    revision: str
    digest: str | None
    relative_path: str | None
    detail: str
    expected_files: int = 0
    reused_files: int = 0
    write_authorized: bool = False  # checkpoint creation != general write permission

    def __post_init__(self) -> None:
        if self.status not in ("PREVIEW_READY", "CREATED_NEW", "ALREADY_VERIFIED", "BLOCKED"):
            raise ValueError("Unknown planned publication status")
        if self.write_authorized:
            raise ValueError("Checkpoint status must never grant further write authority")


def _validate_pins(*, repo_id: str, target_revision: str,
                   expected_baseline_snapshot: int, expected_plan_fingerprint: str,
                   expected_context_sha256: str, root: Path) -> None:
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository")
    if not isinstance(target_revision, str) or SHA40.fullmatch(target_revision) is None:
        raise ValueError("Pinned lowercase SHA40 required; never use main")
    if type(expected_baseline_snapshot) is not int or expected_baseline_snapshot <= 0:
        raise ValueError("Positive expected baseline snapshot required")
    if not isinstance(expected_plan_fingerprint, str) or SHA256.fullmatch(expected_plan_fingerprint) is None:
        raise ValueError("Explicit 64-character plan fingerprint required")
    if not isinstance(expected_context_sha256, str) or SHA256.fullmatch(expected_context_sha256) is None:
        raise ValueError("Explicit 64-character PlannedContext SHA256 required")
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Existing nonsymlink project root required")


def _fresh_plan(*, reader: Callable[..., Any], repo_id: str,
                target_revision: str, expected_baseline_snapshot: int,
                expected_plan_fingerprint: str,
                expected_context_sha256: str) -> PlannedContext:
    # No explicit baseline override: the existing planner must select the current
    # newest completed baseline from PostgreSQL on *each* independent read.
    outcome = reader(repo_id=repo_id, target_revision=target_revision,
                     baseline_snapshot=None)
    if (outcome.decision != "PLAN_NEW_REVISION"
            or outcome.target_snapshot_id is not None
            or outcome.target_revision != target_revision
            or outcome.baseline_snapshot_id != expected_baseline_snapshot):
        raise RuntimeError("Not a new target or newest completed baseline changed")
    p = outcome.planned
    if not isinstance(p, PlannedContext):
        raise RuntimeError("Fresh incremental planner did not return a typed PlannedContext")
    if ((p.repo_id, p.target_revision, p.baseline_revision, p.baseline_snapshot_id,
         p.plan_fingerprint) !=
        (repo_id, target_revision, outcome.baseline_revision,
         expected_baseline_snapshot, expected_plan_fingerprint)):
        raise RuntimeError("Rebuilt plan identity/baseline/fingerprint differs from pins")
    if checkpoint_digest(p) != expected_context_sha256:
        raise RuntimeError("Rebuilt PlannedContext SHA256 differs from operator pin")
    if (p.expected_files < 1 or p.expected_reuse + p.expected_files != p.expected_raw_files
            or any(c.category not in ("NEW", "CHANGED") for c in p.candidates)):
        raise RuntimeError("Incremental plan accounting invalid")
    if len(_canonical(checkpoint_envelope(p))) > MAX_CHECKPOINT_BYTES:
        raise RuntimeError("Planned checkpoint exceeds size limit")
    return p


def _existing_state(*, root: Path, revision: str,
                    checkpoint_reader: Callable[..., Any]) -> PlannedContext | None:
    # _guard_path rejects symlink parents, unsafe traversal and non-file targets.
    dest = _guard_path(root, _checkpoint_rel(revision, "planned"))
    if not dest.exists():
        return None
    stored = checkpoint_reader(root, revision, "planned")
    if not isinstance(stored, PlannedContext):
        raise RuntimeError("Existing PLANNED checkpoint is malformed")
    return stored


def run_guarded(*, repo_id: str, target_revision: str,
                expected_baseline_snapshot: int,
                expected_plan_fingerprint: str,
                expected_context_sha256: str,
                publish: bool = False,
                confirmed: bool = False,
                root: Path = ROOT,
                plan_reader: Callable[..., Any] | None = None,
                checkpoint_writer: Callable[..., Any] = publish_checkpoint,
                checkpoint_reader: Callable[..., Any] = read_checkpoint) -> PlannedPublicationResult:
    """Check exact pinned plan; optionally create ONLY the planned checkpoint.

    Fail-closed: no write unless explicitly confirmed with a fresh double plan
    and an exact operator-supplied digest. Retry may verify identical bytes,
    but never overwrite. A drift *after* publication cannot be rolled back;
    callers must treat failure as BLOCKED and manually inspect the checkpoint.
    """
    _validate_pins(repo_id=repo_id, target_revision=target_revision,
                   expected_baseline_snapshot=expected_baseline_snapshot,
                   expected_plan_fingerprint=expected_plan_fingerprint,
                   expected_context_sha256=expected_context_sha256, root=root)
    if publish and not confirmed:
        raise RuntimeError("Publishing requires --confirm-planned-only")
    if not publish and confirmed:
        raise RuntimeError("Confirmation flag is forbidden in read-only --plan mode")
    if plan_reader is None:
        from scripts.tools.plan_incremental_orchestrator import plan_read_only
        plan_reader = plan_read_only

    first = _fresh_plan(reader=plan_reader, repo_id=repo_id,
                        target_revision=target_revision,
                        expected_baseline_snapshot=expected_baseline_snapshot,
                        expected_plan_fingerprint=expected_plan_fingerprint,
                        expected_context_sha256=expected_context_sha256)
    rel = _checkpoint_rel(target_revision, "planned")
    stored = _existing_state(root=root, revision=target_revision,
                             checkpoint_reader=checkpoint_reader)
    if stored is not None and stored != first:
        raise RuntimeError("Existing planned checkpoint differs; refusing overwrite")
    if not publish:
        return PlannedPublicationResult("PREVIEW_READY", target_revision,
               expected_context_sha256, rel,
               "Read-only, fresh plan matches explicit pins; no checkpoint written",
               first.expected_files, first.expected_reuse)

    # Re-plan immediately before the single permitted filesystem publication.
    second = _fresh_plan(reader=plan_reader, repo_id=repo_id,
                         target_revision=target_revision,
                         expected_baseline_snapshot=expected_baseline_snapshot,
                         expected_plan_fingerprint=expected_plan_fingerprint,
                         expected_context_sha256=expected_context_sha256)
    if second != first:
        raise RuntimeError("Fresh plans differ; checkpoint publication blocked")
    newer_stored = _existing_state(root=root, revision=target_revision,
                                   checkpoint_reader=checkpoint_reader)
    if newer_stored is not None and newer_stored != second:
        raise RuntimeError("Existing checkpoint changed before publication")

    actual_rel, outcome = checkpoint_writer(root, second)
    if (actual_rel != rel or outcome not in ("CREATED_NEW", "ALREADY_VERIFIED")):
        raise RuntimeError("Checkpoint publisher returned unexpected identity/result")
    verified = checkpoint_reader(root, target_revision, "planned")
    if verified != second or checkpoint_digest(verified) != expected_context_sha256:
        raise RuntimeError("Checkpoint post-publication verification failed")
    return PlannedPublicationResult(outcome, target_revision, expected_context_sha256,
                                    rel, "Only planned.json published and independently read back",
                                    second.expected_files, second.expected_reuse)


def format_result(result: PlannedPublicationResult) -> str:
    return "\n".join((
        "=== M4.6.4d.6c.2b.2b.4c.3 | GUARDED PLANNED CHECKPOINT ===",
        f"Status: {result.status}",
        f"Revision: {result.revision}",
        f"PlannedContext SHA256: {result.digest or 'NONE'}",
        f"Checkpoint: {result.relative_path or 'NONE'}",
        f"REUSE: {result.reused_files} | Candidates: {result.expected_files}",
        f"Detail: {result.detail}",
        "PostgreSQL writes: NONE | HF file-body downloads: NONE",
        "Archive/cache writes: NONE | Stage advancement: NOT AUTHORIZED",
        "Checkpoint write: " + ("ONLY PLANNED CREATE-ONLY" if result.status == "CREATED_NEW" else "NONE"),
        "Further write authorization: NO",
    ))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="Read-only preview")
    mode.add_argument("--publish", action="store_true", help="Single PLANNED checkpoint publication")
    parser.add_argument("--confirm-planned-only", action="store_true")
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--expected-baseline-snapshot", type=int, required=True)
    parser.add_argument("--expected-plan-fingerprint", required=True)
    parser.add_argument("--expected-context-sha256", required=True)
    args = parser.parse_args(argv)
    if args.publish != args.confirm_planned_only:
        parser.error("--publish requires --confirm-planned-only; --plan forbids it")
    if SHA40.fullmatch(args.target_revision) is None:
        parser.error("Only a pinned lowercase SHA40 is accepted")
    if SHA256.fullmatch(args.expected_plan_fingerprint) is None:
        parser.error("Invalid expected plan fingerprint")
    if SHA256.fullmatch(args.expected_context_sha256) is None:
        parser.error("Invalid expected PlannedContext SHA256")
    if args.expected_baseline_snapshot <= 0:
        parser.error("Invalid expected baseline")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_guarded(
            repo_id=args.repo_id, target_revision=args.target_revision,
            expected_baseline_snapshot=args.expected_baseline_snapshot,
            expected_plan_fingerprint=args.expected_plan_fingerprint,
            expected_context_sha256=args.expected_context_sha256,
            publish=args.publish, confirmed=args.confirm_planned_only,
        )
    except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
        result = PlannedPublicationResult("BLOCKED", args.target_revision, None, None,
                                          f"{type(exc).__name__}: {exc}")
    print(format_result(result))
    return 2 if result.status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
