#!/usr/bin/env python3
r"""M4.6.4c.7 | Generic, guarded archive-only promotion for NEW/CHANGED JSON.

Use the SAME pinned baseline, SHA40 revision, SyncPlan fingerprint, candidate counts
and reports/ cache as download_incremental_candidates.py. Examples:

  python scripts/tools/promote_incremental_archive.py --baseline-snapshot 365 \
    --target-revision SHA40 --expected-fingerprint SHA256 \
    --expected-files N --expected-bytes BYTES --expected-records RECORDS --plan

  python scripts/tools/promote_incremental_archive.py [same arguments] \
    --promote --confirm-archive-only

--plan: metadata/DB read-only + offline cached-file validation and immutable archive
inspection. No HF file-body download, no archive creation, no PostgreSQL writes.
--promote: after the same checks, publish ONLY content-addressed Raw archive objects
using atomic create-only hardlinks. Per-object promotion is resumable; failures can
leave already-published immutable objects (never partial DB state). Reaudit afterward.

Does NOT create/register snapshots, create manifests, or ingest raw records.
Not approval for PostgreSQL COMMIT. A no-Raw-change revision should be skipped by
M4.6.2; empty candidate sets and 0-record JSON candidates are not supported in V1.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path
import re
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.check_raw_updates import REPO_ID
from scripts.tools.incremental_archive_promotion import (
    ArchiveCandidate, audit_candidates, inspect_existing, publish_immutable,
)
from scripts.tools.download_incremental_candidates import (
    DownloadContext, load_context, validated_cache_path,
)

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_CACHE = "reports/m4_6_content_cache"


@dataclass(frozen=True)
class PromotionBundle:
    context: DownloadContext
    local_files: dict[str, Path]
    candidates: tuple[ArchiveCandidate, ...]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", required=True, type=int)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-files", required=True, type=int)
    p.add_argument("--expected-bytes", required=True, type=int)
    p.add_argument("--expected-records", required=True, type=int)
    p.add_argument("--repo-id", default=REPO_ID)
    p.add_argument("--cache-dir", default=DEFAULT_CACHE)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--promote", action="store_true")
    p.add_argument("--confirm-archive-only", action="store_true")
    args = p.parse_args(argv)
    if args.promote != args.confirm_archive_only:
        p.error("--promote requires --confirm-archive-only; --plan forbids it")
    if args.baseline_snapshot <= 0 or not SHA40.fullmatch(args.target_revision):
        p.error("Positive baseline snapshot and pinned lowercase SHA40 required")
    if not SHA256.fullmatch(args.expected_fingerprint):
        p.error("Expected lowercase SHA256 fingerprint required")
    if (args.expected_files <= 0 or args.expected_bytes <= 0
            or args.expected_records <= 0):
        p.error("Expected candidate files, bytes and records must all be positive")
    if not isinstance(args.repo_id, str) or not args.repo_id.strip():
        p.error("Repository ID must not be blank")
    return args


def check_promotion_contract(plan, candidates: tuple[ArchiveCandidate, ...], *,
                             expected_fingerprint: str, expected_files: int,
                             expected_bytes: int, expected_records: int,
                             actions: tuple | None = None) -> None:
    """Pure safety gate. Kept compatible with the legacy four-expectation API."""
    if not SHA256.fullmatch(expected_fingerprint) or plan.fingerprint != expected_fingerprint:
        raise ValueError("Pinned SyncPlan fingerprint mismatch")
    if plan.count("REMOVED") or plan.count("REVIEW"):
        raise ValueError("Removed/review paths require a separate workflow")
    if plan.count("UNCHANGED") + len(candidates) != plan.target_raw_count:
        raise ValueError("Incomplete target Raw-scope coverage")
    if len(candidates) != expected_files or expected_files <= 0:
        raise ValueError("Candidate count mismatch")
    if len({x.relative_path for x in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate path")
    if sum(x.byte_count for x in candidates) != expected_bytes or expected_bytes <= 0:
        raise ValueError("Candidate byte count mismatch")
    if sum(x.record_count for x in candidates) != expected_records or expected_records <= 0:
        raise ValueError("Candidate record count mismatch")
    if (any(type(x.byte_count) is not int or x.byte_count <= 0
            or type(x.record_count) is not int or x.record_count <= 0
            or not SHA256.fullmatch(x.sha256)
            or x.archive_relative_path != f"raw-archive-v2/objects/{x.sha256[:2]}/{x.sha256}"
            or x.action not in ("READY_NEW", "ALREADY_ARCHIVED")
            for x in candidates)):
        raise ValueError("Invalid immutable archive candidate metadata")
    if actions is not None and ({a.path for a in actions} != {c.relative_path for c in candidates}
                                or len(actions) != len(candidates)
                                or any(a.category not in ("NEW", "CHANGED") for a in actions)):
        raise ValueError("Archive candidate paths differ from pinned NEW/CHANGED actions")


def resolve_cached_candidates(context: DownloadContext, cache: Path, *,
                              fetcher: Callable | None = None) -> dict[str, Path]:
    """Offline HF cache lookups ONLY: no network fetch or body download."""
    if fetcher is None:
        from huggingface_hub import hf_hub_download
        fetcher = hf_hub_download
    cache = Path(cache).resolve(strict=True)
    if not cache.is_dir():
        raise RuntimeError("Validated HF cache directory is missing")
    found: dict[str, Path] = {}
    for action in context.actions:
        local = Path(fetcher(
            repo_id=context.repo_id, filename=action.path, repo_type="dataset",
            revision=context.target_revision, cache_dir=str(cache),
            local_files_only=True,
        ))
        resolved = local.resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(cache):
            raise RuntimeError("HF cached file resolves outside trusted reports cache")
        if action.path in found:
            raise RuntimeError("Duplicate candidate cache path")
        found[action.path] = resolved
    return found


def prepare_bundle(context: DownloadContext, cache: Path, *, expected_fingerprint: str,
                   expected_files: int, expected_bytes: int, expected_records: int,
                   root: Path = ROOT, fetcher: Callable | None = None,
                   auditor: Callable = audit_candidates) -> PromotionBundle:
    """All-local file-body verification; DB access happened in load_context()."""
    local_files = resolve_cached_candidates(context, cache, fetcher=fetcher)
    candidates = tuple(auditor(context.plan, context.target, local_files,
                               root, expected_records=expected_records))
    check_promotion_contract(context.plan, candidates,
                             expected_fingerprint=expected_fingerprint,
                             expected_files=expected_files,
                             expected_bytes=expected_bytes,
                             expected_records=expected_records,
                             actions=context.actions)
    return PromotionBundle(context, local_files, candidates)


def promote_batch(root: Path, candidates: tuple[ArchiveCandidate, ...],
                  local_files: dict[str, Path], target: dict,
                  publisher: Callable = publish_immutable,
                  inspector: Callable = inspect_existing) -> tuple[int, int]:
    """Create-only per object; partial publication can be retried safely."""
    if len({x.relative_path for x in candidates}) != len(candidates):
        raise ValueError("Duplicate promotion paths")
    if {x.relative_path for x in candidates} != set(local_files):
        raise ValueError("Candidate/cache path sets mismatch")
    if set(local_files) - set(target):
        raise ValueError("Candidate identity missing from pinned target")
    created = reused = 0
    for idx, item in enumerate(candidates, 1):
        result = publisher(root, local_files[item.relative_path],
                           target[item.relative_path], item)
        if result == "ARCHIVED_NEW":
            created += 1
        elif result == "ALREADY_ARCHIVED":
            reused += 1
        else:
            raise RuntimeError(f"Unexpected archive publisher outcome: {result!r}")
        _, _, state = inspector(root, item.sha256, item.byte_count)
        if state != "ALREADY_ARCHIVED":
            raise RuntimeError("Published immutable object could not be verified")
        print(f"[VERIFIED {idx}/{len(candidates)}] {result:16} | "
              f"{item.byte_count:>9} bytes | {item.relative_path}", flush=True)
    return created, reused


def verify_post_promotion(bundle: PromotionBundle, *, root: Path = ROOT,
                          expected_records: int, auditor: Callable = audit_candidates) -> None:
    """Verify all files now have immutable objects, not merely successful return codes."""
    reread = tuple(auditor(bundle.context.plan, bundle.context.target,
                           bundle.local_files, root, expected_records=expected_records))
    expected = {c.relative_path: replace(c, action="ALREADY_ARCHIVED") for c in bundle.candidates}
    if len(reread) != len(expected) or any(expected.get(c.relative_path) != c for c in reread):
        raise RuntimeError("Post-promotion archive or candidate metadata drift")


def main(argv=None) -> int:
    args = parse_args(argv)
    # Recreate the SAME pinned SyncPlan and verify read-only DB/HF metadata.
    context = load_context(args)
    cache = validated_cache_path(args.cache_dir, create=False)
    bundle = prepare_bundle(context, cache,
                            expected_fingerprint=args.expected_fingerprint,
                            expected_files=args.expected_files,
                            expected_bytes=args.expected_bytes,
                            expected_records=args.expected_records)
    print("=== M4.6.4c.7 | GENERIC IMMUTABLE ARCHIVE PROMOTION ===")
    print("Repository:", context.repo_id)
    print("Baseline snapshot:", context.baseline_snapshot, "|", context.baseline_revision)
    print("Target revision:", context.target_revision)
    print("SyncPlan fingerprint:", context.plan.fingerprint)
    print("REUSE (untouched):", context.plan.count("UNCHANGED"))
    print("Candidates:", len(bundle.candidates), "| Non-Raw excluded:", context.excluded)
    print("READY_NEW:", sum(c.action == "READY_NEW" for c in bundle.candidates))
    print("ALREADY_ARCHIVED:", sum(c.action == "ALREADY_ARCHIVED" for c in bundle.candidates))
    print("Verified candidate bytes:", sum(c.byte_count for c in bundle.candidates))
    print("Verified candidate records:", sum(c.record_count for c in bundle.candidates))
    if args.plan:
        for item in bundle.candidates:
            print(f"[{item.action:16}] {item.byte_count:>9} bytes | {item.relative_path}")
        print("HF file-body downloads: NONE | Archive writes: NONE | DB writes: NONE")
        print("M4.6.4c.7 ARCHIVE PROMOTION PLAN: PASS (NOT APPROVAL TO PROMOTE)")
        return 0

    # Only --promote --confirm-archive-only can reach archive publication.
    created, reused = promote_batch(ROOT, bundle.candidates,
                                    bundle.local_files, context.target)
    verify_post_promotion(bundle, expected_records=args.expected_records)
    print("--- VERIFIED ARCHIVE SUMMARY ---")
    print("Published new:", created, "| Already archived:", reused)
    print("Verified immutable objects:", len(bundle.candidates))
    print("HF body downloads: NONE | PostgreSQL writes: NONE | Snapshot created: NO")
    print("M4.6.4c.7 ARCHIVE PROMOTION: PASS (NOT APPROVAL FOR DB COMMIT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
