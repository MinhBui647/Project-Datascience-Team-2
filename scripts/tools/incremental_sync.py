#!/usr/bin/env python3
"""M4.6.2d.3 | Read-only incremental sync plan with automatic baseline/state gate.

Uses the newest completed PostgreSQL snapshot unless --baseline-snapshot is
provided. Stops before Hugging Face requests for already-processed revisions,
and skips planning if a different revision changes no Raw-scoped files.

Explicit pinned SHA40 required. Never writes to PostgreSQL or archives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

if __package__:
    from .check_raw_updates import (
        REPO_ID, FileIdentity, load_baseline, load_target, compare_snapshots,
        select_latest_completed_baseline, read_target_snapshot_state,
        classify_target_state,
    )
    from .raw_sync_write_plan import ExistingFile, build_plan, read_db_state
    from .raw_sync_preflight import inspect_entry, read_existing_source_codes
else:
    from check_raw_updates import (
        REPO_ID, FileIdentity, load_baseline, load_target, compare_snapshots,
        select_latest_completed_baseline, read_target_snapshot_state,
        classify_target_state,
    )
    from raw_sync_write_plan import ExistingFile, build_plan, read_db_state
    from raw_sync_preflight import inspect_entry, read_existing_source_codes

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_JSON_FILE_BYTES = 16 * 1024 * 1024
MAX_BATCH_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class SyncAction:
    kind: str
    path: str
    category: str
    baseline_file_id: int | None
    source_code: str | None
    file_format: str | None
    expected_bytes: int | None
    lfs_sha256: str | None
    git_blob_id: str | None


@dataclass(frozen=True)
class SyncPlan:
    actions: tuple[SyncAction, ...]
    baseline_records: int
    reused_records: int
    superseded_records: int
    target_raw_count: int
    candidate_bytes: int
    fingerprint: str

    def count(self, category: str) -> int:
        return sum(a.category == category for a in self.actions)


def assemble_plan(
    baseline: Mapping[str, FileIdentity],
    target: Mapping[str, FileIdentity],
    existing: Mapping[str, ExistingFile],
    registered_sources: set[str],
    *,
    max_json_bytes: int = MAX_JSON_FILE_BYTES,
    max_batch_bytes: int = MAX_BATCH_BYTES,
    allow_csv_planning: bool = False,
    max_csv_bytes: int = 128 * 1024 * 1024,
) -> SyncPlan:
    """Pure fail-closed planner; suitable for offline regression tests.

    A NEW/CHANGED action means *lookup immutable SHA256 version after rehash*;
    it does NOT automatically mean an INSERT, and cannot authorize a write.
    """
    if type(max_json_bytes) is not int or max_json_bytes <= 0:
        raise ValueError("Invalid per-file JSON budget")
    if type(max_batch_bytes) is not int or max_batch_bytes <= 0:
        raise ValueError("Invalid total batch budget")
    if type(allow_csv_planning) is not bool:
        raise ValueError("Invalid CSV planning switch")
    if type(max_csv_bytes) is not int or max_csv_bytes <= 0:
        raise ValueError("Invalid per-file CSV budget")
    core = build_plan(baseline, target, existing)
    actions = []
    for path in core.reuse:
        previous = existing[path]
        actions.append(SyncAction(
            kind="REUSE_FILE_ID", path=path, category="UNCHANGED",
            baseline_file_id=previous.file_id, source_code=None,
            file_format=None, expected_bytes=previous.size,
            lfs_sha256=target[path].lfs_sha256,
            git_blob_id=target[path].git_blob_id,
        ))

    total_bytes = 0
    for category in ("NEW", "CHANGED"):
        for path in getattr(core, category.lower()):
            remote = target[path]
            pf = inspect_entry(path, category, remote)
            errors = list(pf.errors)
            if pf.source_code not in registered_sources:
                errors.append(f"Source {pf.source_code!r} absent from DB registry")
            # This first incremental writer is intentionally JSON-only. Other
            # formats must be handled by explicit separate adapters, not guesswork.
            if pf.file_format == "json":
                file_budget = max_json_bytes
                budget_label = "JSON"
            elif pf.file_format == "csv" and allow_csv_planning:
                file_budget = max_csv_bytes
                budget_label = "CSV"
            else:
                file_budget = None
                budget_label = str(pf.file_format).upper()

                if allow_csv_planning:
                    errors.append(
                        "Incremental planning supports JSON and CSV only"
                    )
                else:
                    errors.append(
                        "Incremental phase 1 accepts JSON files only"
                    )

            if (
                type(remote.size) is not int
                or remote.size <= 0
                or (
                    file_budget is not None
                    and remote.size > file_budget
                )
            ):
                errors.append(
                    f"{budget_label} exceeds per-file byte budget"
                )
            if errors:
                raise RuntimeError(f"Candidate validation blocked: {path}: {'; '.join(errors)}")
            total_bytes += remote.size
            actions.append(SyncAction(
                kind="UPSERT_VERSION_AFTER_REHASH", path=path,
                category=category,
                baseline_file_id=existing[path].file_id if category == "CHANGED" else None,
                source_code=pf.source_code, file_format=pf.file_format,
                expected_bytes=remote.size, lfs_sha256=remote.lfs_sha256,
                git_blob_id=remote.git_blob_id,
            ))
    if total_bytes > max_batch_bytes:
        raise RuntimeError(f"Candidate batch size {total_bytes} exceeds budget {max_batch_bytes}")
    for path in core.removed:
        actions.append(SyncAction(
            kind="NOTIFY_ONLY_NO_DELETE", path=path, category="REMOVED",
            baseline_file_id=existing[path].file_id,
            source_code=None, file_format=None, expected_bytes=None,
            lfs_sha256=None, git_blob_id=None,
        ))
    actions.sort(key=lambda a: (a.path, a.category))
    if len({a.path for a in actions}) != len(actions):
        raise RuntimeError("Duplicate path in sync plan")
    if core.reused_record_count + core.superseded_record_count != core.baseline_linked_record_count:
        raise RuntimeError("Baseline record accounting mismatch")
    if (len(core.reuse) + len(core.new) + len(core.changed)) != len(target):
        raise RuntimeError("Target file accounting mismatch")
    # Fingerprint is for detecting a changed plan, NOT authentication.
    serial = [
        (a.kind, a.path, a.category, a.baseline_file_id, a.source_code,
         a.file_format, a.expected_bytes, a.lfs_sha256, a.git_blob_id)
        for a in actions
    ]
    fingerprint = hashlib.sha256(
        json.dumps(serial, ensure_ascii=True, separators=(",", ":"),
                   allow_nan=False).encode("utf-8")
    ).hexdigest()
    return SyncPlan(
        actions=tuple(actions),
        baseline_records=core.baseline_linked_record_count,
        reused_records=core.reused_record_count,
        superseded_records=core.superseded_record_count,
        target_raw_count=len(target), candidate_bytes=total_bytes,
        fingerprint=fingerprint,
    )


def decide_preflight_action(baseline, target) -> str:
    """Decide whether comparison can stop before touching writer planning.

    Returns fail-closed decisions; pure and deterministic.
    """
    groups = compare_snapshots(baseline, target)
    if groups["REVIEW"]:
        return "BLOCKED_METADATA_REVIEW"
    if not groups["NEW"] and not groups["CHANGED"] and not groups["REMOVED"]:
        return "SKIP_NO_RAW_CHANGES"
    return "PLAN_RAW_CHANGES"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-snapshot", type=int, default=None,
                    help="Optional completed baseline override; omitted = latest completed")
    ap.add_argument("--target-revision", required=True,
                    help="Pinned, lowercase, full SHA40 (no floating main ref)")
    ap.add_argument("--repo-id", default=REPO_ID)
    ap.add_argument("--plan", action="store_true", help="Required; strictly read-only")
    args = ap.parse_args()
    if not args.plan:
        ap.error("--plan is required; this module has NO write mode")
    if args.baseline_snapshot is not None and args.baseline_snapshot <= 0:
        ap.error("Positive baseline snapshot ID required")
    if not SHA40.fullmatch(args.target_revision):
        ap.error("Target must be a pinned, lowercase, full SHA40 revision")

    selection = None
    if args.baseline_snapshot is None:
        selection = select_latest_completed_baseline(args.repo_id)
        baseline_id = selection.snapshot_id
        selection_mode = "AUTO_LATEST_COMPLETED"
    else:
        baseline_id = args.baseline_snapshot
        selection_mode = "EXPLICIT_OVERRIDE"

    print("=== M4.6.2d.3 | INCREMENTAL SYNC ORCHESTRATOR PLAN ===", flush=True)
    base_rev, baseline = load_baseline(baseline_id, args.repo_id)
    if selection is not None and (
        selection.hf_revision != base_rev or
        selection.raw_scope_file_count != len(baseline)
    ):
        raise RuntimeError("Baseline drift between selection and Raw observation lookup")

    # All known target states short-circuit without HF network activity.
    target_state = read_target_snapshot_state(args.repo_id, args.target_revision)
    decision = classify_target_state(baseline_id, base_rev, args.target_revision, target_state)
    print("Repository:", args.repo_id)
    print("Baseline snapshot:", baseline_id, "|", selection_mode, "| completed")
    print("Baseline revision:", base_rev)
    print("Target revision:", args.target_revision)
    print("Target snapshot:", "ABSENT" if target_state is None else
          f"#{target_state.snapshot_id} | {target_state.status}")
    print("Target state decision:", decision)
    if decision != "PLAN_NEW_REVISION":
        print("Hugging Face requests: NONE | Database writes: NONE")
        print("Raw archive writes: NONE")
        if decision.startswith("SKIP_"):
            print("M4.6.2d.3 ORCHESTRATOR PLAN: PASS (NO INGESTION REQUIRED)")
            return 0
        print("M4.6.2d.3 ORCHESTRATOR PLAN: BLOCKED")
        return 2

    target_rev, target, excluded = load_target(
        args.repo_id, args.target_revision, already_resolved=True
    )
    if target_rev != args.target_revision or target_rev == base_rev:
        raise RuntimeError("Pinned target revision invalid or equals baseline")
    raw_decision = decide_preflight_action(baseline, target)
    groups = compare_snapshots(baseline, target)
    print("Target Raw files:", len(target), "| Excluded non-Raw files:", excluded)
    print("Raw identity groups:", ", ".join(f"{k}={len(v)}" for k, v in groups.items()))
    print("Raw change decision:", raw_decision)
    if raw_decision != "PLAN_RAW_CHANGES":
        print("Database writes: NONE | Raw archive writes: NONE")
        if raw_decision == "SKIP_NO_RAW_CHANGES":
            print("M4.6.2d.3 ORCHESTRATOR PLAN: PASS (NO RAW CHANGES)")
            return 0
        print("M4.6.2d.3 ORCHESTRATOR PLAN: BLOCKED")
        return 2

    # Rechecks target absence, uniqueness constraints and completed baseline,
    # guarding against a target appearing between the first DB read and planning.
    db_rev, existing = read_db_state(baseline_id, args.repo_id, target_rev)
    if db_rev != base_rev:
        raise RuntimeError("Baseline revision drift between DB reads")
    registered_sources = read_existing_source_codes()
    plan = assemble_plan(baseline, target, existing, registered_sources)

    print("Target Raw files:", plan.target_raw_count)
    print("REUSE existing file_id:", plan.count("UNCHANGED"))
    print("NEW version candidates:", plan.count("NEW"))
    print("CHANGED version candidates:", plan.count("CHANGED"))
    print("REMOVED notify-only:", plan.count("REMOVED"))
    print("Candidate bytes:", plan.candidate_bytes)
    print("Baseline records:", plan.baseline_records)
    print("Reused record count:", plan.reused_records)
    print("Superseded record count:", plan.superseded_records)
    print("Target record count: PENDING CONTENT INGESTION")
    print("Target snapshot ID: NOT ASSUMED OR CREATED")
    print("Plan fingerprint SHA256:", plan.fingerprint)
    print("\n--- VERSION ACTIONS (NEW/CHANGED) ---")
    for action in plan.actions:
        if action.category not in ("NEW", "CHANGED"):
            continue
        print(f"[{action.category:7}] {action.path} | {action.expected_bytes} bytes "
              f"| source={action.source_code} | action={action.kind}")
    print("\n--- SAFETY BOUNDARIES ---")
    print("Per-file writer, manifest and finalization: NOT INVOKED")
    print("Actual content SHA verification: REQUIRED BEFORE ANY FUTURE WRITE")
    print("Database writes: NONE")
    print("File contents downloaded: NONE")
    print("Raw archive writes: NONE")
    print("M4.6.2d.3 ORCHESTRATOR PLAN: PASS (NOT APPROVAL TO COMMIT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
