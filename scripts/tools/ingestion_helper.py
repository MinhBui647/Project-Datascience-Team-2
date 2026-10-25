#!/usr/bin/env python3
r"""M3.3 | Ingest one verified, pinned FPL archive object into PostgreSQL V2.

Place at scripts/tools/ingestion_helper.py and run from any working directory:
  .\.venv_v2\Scripts\python.exe scripts\tools\ingestion_helper.py --dry-run
  .\.venv_v2\Scripts\python.exe scripts\tools\ingestion_helper.py --commit

This is deliberately a single-file PILOT, NOT an all-276-file importer.
The payload is committed atomically; error logging uses a separate transaction.
Never connects to a DB other than epl_raw_storage_v2.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import dotenv_values
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from raw_parser import ArchiveMember, ParsedRecord, iter_parse_events  # noqa: E402

EXPECTED_DB = "epl_raw_storage_v2"
EXPECTED_REPO = "Toibinguz/epl-premier-league-dataset"
EXPECTED_REVISION = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
EXPECTED_PATH = "2026_2027_new/fpl_fixtures_latest.json"
EXPECTED_SHA256 = "2e156340aada4b71aaa96c0946ba15ee62c87a1975e9f423f7f8400015e6031e"
EXPECTED_RECORDS = 380
RECEIPT = ROOT / "reports" / "m3_1_archive_pilot_receipt.json"
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
ARCHIVE_ROOT = (ROOT / "raw-archive-v2" / "objects").resolve()
EXPECTED_TABLES = frozenset({
    "raw_sources", "snapshot_runs", "raw_files", "file_observations",
    "archive_members", "raw_records", "ingestion_logs",
})


def sha256_size(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def prepare_pilot() -> dict[str, Any]:
    """Validate source provenance and parse ALL records before any DB writes."""
    if not RECEIPT.is_file() or not CATALOG.is_file():
        raise RuntimeError("Missing M3.1 receipt or M1 raw scope catalog")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8-sig"))
    if not isinstance(receipt, dict):
        raise RuntimeError("Malformed archive receipt")
    expected = {
        "repo_id": EXPECTED_REPO,
        "pinned_commit": EXPECTED_REVISION,
        "relative_path": EXPECTED_PATH,
        "format": "json",
        "sha256": EXPECTED_SHA256,
        "size_bytes": 288194,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"M3.1 receipt mismatch: {key}")

    with CATALOG.open("r", encoding="utf-8-sig", newline="") as stream:
        catalog_rows = [r for r in csv.DictReader(stream) if r["relative_path"] == EXPECTED_PATH]
    if len(catalog_rows) != 1:
        raise RuntimeError("Pilot file absent/duplicated in source catalog")
    entry = catalog_rows[0]
    if (entry.get("hf_revision") != EXPECTED_REVISION or
        entry.get("group") != "2026_2027_new" or
        entry.get("source_hint") != "fpl" or
        entry.get("format") != "json" or
        int(entry.get("size_bytes") or -1) != 288194):
        raise RuntimeError("Pinned source catalog mismatch")

    archive_relative = receipt.get("archive_path")
    if not isinstance(archive_relative, str) or not archive_relative:
        raise RuntimeError("Archive receipt path absent")
    archive_path = (ROOT / archive_relative).resolve()
    if not archive_path.is_relative_to(ARCHIVE_ROOT):
        raise RuntimeError("Archive path is outside the V2 archive")
    canonical = ARCHIVE_ROOT / EXPECTED_SHA256[:2] / EXPECTED_SHA256
    if archive_path != canonical:
        raise RuntimeError("Archive object path is not content-addressed as expected")
    if not archive_path.is_file():
        raise RuntimeError("Archived FPL file does not exist")
    sha, nbytes = sha256_size(archive_path)
    if (sha, nbytes) != (EXPECTED_SHA256, 288194):
        raise RuntimeError("Archived bytes no longer match M3.1 receipt")

    records: list[ParsedRecord] = []
    locators: set[str] = set()
    for event in iter_parse_events(archive_path, "json"):
        if not isinstance(event, ParsedRecord) or event.member_index is not None:
            raise RuntimeError("Unexpected parser event in plain JSON file")
        if event.record_kind != "json_item" or event.record_locator in locators:
            raise RuntimeError("Unexpected JSON record kind or duplicate locator")
        locators.add(event.record_locator)
        records.append(event)
    if len(records) != EXPECTED_RECORDS or {
        e.record_locator for e in records
    } != {f"item:{i}" for i in range(EXPECTED_RECORDS)}:
        raise RuntimeError("FPL parse result is not 380 indexed fixture records")

    # Keep season/league hints as evidence, not assumed verified truth.
    evidence = {
        "source_hint": entry["source_hint"],
        "season_hint": entry.get("season_hint") or None,
        "classification": "path_hint_only",
        "hf_revision_observed": EXPECTED_REVISION,
    }
    return {
        "archive_path": archive_path,
        "archive_relative": archive_relative.replace("\\", "/"),
        "sha256": sha,
        "nbytes": nbytes,
        "records": records,
        "evidence": evidence,
        "entry": entry,
    }


def connection_parameters() -> dict[str, Any]:
    env = ROOT / ".env"
    env_v2 = ROOT / ".env.v2"
    if not env.is_file() or not env_v2.is_file():
        raise RuntimeError("Missing .env/.env.v2 in project root")
    base = dotenv_values(env, encoding="utf-8-sig")
    override = dotenv_values(env_v2, encoding="utf-8-sig")
    # Isolate the V2 config strictly: no other setting can override credentials
    # unexpectedly, and database target must be explicitly specified in V2.
    if override.get("DB_NAME") != EXPECTED_DB:
        raise RuntimeError("SAFETY STOP: .env.v2 must set DB_NAME=epl_raw_storage_v2")
    config = {**base, **override}
    for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        if not config.get(key):
            raise RuntimeError(f"Missing database configuration key: {key}")
    return dict(
        host=config["DB_HOST"], port=int(config["DB_PORT"]),
        dbname=config["DB_NAME"], user=config["DB_USER"],
        password=config["DB_PASSWORD"], connect_timeout=10,
        options="-c statement_timeout=60000",
        autocommit=True,
    )


def db_guard(cur: psycopg.Cursor) -> None:
    cur.execute("SELECT current_database()")
    if cur.fetchone()[0] != EXPECTED_DB:
        raise RuntimeError("SAFETY STOP: connection is NOT the V2 database")
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='raw' AND table_type='BASE TABLE'
    """)
    present = {r[0] for r in cur.fetchall()}
    if present != EXPECTED_TABLES:
        raise RuntimeError(f"SAFETY STOP: Unexpected V2 raw tables: {sorted(present)}")


