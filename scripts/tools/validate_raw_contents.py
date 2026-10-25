#!/usr/bin/env python3
"""M4.5.3c: validate pinned HF Raw JSON file bodies without writing PostgreSQL.

Downloads ONLY NEW / CHANGED file bodies to an isolated, ignored local cache.
Verifies byte count and a content hash (LFS SHA256 or non-LFS Git blob SHA1),
then exhausts the existing Raw Storage parser and checks JSONB-friendly records.

This is a validation tool, NOT an ingestion or snapshot creation tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Support both: python -m scripts.tools.validate_raw_contents
# and python scripts/tools/validate_raw_contents.py (run from project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.raw_parser import ParsedRecord, iter_parse_events, MAX_JSON_BYTES
from scripts.tools.check_raw_updates import (
    REPO_ID, FileIdentity, compare_snapshots, load_baseline, load_target,
)
from scripts.tools.raw_sync_preflight import (
    inspect_entry, read_existing_source_codes,
)

DEFAULT_CACHE = Path("reports/m4_5_content_cache")
FULL_SHA = re.compile(r"[a-f0-9]{40}\Z")
MAX_RECORDS = 250_000


@dataclass(frozen=True)
class ContentResult:
    byte_count: int
    sha256: str
    git_blob_sha1: str
    identity_type: str
    record_count: int
    first_locator: str | None
    last_locator: str | None


def check_jsonb_string(value: str) -> None:
    """Reject JSON string values PostgreSQL JSONB cannot represent."""
    if "\x00" in value:
        raise ValueError("NUL character is incompatible with PostgreSQL JSONB")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Unpaired UTF-16 surrogate is incompatible with PostgreSQL JSONB")


def check_jsonb_value(value: Any) -> None:
    """Check keys and strings recursively, plus non-finite numeric values."""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise ValueError("Non-string JSON object key")
                check_jsonb_string(key)
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            check_jsonb_string(current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError("Non-finite JSON number")


def verify_bytes(path: Path, identity: FileIdentity, *, max_file_bytes: int) -> tuple[int, str, str, str]:
    """Verify actual file bytes match size AND an authoritative HF content ID."""
    if not path.is_file():
        raise ValueError("Downloaded file is missing")
    if type(identity.size) is not int or identity.size <= 0:
        raise ValueError("Missing or invalid manifest size")
    if identity.size > max_file_bytes or identity.size > MAX_JSON_BYTES:
        raise ValueError("JSON exceeds configured or parser byte limit")
    actual = path.stat().st_size
    if actual != identity.size:
        raise ValueError(f"Byte size mismatch: expected {identity.size}, got {actual}")

    sha256 = hashlib.sha256()
    git_blob = hashlib.sha1()
    git_blob.update(f"blob {actual}\0".encode("ascii"))
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha256.update(chunk)
            git_blob.update(chunk)
    digest = sha256.hexdigest()
    blob = git_blob.hexdigest()

    if identity.lfs_sha256:
        if not re.fullmatch(r"[a-f0-9]{64}", identity.lfs_sha256):
            raise ValueError("Invalid LFS hash in metadata")
        if digest != identity.lfs_sha256:
            raise ValueError("LFS SHA256 mismatch")
        hash_method = "LFS-SHA256"
    elif identity.git_blob_id:
        if not FULL_SHA.fullmatch(identity.git_blob_id):
            raise ValueError("Invalid Git blob ID in metadata")
        if blob != identity.git_blob_id:
            raise ValueError("Git blob SHA1 mismatch")
        hash_method = "GIT-BLOB-SHA1"
    else:
        raise ValueError("No comparable HF content identity")
    return actual, digest, blob, hash_method


def validate_local_json(
    path: Path, identity: FileIdentity, *, max_file_bytes: int = 16 * 1024 * 1024,
) -> ContentResult:
    """Pure local validation suitable for offline regression tests."""
    size, sha, blob, method = verify_bytes(path, identity, max_file_bytes=max_file_bytes)
    seen: set[str] = set()
    count = 0
    first = None
    last = None
    # Existing parser also rejects duplicate JSON keys, NaN/Infinity literals,
    # malformed UTF-8, BOM misuse, and files over MAX_JSON_BYTES.
    for event in iter_parse_events(path, "json"):
        if not isinstance(event, ParsedRecord):
            raise ValueError("Non-record parser event from JSON file")
        if event.record_kind not in {"json_object", "json_item", "json_scalar"}:
            raise ValueError(f"Unexpected record_kind: {event.record_kind}")
        if not isinstance(event.raw_data, dict):
            raise ValueError("JSONB raw_data is not an object")
        if event.record_locator in seen:
            raise ValueError(f"Duplicate record locator: {event.record_locator}")
        seen.add(event.record_locator)
        check_jsonb_value(event.raw_data)
        # Ensure resulting records can be serialized as strict JSON.
        json.dumps(event.raw_data, ensure_ascii=True, allow_nan=False)
        count += 1
        if count > MAX_RECORDS:
            raise ValueError(f"Too many raw records in one JSON file: {count}")
        if first is None:
            first = event.record_locator
        last = event.record_locator
    if count == 0:
        raise ValueError("JSON parsed, but yields zero Raw records; review required")
    return ContentResult(size, sha, blob, method, count, first, last)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-snapshot", type=int, required=True)
    ap.add_argument("--target-revision", required=True)
    ap.add_argument("--repo-id", default=REPO_ID)
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--max-file-mib", type=int, default=16)
    ap.add_argument("--max-total-mib", type=int, default=64)
    ap.add_argument("--validate", action="store_true", help="Download only NEW/CHANGED to cache, then validate")
    ap.add_argument("--confirm-cache-download", action="store_true", help="Acknowledge local cache writes")
    args = ap.parse_args()
    if not args.validate or not args.confirm_cache_download:
        ap.error("Both --validate and --confirm-cache-download are required")
    if not FULL_SHA.fullmatch(args.target_revision):
        ap.error("--target-revision must be pinned to a full 40-character commit SHA")
    if args.max_file_mib <= 0 or args.max_total_mib <= 0:
        ap.error("Byte budgets must be positive")

    print("=== M4.5.3c | PINNED RAW JSON CONTENT VALIDATION ===", flush=True)
    baseline_rev, old = load_baseline(args.baseline_snapshot, args.repo_id)
    target_rev, current, excluded = load_target(args.repo_id, args.target_revision)
    if target_rev != args.target_revision:
        raise RuntimeError("Target SHA did not match the pinned revision")
    groups = compare_snapshots(old, current)
    if groups["REVIEW"]:
        raise RuntimeError(f"Metadata REVIEW entries block content validation: {len(groups['REVIEW'])}")
    candidates = [(category, path, current[path]) for category in ("NEW", "CHANGED")
                  for path in groups[category]]
    if not candidates:
        print("No new/changed Raw files. No downloads needed.")
        print("M4.5.3c CONTENT VALIDATION: PASS (NO CANDIDATES)")
        return 0

    # Metadata preflight must pass for the entire batch before any download.
    codes = read_existing_source_codes()
    blockers = []
    total_bytes = 0
    for category, path, identity in candidates:
        entry = inspect_entry(path, category, identity)
        blockers.extend(f"{path}: {x}" for x in entry.errors)
        if entry.file_format != "json":
            blockers.append(f"{path}: this checkpoint supports only JSON (not {entry.file_format})")
        if entry.source_code not in codes:
            blockers.append(f"{path}: source not registered: {entry.source_code}")
        if type(identity.size) is int:
            total_bytes += identity.size
            if identity.size > args.max_file_mib * 1024 * 1024:
                blockers.append(f"{path}: file exceeds download byte budget")
    if total_bytes > args.max_total_mib * 1024 * 1024:
        blockers.append("Candidate batch exceeds total download byte budget")
    if blockers:
        for blocker in blockers:
            print("BLOCKER:", blocker)
        print("M4.5.3c CONTENT VALIDATION: REVIEW REQUIRED")
        return 2

    from huggingface_hub import hf_hub_download
    cache = Path(args.cache_dir)
    # Prevent selecting raw-archive, env, or the project root as the cache.
    cache_abs = (PROJECT_ROOT / cache).resolve() if not cache.is_absolute() else cache.resolve()
    expected_cache = (PROJECT_ROOT / "reports").resolve()
    if not cache_abs.is_relative_to(expected_cache) or cache_abs == expected_cache:
        raise RuntimeError("Cache directory must be inside reports/ (not reports/ itself)")
    cache_abs.mkdir(parents=True, exist_ok=True)

    print("Repository:", args.repo_id)
    print("Baseline snapshot:", args.baseline_snapshot)
    print("Baseline revision:", baseline_rev)
    print("Target pinned revision:", target_rev)
    print("Excluded non-Raw files:", excluded)
    print("NEW:", len(groups["NEW"]), "| CHANGED:", len(groups["CHANGED"]))
    print("Total candidate bytes:", total_bytes)
    print("Cache:", cache_abs)
    print("Mode: CACHE DOWNLOAD + VALIDATE (no DB writes, no archive writes)")
    print("\n--- FILE CONTENT VALIDATION ---", flush=True)
    validated = 0
    records = 0
    errors = []
    for category, path, identity in candidates:
        try:
            filename = hf_hub_download(
                repo_id=args.repo_id,
                filename=path,
                repo_type="dataset",
                revision=target_rev,
                cache_dir=str(cache_abs),
            )
            result = validate_local_json(
                Path(filename), identity,
                max_file_bytes=args.max_file_mib * 1024 * 1024,
            )
            validated += 1
            records += result.record_count
            print(f"[PASS] {category:7} | {result.byte_count:>9} bytes | "
                  f"{result.record_count:>6} records | {result.identity_type:14} | {path}", flush=True)
        except Exception as exc:
            errors.append((path, type(exc).__name__, str(exc)))
            print(f"[FAIL] {category:7} | {path} | {type(exc).__name__}: {exc}", flush=True)

    print("\n--- SUMMARY ---")
    print("Candidate files:", len(candidates))
    print("Validated files:", validated)
    print("Validated parser records:", records)
    print("Failures:", len(errors))
    print("Snapshot created: NO")
    print("Database writes: NONE")
    print("Raw archive modified: NO")
    print("Local changes: HF cache files ONLY (under reports/)")
    if errors:
        print("M4.5.3c CONTENT VALIDATION: REVIEW REQUIRED")
        return 2
    print("M4.5.3c CONTENT VALIDATION: PASS (not approval to ingest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
