#!/usr/bin/env python3
"""M3.9.4: pinned V2 snapshot final gate.

--plan: verify registered original manifest (archive bytes & source), M3.9.1
        records/TAR reconciliation, and M3.9.2 prior report. No writes.
--commit --confirm-complete: rerun those checks plus live SHA256 of all 276
        archived objects, then atomically mark one guarded V2 snapshot completed.

No network and no mutations of raw files, records, observation, or archive.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'tools'))

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = '4c6bec3a2d89f36496f40c854ba8de374a7b2b36'
DB = 'epl_raw_storage_v2'
SNAPSHOT_ID = 4
FULL = 294
SCOPE = 276
RECORDS = 8502584
TAR_MEMBERS = 10753
ARCHIVE_BYTES = 833030129
MANIFEST_SHA = '7c619cadedbe9ffc24301f8621c6026e3d6ba728f705264965de932c00f2efb4'


def modules():
    import batch_ingestion as engine
    import reconcile_db as recon
    import verify_archive as archive
    import register_manifest as manifest
    if (engine.REPO, engine.REV, engine.ROOT.resolve()) != (REPO, REV, ROOT.resolve()):
        raise RuntimeError('Engine pinned repository/revision/root mismatch')
    if (recon.REPO, recon.REV, recon.DB) != (REPO, REV, DB):
        raise RuntimeError('Reconciliation module pin mismatch')
    if (archive.REPO, archive.REV, archive.DB) != (REPO, REV, DB):
        raise RuntimeError('Archive verification module pin mismatch')
    if (manifest.REPO, manifest.REV, manifest.DB) != (REPO, REV, DB):
        raise RuntimeError('Manifest registration module pin mismatch')
    return engine, recon, archive, manifest


def db_params(engine, *, readonly):
    p = engine.connection_parameters()
    if p.get('dbname') != DB:
        raise RuntimeError(f'Refusing unexpected database {p.get("dbname")!r}')
    p['options'] = '-c statement_timeout=600000' + (' -c default_transaction_read_only=on' if readonly else '')
    return p


def manifest_gate(engine, manifest):
    original, sha, all_paths = manifest.check_original(engine)
    if sha != MANIFEST_SHA or len(all_paths) != FULL:
        raise RuntimeError('Original full manifest SHA/count differs from pinned successful M3.9.3')
    digest_report = manifest.check_integrity_report()
    if digest_report.get('snapshot_id') != SNAPSHOT_ID:
        raise RuntimeError('Archive check report points to a different snapshot')
    rel = f'raw-archive-v2/manifests/{sha[:2]}/{sha}.json'
    p = ROOT / rel
    if p.is_symlink() or not p.is_file():
        raise RuntimeError('Registered manifest archive object is missing or symlinked')
    archived = p.read_bytes()
    if archived != original or hashlib.sha256(archived).hexdigest() != sha:
        raise RuntimeError('Archived manifest bytes differ from full original manifest')
    return sha, rel


def snapshot_gate(engine, sha, rel):
    import psycopg
    with psycopg.connect(**db_params(engine, readonly=True)) as conn:
        with conn.cursor() as cur:
            engine.db_guard(cur)
            cur.execute('''
                SELECT snapshot_id, status, manifest_file_count, raw_scope_file_count,
                       manifest_archive_path, manifest_sha256
                FROM raw.snapshot_runs WHERE repo_id=%s AND hf_revision=%s
            ''', (REPO, REV))
            rows = cur.fetchall()
    expected = (SNAPSHOT_ID, 'discovered', FULL, SCOPE, rel, sha)
    if rows != [expected]:
        raise RuntimeError(f'Unexpected snapshot/manifest state: {rows!r}; expected {expected!r}')
    return SNAPSHOT_ID


def reconcile_gate(recon):
    print('Rechecking all 276 DB observations and 8,502,584 records (M3.9.1)...', flush=True)
    if recon.reconcile() != 0:
        raise RuntimeError('M3.9.1 reconciliation FAILED')


def archive_gate(archive, sid):
    print('Rehashing all 276 immutable archive objects (M3.9.2)...', flush=True)
    catalog, rows, sid2, registered = archive.load_rows()
    if sid2 != sid or not registered:
        raise RuntimeError('Archive snapshot changed or manifest is not registered')
    items, problems = archive.verified_catalog_entries(catalog, rows)
    if problems or len(items) != SCOPE:
        raise RuntimeError(f'Archive metadata gate failed: {problems[:3]!r}')
    nbytes = 0
    for ix, item in enumerate(items, 1):
        nbytes += archive.verify_bytes(item)
        if ix % 50 == 0 or ix == len(items):
            print(f'  Archive SHA verified: {ix}/{len(items)}', flush=True)
    if nbytes != ARCHIVE_BYTES:
        raise RuntimeError(f'Archive total bytes mismatch: {nbytes} != {ARCHIVE_BYTES}')
    print(f'Physical archive gate: PASS | {len(items)} objects | {nbytes:,} bytes', flush=True)


def final_commit(engine, sid, sha, rel):
    import psycopg
    # Only one table/row is ever updated, after all checks succeed.
    with psycopg.connect(**db_params(engine, readonly=False)) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                engine.db_guard(cur)
                cur.execute('''
                    SELECT status, manifest_file_count, raw_scope_file_count,
                           manifest_archive_path, manifest_sha256
                    FROM raw.snapshot_runs
                    WHERE snapshot_id=%s AND repo_id=%s AND hf_revision=%s
                    FOR UPDATE
                ''', (sid, REPO, REV))
                state = cur.fetchone()
                expected = ('discovered', FULL, SCOPE, rel, sha)
                if state != expected:
                    raise RuntimeError(f'Final snapshot row changed: {state!r}')
                cur.execute('''
                    SELECT count(*), count(DISTINCT o.relative_path),
                           count(DISTINCT o.file_id),
                           count(*) FILTER (WHERE o.presence_status='linked'
                              AND f.archive_state='verified' AND f.parse_state='success'),
                           COALESCE(sum(f.record_count),0)
                    FROM raw.file_observations o
                    JOIN raw.raw_files f ON f.file_id=o.file_id
                    WHERE o.snapshot_id=%s
                ''', (sid,))
                observation_summary = cur.fetchone()
                expected_summary = (SCOPE, SCOPE, SCOPE, SCOPE, RECORDS)
                if observation_summary != expected_summary:
                    raise RuntimeError(f'Final observation/record-count mismatch: {observation_summary!r}')
                cur.execute('''
                    SELECT count(*) FROM raw.raw_records
                    WHERE file_id IN (
                       SELECT file_id FROM raw.file_observations WHERE snapshot_id=%s
                    )
                ''', (sid,))
                physical_count = cur.fetchone()[0]
                if physical_count != RECORDS:
                    raise RuntimeError(f'Final physical record mismatch: {physical_count} != {RECORDS}')
                cur.execute('''
                    SELECT count(*) FROM raw.archive_members
                    WHERE file_id IN (
                       SELECT o.file_id FROM raw.file_observations o
                       JOIN raw.raw_files f ON f.file_id=o.file_id
                       WHERE o.snapshot_id=%s AND f.file_format='tar.gz'
                    )
                ''', (sid,))
                members = cur.fetchone()[0]
                if members != TAR_MEMBERS:
                    raise RuntimeError(f'Final TAR member count mismatch: {members}')
                cur.execute('''
                    UPDATE raw.snapshot_runs SET status='completed'
                    WHERE snapshot_id=%s AND repo_id=%s AND hf_revision=%s
                      AND status='discovered'
                      AND manifest_archive_path=%s AND manifest_sha256=%s
                    RETURNING snapshot_id, status
                ''', (sid, REPO, REV, rel, sha))
                if cur.fetchone() != (sid, 'completed'):
                    raise RuntimeError('Final snapshot status update did not affect one guarded row')
    print('Snapshot ID:', sid)
    print('Snapshot status: completed')
    print('M3.9.4 FINAL GATE: PASS (snapshot completed)')


def main():
    p = argparse.ArgumentParser(description='Final pinned raw snapshot gate: PLAN or guarded COMMIT')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true', help='Read-only DB/source/manifest check')
    mode.add_argument('--commit', action='store_true', help='Full hash recheck and guarded snapshot update')
    p.add_argument('--confirm-complete', action='store_true', help='Required for COMMIT only')
    args = p.parse_args()
    if args.commit != args.confirm_complete:
        p.error('COMMIT requires --confirm-complete; do not use confirmation with --plan')
    engine, recon, archive, manifest = modules()
    print('=== M3.9.4 | FINAL GATE & SNAPSHOT COMPLETION ===', flush=True)
    print('Repo:', REPO)
    print('Revision:', REV)
    print('Database guard:', DB)
    print('Snapshot ID:', SNAPSHOT_ID)
    print('Full HF manifest paths:', FULL)
    print('Raw-scope paths:', SCOPE)
    print('Expected records:', f'{RECORDS:,}')
    print('Expected TAR members:', f'{TAR_MEMBERS:,}')
    print('Expected archive bytes:', f'{ARCHIVE_BYTES:,}')
    print('Mode:', 'PLAN (read-only; no writes/network)' if args.plan else 'COMMIT (explicit confirmation)', flush=True)
    sha, rel = manifest_gate(engine, manifest)
    sid = snapshot_gate(engine, sha, rel)
    print('Registered manifest SHA256:', sha)
    print('Registered immutable manifest:', rel)
    print('Full manifest byte comparison: PASS')
    reconcile_gate(recon)
    if args.plan:
        print('M3.9.2 archive integrity report: PASS (prior checkpoint; no rehash in PLAN)')
        print('Snapshot status: discovered (UNCHANGED)')
        print('M3.9.4 PLAN: PASS (no database writes)')
        return 0
    archive_gate(archive, sid)
    # Reconfirm state/manifest after the long archive byte verification.
    snapshot_gate(engine, sha, rel)
    # Deep DB reconciliation was also run before hashing, and the final
    # transaction below repeats all global counts before the atomic update.
    final_commit(engine, sid, sha, rel)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'M3.9.4 ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
