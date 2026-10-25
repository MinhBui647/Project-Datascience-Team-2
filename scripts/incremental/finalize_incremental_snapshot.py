#!/usr/bin/env python3
"""M4.6.3e.4 | Guarded finalization of ONE pinned incremental Raw snapshot.

Run only AFTER the independently validated manifest has been registered and ALL
REUSE and candidate observations exist. --plan checks manifest+all DB records,
including TAR archive members (read-only). --commit additionally rehashes every
Raw archive object and repeats all checks inside a locked PostgreSQL transaction,
then changes ONLY raw.snapshot_runs.status to 'completed'.

No floating HF revision, snapshot creation, archive modification or raw writes.
The supplied expected-* arguments and the manifest digest must come from the
SAME pinned execution plan used by Registration, REUSE and Candidate Ingestion.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.register_incremental_manifest import (
    ManifestRequest, check_manifest, load_verified_manifest, manifest_identities,
    validate_observations, REPO, DB,
)
from scripts.tools.populate_incremental_reuse import inspect_db, verify_plan, read_only_args
from scripts.tools.register_incremental_snapshot import db_parameters as registration_db_parameters


@dataclass(frozen=True)
class FinalAudit:
    objects: tuple[dict, ...]
    records: int
    observations: int
    reused: int
    candidates: int
    candidate_records: int
    tar_members: int


@dataclass(frozen=True)
class ArchiveReceipt:
    """Exact archive objects passed to a full on-disk checksum scan."""
    objects: tuple[dict, ...]
    total_bytes: int


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-snapshot', type=int, required=True)
    p.add_argument('--target-snapshot-id', type=int, required=True)
    p.add_argument('--target-revision', required=True)
    p.add_argument('--expected-fingerprint', required=True)
    p.add_argument('--repo-id', default=REPO)
    p.add_argument('--expected-baseline-files', type=int, required=True)
    p.add_argument('--expected-full-files', type=int, required=True)
    p.add_argument('--expected-raw-files', type=int, required=True)
    p.add_argument('--expected-reuse', type=int, required=True)
    p.add_argument('--expected-files', type=int, required=True)
    p.add_argument('--expected-bytes', type=int, required=True)
    p.add_argument('--expected-records', type=int, required=True)
    p.add_argument('--expected-baseline-records', type=int, required=True)
    p.add_argument('--expected-reused-records', type=int, required=True)
    p.add_argument('--manifest-report', required=True)
    p.add_argument('--manifest-sha256', required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--commit', action='store_true')
    p.add_argument('--confirm-complete', action='store_true')
    p.add_argument('--allow-csv-finalization-plan-only', action='store_true',
                   help='CSV candidate proof in read-only --plan; not --commit')
    p.add_argument('--allow-csv-finalization-write', action='store_true',
                   help='Explicit CSV completion opt-in; requires --commit --confirm-complete')
    return p


def check_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if bool(args.commit) != bool(args.confirm_complete):
        parser.error('--commit requires --confirm-complete; --plan forbids confirmation')
    if getattr(args, 'allow_csv_finalization_plan_only', False) and not args.plan:
        parser.error('CSV finalization opt-in is read-only: --commit blocked')
    if getattr(args, 'allow_csv_finalization_write', False) and (
            not args.commit or not args.confirm_complete or
            getattr(args, 'allow_csv_finalization_plan_only', False)):
        parser.error('CSV completion requires --commit --confirm-complete and forbids CSV plan-only')


def build_request(args: argparse.Namespace) -> ManifestRequest:
    # Keep EXACTLY the same context type as Manifest Registration: avoid
    # constructing a second, subtly different execution-plan contract.
    from scripts.tools.register_incremental_manifest import build_request as manifest_build
    return manifest_build(args)


def verify_snapshot_row(row, req: ManifestRequest) -> None:
    r = req.reuse.registration
    expected = (r.repo_id, r.target_revision, 'in_progress', r.manifest_files,
                r.raw_files, req.manifest_sha256, req.archive_relative_path)
    if row != expected:
        raise RuntimeError('Snapshot identity/status/registered manifest drift')


def build_items(rows, raw, plan, links, req: ManifestRequest):
    """Pure audit of snapshot observations, with path classification by PLAN action."""
    r = req.reuse.registration
    counts, summary = validate_observations(rows, raw, plan, links, req)
    if (summary.observations, summary.reuse, summary.candidates,
            summary.candidate_records, summary.reused_records, summary.record_references) != (
            r.raw_files, r.reuse_files, r.candidate_files, r.candidate_records,
            req.reuse.expected_reused_records,
            req.reuse.expected_reused_records + r.candidate_records):
        raise RuntimeError('Full-plan observation/record reference reconciliation failed')
    versions, objects = {}, []
    for row in rows:
        (path, _, _, _, _, _, file_id, _, _, _, sha, archive,
         _, _, record_count, _, _, fmt) = row
        if file_id in versions:
            raise RuntimeError('Duplicate immutable file ID in snapshot')
        versions[file_id] = {'path': path, 'format': fmt, 'record_count': record_count}
        meta = raw[path]
        objects.append({
            'relative_path': path,
            'archive_path': archive,
            'sha256': str(sha).strip().lower(),
            'size_bytes': meta['size_bytes'],
            'git_blob_id': (meta.get('git_blob_id') or '').strip().lower(),
            'lfs_sha256': (meta.get('lfs_sha256') or '').strip().lower(),
        })
    if len(versions) != r.raw_files:
        raise RuntimeError('Duplicate/missing Raw file versions')
    return tuple(sorted(objects, key=lambda x: x['relative_path'])), versions, tuple(sorted(counts))


def audit_physical_records(cur, versions, candidate_ids, req: ManifestRequest) -> tuple[int, int]:
    """Count all physically stored rows for referenced file IDs, independently.

    TAR member coverage is discovered from the actual snapshot's TAR versions:
    one successfully represented raw record per stored member under the existing
    tar_json parser contract. No pinned file names/counts from snapshot #365.
    """
    r = req.reuse.registration
    expected_records = req.reuse.expected_reused_records + r.candidate_records
    ids = sorted(versions)
    if not ids:
        raise RuntimeError('No Raw file IDs available for finalization')
    cur.execute('''SELECT file_id,record_kind,(member_id IS NOT NULL) AS has_member,COUNT(*)
                   FROM raw.raw_records WHERE file_id=ANY(%s::bigint[])
                   GROUP BY file_id,record_kind,(member_id IS NOT NULL)''', (ids,))
    counts = dict.fromkeys(ids, 0)
    for fid, kind, has_member, count in cur.fetchall():
        if fid not in versions or type(count) is not int or count < 0:
            raise RuntimeError('Invalid physical Raw record group')
        fmt = versions[fid]['format']
        valid = ('csv_row',) if fmt.startswith('csv') else (
            ('tar_json',) if fmt == 'tar.gz' else
            ('json_item', 'json_object', 'json_scalar'))
        if kind not in valid or bool(has_member) != (fmt == 'tar.gz'):
            raise RuntimeError('Raw record kind/member mismatch: ' + versions[fid]['path'])
        counts[fid] += count
    for fid, meta in versions.items():
        if counts[fid] != meta['record_count']:
            raise RuntimeError('Raw record count inconsistent for ' + meta['path'])
    total = sum(counts.values())
    if total != expected_records:
        raise RuntimeError('Physical record references disagree with pinned plan')

    # A candidate may re-link a pre-existing immutable version; compare its
    # physical row identities, NOT only newly inserted rows this run.
    if candidate_ids:
        cur.execute('''SELECT file_id,COUNT(*),COUNT(DISTINCT record_locator),
                              COUNT(*) FILTER (WHERE member_id IS NOT NULL)
                       FROM raw.raw_records WHERE file_id=ANY(%s::bigint[])
                       GROUP BY file_id''', (list(candidate_ids),))
        candidate_rows = cur.fetchall()
        if (len(candidate_rows) != len(candidate_ids) or
                sum(row[1] for row in candidate_rows) != r.candidate_records or
                any(fid not in versions or n != versions[fid]['record_count']
                    or distinct != n or has_member != 0
                    for fid, n, distinct, has_member in candidate_rows)):
            raise RuntimeError('Candidate physical JSON record integrity mismatch')
    elif r.candidate_files or r.candidate_records:
        raise RuntimeError('Candidate count expected but no candidate file IDs')

    tar_ids = {fid: v for fid, v in versions.items() if v['format'] == 'tar.gz'}
    if not tar_ids:
        return total, 0
    cur.execute('''SELECT file_id,parse_state,COUNT(*) FROM raw.archive_members
                   WHERE file_id=ANY(%s::bigint[])
                   GROUP BY file_id,parse_state''', (sorted(tar_ids),))
    members = dict.fromkeys(tar_ids, 0)
    for fid, state, count in cur.fetchall():
        if fid not in tar_ids or state not in ('success', 'skipped') or type(count) is not int or count < 0:
            raise RuntimeError('Invalid TAR archive member state/count')
        members[fid] += count
    for fid, meta in tar_ids.items():
        # Current V2 TAR parser contract: one tar_json record per manifest member.
        # If this policy changes, modify this invariant with parser regression tests.
        if members[fid] != meta['record_count']:
            raise RuntimeError('TAR member/record coverage mismatch: ' + meta['path'])
    return total, sum(members.values())


def audit_database(cur, req: ManifestRequest, raw, baseline, sources, *,
                   allow_csv_planning: bool = False) -> FinalAudit:
    """Repeat all pinned plan, registered manifest, DB and physical-row checks."""
    req.validate()
    r = req.reuse.registration
    if type(allow_csv_planning) is not bool:
        raise ValueError('Invalid CSV finalization audit permission')
    cur.execute('SELECT current_database()')
    if cur.fetchone() != (DB,):
        raise RuntimeError('Wrong PostgreSQL database')
    base_revision, existing, state = inspect_db(cur, req.reuse)
    if base_revision == r.target_revision or set(baseline) != set(existing):
        raise RuntimeError('Baseline identity/path set drift')
    verify_snapshot_row((state.repo_id, state.revision, state.status,
                         state.manifest_files, state.raw_files,
                         state.manifest_sha, state.manifest_path), req)
    if allow_csv_planning:
        plan, links = verify_plan(req.reuse, baseline, manifest_identities(raw),
                                  r.manifest_files - r.raw_files, existing, sources,
                                  allow_csv_planning=True)
    else:
        plan, links = verify_plan(req.reuse, baseline, manifest_identities(raw),
                                  r.manifest_files - r.raw_files, existing, sources)
    if plan.fingerprint != r.fingerprint:
        raise RuntimeError('Snapshot plan fingerprint changed before finalization')
    cur.execute('''SELECT o.relative_path,o.repo_id,o.presence_status,o.manifest_size_bytes,
                          o.git_blob_id,o.lfs_sha256,
                          f.file_id,f.repo_id,f.relative_path,f.file_size_bytes,
                          f.content_sha256,f.archive_path,f.archive_state,f.parse_state,
                          f.record_count,s.source_id,s.source_code,f.file_format
                   FROM raw.file_observations o
                   LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                   LEFT JOIN raw.raw_sources s ON s.source_id=f.source_id
                   WHERE o.snapshot_id=%s''', (req.reuse.target_snapshot_id,))
    objects, versions, candidates = build_items(cur.fetchall(), raw, plan, links, req)
    physical, tar_members = audit_physical_records(cur, versions, candidates, req)
    return FinalAudit(objects, physical, len(versions), r.reuse_files,
                      r.candidate_files, r.candidate_records, tar_members)


def scan_archives(items, *, root: Path = ROOT) -> ArchiveReceipt:
    """Rehash every object. Expensive and strictly read-only."""
    from scripts.tools.verify_archive import verify_bytes
    root = root.resolve()
    store = root / 'raw-archive-v2/objects'
    trusted = store.resolve(strict=True)
    if not trusted.is_relative_to(root) or trusted != store:
        raise RuntimeError('Archive object store must not resolve outside repository')
    total = 0
    for idx, item in enumerate(items, 1):
        archive_rel = item['archive_path']
        if not isinstance(archive_rel, str) or not archive_rel.startswith('raw-archive-v2/objects/'):
            raise RuntimeError('Invalid archive reference')
        fp = root / archive_rel
        if fp.is_symlink() or not fp.resolve(strict=True).is_relative_to(trusted):
            raise RuntimeError('Archive symlink or path escape')
        total += verify_bytes(item, root=root)
        if idx % 25 == 0 or idx == len(items):
            print(f'  Archive hashes checked: {idx}/{len(items)}', flush=True)
    return ArchiveReceipt(tuple(items), total)


def finalize_in_transaction(cur, req: ManifestRequest, raw, baseline, sources,
                            receipt: ArchiveReceipt, *, root: Path = ROOT,
                            allow_csv_planning: bool = False) -> FinalAudit:
    """Never commits. Locks target, rechecks manifest and all DB rows before status update."""
    if type(allow_csv_planning) is not bool:
        raise ValueError('Invalid finalization CSV write permission')
    req.validate()
    r = req.reuse.registration
    if not isinstance(receipt, ArchiveReceipt) or not receipt.objects or receipt.total_bytes < 0:
        raise RuntimeError('Missing full immutable archive scan receipt')
    cur.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (r.repo_id,))
    if cur.fetchone() != (True,):
        raise RuntimeError('Another incremental writer/finalizer holds the repository lock')
    cur.execute('''SELECT snapshot_id FROM raw.snapshot_runs
                   WHERE snapshot_id=%s AND repo_id=%s FOR UPDATE''',
                (req.reuse.target_snapshot_id, r.repo_id))
    if cur.fetchone() != (req.reuse.target_snapshot_id,):
        raise RuntimeError('Cannot lock target snapshot row')
    checked, _ = load_verified_manifest(req, root=root)
    if checked != raw:
        raise RuntimeError('Manifest bytes/contents changed before finalization')
    if allow_csv_planning:
        result = audit_database(cur, req, checked, baseline, sources,
                                allow_csv_planning=True)
    else:
        result = audit_database(cur, req, checked, baseline, sources)
    if (result.objects != receipt.objects or
            receipt.total_bytes != sum(item['size_bytes'] for item in result.objects)):
        raise RuntimeError('Archive scan is stale/incomplete for target snapshot')
    cur.execute('''UPDATE raw.snapshot_runs SET status='completed'
                   WHERE snapshot_id=%s AND repo_id=%s AND hf_revision=%s
                     AND status='in_progress' AND manifest_file_count=%s
                     AND raw_scope_file_count=%s AND manifest_sha256=%s
                     AND manifest_archive_path=%s
                   RETURNING snapshot_id,status''',
                (req.reuse.target_snapshot_id, r.repo_id, r.target_revision,
                 r.manifest_files, r.raw_files, req.manifest_sha256,
                 req.archive_relative_path))
    if cur.fetchone() != (req.reuse.target_snapshot_id, 'completed'):
        raise RuntimeError('Snapshot status update rejected')
    return result


def main(argv=None) -> int:
    p = make_parser()
    args = p.parse_args(argv)
    check_mode(args, p)
    req = build_request(args)
    r = req.reuse.registration
    raw, size = load_verified_manifest(req)
    from scripts.tools.check_raw_updates import load_baseline
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    base_revision, baseline = load_baseline(r.baseline_snapshot, r.repo_id)
    if base_revision == r.target_revision:
        raise RuntimeError('Baseline and target cannot be the same revision')
    sources = read_existing_source_codes()
    import psycopg
    with psycopg.connect(**db_parameters(readonly=True)) as conn:
        with conn.cursor() as cur:
            if args.allow_csv_finalization_plan_only or args.allow_csv_finalization_write:
                summary = audit_database(cur, req, raw, baseline, sources,
                                         allow_csv_planning=True)
            else:
                summary = audit_database(cur, req, raw, baseline, sources)
    print('=== M4.6.3e.4 | GENERIC SNAPSHOT FINALIZATION ===', flush=True)
    print('Target snapshot:', req.reuse.target_snapshot_id, '| revision:', r.target_revision)
    print('Manifest bytes verified:', size, '| manifest SHA256:', req.manifest_sha256)
    print(f'Raw file observations: {summary.observations} ({summary.reused} REUSE + {summary.candidates} candidates)')
    print('Physical raw record references:', summary.records)
    print('Physical TAR members:', summary.tar_members)
    print('Full archive rehash: NOT RUN during read-only PLAN')
    if args.plan:
        print('Database writes: NONE | Snapshot status: in_progress')
        print('M4.6.3e.4 FINALIZATION PLAN: PASS (NOT APPROVAL TO COMMIT)')
        return 0
    print(f'Hashing all {len(summary.objects)} immutable Raw archive objects...', flush=True)
    receipt = scan_archives(summary.objects)
    print(f'Archive scan PASS: objects={len(receipt.objects)} bytes={receipt.total_bytes}', flush=True)
    with psycopg.connect(**db_parameters(readonly=False)) as conn:
        if conn.autocommit:
            raise RuntimeError('Autocommit connection is unsafe')
        with conn.cursor() as cur:
            if args.allow_csv_finalization_write:
                after = finalize_in_transaction(cur, req, raw, baseline, sources, receipt,
                                                allow_csv_planning=True)
            else:
                after = finalize_in_transaction(cur, req, raw, baseline, sources, receipt)
    print(f'Snapshot #{req.reuse.target_snapshot_id}: completed | raw data writes: NONE')
    print('M4.6.3e.4 SNAPSHOT FINALIZATION: PASS')
    return 0


def db_parameters(*, readonly: bool) -> dict:
    if readonly:
        params = read_only_args()
    else:
        params = registration_db_parameters()
    params['application_name'] = 'm46_finalize_plan' if readonly else 'm46_finalize_commit'
    params['options'] = (params.get('options', '') +
                         ' -c statement_timeout=600000 -c lock_timeout=5000').strip()
    return params


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'M4.6.3e.4 ERROR | {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