def create_source(cur: psycopg.Cursor) -> int:
    cur.execute("""
        INSERT INTO raw.raw_sources (source_code, source_name, description)
        VALUES (%s, %s, %s)
        ON CONFLICT (source_code) DO NOTHING
    """, ("fpl", "FPL (folder classification)", "FPL fixture source, raw EPL project"))
    cur.execute("SELECT source_id FROM raw.raw_sources WHERE source_code=%s", ("fpl",))
    return cur.fetchone()[0]


def create_snapshot(cur: psycopg.Cursor) -> int:
    cur.execute("""
        INSERT INTO raw.snapshot_runs
        (repo_id, hf_revision, manifest_file_count, raw_scope_file_count, status)
        VALUES (%s, %s, 294, 276, 'discovered')
        ON CONFLICT (repo_id, hf_revision) DO NOTHING
    """, (EXPECTED_REPO, EXPECTED_REVISION))
    cur.execute("""
        SELECT snapshot_id, manifest_file_count, raw_scope_file_count
        FROM raw.snapshot_runs WHERE repo_id=%s AND hf_revision=%s
    """, (EXPECTED_REPO, EXPECTED_REVISION))
    snapshot_id, full_count, raw_count = cur.fetchone()
    if full_count not in (None, 294) or raw_count not in (None, 276):
        raise RuntimeError("Existing snapshot counts conflict with pinned manifest")
    return snapshot_id


