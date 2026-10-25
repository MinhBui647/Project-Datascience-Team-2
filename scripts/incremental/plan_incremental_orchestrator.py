#!/usr/bin/env python3
r"""M4.6.4d.3 | Strictly read-only incremental orchestration planner.

Use from repository root:
  .\.venv_v2\Scripts\python.exe scripts/tools/plan_incremental_orchestrator.py \
      --target-revision <FULL_LOWERCASE_SHA40> --plan

Selects a completed baseline, checks the target state BEFORE any HF request,
rebuilds the immutable Raw SyncPlan when needed, and constructs a PlannedContext
in memory. Displays only read-only next-step CLI previews. Never invokes a
child process; never writes checkpoints, cache, archive objects or PostgreSQL.

Existing in_progress targets require separate recovery, NOT re-planning.
This plan is not permission to perform download, promotion or any DB write.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
from typing import Any, Callable

from scripts.tools.incremental_execution_context import (
    PlannedContext, build_planned, checkpoint_digest, cli_arguments,
)

DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class ReadOnlyOutcome:
    decision: str
    baseline_snapshot_id: int
    baseline_revision: str
    target_revision: str
    target_snapshot_id: int | None
    planned: PlannedContext | None

    def __post_init__(self):
        if self.decision == "PLAN_NEW_REVISION":
            if self.planned is None or self.target_snapshot_id is not None:
                raise ValueError("New revision requires a new unregistered PlannedContext")
        elif self.planned is not None:
            raise ValueError("Skipped/blocked decision cannot contain a new context")


def default_readers() -> Any:
    """Late imports keep --help safe and make network/DB use explicit in --plan."""
    from types import SimpleNamespace
    from scripts.tools.check_raw_updates import (
        select_latest_completed_baseline, load_baseline, read_target_snapshot_state,
        classify_target_state, load_target,
    )
    from scripts.tools.incremental_sync import assemble_plan, decide_preflight_action
    from scripts.tools.raw_sync_write_plan import read_db_state
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.incremental_cache_gate import candidate_actions
    from scripts.tools.download_incremental_candidates import DownloadContext
    return SimpleNamespace(
        select_baseline=select_latest_completed_baseline,
        load_baseline=load_baseline,
        read_target_state=read_target_snapshot_state,
        classify_target=classify_target_state,
        load_target=load_target,
        decide_raw_action=decide_preflight_action,
        read_db_state=read_db_state,
        source_codes=read_existing_source_codes,
        assemble_plan=assemble_plan,
        candidate_actions=candidate_actions,
        download_context_type=DownloadContext,
    )


def plan_read_only(*, repo_id: str, target_revision: str,
                   baseline_snapshot: int | None = None,
                   readers: Any = None,
                   allow_csv_planning: bool = False) -> ReadOnlyOutcome:
    """Only DB reads and HF metadata queries; never writes or downloads bodies.

    Uses existing target-state gates. The registered target is NEVER treated as
    a new ingestion candidate. Current revision exits before HF requests.
    """
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository ID")
    if not isinstance(target_revision, str) or SHA40.fullmatch(target_revision) is None:
        raise ValueError("Target revision must be a full lowercase SHA40")
    if baseline_snapshot is not None and (type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise ValueError("Invalid explicit baseline snapshot ID")
    if type(allow_csv_planning) is not bool:
        raise ValueError("Invalid CSV read-only planning opt-in")
    r = readers if readers is not None else default_readers()

    selection = None
    if baseline_snapshot is None:
        selection = r.select_baseline(repo_id)
        baseline_snapshot = selection.snapshot_id
        if type(baseline_snapshot) is not int or baseline_snapshot <= 0:
            raise RuntimeError("Invalid selected baseline snapshot ID")
    base_revision, baseline = r.load_baseline(baseline_snapshot, repo_id)
    if not isinstance(base_revision, str) or SHA40.fullmatch(base_revision) is None:
        raise RuntimeError("Invalid completed baseline revision")
    if not isinstance(baseline, dict) or not baseline:
        raise RuntimeError("Completed baseline has no Raw files")
    if selection is not None and (selection.hf_revision != base_revision
                                   or selection.raw_scope_file_count != len(baseline)):
        raise RuntimeError("Baseline selection and observations drifted")

    target_state = r.read_target_state(repo_id, target_revision)
    decision = r.classify_target(baseline_snapshot, base_revision, target_revision, target_state)
    target_id = target_state.snapshot_id if target_state is not None else None
    if decision != "PLAN_NEW_REVISION":
        return ReadOnlyOutcome(decision, baseline_snapshot, base_revision,
                               target_revision, target_id, None)
    if target_state is not None:
        raise RuntimeError("Target state gate produced invalid new-revision decision")

    # The state gate has cleared; HF metadata is allowed but no file bodies.
    revision, target, excluded = r.load_target(repo_id, target_revision, already_resolved=True)
    if revision != target_revision or revision == base_revision:
        raise RuntimeError("Target SHA40 changed or equals completed baseline")
    if type(excluded) is not int or excluded < 0 or not isinstance(target, dict) or not target:
        raise RuntimeError("Invalid HF target inventory")
    raw_decision = r.decide_raw_action(baseline, target)
    if raw_decision == "SKIP_NO_RAW_CHANGES":
        return ReadOnlyOutcome(raw_decision, baseline_snapshot, base_revision,
                               target_revision, None, None)
    if raw_decision != "PLAN_RAW_CHANGES":
        return ReadOnlyOutcome(raw_decision, baseline_snapshot, base_revision,
                               target_revision, None, None)

    # No removed paths supported by JSON-only incremental V1; block before DB plan.
    if set(baseline) - set(target):
        return ReadOnlyOutcome("BLOCKED_REMOVED_PATHS", baseline_snapshot, base_revision,
                               target_revision, None, None)

    db_revision, existing = r.read_db_state(baseline_snapshot, repo_id, target_revision)
    if db_revision != base_revision:
        raise RuntimeError("Baseline revision drifted across DB preflight")
    sources = r.source_codes()
    if allow_csv_planning:
        plan = r.assemble_plan(
            baseline, target, existing, sources, allow_csv_planning=True
        )
    else:
        plan = r.assemble_plan(baseline, target, existing, sources)
    if plan.count("REMOVED") or plan.count("REVIEW"):
        raise RuntimeError("Blocked SyncPlan category")
    if allow_csv_planning:
        actions = tuple(r.candidate_actions(plan, target, allow_csv=True))
    else:
        actions = tuple(r.candidate_actions(plan, target))
    ctx = r.download_context_type(
        repo_id, baseline_snapshot, base_revision, target_revision,
        plan, target, actions, excluded,
        *( (True,) if allow_csv_planning else () ),
    )
    planned_kwargs = dict(expected_baseline_files=len(baseline),
                          expected_full_files=len(target) + excluded)
    if allow_csv_planning:
        planned_kwargs["allow_csv_context"] = True
    planned = build_planned(ctx, **planned_kwargs)
    return ReadOnlyOutcome("PLAN_NEW_REVISION", baseline_snapshot, base_revision,
                           target_revision, None, planned)


def preview_only_commands(planned: PlannedContext) -> tuple[tuple[str, ...], ...]:
    """Only safe read-only downstream commands; never --commit/--apply/--download.

    Candidate downloader's current --plan expects its cache folder to exist;
    the command is a preview and is NOT automatically run here.
    """
    if not isinstance(planned, PlannedContext):
        raise TypeError("PlannedContext required")
    return (
        ("scripts/tools/prepare_incremental_manifest.py", *cli_arguments(planned, "manifest_preparation"), "--plan"),
        ("scripts/tools/download_incremental_candidates.py", *cli_arguments(planned, "download"), "--plan"),
    )


def format_report(outcome: ReadOnlyOutcome) -> str:
    """Human-readable report. Absolutely no write command is generated."""
    if not isinstance(outcome, ReadOnlyOutcome):
        raise TypeError("ReadOnlyOutcome required")
    lines = [
        "=== M4.6.4d.3 | READ-ONLY INCREMENTAL ORCHESTRATOR ===",
        f"Baseline snapshot: {outcome.baseline_snapshot_id} | completed",
        f"Baseline revision: {outcome.baseline_revision}",
        f"Target revision: {outcome.target_revision}",
        f"Target snapshot: {'ABSENT' if outcome.target_snapshot_id is None else '#' + str(outcome.target_snapshot_id)}",
        f"Decision: {outcome.decision}",
    ]
    if outcome.planned is not None:
        p = outcome.planned
        lines.extend([
            f"Plan fingerprint SHA256: {p.plan_fingerprint}",
            f"Raw: {p.expected_raw_files} | full manifest: {p.expected_full_files}",
            f"REUSE: {p.expected_reuse} | candidates: {p.expected_files} | candidate bytes: {p.expected_bytes}",
            f"Baseline record references: {p.expected_baseline_records}",
            f"REUSE record references: {p.expected_reused_records}",
            f"Planned checkpoint SHA256 (in memory; NOT WRITTEN): {checkpoint_digest(p)}",
            "NEXT READ-ONLY COMMAND PREVIEWS (NOT INVOKED):",
        ])
        for argv in preview_only_commands(p):
            lines.append("  python " + " ".join(argv))
        if any(c.path.endswith(".csv") for c in getattr(p, "candidates", ())):
            lines.append("CSV is read-only PREVIEW ONLY: downstream CLI/registration remain DISABLED.")
            lines.append("Do NOT execute the downstream command previews for CSV yet.")
        lines.append("Downloader --plan may require an existing reports/ cache directory.")
        lines.append("Candidate records, manifest SHA256 and snapshot ID: PENDING VERIFIED STAGES")
    elif outcome.decision == "RECOVERY_REQUIRED":
        lines.append("Existing in_progress snapshot: STOP; use a separate guarded recovery audit.")
    elif outcome.decision.startswith("BLOCKED_"):
        lines.append("Blocked: manual review required; no ingestion permitted.")
    else:
        lines.append("No new Raw ingestion plan required.")
    lines.extend([
        "HF file-body downloads: NONE | Subprocesses: NONE",
        "PostgreSQL writes: NONE | Cache/archive writes: NONE | Checkpoints written: NONE",
        "M4.6.4d.3 READ-ONLY ORCHESTRATION: " +
        ("PASS (NOT WRITE APPROVAL)" if outcome.planned else
         "PASS (NO INGESTION REQUIRED)" if outcome.decision.startswith("SKIP_") else
         "BLOCKED (NO WRITES)"),
    ])
    return "\n".join(lines)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-revision", required=True, help="Pinned SHA40 only; never 'main'")
    p.add_argument("--baseline-snapshot", type=int, default=None,
                   help="Optional completed baseline; omitted = newest completed")
    p.add_argument("--repo-id", default=DEFAULT_REPO)
    p.add_argument("--plan", action="store_true", required=True,
                   help="Read-only; no write mode exists")
    p.add_argument("--allow-csv-plan-only", action="store_true",
                   help="Explicit read-only CSV planning; NEVER authorizes cache, archive or DB writes")
    args = p.parse_args(argv)
    if not args.repo_id or not args.repo_id.strip():
        p.error("Repository cannot be blank")
    if not SHA40.fullmatch(args.target_revision):
        p.error("Target revision must be a full lowercase SHA40")
    if args.baseline_snapshot is not None and args.baseline_snapshot <= 0:
        p.error("Explicit baseline snapshot must be positive")
    return args


def main(argv=None, *, readers=None, output: Callable[[str], None] = print) -> int:
    args = parse_args(argv)
    options = dict(repo_id=args.repo_id, target_revision=args.target_revision,
                   baseline_snapshot=args.baseline_snapshot, readers=readers)
    if args.allow_csv_plan_only:
        options["allow_csv_planning"] = True
    result = plan_read_only(**options)
    output(format_report(result))
    if result.planned is not None or result.decision.startswith("SKIP_"):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
