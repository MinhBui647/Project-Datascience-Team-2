#!/usr/bin/env python3
r"""M4.6.4c.6: explicit, revision-pinned NEW/CHANGED JSON candidate cache downloader.

From repository root (Windows):
  .\.venv_v2\Scripts\python.exe scripts\tools\download_incremental_candidates.py \
      --baseline-snapshot ID --target-revision SHA40 \
      --expected-fingerprint SHA256 --expected-files N --expected-bytes BYTES --plan

  .\.venv_v2\Scripts\python.exe scripts\tools\download_incremental_candidates.py \
      --baseline-snapshot ID --target-revision SHA40 \
      --expected-fingerprint SHA256 --expected-files N --expected-bytes BYTES \
      --download --confirm-cache-only

Both modes rebuild and verify the pinned SyncPlan using PostgreSQL read-only state
and HF metadata. --plan never requests file bodies or creates cache directories.
--download may write ONLY Hugging Face cache content within reports/. It downloads
exactly the NEW/CHANGED JSON candidate paths, validates bytes, Git/LFS identity,
JSON parsing and nonempty record counts, then prints the downstream expectations.

NEVER creates/registers snapshots, writes PostgreSQL, or publishes archive objects.
No retry of removed/review paths. A partially filled HF cache can be reused on retry.
This command is NOT approval for archive promotion, ingestion or DB COMMIT.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
DEFAULT_CACHE = "reports/m4_6_content_cache"
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")


@dataclass(frozen=True)
class DownloadContext:
    repo_id: str
    baseline_snapshot: int
    baseline_revision: str
    target_revision: str
    plan: Any
    target: dict
    actions: tuple
    excluded: int
    allow_csv: bool = False


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-files", type=int, required=True)
    p.add_argument("--expected-bytes", type=int, required=True)
    p.add_argument("--expected-records", type=int, default=None,
                   help="Optional candidate record count from a prior validated download")
    p.add_argument("--repo-id", default=DEFAULT_REPO)
    p.add_argument("--cache-dir", default=DEFAULT_CACHE)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--download", action="store_true")
    p.add_argument("--confirm-cache-only", action="store_true")
    p.add_argument("--allow-csv-cache-only", action="store_true",
                   help="Explicit CSV opt-in for cache verification only; not DB write authorization")
    args = p.parse_args(argv)
    if args.download != args.confirm_cache_only:
        p.error("--download requires --confirm-cache-only; --plan forbids it")
    if type(args.baseline_snapshot) is not int or args.baseline_snapshot <= 0:
        p.error("--baseline-snapshot must be positive")
    if not SHA40.fullmatch(args.target_revision):
        p.error("--target-revision must be one lowercase full SHA40")
    if not SHA256.fullmatch(args.expected_fingerprint):
        p.error("--expected-fingerprint must be lowercase SHA256")
    if args.expected_files <= 0 or args.expected_bytes <= 0:
        p.error("--expected-files and --expected-bytes must be positive")
    if args.expected_records is not None and args.expected_records <= 0:
        p.error("--expected-records, if supplied, must be positive")
    if not args.repo_id or not args.repo_id.strip():
        p.error("--repo-id cannot be empty")
    return args


def candidate_path_ok(path: object, *, allow_csv: bool = False) -> bool:
    if type(allow_csv) is not bool:
        return False
    return (isinstance(path, str) and 0 < len(path) <= 1024
            and path.startswith(RAW_PREFIXES)
            and (path.endswith(".json") or (allow_csv and path.endswith(".csv")))
            and "\\" not in path and not path.startswith("/")
            and all(part not in ("", ".", "..") for part in path.split("/"))
            and all(ord(char) >= 32 for char in path))


def validated_cache_path(cache_arg: str, *, root: Path = ROOT, create: bool = False) -> Path:
    """Require cache strictly inside reports/, reject symlinked path components."""
    root = Path(root).resolve(strict=True)
    reports = root / "reports"
    p = Path(cache_arg)
    candidate = p if p.is_absolute() else root / p
    if ".." in candidate.parts:
        raise ValueError("Parent traversal is forbidden in HF cache path")
    if reports.is_symlink() or reports.resolve(strict=False) != reports:
        raise ValueError("reports/ must not be symlinked")
    # Inspect original components BEFORE resolving: even an inward symlink is
    # forbidden, and resolving first would hide the symlinked component.
    try:
        raw_parts = candidate.relative_to(reports).parts
    except ValueError as exc:
        raise ValueError("HF cache must be inside reports/") from exc
    if not raw_parts:
        raise ValueError("Cache directory cannot equal reports/")
    current = reports
    for part in raw_parts:
        if part in ("", ".", ".."):
            raise ValueError("Unsafe HF cache path component")
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink not permitted inside HF cache path")
        if current.exists() and not current.is_dir():
            raise ValueError("Non-directory component inside cache path")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(reports) or resolved == reports:
        raise ValueError("HF cache must remain inside reports/")
    if create:
        reports.mkdir(parents=True, exist_ok=True)
        resolved.mkdir(parents=True, exist_ok=True)
        if resolved.resolve(strict=True) != resolved:
            raise ValueError("HF cache directory changed after creation")
    return resolved


def verify_plan_contract(plan: Any, actions: tuple, target: dict, *,
                         fingerprint: str, files: int, total_bytes: int,
                         allow_csv: bool = False) -> None:
    if type(allow_csv) is not bool:
        raise ValueError("Invalid CSV cache permission")
    if not SHA256.fullmatch(fingerprint) or plan.fingerprint != fingerprint:
        raise RuntimeError("Pinned SyncPlan fingerprint drift")
    if plan.count("REMOVED") or plan.count("REVIEW"):
        raise RuntimeError("Removed/review files require separate handling")
    if plan.count("UNCHANGED") + len(actions) != plan.target_raw_count:
        raise RuntimeError("Incomplete Raw-scope coverage")
    if len(actions) != files or files <= 0:
        raise RuntimeError("Candidate count differs from pinned expectation")
    if len({a.path for a in actions}) != len(actions) or len(target) != plan.target_raw_count:
        raise RuntimeError("Duplicate or missing target candidate paths")
    if any(not candidate_path_ok(a.path, allow_csv=allow_csv)
           or a.category not in ("NEW", "CHANGED")
           or a.kind != "UPSERT_VERSION_AFTER_REHASH"
           or a.file_format not in ({"json", "csv"} if allow_csv else {"json"})
           or not a.path.endswith("." + a.file_format)
           or a.path not in target or a.expected_bytes != target[a.path].size
           or type(a.expected_bytes) is not int or a.expected_bytes <= 0
           for a in actions):
        raise RuntimeError("Unsafe/unsupported candidate action or metadata drift")
    if (sum(a.expected_bytes for a in actions) != total_bytes
            or plan.candidate_bytes != total_bytes):
        raise RuntimeError("Candidate bytes differ from pinned expectation")


def load_context(args: argparse.Namespace) -> DownloadContext:
    """Only read-only DB / HF metadata; no file-body download."""
    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.incremental_sync import assemble_plan
    from scripts.tools.raw_sync_write_plan import read_db_state
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.incremental_cache_gate import candidate_actions

    base_revision, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    revision, target, excluded = load_target(args.repo_id, args.target_revision)
    if revision != args.target_revision or revision == base_revision:
        raise RuntimeError("HF target SHA differs from pinned revision or baseline")
    db_revision, existing = read_db_state(args.baseline_snapshot, args.repo_id, revision)
    if db_revision != base_revision:
        raise RuntimeError("Baseline revision drift across read-only DB checks")
    csv_opt_in = bool(getattr(args, "allow_csv_cache_only", False))
    sources = read_existing_source_codes()
    if csv_opt_in:
        plan = assemble_plan(
            baseline, target, existing, sources, allow_csv_planning=True
        )
        actions = tuple(candidate_actions(plan, target, allow_csv=True))
        verify_plan_contract(
            plan, actions, target, fingerprint=args.expected_fingerprint,
            files=args.expected_files, total_bytes=args.expected_bytes,
            allow_csv=True,
        )
    else:
        plan = assemble_plan(baseline, target, existing, sources)
        actions = tuple(candidate_actions(plan, target))
        verify_plan_contract(
            plan, actions, target, fingerprint=args.expected_fingerprint,
            files=args.expected_files, total_bytes=args.expected_bytes,
        )
    return DownloadContext(args.repo_id, args.baseline_snapshot, base_revision,
                           revision, plan, target, actions, excluded, csv_opt_in)


def download_candidates(context: DownloadContext, cache: Path, *,
                        downloader: Callable | None = None,
                        validator: Callable | None = None,
                        assessor: Callable | None = None,
                        expected_records: int | None = None) -> tuple[tuple, int]:
    """Cache-only writes via hf_hub_download; validate every file after it returns."""
    if downloader is None:
        from huggingface_hub import hf_hub_download
        downloader = hf_hub_download
    if validator is None or assessor is None:
        from scripts.tools.incremental_cache_gate import check_cached_candidate, assess_results
        if validator is None:
            if getattr(context, "allow_csv", False):
                from functools import partial
                validator = partial(check_cached_candidate, allow_csv=True)
            else:
                validator = check_cached_candidate
        if assessor is None:
            if getattr(context, "allow_csv", False):
                from functools import partial
                assessor = partial(assess_results, allow_csv=True)
            else:
                assessor = assess_results
    cache = Path(cache).resolve(strict=True)
    if not cache.is_dir():
        raise RuntimeError("Validated HF cache directory does not exist")
    checked = []
    for idx, action in enumerate(context.actions, 1):
        downloaded = Path(downloader(
            repo_id=context.repo_id, repo_type="dataset", filename=action.path,
            revision=context.target_revision, cache_dir=str(cache),
            token=os.getenv("HF_TOKEN") or None,
            local_files_only=False,
        ))
        resolved = downloaded.resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(cache):
            raise RuntimeError("Downloaded candidate resolves outside trusted HF cache")
        info = validator(action, context.target[action.path], resolved)
        if (info.path != action.path or info.category != action.category
                or info.bytes != action.expected_bytes or info.bytes <= 0
                or info.records <= 0 or not SHA256.fullmatch(info.sha256)):
            raise RuntimeError("Candidate validation produced inconsistent content metadata")
        checked.append(info)
        print(f"[VERIFIED {idx}/{len(context.actions)}] {info.category:7} | "
              f"{info.bytes:>9} bytes | {info.records:>6} records | {info.path}", flush=True)
    # assess_results() independently checks full candidate path set, byte count,
    # positive records and (when given) exact expected record count.
    record_refs = assessor(context.plan, tuple(checked),
                           expected_record_count=expected_records)
    if type(record_refs) is not int or record_refs != (
        context.plan.reused_records + sum(row.records for row in checked)
    ):
        raise RuntimeError("Inconsistent projected record reference count")
    return tuple(checked), record_refs


def main(argv=None) -> int:
    args = parse_args(argv)
    context = load_context(args)
    cache = validated_cache_path(args.cache_dir, create=False)
    print("=== M4.6.4c.6 | PINNED CANDIDATE CACHE PREPARATION ===")
    print("Repo:", context.repo_id)
    print("Baseline:", context.baseline_snapshot, context.baseline_revision)
    print("Target:", context.target_revision)
    print("SyncPlan fingerprint:", context.plan.fingerprint)
    print("Raw: REUSE", context.plan.count("UNCHANGED"),
          "| NEW", context.plan.count("NEW"),
          "| CHANGED", context.plan.count("CHANGED"),
          "| non-Raw excluded", context.excluded)
    print("Candidate expected: files", len(context.actions),
          "| bytes", context.plan.candidate_bytes)
    print("HF cache path:", cache)
    if args.plan:
        for action in context.actions:
            print(f"[TO CACHE {action.category:7}] {action.expected_bytes:>9} bytes | {action.path}")
        print("File-body downloads: NONE | Cache writes: NONE | DB writes: NONE | Archive writes: NONE")
        print("M4.6.4c.6 CANDIDATE DOWNLOAD PLAN: PASS (NOT APPROVAL TO DOWNLOAD)")
        return 0

    # All cache mutations are isolated to the explicitly selected reports/ folder.
    cache = validated_cache_path(args.cache_dir, create=True)
    verified, projected = download_candidates(context, cache, expected_records=args.expected_records)
    records = sum(item.records for item in verified)
    print("--- VERIFIED CACHE SUMMARY ---")
    print("Verified files:", len(verified))
    print("Verified bytes:", sum(item.bytes for item in verified))
    print("Verified candidate records:", records)
    print("Projected target raw record references:", projected)
    print("Downstream --expected-records:", records)
    print("Archive promotion: NOT STARTED | DB writes: NONE | Snapshot created: NO")
    print("M4.6.4c.6 CANDIDATE CACHE DOWNLOAD: PASS (NOT APPROVAL TO PROMOTE/COMMIT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
