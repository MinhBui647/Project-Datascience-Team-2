#!/usr/bin/env python3
r"""Unified V2 chunked CSV.GZ ingester for three pinned Transfermarkt sources.

Read-only inspection:
 .\.venv_v2\Scripts\python.exe scripts\tools\chunked_csv_gz_runner.py --target appearances --plan

Commit mode is intentionally NOT used during refactor validation.

The checkpoint is in raw.raw_files.record_count, with parse_state='partial'. Each
up-to-10,000-row group commits independently; restart uses identical COMMIT command.
Only after validating the COMPLETE expected stream does the file become 'success'
and the snapshot observation become 'linked'. Never alters V1, schema or archive bytes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
DB_NAME = "epl_raw_storage_v2"
MIN_CHUNK = 1_000
MAX_CHUNK = 10_000
DEFAULT_CHUNK = 10_000
GLOBAL_BATCH_LOCK = "epl_raw_storage_v2:m3_4d:single_batch_writer"

from dataclasses import dataclass


@dataclass(frozen=True)
class CsvGzProfile:
    name: str
    target: str
    preflight_name: str
    expected_rows: int
    expected_uncompressed_bytes: int
    max_compressed_bytes: int
    marker: str  # MUST match historic marker to resume existing 'partial' checkpoints

    @property
    def report(self) -> Path:
        return ROOT / "reports" / self.preflight_name


PROFILES = {
    "appearances": CsvGzProfile(
        "appearances", "raw_data/transfermarkt/tfm_appearances_full.csv.gz",
        "m3_7_6_tfm_appearances_full_preflight.json",
        1_894_350, 198_398_405, 64 * 1024**2, "m3_7_8_chunked_csv_gz_v2"),
    "game_events": CsvGzProfile(
        "game_events", "raw_data/transfermarkt/tfm_game_events_full.csv.gz",
        "m3_7_6_tfm_game_events_full_preflight.json",
        1_274_469, 179_098_694, 64 * 1024**2, "m3_7_9_game_events_chunked_v2"),
    "game_lineups": CsvGzProfile(
        "game_lineups", "raw_data/transfermarkt/tfm_game_lineups_full.csv.gz",
        "m3_7_6_tfm_game_lineups_full_preflight.json",
        3_179_016, 415_978_884, 160 * 1024**2, "m3_7_10_game_lineups_chunked_v2"),
}


def load_plan(profile: CsvGzProfile) -> dict[str, Any]:
    with CATALOG.open("r", encoding="utf-8-sig", newline="") as fp:
        records = sorted(csv.DictReader(fp), key=lambda r: r["relative_path"])
    if len(records) != 276 or len({r["relative_path"] for r in records}) != 276:
        raise RuntimeError("Pinned raw catalog must contain exactly 276 unique paths")
    if any(row["hf_revision"] != REV for row in records):
        raise RuntimeError("Catalog revision mismatch")
    matches = [(i, r) for i, r in enumerate(records) if r["relative_path"] == profile.target]
    if len(matches) != 1:
        raise RuntimeError("Exactly one target CSV.GZ required")
    index, entry = matches[0]
    if entry["format"] != "csv.gz" or entry["group"] != "raw_data" or entry["source_hint"] != "transfermarkt":
        raise RuntimeError("Target metadata unexpected")
    size = int(entry["size_bytes"])
    if size <= 0 or size > profile.max_compressed_bytes:
        raise RuntimeError("Compressed source size exceeds approved range")
    sha = entry["lfs_sha256"].lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise RuntimeError("Pinned LFS SHA256 is missing or malformed")
    report = json.loads(profile.report.read_text(encoding="utf-8-sig"))
    required = {
        "repo_id": REPO,
        "revision": REV,
        "relative_path": profile.target,
        "index": index,
        "sha256": sha,
        "compressed_bytes": size,
        "decompressed_bytes": profile.expected_uncompressed_bytes,
        "raw_parser_records": profile.expected_rows,
        "status": "pass",
    }
    for key, expected in required.items():
        if report.get(key) != expected:
            raise RuntimeError(f"Preflight report mismatch for {key}: {report.get(key)!r}")
    return {"index": index, "entry": entry, "source_bytes": size, "sha256": sha}


def render_plan(plan: dict[str, Any], chunk_rows: int, profile: CsvGzProfile) -> None:
    print("=== R3 CSV.GZ | POSTGRESQL-RESUMABLE CSV.GZ ===")
    print("Repo:", REPO)
    print("Revision:", REV)
    print("Database guard:", DB_NAME)
    print("Catalog index:", plan["index"])
    print("Path:", profile.target)
    print("Compressed source bytes:", plan["source_bytes"])
    print("Expected expanded bytes:", profile.expected_uncompressed_bytes)
    print("Expected parser records:", profile.expected_rows)
    print("Commit chunk rows:", chunk_rows)
    print("Estimated chunk transactions:", (profile.expected_rows + chunk_rows - 1) // chunk_rows)
    print("State policy: partial -> success only after full stream and count checks")
    print("Checkpoint: PostgreSQL raw.raw_files.record_count (durable per chunk)")
    print("Snapshot: remains discovered until separate full-catalog reconciliation")


def check_previous_file(cur, sha: str, size: int, archive_rel: str, profile: CsvGzProfile):
    cur.execute("""
        SELECT file_id,file_format,file_size_bytes,archive_path,archive_state,
               parse_state,record_count,metadata_evidence
        FROM raw.raw_files WHERE repo_id=%s AND relative_path=%s AND content_sha256=%s
        FOR UPDATE
    """, (REPO, profile.target, sha))
    prior = cur.fetchone()
    if prior is None:
        return None
    fid, fmt, old_size, old_archive, state, parse_state, count, evidence = prior
    if (fmt, old_size, old_archive, state) != ("csv.gz", size, archive_rel, "verified"):
        raise RuntimeError("DB file version differs from verified pinned archive")
    if parse_state == "success":
        if count != profile.expected_rows:
            raise RuntimeError("Successful version's row count differs from pinned preflight")
        return fid, "success", count
    if parse_state != "partial" or not isinstance(evidence, dict) or evidence.get("chunked_ingester") != profile.marker:
        raise RuntimeError("Existing incomplete file was not created by this runner; refuse takeover")
    if evidence.get("expected_preflight_rows") != profile.expected_rows:
        raise RuntimeError("Stored partial file was created with different preflight expectations")
    if count is None or count < 0 or count > profile.expected_rows:
        raise RuntimeError("Invalid stored checkpoint count")
    return fid, "partial", count


def validate_checkpoint(cur, file_id: int, checkpoint: int) -> tuple[Any, Any]:
    cur.execute("SELECT count(*) FROM raw.raw_records WHERE file_id=%s", (file_id,))
    actual = cur.fetchone()[0]
    if actual != checkpoint:
        raise RuntimeError(f"Checkpoint mismatch: metadata={checkpoint} database={actual}")
    if not checkpoint:
        return None, None
    cur.execute("SELECT raw_data FROM raw.raw_records WHERE file_id=%s AND record_locator=%s", (file_id, "row:1"))
    first = cur.fetchone()
    cur.execute("SELECT raw_data FROM raw.raw_records WHERE file_id=%s AND record_locator=%s", (file_id, f"row:{checkpoint}"))
    last = cur.fetchone()
    if first is None or last is None:
        raise RuntimeError("Checkpoint boundary records are missing")
    return first[0], last[0]


def register_or_resume(conn, engine, plan, sha, size, archive_rel, profile: CsvGzProfile):
    from psycopg.types.json import Jsonb
    with conn.transaction():
        with conn.cursor() as cur:
            engine.db_guard(cur)
            source_id = engine.get_source(cur, plan["entry"]["source_hint"])
            snapshot_id = engine.get_snapshot(cur)
            existing = check_previous_file(cur, sha, size, archive_rel, profile)
            if existing is None:
                evidence = {
                    "chunked_ingester": profile.marker,
                    "source_hint": plan["entry"]["source_hint"],
                    "season_hint": plan["entry"].get("season_hint") or None,
                    "catalog_group": plan["entry"]["group"],
                    "classification": "path_hint_only",
                    "hf_revision_observed": REV,
                    "expected_preflight_rows": profile.expected_rows,
                }
                cur.execute("""
                    INSERT INTO raw.raw_files
                      (repo_id,relative_path,source_id,file_format,content_sha256,file_size_bytes,
                       archive_path,archive_state,parse_state,record_count,league_scope,
                       season_scope,metadata_evidence)
                    VALUES (%s,%s,%s,'csv.gz',%s,%s,%s,'verified','partial',0,'unknown','unknown',%s)
                    RETURNING file_id
                """, (REPO, profile.target, source_id, sha, size, archive_rel, Jsonb(evidence)))
                fid = cur.fetchone()[0]
                return fid, snapshot_id, "partial", 0, None, None
            fid, state, checkpoint = existing
            first, last = validate_checkpoint(cur, fid, checkpoint)
            if state == "success":
                cur.execute("""
                    SELECT file_id,presence_status FROM raw.file_observations
                    WHERE snapshot_id=%s AND relative_path=%s
                """, (snapshot_id, profile.target))
                if cur.fetchone() != (fid, "linked"):
                    raise RuntimeError("Completed file has invalid snapshot observation")
            return fid, snapshot_id, state, checkpoint, first, last


def flush_chunk(conn, file_id: int, before: int, chunk: list[tuple[str, str, Any]]) -> int:
    """Atomically write one chunk and advance the DB checkpoint."""
    from psycopg.types.json import Jsonb
    if not chunk or len(chunk) > MAX_CHUNK:
        raise RuntimeError("Illegal chunk size")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO raw.raw_records(file_id,member_id,record_locator,record_kind,raw_data)
                VALUES (%s,NULL,%s,%s,%s)
            """, [(file_id, locator, kind, Jsonb(raw)) for locator, kind, raw in chunk])
            after = before + len(chunk)
            cur.execute("""
                UPDATE raw.raw_files SET record_count=%s
                WHERE file_id=%s AND parse_state='partial' AND record_count=%s
                RETURNING file_id
            """, (after, file_id, before))
            if cur.fetchone() is None:
                raise RuntimeError("Checkpoint race or unexpected file status")
    return after


