#!/usr/bin/env python3
"""M3.9.3: verify and optionally register the original pinned HF manifest.

--plan: read-only local manifest/report and DB; no network, no writes.
--commit --confirm-manifest: rerun M3.9.1/M3.9.2 checks, archive exact original
manifest bytes immutably and register manifest SHA/path in V2 DB; snapshot status
remains 'discovered'. No V1 changes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'tools'))
REPO = 'Toibinguz/epl-premier-league-dataset'
REV = '4c6bec3a2d89f36496f40c854ba8de374a7b2b36'
DB = 'epl_raw_storage_v2'
TOTAL = 294
RAW = 276
ARCHIVE_BYTES = 833030129
ORIGINAL = ROOT / 'reports' / 'hf_manifest_v2.json'
CHECK_REPORT = ROOT / 'reports' / 'm3_9_2_archive_integrity_v2.json'
SHA256 = re.compile(r'^[0-9a-f]{64}$')
SHA1 = re.compile(r'^[0-9a-f]{40}$')


def normalize_path(value):
    if not isinstance(value, str) or not value or value.startswith('/') or '\\' in value:
        raise RuntimeError(f'Unsafe manifest path: {value!r}')
    if any(part in ('', '.', '..') for part in value.split('/')):
        raise RuntimeError(f'Unsafe manifest path: {value!r}')
    return value


def check_original(engine):
    if not ORIGINAL.is_file():
        raise RuntimeError('Missing reports/hf_manifest_v2.json. Do not substitute the 276-file scope CSV for the full 294-file HF manifest; ask for the original M1 report.')
    if ORIGINAL.is_symlink():
        raise RuntimeError('Original manifest may not be a symlink')
    source_bytes = ORIGINAL.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    manifest = json.loads(source_bytes)
    if manifest.get('repo_id') != REPO or manifest.get('repo_type') != 'dataset':
        raise RuntimeError('HF manifest wrong repo/type')
    if manifest.get('resolved_commit') != REV:
        raise RuntimeError('HF manifest not pinned to the expected commit')
    files = manifest.get('files')
    if manifest.get('file_count') != TOTAL or not isinstance(files, list) or len(files) != TOTAL:
        raise RuntimeError('HF full manifest must have exactly 294 files')
    all_paths = {}
    for file in files:
        p = normalize_path(file.get('relative_path'))
        if p in all_paths:
            raise RuntimeError(f'Duplicate full manifest path: {p}')
        sz = file.get('size_bytes')
        if not isinstance(sz, int) or isinstance(sz, bool) or sz < 0:
            raise RuntimeError(f'Invalid full manifest size: {p}')
        if file.get('hf_revision') != REV:
            raise RuntimeError(f'Wrong per-file pinned revision: {p}')
        sha1 = (file.get('git_blob_id') or '').strip().lower()
        sha2 = (file.get('lfs_sha256') or '').strip().lower()
        if sha1 and not SHA1.fullmatch(sha1):
            raise RuntimeError(f'Invalid Git blob ID: {p}')
        if sha2 and not SHA256.fullmatch(sha2):
            raise RuntimeError(f'Invalid LFS SHA256: {p}')
        if not sha1 and not sha2:
            raise RuntimeError(f'Missing Git/LFS object identity: {p}')
        all_paths[p] = file

    catalog = engine.load_catalog()
    if len(catalog) != RAW or not set(catalog).issubset(all_paths):
        raise RuntimeError('Raw scope does not match the full pinned HF manifest')
    for p, row in catalog.items():
        item = all_paths[p]
        for name, left, right in (
            ('size', int(row['size_bytes']), item['size_bytes']),
            ('format', row['format'], item['format']),
            ('lfs_sha256', (row.get('lfs_sha256') or '').strip().lower(), (item.get('lfs_sha256') or '').strip().lower()),
            ('git_blob_id', (row.get('git_blob_id') or '').strip().lower(), (item.get('git_blob_id') or '').strip().lower()),
        ):
            if left != right:
                raise RuntimeError(f'Catalog/full manifest {name} mismatch for {p}')
    return source_bytes, digest, all_paths


def check_integrity_report():
    if not CHECK_REPORT.is_file():
        raise RuntimeError('M3.9.2 report is missing. Run verify_archive.py --check first.')
    r = json.loads(CHECK_REPORT.read_text(encoding='utf-8-sig'))
    checks = {
        'check': 'M3.9.2', 'repo_id': REPO, 'hf_revision': REV,
        'expected_files': RAW, 'catalog_paths': RAW, 'observation_rows': RAW,
        'verified_file_objects': RAW, 'verified_archive_bytes': ARCHIVE_BYTES,
        'mismatch_count': 0, 'database_writes': 0,
    }
    for key, value in checks.items():
        if r.get(key) != value:
            raise RuntimeError(f'Integrity report {key} not verified: {r.get(key)!r} (expected {value!r})')
    return r


def connect_params(engine, *, readonly):
    params = engine.connection_parameters()
    if params.get('dbname') != DB:
        raise RuntimeError(f'Database guard: refusing {params.get("dbname")!r}')
    if readonly:
        params['options'] = '-c default_transaction_read_only=on -c statement_timeout=600000'
    return params


def db_preflight(engine):
    import psycopg
    with psycopg.connect(**connect_params(engine, readonly=True)) as conn:
        with conn.cursor() as cur:
            engine.db_guard(cur)
            cur.execute('''SELECT snapshot_id, status, manifest_file_count, raw_scope_file_count,
                                  manifest_archive_path, manifest_sha256
                           FROM raw.snapshot_runs WHERE repo_id=%s AND hf_revision=%s''', (REPO, REV))
            snaps = cur.fetchall()
            if len(snaps) != 1:
                raise RuntimeError(f'Expected 1 snapshot; found {len(snaps)}')
            sid, status, total, scope, existing_path, existing_sha = snaps[0]
            if status != 'discovered' or total != TOTAL or scope != RAW:
                raise RuntimeError(f'Snapshot state/count unexpected: {status}, {total}, {scope}')
            if existing_path or existing_sha:
                raise RuntimeError('Manifest already registered: do not overwrite it automatically')
            cur.execute('''SELECT count(*), count(DISTINCT o.relative_path),
                                  count(DISTINCT o.file_id),
                                  count(*) FILTER (WHERE o.presence_status='linked'
                                     AND f.archive_state='verified' AND f.parse_state='success')
                           FROM raw.file_observations o
                           LEFT JOIN raw.raw_files f ON f.file_id = o.file_id
                           WHERE o.snapshot_id=%s''', (sid,))
            counts = cur.fetchone()
            if counts != (RAW, RAW, RAW, RAW):
                raise RuntimeError(f'Observation state not ready: {counts!r}')
    return int(sid)


def store_original_manifest(source_bytes, sha):
    rel = f'raw-archive-v2/manifests/{sha[:2]}/{sha}.json'
    target = ROOT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise RuntimeError('Manifest archive path is a symlink')
    if not target.exists():
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=target.parent, prefix='.manifest_', suffix='.tmp', delete=False) as fh:
                tmp = Path(fh.name)
                fh.write(source_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            # Hardlink is a no-overwrite atomic publication (works on NTFS).
            try:
                os.link(tmp, target)
            except FileExistsError:
                pass
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
    if not target.is_file() or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
        raise RuntimeError('Immutable manifest object hash mismatch')
    return rel


def commit(engine, source_bytes, sha, sid):
    import psycopg
    # Re-run independently validated DB reconciliation and full archive hashes.
    import reconcile_db as db_check
    import verify_archive as archive_check
    print('Rechecking M3.9.1 PostgreSQL reconciliation...', flush=True)
    if db_check.reconcile() != 0:
        raise RuntimeError('M3.9.1 DB reconciliation failed; manifest not registered')
    print('Rechecking all 276 archive object checksums...', flush=True)
    cat, rows, sid2, registered = archive_check.load_rows()
    if sid2 != sid or registered:
        raise RuntimeError('Snapshot changed after PLAN')
    objects, errors = archive_check.verified_catalog_entries(cat, rows)
    if errors:
        raise RuntimeError(f'Archive object metadata verification failed: {errors[:2]!r}')
    nbytes = 0
    for item in objects:
        nbytes += archive_check.verify_bytes(item)
    if len(objects) != RAW or nbytes != ARCHIVE_BYTES:
        raise RuntimeError('Archive checksum/count checkpoint failed')
    rel = store_original_manifest(source_bytes, sha)
    params = connect_params(engine, readonly=False)
    with psycopg.connect(**params) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                engine.db_guard(cur)
                cur.execute('''SELECT status, manifest_file_count, raw_scope_file_count,
                                      manifest_archive_path, manifest_sha256
                               FROM raw.snapshot_runs
                               WHERE snapshot_id=%s AND repo_id=%s AND hf_revision=%s
                               FOR UPDATE''', (sid, REPO, REV))
                state = cur.fetchone()
                if state != ('discovered', TOTAL, RAW, None, None):
                    raise RuntimeError(f'Snapshot changed before registration: {state!r}')
                cur.execute('''UPDATE raw.snapshot_runs
                               SET manifest_archive_path=%s, manifest_sha256=%s
                               WHERE snapshot_id=%s AND status='discovered'
                                  AND manifest_archive_path IS NULL AND manifest_sha256 IS NULL
                               RETURNING snapshot_id, status''', (rel, sha, sid))
                result = cur.fetchone()
                if result != (sid, 'discovered'):
                    raise RuntimeError('Manifest registration did not update exactly one snapshot')
    print('Manifest registered:', rel)
    print('Manifest SHA256:', sha)
    print('Snapshot status: discovered (UNCHANGED)')
    print('M3.9.3 MANIFEST REGISTRATION: PASS')


def main():
    p = argparse.ArgumentParser(description='Verify and register pinned 294-file HF manifest for 276 raw files')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--commit', action='store_true')
    p.add_argument('--confirm-manifest', action='store_true')
    args = p.parse_args()
    if args.commit != args.confirm_manifest:
        p.error('--commit requires --confirm-manifest; never use confirmation with --plan')
    import batch_ingestion as engine
    if (engine.REPO, engine.REV, engine.ROOT.resolve()) != (REPO, REV, ROOT.resolve()):
        raise RuntimeError('Engine repository/revision/root mismatch')
    source_bytes, sha, all_files = check_original(engine)
    report = check_integrity_report()
    sid = db_preflight(engine)
    if report.get('snapshot_id') != sid:
        raise RuntimeError('M3.9.2 report references a different snapshot')
    print('=== M3.9.3 | PINNED MANIFEST REGISTRATION ===')
    print('Repo:', REPO)
    print('Revision:', REV)
    print('Snapshot ID:', sid)
    print('Full HF manifest paths:', len(all_files))
    print('Raw-scope paths:', RAW)
    print('Excluded from raw-scope:', len(all_files) - RAW)
    print('Original manifest SHA256:', sha)
    print('Original manifest bytes:', len(source_bytes))
    print('Archive integrity checkpoint:', '276/276 PASS; 833030129 bytes')
    print('Database target:', DB)
    if args.plan:
        print('Mode: PLAN (read-only local manifest/report + PostgreSQL; no writes/network)')
        print('Snapshot status: discovered (UNCHANGED)')
        print('M3.9.3 PLAN: PASS')
    else:
        print('Mode: COMMIT (explicit confirmation)', flush=True)
        commit(engine, source_bytes, sha, sid)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'M3.9.3: ERROR | {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
