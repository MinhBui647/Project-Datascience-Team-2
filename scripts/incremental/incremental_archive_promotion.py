#!/usr/bin/env python3
"""M4.5.4c.3h: plan-only pinned JSON cache -> immutable object store gate.

The CLI intentionally offers ONLY --plan (no production archive mutations).
The reusable publish_immutable() helper has offline regression tests against
TemporaryDirectory; a separate confirmed production coordinator is needed for
any actual archive writes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.check_raw_updates import FileIdentity, REPO_ID, load_baseline, load_target
from scripts.tools.incremental_sync import assemble_plan
from scripts.tools.raw_sync_write_plan import read_db_state
from scripts.tools.raw_sync_preflight import read_existing_source_codes
from scripts.tools.incremental_cache_gate import candidate_actions, cache_directory
from scripts.tools.validate_raw_contents import validate_local_json, verify_bytes

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_BYTES = 16 * 1024 * 1024
MAX_CSV_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveCandidate:
    relative_path: str
    sha256: str
    byte_count: int
    record_count: int
    archive_relative_path: str
    action: str


def canonical_path(root: Path, sha256: str) -> tuple[Path, str]:
    if not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
        raise ValueError("Expected a lowercase 64-character SHA256")
    rel = f"raw-archive-v2/objects/{sha256[:2]}/{sha256}"
    root = Path(root).resolve(strict=True)
    # Detect symlinked ancestor directories before resolving the destination.
    parent = root
    for part in ("raw-archive-v2", "objects", sha256[:2]):
        parent = parent / part
        if parent.is_symlink():
            raise RuntimeError("Symlink inside immutable object-store path")
        if parent.exists() and not parent.is_dir():
            raise RuntimeError("Immutable object-store path is not a directory")
    destination = root / rel
    if destination.is_symlink():
        raise RuntimeError("Symlink cannot be an immutable archive object")
    if destination.exists() and not destination.is_file():
        raise RuntimeError("Archive destination is not a regular file")
    return destination, rel


def digest_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            count += len(data)
            hasher.update(data)
    return hasher.hexdigest(), count


def inspect_existing(root: Path, sha256: str, byte_count: int, *,
                     max_file_bytes: int = MAX_BYTES) -> tuple[Path, str, str]:
    if (type(max_file_bytes) is not int or max_file_bytes <= 0 or
            type(byte_count) is not int or not 0 < byte_count <= max_file_bytes):
        raise ValueError("Invalid JSON object size" if max_file_bytes == MAX_BYTES else "Invalid immutable object size/budget")
    destination, relative = canonical_path(root, sha256)
    if not destination.exists():
        return destination, relative, "READY_NEW"
    actual = digest_file(destination)
    if actual != (sha256, byte_count):
        raise RuntimeError("Existing immutable archive object corrupted; refuse overwrite")
    return destination, relative, "ALREADY_ARCHIVED"


def inspect_cached(root: Path, path: str, cache_file: Path,
                   identity: FileIdentity, *, allow_csv: bool = False) -> ArchiveCandidate:
    """Verify a pinned cache object; CSV is opt-in, never implicit."""
    if type(allow_csv) is not bool:
        raise ValueError("Invalid CSV archive opt-in")
    if not isinstance(path, str) or not path.startswith(("raw_data/", "2026_2027_new/")):
        raise ValueError("Candidate outside approved Raw scope")
    if any(part in ("", ".", "..") for part in path.split("/")) or "\\" in path:
        raise ValueError("Unsafe candidate path")
    if path.lower().endswith(".json"):
        result = validate_local_json(Path(cache_file), identity, max_file_bytes=MAX_BYTES)
        sha, size, count = result.sha256, result.byte_count, result.record_count
        limit = MAX_BYTES
    elif path.endswith(".csv") and allow_csv:
        from scripts.incremental.incremental_csv_adapter import inspect_csv_archive
        result = inspect_csv_archive(
            Path(cache_file), expected_size=identity.size,
            expected_lfs_sha256=identity.lfs_sha256,
            expected_git_blob_id=identity.git_blob_id,
            max_file_bytes=MAX_CSV_BYTES, max_records=250_000,
        )
        sha, size, count = result.content_sha256, result.file_size_bytes, result.record_count
        limit = MAX_CSV_BYTES
    else:
        raise ValueError("Only JSON candidates are supported by default; CSV requires opt-in")
    if allow_csv and path.endswith(".csv"):
        _, relative, action = inspect_existing(root, sha, size, max_file_bytes=limit)
    else:
        _, relative, action = inspect_existing(root, sha, size)
    return ArchiveCandidate(path, sha, size, count, relative, action)


def publish_immutable(root: Path, cache_file: Path, identity: FileIdentity,
                      candidate: ArchiveCandidate, *, allow_csv: bool = False) -> str:
    """Atomically create a file object without overwriting existing bytes.

    Only invoked in offline unit tests by this package; NOT exposed via CLI.
    Revalidates cache bytes, copies to a same-volume temporary file, hashes the
    staged copy, uses os.link as create-only publish, verifies final object.
    """
    refreshed = (inspect_cached(root, candidate.relative_path, cache_file, identity,
                                allow_csv=True) if allow_csv else
                 inspect_cached(root, candidate.relative_path, cache_file, identity))
    if refreshed != candidate and not (
        refreshed.sha256 == candidate.sha256
        and refreshed.record_count == candidate.record_count
        and refreshed.byte_count == candidate.byte_count
        and refreshed.archive_relative_path == candidate.archive_relative_path
        and refreshed.action == "ALREADY_ARCHIVED"
        and candidate.action == "READY_NEW"
    ):
        raise RuntimeError("Candidate changed since archive preflight")
    limit = MAX_CSV_BYTES if candidate.relative_path.endswith(".csv") and allow_csv else MAX_BYTES
    if allow_csv and candidate.relative_path.endswith(".csv"):
        dest, _, current = inspect_existing(root, candidate.sha256, candidate.byte_count,
                                            max_file_bytes=limit)
    else:
        dest, _, current = inspect_existing(root, candidate.sha256, candidate.byte_count)
    if current == "ALREADY_ARCHIVED":
        return current

    # Archive path only: never write into the HF cache, never rename a destination.
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Recheck path after creation, including any unexpected symlink.
    verify_dest, _ = canonical_path(root, candidate.sha256)
    if verify_dest != dest:
        raise RuntimeError("Archive destination changed")
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=dest.parent,
                                         prefix=".stage-", delete=False) as out:
            staged = Path(out.name)
            with Path(cache_file).open("rb") as source:
                shutil.copyfileobj(source, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if digest_file(staged) != (candidate.sha256, candidate.byte_count):
            raise RuntimeError("Staged object differs from verified cache")
        try:
            os.link(staged, dest)
            action = "ARCHIVED_NEW"
        except FileExistsError:
            action = "ALREADY_ARCHIVED"
        # Hardlinks provide atomic no-overwrite on NTFS and POSIX filesystems.
        if allow_csv and candidate.relative_path.endswith(".csv"):
            _, _, verified = inspect_existing(root, candidate.sha256, candidate.byte_count,
                                              max_file_bytes=limit)
        else:
            _, _, verified = inspect_existing(root, candidate.sha256, candidate.byte_count)
        if verified != "ALREADY_ARCHIVED":
            raise RuntimeError("Published archive object verification failed")
        return action
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def audit_candidates(plan, target, local_files: dict[str, Path], root: Path,
                     *, expected_records: int | None = None,
                     allow_csv: bool = False):
    if type(allow_csv) is not bool:
        raise ValueError("Invalid CSV audit opt-in")
    actions = (candidate_actions(plan, target, allow_csv=True) if allow_csv
               else candidate_actions(plan, target))
    if set(local_files) != {a.path for a in actions}:
        raise ValueError("Local candidate set differs from pinned plan")
    results = []
    for action in actions:
        if action.expected_bytes != target[action.path].size:
            raise ValueError("Candidate metadata drift")
        candidate = (inspect_cached(root, action.path, local_files[action.path], target[action.path],
                                    allow_csv=True) if allow_csv else
                     inspect_cached(root, action.path, local_files[action.path], target[action.path]))
        if candidate.byte_count != action.expected_bytes:
            raise ValueError("Unexpected file byte count")
        results.append(candidate)
    if len(results) != len(actions) or sum(x.byte_count for x in results) != plan.candidate_bytes:
        raise RuntimeError("Incomplete archive promotion plan")
    count = sum(x.record_count for x in results)
    if expected_records is not None and count != expected_records:
        raise RuntimeError("Candidate parser record count drift" if allow_csv else
                           "Candidate JSON record count drift")
    return tuple(results)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-records", type=int, required=True)
    p.add_argument("--repo-id", default=REPO_ID)
    p.add_argument("--cache-dir", default="reports/m4_5_content_cache")
    p.add_argument("--plan", required=True, action="store_true")
    p.add_argument("--allow-csv-archive-plan-only", action="store_true",
                   help="CSV verification opt-in; read-only, never publishes objects")
    args = p.parse_args()
    if args.baseline_snapshot <= 0 or not SHA40.fullmatch(args.target_revision):
        p.error("Snapshot must be positive and target must be a full pinned SHA40")
    if not SHA256.fullmatch(args.expected_fingerprint) or args.expected_records <= 0:
        p.error("Expected SHA256 fingerprint and positive record count required")

    print("=== M4.5.4c.3h | IMMUTABLE ARCHIVE PROMOTION PLAN ===", flush=True)
    base_rev, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    revision, target, excluded = load_target(args.repo_id, args.target_revision)
    db_base, existing = read_db_state(args.baseline_snapshot, args.repo_id, revision)
    if base_rev != db_base or revision != args.target_revision or revision == base_rev:
        raise RuntimeError("Target and baseline revisions differ from pinned plan")
    if args.allow_csv_archive_plan_only:
        plan = assemble_plan(baseline, target, existing, read_existing_source_codes(),
                             allow_csv_planning=True)
    else:
        plan = assemble_plan(baseline, target, existing, read_existing_source_codes())
    if plan.fingerprint != args.expected_fingerprint or plan.count("REMOVED") or plan.count("REVIEW"):
        raise RuntimeError("Target plan fingerprint changed or has blocked categories")
    actions = (candidate_actions(plan, target, allow_csv=True)
               if args.allow_csv_archive_plan_only else candidate_actions(plan, target))
    cache = cache_directory(args.cache_dir)
    from huggingface_hub import hf_hub_download
    files = {}
    for action in actions:
        file = Path(hf_hub_download(repo_id=args.repo_id, filename=action.path,
                    repo_type="dataset", revision=revision,
                    cache_dir=str(cache), local_files_only=True))
        file = file.resolve(strict=True)
        if not file.is_file() or not file.is_relative_to(cache):
            raise RuntimeError("Cached object escaped the pinned HF cache directory")
        files[action.path] = file
    results = (audit_candidates(plan, target, files, ROOT,
                               expected_records=args.expected_records, allow_csv=True)
               if args.allow_csv_archive_plan_only else
               audit_candidates(plan, target, files, ROOT,
                                expected_records=args.expected_records))
    print("Database: epl_raw_storage_v2 (read only)")
    print("Baseline snapshot:", args.baseline_snapshot, "| completed")
    print("Target revision:", revision)
    print("Plan fingerprint:", plan.fingerprint)
    print("REUSE:", plan.count("UNCHANGED"), "| candidates:", len(results), "| excluded non-Raw:", excluded)
    for result in results:
        print(f"[{result.action:16}] {result.byte_count:>9} bytes | "
              f"{result.record_count:>4} records | {result.relative_path}")
    print("--- SUMMARY ---")
    print("Validated cache files:", len(results))
    print("Validated bytes:", sum(r.byte_count for r in results))
    print("Validated records:", sum(r.record_count for r in results))
    print("Archive objects ready to create:", sum(r.action == "READY_NEW" for r in results))
    print("Already archived objects:", sum(r.action == "ALREADY_ARCHIVED" for r in results))
    print("Archive mutations: NONE | DB writes: NONE | Snapshot created: NO")
    print("M4.5.4c.3h IMMUTABLE ARCHIVE PLAN: PASS (NOT APPROVAL TO PROMOTE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
