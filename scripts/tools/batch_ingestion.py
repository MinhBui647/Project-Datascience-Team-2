#!/usr/bin/env python3
r"""M3.4A | Pinned HF -> immutable V2 archive -> parser -> PostgreSQL JSONB.

Place at: scripts/tools/batch_ingestion.py
Requirements: M3.1/M3.2/M3.3 files retained in their documented locations.

From the raw_storage project root:
    .\.venv_v2\Scripts\python.exe scripts\tools\batch_ingestion.py --plan
    .\.venv_v2\Scripts\python.exe scripts\tools\batch_ingestion.py --commit
    .\.venv_v2\Scripts\python.exe scripts\tools\batch_ingestion.py --commit

Default pilot: 3 pinned files (1 prior fixture, 1 CSV, 1 JSON object).
--all is opt-in for a separate milestone, after pilot + edge-case tests.

Safety:
 - --plan makes no network requests, no database connections, no writes.
 - --commit connects exclusively to epl_raw_storage_v2.
 - one DB transaction PER FILE; failure is independently logged after rollback.
 - bytes are verified against pinned manifest and saved via atomic, no-overwrite hardlink.
 - existing successful file is verified and skipped; no duplicate record insertions.
 - snapshot status remains 'discovered'; not marked complete by this script.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
import os
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "tools"))

from raw_parser import ArchiveMember, ParsedRecord, iter_parse_events  # noqa: E402
from archive_manager import archive_immutable, verify_download, sha256_and_size  # noqa: E402
from ingestion_helper import connection_parameters, db_guard  # noqa: E402

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
ARCHIVE_DIR = ROOT / "raw-archive-v2" / "objects"
REPORT_DIR = ROOT / "reports"
PILOT = (
    "2026_2027_new/fpl_fixtures_latest.json",  # existing, expect SKIPPED_UNCHANGED
    "2026_2027_new/football_data_raw_2627.csv",  # CSV, first new
    "2026_2027_new/fpl_bootstrap_latest.json",  # nested JSON object, first new
)
REQUIRED_FORMATS = {"csv", "csv.gz", "json", "tar.gz"}
REQUIRED_GROUPS = {"raw_data", "2026_2027_new"}
BATCH_INSERT_SIZE = 250


def load_catalog() -> dict[str, dict[str, str]]:
    if not CATALOG.is_file():
        raise RuntimeError(f"Missing pinned raw catalog: {CATALOG}")
    with CATALOG.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"relative_path", "group", "source_hint", "format", "size_bytes", "hf_revision"}
        if not required <= set(reader.fieldnames or []):
            raise RuntimeError("Raw catalog missing required columns")
        entries: dict[str, dict[str, str]] = {}
        for row in reader:
            relative = row["relative_path"]
            parts = relative.split("/")
            if (not relative or relative.startswith("/") or "\\" in relative
                    or "//" in relative or any(part in ("", ".", "..") for part in parts)):
                raise RuntimeError(f"Unsafe catalog path: {relative!r}")
            if relative in entries:
                raise RuntimeError(f"Duplicate raw catalog path: {relative}")
            if row["hf_revision"] != REV:
                raise RuntimeError(f"Wrong pinned commit for {relative}")
            if row["group"] not in REQUIRED_GROUPS or row["format"] not in REQUIRED_FORMATS:
                raise RuntimeError(f"Unexpected raw-scope group or format: {relative}")
            try:
                size = int(row["size_bytes"])
            except (ValueError, TypeError):
                raise RuntimeError(f"Invalid manifest size for {relative}")
            if size < 0:
                raise RuntimeError(f"Negative manifest size for {relative}")
            hint = (row.get("source_hint") or "").strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", hint):
                raise RuntimeError(f"Unsafe source_hint for {relative}: {hint!r}")
            entries[relative] = row
    if len(entries) != 276:
        raise RuntimeError(f"Expected 276 raw files, catalog has {len(entries)}")
    return entries


def select_entries(entries: dict[str, dict[str, str]], all_files: bool, limit: int | None):
    paths = sorted(entries) if all_files else list(PILOT)
    if limit is not None:
        paths = paths[:limit]
    if not paths:
        raise RuntimeError("Nothing selected")
    return [entries[p] for p in paths]


def verify_archive(path: Path, sha: str, size: int) -> None:
    stored_sha, stored_size = sha256_and_size(path)
    if (stored_sha, stored_size) != (sha, size):
        raise RuntimeError("Archive integrity failed: SHA-256/size mismatch")


def obtain_archive(entry: dict[str, str]) -> tuple[Path, str, int, str, str]:
    source_path = Path(hf_hub_download(
        repo_id=REPO, repo_type="dataset", filename=entry["relative_path"], revision=REV,
    ))
    actual_sha, size = verify_download(source_path, entry)  # manifest size + LFS SHA/git blob
    stored, action = archive_immutable(source_path, actual_sha, size)
    canonical = ARCHIVE_DIR / actual_sha[:2] / actual_sha
    if stored.resolve() != canonical.resolve():
        raise RuntimeError("Unexpected archive destination")
    verify_archive(stored, actual_sha, size)
    return stored, actual_sha, size, stored.relative_to(ROOT).as_posix(), action


def get_source(cur: psycopg.Cursor, hint: str) -> int:
    cur.execute("""
        INSERT INTO raw.raw_sources(source_code,source_name,description)
        VALUES (%s,%s,%s)
        ON CONFLICT(source_code) DO NOTHING
    """, (hint, f"{hint} (path classification)", "Folder-based classification; not guaranteed provider identity"))
    cur.execute("SELECT source_id FROM raw.raw_sources WHERE source_code=%s", (hint,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Cannot find source after insert")
    return row[0]


def get_snapshot(cur: psycopg.Cursor) -> int:
    cur.execute("""
        INSERT INTO raw.snapshot_runs
            (repo_id,hf_revision,manifest_file_count,raw_scope_file_count,status)
        VALUES (%s,%s,294,276,'discovered')
        ON CONFLICT(repo_id,hf_revision) DO NOTHING
    """, (REPO, REV))
    cur.execute("""
        SELECT snapshot_id,manifest_file_count,raw_scope_file_count
        FROM raw.snapshot_runs WHERE repo_id=%s AND hf_revision=%s
    """, (REPO, REV))
    sid, total, scope = cur.fetchone()
    if total not in (None, 294) or scope not in (None, 276):
        raise RuntimeError("Existing snapshot counts do not match pinned catalog")
    return sid


def get_file(cur: psycopg.Cursor, entry: dict[str, str], source_id: int,
             sha: str, size: int, stored_rel: str) -> tuple[int, bool, int]:
    path = entry["relative_path"]
    cur.execute("""
        SELECT file_id,file_format,file_size_bytes,archive_path,archive_state,parse_state,record_count
        FROM raw.raw_files WHERE repo_id=%s AND relative_path=%s AND content_sha256=%s
        FOR UPDATE
    """, (REPO, path, sha))
    prior = cur.fetchone()
    if prior:
        fid, fmt, old_size, archive_path, archive_state, parse_state, record_count = prior
        if (fmt, old_size, archive_path, archive_state) != (
            entry["format"], size, stored_rel, "verified"
        ):
            raise RuntimeError("Stored file metadata conflicts with verified archive")
        if parse_state != "success" or record_count is None:
            raise RuntimeError("Stored file incomplete: refuse automatic overwrite")
        cur.execute("SELECT count(*) FROM raw.raw_records WHERE file_id=%s", (fid,))
        if cur.fetchone()[0] != record_count:
            raise RuntimeError("Stored file's record_count disagrees with actual DB records")
        return fid, False, record_count
    evidence = {
        "source_hint": entry["source_hint"],
        "season_hint": entry.get("season_hint") or None,
        "catalog_group": entry["group"],
        "classification": "path_hint_only",
        "hf_revision_observed": REV,
    }
    cur.execute("""
        INSERT INTO raw.raw_files
        (repo_id,relative_path,source_id,file_format,content_sha256,file_size_bytes,
         archive_path,archive_state,parse_state,league_scope,season_scope,metadata_evidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'verified','pending','unknown','unknown',%s)
        RETURNING file_id
    """, (REPO, path, source_id, entry["format"], sha, size, stored_rel, Jsonb(evidence)))
    return cur.fetchone()[0], True, 0


def link_observation(cur: psycopg.Cursor, sid: int, fid: int,
                     entry: dict[str, str]) -> None:
    path = entry["relative_path"]
    cur.execute("""
        INSERT INTO raw.file_observations
          (snapshot_id,repo_id,relative_path,file_id,presence_status,
           manifest_size_bytes,git_blob_id,lfs_sha256)
        VALUES (%s,%s,%s,%s,'linked',%s,%s,%s)
        ON CONFLICT(snapshot_id,relative_path) DO NOTHING
    """, (sid, REPO, path, fid, int(entry["size_bytes"]),
          entry.get("git_blob_id") or None, entry.get("lfs_sha256") or None))
    cur.execute("""
        SELECT file_id,presence_status FROM raw.file_observations
        WHERE snapshot_id=%s AND relative_path=%s
    """, (sid, path))
    row = cur.fetchone()
    if row != (fid, "linked"):
        raise RuntimeError("Snapshot observation conflicts with incoming version")


def parse_to_database(cur: psycopg.Cursor, fid: int, archive: Path, fmt: str) -> tuple[int, int]:
    """Consumes parser iterator once; all mutations belong to a single file txn."""
    members: dict[int, int] = {}
    count = 0
    member_count = 0
    batch: list[tuple[Any, ...]] = []
    statement = """
        INSERT INTO raw.raw_records(file_id,member_id,record_locator,record_kind,raw_data)
        VALUES (%s,%s,%s,%s,%s)
    """

    def flush() -> None:
        nonlocal batch
        if batch:
            cur.executemany(statement, batch)
            batch = []

    for event in iter_parse_events(archive, fmt):
        if isinstance(event, ArchiveMember):
            flush()
            cur.execute("""
                INSERT INTO raw.archive_members
                (file_id,member_index,member_path,member_size_bytes,member_sha256,
                 member_format,parse_state)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING member_id
            """, (fid, event.member_index, event.member_path, event.member_size_bytes,
                  event.member_sha256, event.member_format, event.parse_state))
            members[event.member_index] = cur.fetchone()[0]
            member_count += 1
        elif isinstance(event, ParsedRecord):
            member_id = None
            if event.member_index is not None:
                if event.member_index not in members:
                    raise RuntimeError("TAR record event without registered member")
                member_id = members[event.member_index]
            batch.append((fid, member_id, event.record_locator,
                          event.record_kind, Jsonb(event.raw_data)))
            count += 1
            if len(batch) >= BATCH_INSERT_SIZE:
                flush()
        else:
            raise RuntimeError(f"Unknown parser event type {type(event).__name__}")
    flush()
    cur.execute("UPDATE raw.raw_files SET parse_state='success',record_count=%s WHERE file_id=%s",
                (count, fid))
    cur.execute("SELECT COUNT(*) FROM raw.raw_records WHERE file_id=%s", (fid,))
    if cur.fetchone()[0] != count:
        raise RuntimeError("DB record count mismatch after insert")
    return count, member_count


def success_log(cur: psycopg.Cursor, bid: uuid.UUID, sid: int, fid: int,
                path: str, action: str, inserted: int) -> None:
    cur.execute("""
        INSERT INTO raw.ingestion_logs
        (batch_id,snapshot_id,file_id,relative_path,status,rows_inserted,finished_at)
        VALUES (%s,%s,%s,%s,%s,%s,now())
    """, (bid, sid, fid, path, "success" if action == "INSERTED_NEW" else "skipped", inserted))


def log_failure(conn: psycopg.Connection, bid: uuid.UUID,
                path: str, error: Exception) -> None:
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                db_guard(cur)
                cur.execute("SELECT snapshot_id FROM raw.snapshot_runs WHERE repo_id=%s AND hf_revision=%s",
                            (REPO, REV))
                existing = cur.fetchone()
                snapshot_id = existing[0] if existing else None
                cur.execute("""
                    INSERT INTO raw.ingestion_logs
                    (batch_id,snapshot_id,relative_path,status,rows_inserted,finished_at,error_message)
                    VALUES (%s,%s,%s,'failed',0,now(),%s)
                """, (bid, snapshot_id, path, f"{type(error).__name__}: {error}"[:1000]))
    except Exception as exc:
        print(f"WARN | unable to write failure log: {type(exc).__name__}", file=sys.stderr)


def ingest_file(conn: psycopg.Connection, bid: uuid.UUID,
                entry: dict[str, str], stored: Path,
                sha: str, size: int, stored_rel: str) -> tuple[str, int, int, int]:
    path = entry["relative_path"]
    with conn.transaction():
        with conn.cursor() as cur:
            db_guard(cur)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (REPO + ":" + path,))
            source_id = get_source(cur, entry["source_hint"])
            snapshot_id = get_snapshot(cur)
            file_id, is_new, existing_count = get_file(cur, entry, source_id, sha, size, stored_rel)
            link_observation(cur, snapshot_id, file_id, entry)
            if is_new:
                count, members = parse_to_database(cur, file_id, stored, entry["format"])
                action, inserted = "INSERTED_NEW", count
            else:
                action, count, members, inserted = "SKIPPED_UNCHANGED", existing_count, 0, 0
            success_log(cur, bid, snapshot_id, file_id, path, action, inserted)
            return action, inserted, count, members


def write_report(summary: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"m3_4_batch_{summary['batch_id']}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp",
                                     prefix=".m3_4-", dir=REPORT_DIR, delete=False) as fp:
        temp = Path(fp.name)
        json.dump(summary, fp, indent=2, ensure_ascii=False)
        fp.write("\n")
        fp.flush()
        os.fsync(fp.fileno())
    try:
        os.link(temp, report_path)  # atomic create-only; never overwrite a prior batch report
    finally:
        temp.unlink(missing_ok=True)
    return report_path


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__)
    mode = args.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="offline manifest-only plan; zero network/DB calls")
    mode.add_argument("--commit", action="store_true", help="download, verify and ingest selected raw files")
    args.add_argument("--all", action="store_true", help="explicitly select all 276 raw paths; do not run until pilot validated")
    args.add_argument("--limit", type=int, default=None, help="optional maximum files from selected path list")
    ns = args.parse_args()
    if ns.limit is not None and ns.limit <= 0:
        args.error("--limit must be positive")
    entries = load_catalog()
    selected = select_entries(entries, ns.all, ns.limit)

    print("=== M3.4 | PINNED RAW BATCH INGESTION ===")
    print("Repo:", REPO)
    print("Revision:", REV)
    print("Catalog scope:", len(entries))
    print("Selected files:", len(selected))
    print("Mode:", "PLAN (no network, DB, writes)" if ns.plan else "COMMIT")
    if ns.plan:
        for i, entry in enumerate(selected, start=1):
            print(f"{i:3} | {entry['format']:7} | {int(entry['size_bytes']):10} bytes | {entry['relative_path']}")
        print("M3.4 PLAN: PASS")
        return 0

    bid = uuid.uuid4()
    result: dict[str, Any] = {
        "repo_id": REPO, "revision": REV, "batch_id": str(bid),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_count": len(selected), "success_files": 0, "skipped_files": 0,
        "failed_files": 0, "rows_inserted": 0, "results": [],
        "snapshot_status": "discovered",
    }
    with psycopg.connect(**connection_parameters()) as conn:
        with conn.cursor() as cur:
            db_guard(cur)
        print("Connected database: epl_raw_storage_v2")
        for i, entry in enumerate(selected, 1):
            path = entry["relative_path"]
            try:
                archive, sha, size, rel_archive, archive_action = obtain_archive(entry)
                action, inserted, record_count, members = ingest_file(
                    conn, bid, entry, archive, sha, size, rel_archive)
                if action == "INSERTED_NEW":
                    result["success_files"] += 1
                else:
                    result["skipped_files"] += 1
                result["rows_inserted"] += inserted
                result["results"].append({
                    "path": path, "status": action, "sha256": sha,
                    "archive_action": archive_action, "record_count": record_count,
                    "rows_inserted": inserted, "archive_member_count": members,
                })
                print(f"[{i}/{len(selected)}] {action} | +{inserted} | total {record_count} | {path}")
            except Exception as exc:
                result["failed_files"] += 1
                result["results"].append({
                    "path": path, "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}"[:800],
                })
                print(f"[{i}/{len(selected)}] FAILED | {path} | {type(exc).__name__}: {exc}", file=sys.stderr)
                log_failure(conn, bid, path, exc)
    report = write_report(result)
    print("Inserted new file versions:", result["success_files"])
    print("Skipped unchanged files:", result["skipped_files"])
    print("Failed files:", result["failed_files"])
    print("Records inserted this run:", result["rows_inserted"])
    print("Report:", report.relative_to(ROOT).as_posix())
    print("Snapshot remains: discovered (not marking all 276 files complete)")
    if result["failed_files"]:
        print("M3.4 RESULT: PARTIAL_FAILURE (see report)")
        return 1
    print("M3.4 RESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M3.4 RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
