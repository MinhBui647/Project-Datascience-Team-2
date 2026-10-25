"""Safely PLAN or install isolated M4.4.4 handover tables in Raw Storage V2.

Use --plan first. --apply --confirm-schema performs one explicit DDL transaction.
No checkpoint rows are seeded and raw.* tables are never modified.
"""
from __future__ import annotations

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = PROJECT_ROOT / 'sql' / 'm4_4_handover_schema_v2.sql'
DB_NAME = 'epl_raw_storage_v2'
EXPECTED_SNAPSHOT = 4
EXPECTED_LINKS = 276


def get_configuration() -> dict[str, str]:
    from dotenv import dotenv_values
    cfg = {
        **dotenv_values(PROJECT_ROOT / '.env', encoding='utf-8-sig'),
        **dotenv_values(PROJECT_ROOT / '.env.v2', encoding='utf-8-sig'),
    }
    required = ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise RuntimeError('Missing DB configuration: ' + ', '.join(missing))
    if cfg['DB_NAME'] != DB_NAME:
        raise RuntimeError(f'Database guard: expected {DB_NAME!r}, got {cfg["DB_NAME"]!r}')
    return cfg


def preflight(cur) -> dict[str, object]:
    cur.execute('SELECT current_database()')
    db = cur.fetchone()[0]
    if db != DB_NAME:
        raise RuntimeError(f'Connected to wrong database: {db}')
    cur.execute('''
        SELECT status, manifest_sha256
        FROM raw.snapshot_runs WHERE snapshot_id = %s
    ''', (EXPECTED_SNAPSHOT,))
    snapshot = cur.fetchone()
    if snapshot is None or snapshot[0] != 'completed' or not snapshot[1]:
        raise RuntimeError('Snapshot #4 must be completed with a registered manifest')
    cur.execute('''
        SELECT count(*) FROM raw.file_observations
        WHERE snapshot_id = %s AND presence_status = 'linked' AND file_id IS NOT NULL
    ''', (EXPECTED_SNAPSHOT,))
    linked = cur.fetchone()[0]
    if linked != EXPECTED_LINKS:
        raise RuntimeError(f'Baseline linked files mismatch: {linked} != {EXPECTED_LINKS}')
    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'handover')")
    schema_exists = cur.fetchone()[0]
    cur.execute('''
        SELECT to_regclass('handover.delivery_checkpoints')::text,
               to_regclass('handover.delivery_events')::text
    ''')
    tables = cur.fetchone()
    return {'database': db, 'snapshot_status': snapshot[0],
            'linked': linked, 'schema_exists': schema_exists, 'tables': tables}


def connect(cfg, read_only: bool):
    import psycopg
    options = '-c default_transaction_read_only=on' if read_only else None
    return psycopg.connect(
        host=cfg['DB_HOST'], port=int(cfg['DB_PORT']), dbname=DB_NAME,
        user=cfg['DB_USER'], password=cfg['DB_PASSWORD'], options=options,
    )


def run_plan(cfg) -> None:
    if not SQL_PATH.is_file():
        raise FileNotFoundError(f'Migration SQL not found: {SQL_PATH}')
    with connect(cfg, read_only=True) as conn, conn.cursor() as cur:
        state = preflight(cur)
    print('=== M4.4.4 | HANDOVER SCHEMA INSTALL PLAN ===')
    print('Database:', state['database'])
    print('Baseline snapshot:', EXPECTED_SNAPSHOT, '|', state['snapshot_status'])
    print('Linked file versions:', state['linked'])
    print('Handover schema:', 'ALREADY EXISTS' if state['schema_exists'] else 'NOT INSTALLED')
    print('Checkpoint/event tables:', state['tables'])
    print('Migration file:', SQL_PATH.relative_to(PROJECT_ROOT).as_posix())
    print('Operation: create handover schema + 2 empty tables + 2 indexes')
    print('Seed/acknowledgement: NONE')
    print('Raw schema updates: NONE')
    print('Mode: PLAN (read-only; no writes)')
    if state['schema_exists'] or any(state['tables']):
        raise RuntimeError('Existing handover schema/tables: stop and review before any migration')
    print('M4.4.4 SCHEMA INSTALL PLAN: PASS')


def run_apply(cfg) -> None:
    ddl = SQL_PATH.read_text(encoding='utf-8-sig')
    with connect(cfg, read_only=False) as conn:
        with conn.cursor() as cur:
            state = preflight(cur)
            if state['schema_exists'] or any(state['tables']):
                raise RuntimeError('Existing handover schema/tables: refusing overwrite')
            # psycopg3 supports multi-statement execution without parameters.
            # The whole migration is inside one PostgreSQL transaction.
            cur.execute(ddl)
            cur.execute('''
                SELECT to_regclass('handover.delivery_checkpoints')::text,
                       to_regclass('handover.delivery_events')::text
            ''')
            a, b = cur.fetchone()
            if not a or not b:
                raise RuntimeError('Migration did not create both required tables')
            for name in ('delivery_checkpoints', 'delivery_events'):
                cur.execute(f'SELECT count(*) FROM handover.{name}')
                if cur.fetchone()[0] != 0:
                    raise RuntimeError(f'handover.{name} must be empty after installation')
        # connection context commits on success and rolls back on exception
    print('=== M4.4.4 | HANDOVER SCHEMA INSTALL ===')
    print('Database:', DB_NAME)
    print('Schema: handover')
    print('Tables: delivery_checkpoints, delivery_events (both EMPTY)')
    print('Raw schema updates: NONE')
    print('Snapshot #4: completed (UNCHANGED)')
    print('M4.4.4 SCHEMA INSTALL: PASS')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--apply', action='store_true')
    ap.add_argument('--confirm-schema', action='store_true')
    args = ap.parse_args()
    cfg = get_configuration()
    if args.plan:
        if args.confirm_schema:
            ap.error('--confirm-schema is only valid with --apply')
        run_plan(cfg)
    else:
        if not args.confirm_schema:
            ap.error('Schema installation requires --apply --confirm-schema')
        run_apply(cfg)


if __name__ == '__main__':
    main()
