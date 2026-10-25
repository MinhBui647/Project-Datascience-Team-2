"""M5.5.4c | Read-only, fail-closed bridge from M4 StageReview to M4.CSV WriterIntent.

It DOES NOT run a subprocess, write a checkpoint, grant write authorization,
or modify PostgreSQL/archive/Hugging Face. A subsequent, separately reviewed
executor would need its own explicit single-stage approval, fresh re-audit,
execution gate, and post-write verifier. A READY result here is NOT approval.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
from typing import Any

from scripts.incremental.incremental_guarded_stage_executor import (
    StageReview,
    review_one_stage,
    validate_read_only_commands,
)
from scripts.incremental.incremental_guarded_stage_planner import STAGE_ORDER
from scripts.orchestration.csv_write_intent import (
    STAGE_WRITERS,
    WriterIntent,
    build_writer_intent,
)

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class CoordinatorReview:
    state: str
    stage: str
    repo_id: str
    target_revision: str
    expected_context_sha256: str | None
    module: str | None = None
    csv_flag: str | None = None
    reason: str = ""
    write_authorized: bool = False
    write_executed: bool = False

    def __post_init__(self) -> None:
        if self.write_authorized or self.write_executed:
            raise ValueError("Read-only coordinator review cannot authorize or execute a write")


def _view(review: StageReview, stage: str, digest: str | None, state: str,
          *, reason: str = "", intent: WriterIntent | None = None) -> CoordinatorReview:
    return CoordinatorReview(
        state=state, stage=stage, repo_id=review.plan.repo_id,
        target_revision=review.plan.target_revision,
        expected_context_sha256=digest,
        module=None if intent is None else intent.module,
        csv_flag=None if intent is None else intent.csv_flag,
        reason=reason,
    )


def review_stage_writer(*, review: StageReview, stage: str,
                        expected_context_sha256: str | None = None,
                        expected_candidate_cache: str | None = None) -> CoordinatorReview:
    """Validate a typed, read-only M4 review and return an UNAPPROVED writer description.

    No command execution occurs. All CLI argv MUST come from M4's independently
    validated, stage-specific Execution Contract; arbitrary argv is rejected.
    """
    if not isinstance(review, StageReview):
        raise TypeError("A typed M4 StageReview is required")
    if stage not in STAGE_ORDER[1:]:
        raise ValueError("Unknown or non-executable stage")
    if review.plan.write_authorized or review.contract.write_authorized:
        raise RuntimeError("M4 review unexpectedly authorized a write")
    if (review.plan.decision.startswith("BLOCKED_")
            or review.contract.disposition == "BLOCKED"
            or review.plan.blockers or review.contract.blockers):
        return _view(review, stage, expected_context_sha256, "BLOCKED",
                     reason="M4 routing/execution contract blocked this stage")
    if review.plan.decision.startswith("SKIP_"):
        return _view(review, stage, expected_context_sha256, "SKIPPED",
                     reason="Completed/no-change target; no writer required")
    if stage != review.plan.next_review_stage or stage != review.contract.review_stage:
        raise RuntimeError("Requested stage differs from current M4 review stage")
    if review.contract.disposition != "REVIEW_READ_ONLY_PLAN" or review.contract.blockers:
        return _view(review, stage, expected_context_sha256, "AWAITING_EVIDENCE",
                     reason="M4 review has no unblocked stage-specific preview")
    digest = review.contract.context_sha256
    if (not isinstance(expected_context_sha256, str)
            or not SHA256.fullmatch(expected_context_sha256)
            or digest != expected_context_sha256):
        raise RuntimeError("Missing/mismatched pinned execution-context SHA256")
    if stage not in STAGE_WRITERS:
        return _view(review, stage, digest, "UNSUPPORTED_PREPARATION_STAGE",
                     reason="No CSV write mapping for this stage; no implicit writer substitution")
    commands = validate_read_only_commands(review)
    module = f"scripts.tools.{STAGE_WRITERS[stage][0]}"
    matching = [argv for argv in commands if len(argv) >= 3 and argv[2] == module]
    if len(matching) != 1:
        raise RuntimeError("Expected exactly one validated M4 read-only writer preview")
    intent = build_writer_intent(stage, matching[0])
    options = dict(zip(matching[0][3:-1:2], matching[0][4:-1:2]))
    if (options.get("--repo-id") != review.plan.repo_id
            or options.get("--target-revision") != review.plan.target_revision):
        raise RuntimeError("Writer preview target/repository identity drift")
    target_sid = review.plan.target_snapshot_id
    if target_sid is not None and options.get("--target-snapshot-id") != str(target_sid):
        raise RuntimeError("Writer preview target snapshot ID drift")
    if stage == "CANDIDATE_INGESTION":
        # Cache drift previously blocked M4.CSV. Never silently fall back to M4.5.
        cache = options.get("--cache-dir")
        if (not isinstance(expected_candidate_cache, str)
                or not expected_candidate_cache.startswith("reports/")
                or "\\" in expected_candidate_cache
                or any(part in ("", ".", "..") for part in expected_candidate_cache.split("/"))
                or cache != expected_candidate_cache):
            raise RuntimeError("Candidate cache must match explicit pinned cache directory")
    if intent.write_authorized:
        raise RuntimeError("WriterIntent unexpectedly granted write authority")
    return _view(review, stage, digest, "READY_FOR_SEPARATE_WRITE_GATE", intent=intent,
                 reason="Command shape mapped; no write granted or executed")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", required=True,
                        help="Read-only preview only; no apply/commit mode")
    parser.add_argument("--repo-id", default="Toibinguz/epl-premier-league-dataset")
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--baseline-snapshot", type=int, default=None)
    parser.add_argument("--stage", choices=STAGE_ORDER[1:], required=True)
    parser.add_argument("--expected-context-sha256", default=None)
    parser.add_argument("--expected-candidate-cache", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if not SHA40.fullmatch(args.target_revision):
        raise ValueError("Target revision must be pinned lowercase SHA40")
    if args.baseline_snapshot is not None and args.baseline_snapshot <= 0:
        raise ValueError("Invalid baseline snapshot")
    review = review_one_stage(
        repo_id=args.repo_id, target_revision=args.target_revision,
        baseline_snapshot=args.baseline_snapshot,
    )
    result = review_stage_writer(
        review=review, stage=args.stage,
        expected_context_sha256=args.expected_context_sha256,
        expected_candidate_cache=args.expected_candidate_cache,
    )
    print(json.dumps(vars(result), ensure_ascii=False, indent=2))
    if result.state in ("SKIPPED", "READY_FOR_SEPARATE_WRITE_GATE"):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