def finalize(conn, engine, file_id: int, snapshot_id: int, count: int, entry, batch_id, profile: CsvGzProfile):
    if count != profile.expected_rows:
        raise RuntimeError(f"Parser row count {count} does not match preflight {profile.expected_rows}")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.raw_records WHERE file_id=%s", (file_id,))
            actual = cur.fetchone()[0]
            if actual != count:
                raise RuntimeError(f"Final records in database {actual}, expected {count}")
            cur.execute("""
                UPDATE raw.raw_files SET parse_state='success'
                WHERE file_id=%s AND parse_state='partial' AND record_count=%s
                RETURNING file_id
            """, (file_id, count))
            if cur.fetchone() is None:
                raise RuntimeError("File state changed during finalization")
            engine.link_observation(cur, snapshot_id, file_id, entry)
            engine.success_log(cur, batch_id, snapshot_id, file_id, profile.target, "INSERTED_NEW", count)


def commit(plan, chunk_rows, profile: CsvGzProfile):
    import psycopg
    tools = str(ROOT / "scripts" / "tools")
    scripts = str(ROOT / "scripts")
    for location in (tools, scripts):
        if location not in sys.path:
            sys.path.insert(0, location)
    import batch_ingestion as engine
    from raw_parser import ParsedRecord, iter_parse_events

    if (engine.ROOT.resolve() != ROOT.resolve() or engine.REPO != REPO
            or engine.REV != REV or engine.CATALOG.resolve() != CATALOG.resolve()):
        raise RuntimeError("M3.4 engine does not match pinned project/revision")
    print(f"Mode: COMMIT (pinned CSV.GZ: {profile.name}; chunk-atomic)", flush=True)
    batch_id = uuid.uuid4()
    # connection_parameters already includes autocommit=True; do not pass it twice.
    with psycopg.connect(**engine.connection_parameters()) as conn:
        with conn.cursor() as cur:
            engine.db_guard(cur)
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (GLOBAL_BATCH_LOCK,))
            acquired = cur.fetchone()[0]
        if not acquired:
            raise RuntimeError("Another ingestion process holds the global batch lock")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (REPO + ":" + profile.target,))
                file_locked = cur.fetchone()[0]
            if not file_locked:
                raise RuntimeError("Another ingestion process holds the per-file lock")
            try:
                archive, sha, size, stored_rel, archive_action = engine.obtain_archive(plan["entry"])
                if (sha, size) != (plan["sha256"], plan["source_bytes"]):
                    raise RuntimeError("Pinned source hash/size mismatch after archive")
                print("Pinned archive:", archive_action, "|", stored_rel, flush=True)
                fid, sid, state, checkpoint, first, last = register_or_resume(
                    conn, engine, plan, sha, size, stored_rel, profile)
                print("File version ID:", fid, "| State:", state,
                      "| Durable checkpoint:", checkpoint, flush=True)
                if state == "success":
                    print("R3 CSV.GZ RESULT: SKIPPED_UNCHANGED (already complete)")
                    return 0
                pending: list[tuple[str, str, dict]] = []
                seen = 0
                committed = checkpoint
                for ev in iter_parse_events(archive, "csv.gz"):
                    if not isinstance(ev, ParsedRecord) or ev.record_kind != "csv_row" or ev.member_index is not None:
                        raise RuntimeError("Parser emitted unexpected non-CSV event")
                    seen += 1
                    if ev.record_locator != f"row:{seen}":
                        raise RuntimeError(f"Unexpected record locator at row {seen}")
                    if seen <= checkpoint:
                        if seen == 1 and ev.raw_data != first:
                            raise RuntimeError("First checkpoint record disagrees with current parser")
                        if seen == checkpoint and ev.raw_data != last:
                            raise RuntimeError("Last checkpoint record disagrees with current parser")
                        continue
                    if seen > profile.expected_rows:
                        raise RuntimeError("Parser yielded more rows than preflight expected")
                    pending.append((ev.record_locator, ev.record_kind, ev.raw_data))
                    if len(pending) == chunk_rows:
                        committed = flush_chunk(conn, fid, committed, pending)
                        pending.clear()
                        print(f"COMMITTED CHUNK | total={committed:,}/{profile.expected_rows:,}", flush=True)
                if pending:
                    committed = flush_chunk(conn, fid, committed, pending)
                    print(f"COMMITTED CHUNK | total={committed:,}/{profile.expected_rows:,}", flush=True)
                if seen != profile.expected_rows or committed != seen:
                    raise RuntimeError(f"Final parser count {seen}, committed count {committed}, expected {profile.expected_rows}")
                finalize(conn, engine, fid, sid, seen, plan["entry"], batch_id, profile)
                print("R3 CSV.GZ FINAL FILE STATUS: success | snapshot observation: linked", flush=True)
                print("R3 CSV.GZ RESULT: PASS", flush=True)
                return 0
            except Exception as exc:
                engine.log_failure(conn, batch_id, profile.target, exc)
                raise
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (REPO + ":" + profile.target,))
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (GLOBAL_BATCH_LOCK,))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--commit", action="store_true")
    ap.add_argument("--confirm-chunked", action="store_true")
    ap.add_argument("--chunk-rows", type=int, default=DEFAULT_CHUNK)
    ap.add_argument("--target", choices=sorted(PROFILES), required=True)
    args = ap.parse_args()
    if not MIN_CHUNK <= args.chunk_rows <= MAX_CHUNK:
        ap.error(f"chunk-rows must be between {MIN_CHUNK} and {MAX_CHUNK}")
    if args.commit != args.confirm_chunked:
        ap.error("--confirm-chunked is required for COMMIT and forbidden for PLAN")
    profile = PROFILES[args.target]
    plan = load_plan(profile)
    render_plan(plan, args.chunk_rows, profile)
    if args.plan:
        print("Mode: PLAN (local catalog/preflight only; no DB/network/writes)")
        print("R3 CSV.GZ PLAN: PASS")
        return 0
    return commit(plan, args.chunk_rows, profile)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R3 CSV.GZ RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        print("Committed chunks remain durable; re-run same command only after reviewing failure.", file=sys.stderr)
        raise SystemExit(1)