def get_or_create_file(cur: psycopg.Cursor, pilot: dict[str, Any], source_id: int) -> tuple[int, str]:
    cur.execute("""
        SELECT file_id, file_format, file_size_bytes, archive_path,
               parse_state, record_count, archive_state
        FROM raw.raw_files
        WHERE repo_id=%s AND relative_path=%s AND content_sha256=%s
    """, (EXPECTED_REPO, EXPECTED_PATH, EXPECTED_SHA256))
    row = cur.fetchone()
    if row is not None:
        file_id, fmt, size, archive_path, state, num_records, archive_state = row
        if (fmt, size, archive_path, archive_state) != (
            "json", pilot["nbytes"], pilot["archive_relative"], "verified"
        ):
            raise RuntimeError("Existing file metadata differs from verified pilot")
        if state == "success":
            if num_records != EXPECTED_RECORDS:
                raise RuntimeError("Already parsed file has unexpected record_count")
            return file_id, "existing_complete"
        raise RuntimeError(
            f"Existing file_id={file_id} parse_state={state!r} requires manual review; "
            "not modifying or duplicating the row"
        )
    cur.execute("""
        INSERT INTO raw.raw_files (
            repo_id, relative_path, source_id, file_format, content_sha256,
            file_size_bytes, archive_path, archive_state, parse_state,
            league_scope, season_scope, metadata_evidence
        ) VALUES (%s,%s,%s,'json',%s,%s,%s,'verified','pending','unknown','unknown',%s)
        RETURNING file_id
    """, (
        EXPECTED_REPO, EXPECTED_PATH, source_id, EXPECTED_SHA256,
        pilot["nbytes"], pilot["archive_relative"], Jsonb(pilot["evidence"]),
    ))
    return cur.fetchone()[0], "new"


def link_observation(cur: psycopg.Cursor, snapshot_id: int, file_id: int,
                     entry: dict[str, str]) -> None:
    cur.execute("""
        INSERT INTO raw.file_observations (
            snapshot_id, repo_id, relative_path, file_id,
            presence_status, manifest_size_bytes, git_blob_id, lfs_sha256
        ) VALUES (%s,%s,%s,%s,'linked',%s,%s,%s)
        ON CONFLICT (snapshot_id,relative_path) DO NOTHING
    """, (
        snapshot_id, EXPECTED_REPO, EXPECTED_PATH, file_id,
        int(entry["size_bytes"]), entry.get("git_blob_id") or None,
        entry.get("lfs_sha256") or None,
    ))
    cur.execute("""
        SELECT file_id, presence_status FROM raw.file_observations
        WHERE snapshot_id=%s AND relative_path=%s
    """, (snapshot_id, EXPECTED_PATH))
    attached_id, status = cur.fetchone()
    if (attached_id, status) != (file_id, "linked"):
        raise RuntimeError("Existing snapshot observation conflicts with this file version")


def verify_integrity(cur: psycopg.Cursor, file_id: int) -> int:
    cur.execute("SELECT parse_state, record_count FROM raw.raw_files WHERE file_id=%s", (file_id,))
    state, declared_count = cur.fetchone()
    cur.execute("""
        SELECT COUNT(*), COUNT(DISTINCT record_locator),
               COUNT(*) FILTER (WHERE record_kind='json_item'),
               COUNT(*) FILTER (WHERE member_id IS NOT NULL)
        FROM raw.raw_records WHERE file_id=%s
    """, (file_id,))
    num, unique_locators, num_items, with_member = cur.fetchone()
    if (state != "success" or declared_count != EXPECTED_RECORDS or
        num != EXPECTED_RECORDS or unique_locators != EXPECTED_RECORDS or
        num_items != EXPECTED_RECORDS or with_member != 0):
        raise RuntimeError("DB verification failed: fixture record count/metadata mismatch")
    cur.execute("""
        SELECT COUNT(*) FROM raw.raw_records
        WHERE file_id=%s AND record_locator LIKE 'item:%%'
    """, (file_id,))
    if cur.fetchone()[0] != EXPECTED_RECORDS:
        raise RuntimeError("DB locators do not look like FPL array items")
    return num


