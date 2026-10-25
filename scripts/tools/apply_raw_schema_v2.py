"""Create EPL Raw Storage V2 schema once, only in the explicitly chosen DB.

Copy to scripts/tools/apply_raw_schema_v2.py; SQL at sql/schema/raw_schema_v2.sql.
Uses .env for shared connection credentials and .env.v2 for database override.
This script contains no hard-coded credentials and writes only DDL.
"""
from pathlib import Path
import sys

import psycopg
from dotenv import dotenv_values

EXPECTED_DB = "epl_raw_storage_v2"
EXPECTED_TABLES = {
    "raw_sources", "snapshot_runs", "raw_files", "file_observations",
    "archive_members", "raw_records", "ingestion_logs",
}
ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = ROOT / "sql" / "schema" / "raw_schema_v2.sql"


def main() -> int:
    env_base = ROOT / ".env"
    env_v2 = ROOT / ".env.v2"
    if not env_base.is_file() or not env_v2.is_file():
        raise RuntimeError("Missing .env or .env.v2 in project root")
    if not SQL_PATH.is_file():
        raise RuntimeError(f"Missing SQL file: {SQL_PATH}")

    cfg = {
        **dotenv_values(env_base, encoding="utf-8-sig"),
        **dotenv_values(env_v2, encoding="utf-8-sig"),
    }
    needed = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in needed if not cfg.get(key)]
    if missing:
        raise RuntimeError(f"Missing configuration variable names: {missing}")
    if cfg["DB_NAME"] != EXPECTED_DB:
        raise RuntimeError("SAFETY STOP: .env.v2 DB_NAME is not epl_raw_storage_v2")

    print("=== RAW STORAGE V2 SCHEMA BOOTSTRAP ===")
    with psycopg.connect(
        host=cfg["DB_HOST"],
        port=int(cfg["DB_PORT"]),
        dbname=cfg["DB_NAME"],
        user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        connect_timeout=10,
        options="-c statement_timeout=30000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            actual_db = cur.fetchone()[0]
            print("Connected database:", actual_db)
            if actual_db != EXPECTED_DB:
                raise RuntimeError("SAFETY STOP: actual PostgreSQL database is wrong")

            # DDL is one transaction: on any failure conn context rolls it back.
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='raw')")
            if cur.fetchone()[0]:
                raise RuntimeError("SAFETY STOP: raw schema already exists; no changes made")

            cur.execute(SQL_PATH.read_text(encoding="utf-8-sig"), prepare=False)

            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'raw' AND table_type = 'BASE TABLE'
            """)
            tables = {row[0] for row in cur.fetchall()}
            if tables != EXPECTED_TABLES:
                raise RuntimeError(f"Verification failed: observed tables = {sorted(tables)}")

            cur.execute("""
                SELECT count(*)
                FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'raw' AND c.contype = 'f'
            """)
            fk_count = cur.fetchone()[0]
            if fk_count != 8:
                raise RuntimeError(f"Verification failed: expected 8 foreign keys, found {fk_count}")

            # Ensure this bootstrap has not accidentally inserted any data.
            for name in sorted(EXPECTED_TABLES):
                cur.execute(f"SELECT count(*) FROM raw.{name}")
                if cur.fetchone()[0] != 0:
                    raise RuntimeError(f"Verification failed: unexpected data in raw.{name}")

            print("Tables created:", ", ".join(sorted(tables)))
            print("Foreign keys:", fk_count)
            print("Row count per table: 0")
            # Context manager commits only after everything succeeds.

    print("DDL transaction: COMMITTED")
    print("V2 schema bootstrap: PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, psycopg.Error, OSError, ValueError) as exc:
        print("V2 schema bootstrap: FAILED (transaction rolled back)")
        print(f"Reason: {exc.__class__.__name__}: {exc}")
        sys.exit(1)
