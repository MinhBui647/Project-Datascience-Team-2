"""M4.4.5: transactional, file-version-aware handover checkpoint service.

--plan: read-only metadata/schema and optional export eligibility check.
--register-export: only verified, COMPLETE single-file JSONL handovers.
--ack-received / --ack-processed / --mark-failed: explicit, evidenced updates.

This is a LOCAL TRUSTED OPERATOR utility. --actor is an audit label, NOT
authentication of Cleaning member identity. Never fabricate acknowledgements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
DB = 'epl_raw_storage_v2'
SHA = re.compile(r'^[a-f0-9]{64}$')
VALID = {
    'PENDING': {'EXPORTED'},
    'EXPORTED': {'RECEIVED', 'FAILED'},
    'RECEIVED': {'PROCESSED', 'FAILED'},
    'FAILED': {'EXPORTED'},
    'PROCESSED': set(),
}


def config():
    cfg = {
        **dotenv_values(ROOT / '.env', encoding='utf-8-sig'),
        **dotenv_values(ROOT / '.env.v2', encoding='utf-8-sig'),
    }
    required = ('DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME')
    if any(not cfg.get(k) for k in required) or cfg['DB_NAME'] != DB:
        raise RuntimeError('Database configuration missing or not epl_raw_storage_v2')
    return cfg


def connect(read_only=True):
    c = config()
    return psycopg.connect(host=c['DB_HOST'], port=int(c['DB_PORT']),
                           user=c['DB_USER'], password=c['DB_PASSWORD'],
                           dbname=DB,
                           options='-c default_transaction_read_only=on' if read_only else None)


def digest(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as fp:
        for buf in iter(lambda: fp.read(1024 * 1024), b''):
            h.update(buf)
    return h.hexdigest()


def load_json_file(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    obj = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(obj, dict):
        raise ValueError('Expected JSON object')
    return obj


def guard(cur):
    cur.execute('SELECT current_database()')
    if cur.fetchone()[0] != DB:
        raise RuntimeError('Wrong database')
    cur.execute('''SELECT to_regclass('handover.delivery_checkpoints')::text,
                          to_regclass('handover.delivery_events')::text''')
    if cur.fetchone() != ('handover.delivery_checkpoints', 'handover.delivery_events'):
        raise RuntimeError('Handover schema/tables not installed')


def file_context(cur, snapshot_id, path):
    cur.execute('''
        SELECT f.file_id, f.record_count, f.file_format, f.content_sha256::text,
               s.hf_revision, s.status, f.parse_state, f.archive_state
        FROM raw.snapshot_runs s
        JOIN raw.file_observations o ON o.snapshot_id=s.snapshot_id
        JOIN raw.raw_files f ON f.file_id=o.file_id
        WHERE s.snapshot_id=%s AND f.relative_path=%s
          AND o.presence_status='linked' AND o.repo_id=s.repo_id
    ''', (snapshot_id, path))
    row = cur.fetchone()
    if row is None:
        raise ValueError('Requested file is not linked to this snapshot')
    file_id, count, fmt, sha, revision, status, parse_state, archive_state = row
    if status != 'completed' or parse_state != 'success' or archive_state != 'verified':
        raise ValueError('Snapshot or file is not completed/verified')
    return dict(file_id=file_id, record_count=count, file_format=fmt,
                content_sha256=sha.strip(), revision=revision)


def manifest_context(manifest_path, cur, *, strict=False):
    p = Path(manifest_path).resolve()
    mf = load_json_file(p)
    required = ('snapshot_id', 'source_path', 'hf_revision', 'record_count',
                'jsonl_file', 'jsonl_sha256', 'scope')
    for key in required:
        if key not in mf:
            raise ValueError(f'Manifest missing {key}')
    if not isinstance(mf['snapshot_id'], int) or mf['snapshot_id'] < 1:
        raise ValueError('Invalid snapshot_id')
    if not isinstance(mf['source_path'], str) or not mf['source_path']:
        raise ValueError('Invalid source_path')
    if not isinstance(mf['record_count'], int) or mf['record_count'] < 0:
        raise ValueError('Invalid record_count')
    info = file_context(cur, mf['snapshot_id'], mf['source_path'])
    qualified = (mf['scope'] == 'full_file'
                 and mf['record_count'] == info['record_count']
                 and mf['hf_revision'] == info['revision']
                 and mf.get('file_id') == info['file_id']
                 and bool(SHA.fullmatch(str(mf['jsonl_sha256']))))
    if strict and not qualified:
        raise ValueError('NOT A VERIFIED FULL-FILE EXPORT: sample/missing file_id/count/revision/scope mismatch')
    return p, mf, info, qualified


def validate_full_jsonl(manifest_path, mf, info):
    # A relative single-basename file is required: prevent path traversal.
    name = mf['jsonl_file']
    if not isinstance(name, str) or Path(name).name != name or not name.endswith('.jsonl'):
        raise ValueError('jsonl_file must be a local .jsonl basename')
    path = manifest_path.parent / name
    if not path.is_file() or path.is_symlink():
        raise ValueError('Export JSONL missing or symlink')
    if digest(path) != mf['jsonl_sha256']:
        raise ValueError('Export JSONL SHA256 mismatch')
    if 'jsonl_bytes' in mf and path.stat().st_size != mf['jsonl_bytes']:
        raise ValueError('Export bytes mismatch')
    total, previous, first = 0, None, None
    with path.open('r', encoding='utf-8') as fp:
        for line in fp:
            r = json.loads(line)
            rid = r.get('raw_record_id')
            if not isinstance(rid, int) or (previous is not None and rid <= previous):
                raise ValueError('Raw IDs not strictly increasing')
            if (r.get('file_id') != info['file_id'] or
                r.get('snapshot_id') != mf['snapshot_id'] or
                r.get('source_path') != mf['source_path'] or
                r.get('source_revision') != info['revision']):
                raise ValueError('Export row provenance mismatch')
            if not isinstance(r.get('raw_data'), (dict, list)):
                raise ValueError('Invalid raw_data')
            first = rid if first is None else first
            previous = rid
            total += 1
    if total != info['record_count'] or total != mf['record_count']:
        raise ValueError('Incomplete JSONL export')
    if (mf.get('first_raw_record_id') != first or
        mf.get('last_raw_record_id') != previous):
        raise ValueError('First/last raw record ID mismatch')
    return digest(manifest_path)


def plan(manifest_path=None):
    with connect(read_only=True) as conn, conn.cursor() as cur:
        guard(cur)
        cur.execute('SELECT count(*) FROM handover.delivery_checkpoints')
        checkpoints = cur.fetchone()[0]
        cur.execute('SELECT count(*) FROM handover.delivery_events')
        events = cur.fetchone()[0]
        cur.execute("SELECT status FROM raw.snapshot_runs WHERE snapshot_id=4")
        snapshot = cur.fetchone()
        if snapshot is None or snapshot[0] != 'completed':
            raise RuntimeError('Snapshot #4 must remain completed')
        verdict = 'NOT CHECKED'
        if manifest_path:
            _p, mf, info, eligible = manifest_context(manifest_path, cur)
            verdict = 'ELIGIBLE METADATA (full JSONL bytes NOT scanned in PLAN)' if eligible else 'NOT ELIGIBLE (sample/partial or metadata mismatch)'
    print('=== M4.4.5 | HANDOVER WRITE SERVICE PLAN ===')
    print('Database:', DB)
    print('Snapshot #4: completed')
    print('Checkpoint rows:', checkpoints)
    print('Event rows:', events)
    print('Transition policy: PENDING -> EXPORTED -> RECEIVED -> PROCESSED')
    print('Failed delivery: EXPORTED/RECEIVED -> FAILED -> EXPORTED')
    print('Export registration requires scope=full_file and complete count')
    print('Export manifest eligibility:', verdict)
    print('Explicit Cleaning acknowledgement required for RECEIVED/PROCESSED')
    print('Actor label alone does not authenticate the actor')
    print('Mode: PLAN (read-only; no writes)')
    print('M4.4.5 HANDOVER SERVICE PLAN: PASS')


def register_export(manifest_path, consumer, actor):
    with connect(read_only=False) as conn:
        with conn.cursor() as cur:
            guard(cur)
            path, mf, info, eligible = manifest_context(manifest_path, cur, strict=True)
            manifest_sha = validate_full_jsonl(path, mf, info)
            cur.execute('''
                SELECT checkpoint_id, status, export_manifest_sha256
                FROM handover.delivery_checkpoints
                WHERE consumer_code=%s AND file_id=%s FOR UPDATE
            ''', (consumer, info['file_id']))
            old = cur.fetchone()
            if old is None:
                cur.execute('''
                    INSERT INTO handover.delivery_checkpoints
                      (consumer_code,file_id,first_snapshot_id,status)
                    VALUES (%s,%s,%s,'PENDING') RETURNING checkpoint_id
                ''', (consumer, info['file_id'], mf['snapshot_id']))
                checkpoint_id = cur.fetchone()[0]
                cur.execute('''INSERT INTO handover.delivery_events
                  (checkpoint_id,from_status,to_status,actor,evidence)
                  VALUES (%s,NULL,'PENDING',%s,%s)''',
                  (checkpoint_id, actor, Jsonb({'reason':'initialization'})))
                current, previous_sha = 'PENDING', None
            else:
                checkpoint_id, current, previous_sha = old
            if current == 'EXPORTED' and previous_sha == manifest_sha:
                print('M4.4.5 EXPORT ALREADY REGISTERED: IDEMPOTENT (no writes)')
                return
            if 'EXPORTED' not in VALID[current]:
                raise ValueError('Forbidden transition: '+current+' -> EXPORTED')
            cur.execute('''UPDATE handover.delivery_checkpoints SET
              status='EXPORTED',export_manifest_sha256=%s,receipt_evidence=NULL,
              processing_evidence=NULL,last_error=NULL,updated_at=now()
              WHERE checkpoint_id=%s''', (manifest_sha, checkpoint_id))
            cur.execute('''INSERT INTO handover.delivery_events
              (checkpoint_id,from_status,to_status,actor,evidence)
              VALUES (%s,%s,'EXPORTED',%s,%s)''',
              (checkpoint_id, current, actor, Jsonb({
                  'manifest_sha256':manifest_sha, 'source_path':mf['source_path'],
                  'record_count':mf['record_count'], 'jsonl_sha256':mf['jsonl_sha256']})))
    print('M4.4.5 FULL-FILE EXPORT REGISTERED: PASS')


def apply_event(mode, consumer, file_id, actor, evidence_path):
    evidence = load_json_file(Path(evidence_path).resolve())
    goal = {'ack-received':'RECEIVED', 'ack-processed':'PROCESSED',
            'mark-failed':'FAILED'}[mode]
    action = {'RECEIVED':'received','PROCESSED':'processed','FAILED':'failed'}[goal]
    mandatory = {'consumer_code', 'file_id', 'acknowledgement_type', 'acknowledged_by'}
    if not mandatory.issubset(evidence):
        raise ValueError('Evidence must contain consumer_code, file_id, acknowledgement_type, acknowledged_by')
    if evidence['consumer_code'] != consumer or evidence['file_id'] != file_id or evidence['acknowledgement_type'] != action:
        raise ValueError('Evidence identity/action mismatch')
    if not isinstance(evidence['acknowledged_by'], str) or not evidence['acknowledged_by'].strip():
        raise ValueError('Missing acknowledgement author')
    if goal == 'PROCESSED' and not evidence.get('cleaned_output_reference'):
        raise ValueError('PROCESSED must reference cleaned output evidence')
    if goal == 'FAILED' and not evidence.get('reason'):
        raise ValueError('FAILED requires reason')
    if goal == 'RECEIVED' and not evidence.get('export_manifest_sha256'):
        raise ValueError('RECEIVED must identify the export manifest')
    with connect(read_only=False) as conn:
        with conn.cursor() as cur:
            guard(cur)
            cur.execute('''SELECT checkpoint_id,status,export_manifest_sha256
                FROM handover.delivery_checkpoints
                WHERE consumer_code=%s AND file_id=%s FOR UPDATE''', (consumer,file_id))
            old = cur.fetchone()
            if old is None:
                raise ValueError('No previously exported checkpoint')
            checkpoint_id, current, manifest_sha = old
            if goal not in VALID[current]:
                raise ValueError(f'Forbidden transition: {current} -> {goal}')
            if goal == 'RECEIVED' and evidence['export_manifest_sha256'] != manifest_sha:
                raise ValueError('Receipt references wrong manifest')
            if goal == 'PROCESSED' and evidence.get('export_manifest_sha256') != manifest_sha:
                raise ValueError('Processing references wrong manifest')
            if goal == 'FAILED':
                cur.execute('''UPDATE handover.delivery_checkpoints
                    SET status='FAILED',last_error=%s,updated_at=now()
                    WHERE checkpoint_id=%s''', (str(evidence['reason']),checkpoint_id))
            elif goal == 'RECEIVED':
                cur.execute('''UPDATE handover.delivery_checkpoints
                    SET status='RECEIVED',receipt_evidence=%s,updated_at=now()
                    WHERE checkpoint_id=%s''', (Jsonb(evidence),checkpoint_id))
            else:
                cur.execute('''UPDATE handover.delivery_checkpoints
                    SET status='PROCESSED',processing_evidence=%s,updated_at=now()
                    WHERE checkpoint_id=%s''', (Jsonb(evidence),checkpoint_id))
            cur.execute('''INSERT INTO handover.delivery_events
              (checkpoint_id,from_status,to_status,actor,evidence)
              VALUES (%s,%s,%s,%s,%s)''',
              (checkpoint_id,current,goal,actor,Jsonb(evidence)))
    print('M4.4.5 HANDOVER EVENT REGISTERED: PASS | state='+goal)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--plan', action='store_true')
    g.add_argument('--register-export', action='store_true')
    g.add_argument('--ack-received', action='store_true')
    g.add_argument('--ack-processed', action='store_true')
    g.add_argument('--mark-failed', action='store_true')
    p.add_argument('--manifest')
    p.add_argument('--consumer', default='cleaning_member_2')
    p.add_argument('--actor')
    p.add_argument('--file-id', type=int)
    p.add_argument('--evidence-file')
    p.add_argument('--confirm-write', action='store_true')
    a = p.parse_args()
    if a.plan:
        if a.confirm_write:
            p.error('--confirm-write cannot be used with PLAN')
        return plan(a.manifest)
    if not a.confirm_write or not a.actor or not a.actor.strip():
        p.error('Write requires --confirm-write and nonempty --actor')
    if not a.consumer or not a.consumer.strip():
        p.error('Nonempty consumer is required')
    if a.register_export:
        if not a.manifest:
            p.error('Register export requires --manifest')
        register_export(a.manifest, a.consumer, a.actor)
    else:
        if not a.file_id or not a.evidence_file:
            p.error('Acknowledgement/failure requires --file-id and --evidence-file')
        mode = ('ack-received' if a.ack_received else
                'ack-processed' if a.ack_processed else 'mark-failed')
        apply_event(mode, a.consumer, a.file_id, a.actor, a.evidence_file)


if __name__ == '__main__':
    main()