def write_log(cur: psycopg.Cursor, batch_id: uuid.UUID, status: str,
              rows_inserted: int, file_id: int | None = None,
              snapshot_id: int | None = None, error: str | None = None) -> None:
    cur.execute("""
        INSERT INTO raw.ingestion_logs (
            batch_id,snapshot_id,file_id,relative_path,status,rows_inserted,
            finished_at,error_message
        ) VALUES (%s,%s,%s,%s,%s,%s,now(),%s)
    """, (
        batch_id, snapshot_id, file_id, EXPECTED_PATH, status,
        rows_inserted, (error or "")[:1000] or None,
    ))


def insert_fixture_records(cur: psycopg.Cursor, file_id: int,
                           records: list[ParsedRecord]) -> None:
    for row in records:
        cur.execute("""
            INSERT INTO raw.raw_records (file_id,record_locator,record_kind,raw_data)
            VALUES (%s,%s,%s,%s)
        """, (file_id, row.record_locator, row.record_kind, Jsonb(row.raw_data)))
    cur.execute("""
        UPDATE raw.raw_files SET parse_state='success',record_count=%s
        WHERE file_id=%s
    """, (len(records), file_id))


def ingest(conn: psycopg.Connection, pilot: dict[str, Any],
           batch_id: uuid.UUID) -> tuple[str, int, int, int]:
    """One transaction for all objects and success/skip log."""
    with conn.transaction():
        with conn.cursor() as cur:
            db_guard(cur)
            # Serialize concurrent attempts for the same logical file across runs.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (
                EXPECTED_REPO + ":" + EXPECTED_PATH,
            ))
            source_id = create_source(cur)
            snapshot_id = create_snapshot(cur)
            file_id, state = get_or_create_file(cur, pilot, source_id)
            link_observation(cur, snapshot_id, file_id, pilot["entry"])
            if state == "new":
                insert_fixture_records(cur, file_id, pilot["records"])
                action, inserted = "INSERTED_NEW", EXPECTED_RECORDS
                write_log(cur, batch_id, "success", inserted, file_id, snapshot_id)
            else:
                action, inserted = "SKIPPED_UNCHANGED", 0
                write_log(cur, batch_id, "skipped", 0, file_id, snapshot_id)
            verified = verify_integrity(cur, file_id)
            return action, inserted, file_id, verified


def try_failed_log(conn: psycopg.Connection, batch_id: uuid.UUID, exc: Exception) -> None:
    """Insert failed log AFTER payload rollback. Never suppress original error."""
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                db_guard(cur)
                write_log(cur, batch_id, "failed", 0, error=f"{type(exc).__name__}: {exc}")
        print("Failure audit log: recorded (payload was rolled back)", file=sys.stderr)
    except Exception as log_exc:
        print(f"Failure audit log not available: {type(log_exc).__name__}", file=sys.stderr)


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__)
    mode = args.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="verify provenance and all 380 records, no DB access")
    mode.add_argument("--commit", action="store_true", help="write verified pilot data into V2 PostgreSQL")
    ns = args.parse_args()

    print("=== M3.3 | RAW POSTGRESQL INGESTION PILOT ===")
    pilot = prepare_pilot()
    print("Pinned revision:", EXPECTED_REVISION)
    print("Verified SHA-256:", pilot["sha256"])
    print("Parsed fixtures:", len(pilot["records"]))
    if ns.dry_run:
        print("Mode: DRY_RUN | PostgreSQL connections: 0 | writes: 0")
        print("M3.3 PRECHECK: PASS")
        return 0

    batch_id = uuid.uuid4()
    kwargs = connection_parameters()
    with psycopg.connect(**kwargs) as conn:
        with conn.cursor() as cur:
            db_guard(cur)  # must pass before permitting any inserts
        print("Connected database:", EXPECTED_DB)
        try:
            action, inserted, file_id, verified = ingest(conn, pilot, batch_id)
        except Exception as exc:
            try_failed_log(conn, batch_id, exc)
            raise
    print("Archive path:", pilot["archive_relative"])
    print("File ID:", file_id)
    print("Ingestion action:", action)
    print("Rows inserted this run:", inserted)
    print("Verified records in DB:", verified)
    print("Snapshot status: discovered (full 276-file snapshot not yet ingested)")
    print("M3.3 RESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M3.3 RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
