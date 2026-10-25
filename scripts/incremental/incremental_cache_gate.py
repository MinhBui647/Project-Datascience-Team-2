#!/usr/bin/env python3
"""M4.5.4c.3g: fail-closed read-only gate for 14 pinned HF JSON cache files.

Rebuilds the DB+HF plan, verifies fingerprint, reads ONLY previously cached
candidate bodies (local_files_only=True), revalidates parser+content hashes,
and computes the snapshot record count estimate. No DB/archive writes.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.check_raw_updates import REPO_ID, FileIdentity, load_baseline, load_target
from scripts.tools.incremental_sync import SyncPlan, assemble_plan
from scripts.tools.raw_sync_write_plan import read_db_state
from scripts.tools.raw_sync_preflight import read_existing_source_codes
from scripts.tools.validate_raw_contents import ContentResult, validate_local_json

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_CACHE = "reports/m4_5_content_cache"


@dataclass(frozen=True)
class CandidateCheck:
    path: str
    category: str
    bytes: int
    sha256: str
    records: int


def candidate_actions(plan: SyncPlan, target: Mapping[str, FileIdentity], *, allow_csv: bool = False):
    if type(allow_csv) is not bool:
        raise ValueError("Invalid CSV cache permission")
    if not isinstance(plan, SyncPlan) or plan.target_raw_count != len(target):
        raise ValueError("Plan/target count mismatch")
    if plan.count("REMOVED") != 0 or plan.count("REVIEW") != 0:
        raise ValueError("Removed/review path requires separate workflow")
    if len(plan.actions) != len(target):
        raise ValueError("Plan/target action mismatch")
    if {a.path for a in plan.actions} != set(target):
        raise ValueError("Plan/target paths mismatch")
    picked = tuple(a for a in plan.actions if a.category in ("NEW", "CHANGED"))
    if len(picked) != plan.count("NEW") + plan.count("CHANGED"):
        raise ValueError("Candidate action count mismatch")
    if not picked:
        raise ValueError("No candidate file bodies to validate")
    permitted = {"json", "csv"} if allow_csv else {"json"}
    if any(a.kind != "UPSERT_VERSION_AFTER_REHASH" or a.file_format not in permitted
           or not a.path.endswith("." + a.file_format) for a in picked):
        raise ValueError("Unsupported candidate format/action" if allow_csv
                         else "Non-JSON candidate or invalid action")
    if sum(a.expected_bytes for a in picked) != plan.candidate_bytes:
        raise ValueError("Candidate byte count mismatch")
    return picked


def check_cached_candidate(action, identity: FileIdentity, local_path: Path, *,
                           allow_csv: bool = False) -> CandidateCheck:
    """Revalidate JSON or explicitly opted-in CSV bytes; strictly read-only."""
    if type(allow_csv) is not bool:
        raise ValueError("Invalid CSV cache permission")
    if action.path is None or action.expected_bytes != identity.size:
        raise ValueError("Candidate size mismatch")
    if (action.git_blob_id != identity.git_blob_id or action.lfs_sha256 != identity.lfs_sha256):
        raise ValueError("Candidate content identity mismatch")
    if not allow_csv or (getattr(action, "file_format", "json") == "json"
                         and action.path.endswith(".json")):
        result: ContentResult = validate_local_json(local_path, identity)
        nbytes, digest, nrecords = result.byte_count, result.sha256, result.record_count
    elif allow_csv and getattr(action, "file_format", None) == "csv" and action.path.endswith(".csv"):
        from scripts.incremental.incremental_csv_adapter import inspect_csv_archive
        result = inspect_csv_archive(
            local_path, expected_size=identity.size,
            expected_lfs_sha256=identity.lfs_sha256,
            expected_git_blob_id=identity.git_blob_id,
        )
        nbytes, digest, nrecords = (
            result.file_size_bytes, result.content_sha256, result.record_count
        )
    else:
        raise ValueError("Unsupported incremental candidate format")
    if nbytes != action.expected_bytes or nrecords <= 0:
        raise ValueError("Validated byte count/records mismatch")
    return CandidateCheck(action.path, action.category, nbytes, digest, nrecords)

def assess_results(plan: SyncPlan, checks: tuple[CandidateCheck, ...], *,
                   expected_record_count: int | None = None,
                   allow_csv: bool = False) -> int:
    paths = {a.path: None for a in plan.actions}
    candidates = (candidate_actions(plan, paths, allow_csv=True)
                  if allow_csv else candidate_actions(plan, paths))
    if len(checks) != len(candidates) or {c.path for c in checks} != {a.path for a in candidates}:
        raise ValueError("Validated candidate path set mismatch")
    if any(c.records <= 0 or c.bytes <= 0 or not SHA256.fullmatch(c.sha256) for c in checks):
        raise ValueError("Invalid candidate validation result")
    if sum(c.bytes for c in checks) != plan.candidate_bytes:
        raise ValueError("Total candidate bytes mismatch")
    result = sum(c.records for c in checks)
    if expected_record_count is not None and result != expected_record_count:
        raise ValueError(f"Candidate records changed: expected={expected_record_count}, got={result}")
    return plan.reused_records + result


def cache_directory(raw: str) -> Path:
    reports = (ROOT / "reports").resolve()
    selected = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if not selected.is_relative_to(reports) or selected == reports:
        raise ValueError("Cache must reside inside reports/ (not archive or project root)")
    if not selected.is_dir():
        raise FileNotFoundError("HF cache directory absent; run M4.5.3c validation first")
    return selected


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-snapshot", type=int, required=True)
    ap.add_argument("--target-revision", required=True)
    ap.add_argument("--expected-fingerprint", required=True)
    ap.add_argument("--expected-records", type=int, default=None)
    ap.add_argument("--repo-id", default=REPO_ID)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--plan", action="store_true", required=True)
    args = ap.parse_args()
    if args.baseline_snapshot <= 0 or not SHA40.fullmatch(args.target_revision):
        ap.error("Positive baseline ID and pinned 40-character target SHA required")
    if not SHA256.fullmatch(args.expected_fingerprint):
        ap.error("Expected plan fingerprint must be a 64-character SHA256")
    if args.expected_records is not None and args.expected_records <= 0:
        ap.error("Expected candidate record count must be positive")

    print("=== M4.5.4c.3g | PINNED INCREMENTAL CACHE GATE ===", flush=True)
    old_revision, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    revision, target, excluded = load_target(args.repo_id, args.target_revision)
    if revision != args.target_revision or revision == old_revision:
        raise RuntimeError("Wrong target revision")
    db_revision, existing = read_db_state(args.baseline_snapshot, args.repo_id, revision)
    if db_revision != old_revision:
        raise RuntimeError("Baseline DB revision drift")
    plan = assemble_plan(baseline, target, existing, read_existing_source_codes())
    if plan.fingerprint != args.expected_fingerprint:
        raise RuntimeError("Fingerprint changed: refusing to use cached files")
    candidates = candidate_actions(plan, target)
    cache = cache_directory(args.cache_dir)
    from huggingface_hub import hf_hub_download
    checks = []
    print("Repository:", args.repo_id)
    print("Baseline:", args.baseline_snapshot, old_revision)
    print("Pinned target:", revision)
    print("Fingerprint:", plan.fingerprint)
    print("REUSE:", plan.count("UNCHANGED"), "| Candidates:", len(candidates),
          "| Excluded non-Raw:", excluded)
    for action in candidates:
        # Offline cache lookup. Hugging Face must NOT fetch a body or metadata
        # over the network for this call.
        try:
            filename = hf_hub_download(repo_id=args.repo_id, filename=action.path,
                                       repo_type="dataset", revision=revision,
                                       cache_dir=str(cache), local_files_only=True)
        except Exception as exc:
            raise RuntimeError(f"No validated offline cache entry for {action.path}; "
                               "rerun M4.5.3c to fill cache") from exc
        record = check_cached_candidate(action, target[action.path], Path(filename))
        checks.append(record)
        print(f"[PASS] {record.category:7} | {record.records:>5} records | "
              f"{record.bytes:>9} bytes | {record.path}", flush=True)
    estimate = assess_results(plan, tuple(checks), expected_record_count=args.expected_records)
    print("--- SUMMARY ---")
    print("Candidate files validated from offline cache:", len(checks))
    print("Candidate bytes validated:", sum(x.bytes for x in checks))
    print("Candidate records validated:", sum(x.records for x in checks))
    print("Snapshot record references if ingested:", estimate)
    print("Existing raw records reinserted: 0")
    print("HF network downloads: NONE (local_files_only=True)")
    print("DB writes: NONE | Snapshot created: NO | Archive writes: NONE")
    print("M4.5.4c.3g PINNED CACHE GATE: PASS (NOT APPROVAL TO COMMIT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
