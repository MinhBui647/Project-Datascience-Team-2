#!/usr/bin/env python3
"""M4.CSV.3b.4i | Explicit, pinned, create-only Raw candidate archive promotion.

This is a *separate* coordinator. The historical JSON-only archive CLI stays
unchanged. The --plan mode has NO local/DB writes. The --promote mode ONLY
creates verified immutable archive objects, requires pinned receipt SHA256 and
explicit confirmation. No snapshot/observation/record/manifest DB mutation.

A partial promotion may safely leave immutable objects: rerun with the SAME
pinned revision and receipt to verify/idempotently finish. Never delete data.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = 'Toibinguz/epl-premier-league-dataset'
SHA40 = re.compile(r'[a-f0-9]{40}\Z')
SHA256 = re.compile(r'[a-f0-9]{64}\Z')


@dataclass(frozen=True)
class ArchivePromotionPlan:
    repo_id: str
    baseline_snapshot: int
    target_revision: str
    plan_fingerprint: str
    candidates: tuple
    local_files: Mapping[str, Path]
    identities: Mapping[str, object]
    receipt_sha256: str


def receipt_hash(candidates: tuple) -> str:
    """Stable cross-run evidence, independent of READY_NEW/ALREADY_ARCHIVED."""
    if not candidates:
        raise ValueError('No candidate archives to identify')
    rows, seen = [], set()
    for x in sorted(candidates, key=lambda a: a.relative_path):
        if (x.relative_path in seen or not isinstance(x.relative_path, str)
                or not x.relative_path.startswith(('raw_data/', '2026_2027_new/'))
                or not x.relative_path.endswith(('.json', '.csv'))
                or '\\' in x.relative_path or any(t in ('', '.', '..') for t in x.relative_path.split('/'))
                or not SHA256.fullmatch(x.sha256)
                or type(x.byte_count) is not int or x.byte_count <= 0
                or type(x.record_count) is not int or x.record_count <= 0
                or x.archive_relative_path !=
                   f'raw-archive-v2/objects/{x.sha256[:2]}/{x.sha256}'
                or x.action not in ('READY_NEW', 'ALREADY_ARCHIVED')):
            raise ValueError('Invalid or duplicated verified candidate receipt')
        seen.add(x.relative_path)
        rows.append([x.relative_path, x.sha256, x.byte_count,
                     x.record_count, x.archive_relative_path])
    raw = json.dumps(rows, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-snapshot', required=True, type=int)
    p.add_argument('--target-revision', required=True)
    p.add_argument('--expected-fingerprint', required=True)
    p.add_argument('--expected-files', required=True, type=int)
    p.add_argument('--expected-bytes', required=True, type=int)
    p.add_argument('--expected-records', required=True, type=int)
    p.add_argument('--repo-id', default=REPO)
    p.add_argument('--cache-dir', default='reports/m4_6_content_cache')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--promote', action='store_true')
    p.add_argument('--confirm-csv-archive-only', action='store_true')
    p.add_argument('--expected-receipt-sha256')
    return p


def parse_args(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if (args.repo_id != REPO or type(args.baseline_snapshot) is not int
            or args.baseline_snapshot <= 0 or not SHA40.fullmatch(args.target_revision)
            or not SHA256.fullmatch(args.expected_fingerprint)
            or any(type(x) is not int or x <= 0 for x in
                   (args.expected_files, args.expected_bytes, args.expected_records))):
        p.error('Exact EPL repository and positive, pinned snapshot/revision/counts required')
    if args.promote:
        if (not args.confirm_csv_archive_only
                or not isinstance(args.expected_receipt_sha256, str)
                or not SHA256.fullmatch(args.expected_receipt_sha256)):
            p.error('--promote requires --confirm-csv-archive-only and --expected-receipt-sha256')
    elif args.confirm_csv_archive_only or args.expected_receipt_sha256 is not None:
        p.error('--plan forbids archive promotion confirmation and receipt SHA256')
    if (not isinstance(args.cache_dir, str)
            or not args.cache_dir.startswith('reports/') or '\\' in args.cache_dir
            or any(t in ('', '.', '..') for t in args.cache_dir.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 for c in args.cache_dir)):
        p.error('Unsafe project-relative HF cache directory')
    return args


def construct_plan(args, *, root: Path = ROOT) -> ArchivePromotionPlan:
    """Pinned HF metadata + PostgreSQL SELECT, then full offline cache rehash."""
    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.raw_sync_write_plan import read_db_state
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.incremental_sync import assemble_plan
    from scripts.tools.incremental_cache_gate import candidate_actions, cache_directory
    from scripts.tools.incremental_archive_promotion import audit_candidates
    from huggingface_hub import hf_hub_download

    if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
        raise RuntimeError('Archive repository root is not canonical')
    baseline_revision, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    revision, target, excluded = load_target(args.repo_id, args.target_revision)
    db_revision, existing = read_db_state(args.baseline_snapshot, args.repo_id, revision)
    if revision != args.target_revision or baseline_revision == revision or db_revision != baseline_revision:
        raise RuntimeError('Pinned target/baseline/DB drift')
    plan = assemble_plan(baseline, target, existing, read_existing_source_codes(),
                         allow_csv_planning=True)
    if (plan.fingerprint != args.expected_fingerprint or plan.count('REMOVED')
            or plan.count('REVIEW') or plan.target_raw_count != len(target)):
        raise RuntimeError('Blocked categories or plan fingerprint drift')
    actions = tuple(candidate_actions(plan, target, allow_csv=True))
    if (len(actions) != args.expected_files or len({a.path for a in actions}) != len(actions)
            or plan.candidate_bytes != args.expected_bytes
            or not any(a.file_format == 'csv' for a in actions)
            or any(a.file_format not in ('json', 'csv') for a in actions)):
        raise RuntimeError('Candidate plan is incomplete or has no CSV candidate')
    cache = cache_directory(args.cache_dir)
    if cache.is_symlink() or not cache.resolve(strict=True).is_relative_to(root / 'reports'):
        raise RuntimeError('HF cache path is untrusted')
    local = {}
    for a in actions:
        fp = Path(hf_hub_download(
            repo_id=args.repo_id, filename=a.path, repo_type='dataset',
            revision=revision, cache_dir=str(cache), local_files_only=True))
        # HF snapshot entries may link to blobs inside the trusted cache.
        resolved = fp.resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(cache):
            raise RuntimeError('HF cache file escapes trusted directory: ' + a.path)
        local[a.path] = resolved
    verified = tuple(audit_candidates(
        plan, target, local, root, expected_records=args.expected_records, allow_csv=True))
    if (len(verified) != args.expected_files
            or sum(c.byte_count for c in verified) != args.expected_bytes
            or sum(c.record_count for c in verified) != args.expected_records
            or set(local) != {c.relative_path for c in verified}):
        raise RuntimeError('Archive promotion coverage/count mismatch')
    return ArchivePromotionPlan(args.repo_id, args.baseline_snapshot, revision,
                                plan.fingerprint, verified, local, target,
                                receipt_hash(verified))


def promote(plan: ArchivePromotionPlan, *, root: Path = ROOT) -> tuple[int, int]:
    """Only immutable object-store create-only hardlinks; no SQL whatsoever."""
    from scripts.tools.incremental_archive_promotion import publish_immutable, inspect_cached
    created = already = 0
    for candidate in plan.candidates:
        path = candidate.relative_path
        # Revalidate every candidate immediately before copying.
        refreshed = inspect_cached(root, path, plan.local_files[path],
                                   plan.identities[path], allow_csv=True)
        if (refreshed.sha256, refreshed.byte_count, refreshed.record_count,
                refreshed.archive_relative_path) != (
                candidate.sha256, candidate.byte_count, candidate.record_count,
                candidate.archive_relative_path):
            raise RuntimeError('Cache drift detected before publication: ' + path)
        action = publish_immutable(root, plan.local_files[path], plan.identities[path],
                                   candidate, allow_csv=True)
        if action == 'ARCHIVED_NEW':
            created += 1
        elif action == 'ALREADY_ARCHIVED':
            already += 1
        else:
            raise RuntimeError('Unexpected immutable archive publication outcome')
    return created, already


def main(argv=None) -> int:
    args = parse_args(argv)
    print('=== M4.CSV.3b.4i | PINNED CSV IMMUTABLE ARCHIVE COORDINATOR ===', flush=True)
    prepared = construct_plan(args)
    print('Repository:', prepared.repo_id)
    print('Baseline:', prepared.baseline_snapshot, '| target:', prepared.target_revision)
    print('Plan fingerprint:', prepared.plan_fingerprint)
    print('Archive candidate receipt SHA256:', prepared.receipt_sha256)
    print('Verified candidate archives:', len(prepared.candidates))
    print('Candidate bytes:', sum(x.byte_count for x in prepared.candidates))
    print('Candidate records:', sum(x.record_count for x in prepared.candidates))
    print('READY_NEW:', sum(x.action == 'READY_NEW' for x in prepared.candidates))
    print('ALREADY_ARCHIVED:', sum(x.action == 'ALREADY_ARCHIVED' for x in prepared.candidates))
    if args.plan:
        print('HF body downloads: NONE | Archive/DB/Checkpoint writes: NONE')
        print('CSV IMMUTABLE ARCHIVE PLAN: PASS (NOT WRITE APPROVAL)')
        return 0
    if prepared.receipt_sha256 != args.expected_receipt_sha256:
        raise RuntimeError('Candidate archive receipt changed since reviewed --plan')
    created, already = promote(prepared)
    print('Archives created:', created, '| pre-existing:', already)
    print('Snapshot registration: NONE | PostgreSQL writes: NONE')
    print('M4.CSV.3b.4i CSV ARCHIVE PROMOTION: PASS')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('M4.CSV.3b.4i BLOCKED: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
