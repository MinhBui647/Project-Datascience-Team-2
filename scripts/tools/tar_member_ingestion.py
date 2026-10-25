#!/usr/bin/env python3
r"""M3.8.3 | PostgreSQL-resumable, member-atomic ingestion of pinned Sofascore TAR.GZ.

Windows PowerShell from raw_storage root:
 .\.venv_v2\Scripts\python.exe scripts\tools\tar_member_ingestion.py --plan
 .\.venv_v2\Scripts\python.exe scripts\tools\tar_member_ingestion.py --commit --confirm-tar

PLAN is offline/read-only and binds expected members/records to the M3.8 inspect
receipt (NOT guessed constants). COMMIT uses immutable archive, TAR parser and
DB helpers already used in M3.4; commits one complete member per transaction.
Partial files are not linked to snapshots. A retry skips previously committed
members only after validating the durable DB checkpoint and parser stream.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
PREFLIGHT = ROOT / "reports" / "m3_8_sofascore_tar_preflight.json"
REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
TARGET = "raw_data/sofascore/sofascore_matches_cache.tar.gz"
DB_NAME = "epl_raw_storage_v2"
EXPECTED_SHA = "86ed16115cc9f4d82875718b393d9461c51752b513db9a5af2f0d47eec77931b"
EXPECTED_SIZE = 96_170_246
MAX_MEMBERS = 100_000
MAX_EXPANDED = 1536 * 1024**2
MAX_MEMBER_BYTES = 48 * 1024**2
BATCH_ROWS = 250
MARKER = "m3_8_3_tar_member_ingest_v2"
GLOBAL_LOCK = "epl_raw_storage_v2:m3_4d:single_batch_writer"


@dataclass(frozen=True)
class Plan:
    index: int
    entry: dict[str, str]
    receipt: dict[str, Any]
    members: int
    records: int
    json_members: int
    skipped_members: int
    expanded: int


def require_int(data: dict, field: str, low: int, high: int) -> int:
    value = data.get(field)
    if type(value) is not int or not low <= value <= high:
        raise RuntimeError(f"Invalid preflight {field}: {value!r}")
    return value


def read_plan() -> Plan:
    if not CATALOG.is_file():
        raise RuntimeError(f"Missing catalog: {CATALOG}")
    with CATALOG.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = sorted(csv.DictReader(stream), key=lambda r: r["relative_path"])
    if len(rows) != 276 or len({r["relative_path"] for r in rows}) != 276:
        raise RuntimeError("Catalog must contain 276 unique raw paths")
    if any(r.get("hf_revision") != REV for r in rows):
        raise RuntimeError("Catalog revision mismatch")
    found = [(i, r) for i, r in enumerate(rows) if r["relative_path"] == TARGET]
    if len(found) != 1:
        raise RuntimeError("Expected exactly one Sofascore archive in catalog")
    index, entry = found[0]
    for key, expected in {
        "group": "raw_data",
        "source_hint": "sofascore",
        "format": "tar.gz",
        "size_bytes": str(EXPECTED_SIZE),
        "lfs_sha256": EXPECTED_SHA,
    }.items():
        if (entry.get(key) or "").lower() != expected.lower():
            raise RuntimeError(f"Catalog {key} disagrees with pinned Sofascore target")
    if not PREFLIGHT.is_file():
        raise RuntimeError(
            "Missing M3.8 INSPECT receipt. Run tar_preflight.py --inspect first."
        )
    receipt = json.loads(PREFLIGHT.read_text(encoding="utf-8-sig"))
    for key, expected in {
        "repo_id": REPO, "revision": REV, "relative_path": TARGET,
        "catalog_index": index, "sha256": EXPECTED_SHA,
        "compressed_bytes": EXPECTED_SIZE, "status": "pass",
    }.items():
        if receipt.get(key) != expected:
            raise RuntimeError(f"Preflight receipt {key} mismatch: {receipt.get(key)!r}")
    members = require_int(receipt, "archive_members", 1, MAX_MEMBERS)
    json_members = require_int(receipt, "json_members", 1, members)
    skipped = require_int(receipt, "skipped_non_json_members", 0, members)
    if json_members + skipped != members:
        raise RuntimeError("JSON + skipped member count mismatch")
    records = require_int(receipt, "raw_parser_records", 1, 100_000_000)
    expanded = require_int(receipt, "gzip_expanded_bytes", 1, MAX_EXPANDED)
    payload = require_int(receipt, "member_payload_bytes", 0, 1024**3)
    largest = require_int(receipt, "largest_member_bytes", 0, MAX_MEMBER_BYTES)
    if largest > payload:
        raise RuntimeError("Largest member exceeds total member payload")
    kinds = receipt.get("record_kinds")
    if not isinstance(kinds, dict) or kinds != {"tar_json": records}:
        raise RuntimeError("Preflight record kinds must all be tar_json")
    return Plan(index, entry, receipt, members, records, json_members, skipped, expanded)


def print_plan(plan: Plan) -> None:
    print("=== M3.8.3 | SOFASCORE TAR MEMBER-ATOMIC INGESTION ===")
    print("Pinned revision:", REV)
    print("Database guard:", DB_NAME)
    print("Catalog index:", plan.index)
    print("Path:", TARGET)
    print("Compressed bytes:", EXPECTED_SIZE)
    print("GZIP expanded bytes (verified preflight):", plan.expanded)
    print("Expected TAR members:", plan.members)
    print("Expected JSON members:", plan.json_members)
    print("Expected non-JSON/skipped members:", plan.skipped_members)
    print("Expected parser records:", plan.records)
    print("Commit boundary: one complete archive member per DB transaction")
    print("Checkpoint: raw.raw_files metadata_evidence.committed_members + record_count")
    print("Retry policy: validate committed members and resume from next member")
    print("Snapshot policy: link only after complete verification; status unchanged")


def verify_prior(cur, plan: Plan, sha: str, size: int, archive_rel: str):
    cur.execute("""
        SELECT file_id,file_format,file_size_bytes,archive_path,archive_state,
               parse_state,record_count,metadata_evidence
          FROM raw.raw_files
         WHERE repo_id=%s AND relative_path=%s AND content_sha256=%s
         FOR UPDATE
    """, (REPO, TARGET, sha))
    row = cur.fetchone()
    if row is None:
        return None
    file_id, fmt, old_size, old_path, archive_state, state, count, evidence = row
    if (fmt, old_size, old_path, archive_state) != ("tar.gz", size, archive_rel, "verified"):
        raise RuntimeError("Existing DB file differs from pinned immutable archive")
    if state not in ("partial", "success"):
        raise RuntimeError(f"Existing file state {state!r} cannot be taken over")
    if type(count) is not int or not 0 <= count <= plan.records:
        raise RuntimeError("Stored file record_count is invalid")
    if state == "partial":
        if not isinstance(evidence, dict) or evidence.get("chunked_ingester") != MARKER:
            raise RuntimeError("Existing partial file was not created by this runner")
        if (evidence.get("expected_preflight_records") != plan.records or
                evidence.get("expected_preflight_members") != plan.members):
            raise RuntimeError("Stored partial file has different preflight expectations")
        member_checkpoint = evidence.get("committed_members")
        if type(member_checkpoint) is not int or not 0 <= member_checkpoint <= plan.members:
            raise RuntimeError("Invalid completed-member checkpoint")
    else:
        if count != plan.records:
            raise RuntimeError("Existing successful file has unexpected record count")
        member_checkpoint = plan.members
    cur.execute("SELECT count(*) FROM raw.raw_records WHERE file_id=%s", (file_id,))
    if cur.fetchone()[0] != count:
        raise RuntimeError("Stored record_count differs from physical DB records")
    cur.execute("""
        SELECT member_index,member_path,member_size_bytes,member_sha256,
               member_format,parse_state
          FROM raw.archive_members WHERE file_id=%s ORDER BY member_index
    """, (file_id,))
    members_rows = cur.fetchall()
    if len(members_rows) != member_checkpoint:
        raise RuntimeError("Stored committed_members differs from physical archive_members")
    # Materialized checkpoint members provide exact path/size/SHA to compare with parser on resume.
    return file_id, state, count, member_checkpoint, members_rows


def register_or_resume(conn, engine, plan: Plan, sha: str, size: int, archive_rel: str):
    from psycopg.types.json import Jsonb
    with conn.transaction():
        with conn.cursor() as cur:
            engine.db_guard(cur)
            source_id = engine.get_source(cur, plan.entry["source_hint"])
            sid = engine.get_snapshot(cur)
            existing = verify_prior(cur, plan, sha, size, archive_rel)
            if existing is not None:
                file_id, state, count, checkpoint, stored_members = existing
                if state == "success":
                    cur.execute("""
                        SELECT file_id,presence_status FROM raw.file_observations
                         WHERE snapshot_id=%s AND relative_path=%s
                    """, (sid, TARGET))
                    if cur.fetchone() != (file_id, "linked"):
                        raise RuntimeError("Successful file lacks correct snapshot observation")
                return file_id, sid, state, count, checkpoint, stored_members
            evidence = {
                "chunked_ingester": MARKER,
                "source_hint": plan.entry["source_hint"],
                "season_hint": plan.entry.get("season_hint") or None,
                "catalog_group": plan.entry["group"],
                "classification": "path_hint_only",
                "hf_revision_observed": REV,
                "expected_preflight_members": plan.members,
                "expected_preflight_records": plan.records,
                "committed_members": 0,
            }
            cur.execute("""
                INSERT INTO raw.raw_files
                  (repo_id,relative_path,source_id,file_format,content_sha256,file_size_bytes,
                   archive_path,archive_state,parse_state,record_count,league_scope,
                   season_scope,metadata_evidence)
                VALUES (%s,%s,%s,'tar.gz',%s,%s,%s,'verified','partial',0,'unknown','unknown',%s)
                RETURNING file_id
            """, (REPO, TARGET, source_id, sha, size, archive_rel, Jsonb(evidence)))
            fid = cur.fetchone()[0]
            return fid, sid, "partial", 0, 0, []


def consume_member_records(events, member, cur=None, fid=None, member_id=None):
    """Stream one member; return (first next ArchiveMember or None, rows in member).

    If cur is set, insert in batches of 250 inside caller's member transaction.
    Past checkpoint members are parsed/validated without any DB mutation.
    """
    from psycopg.types.json import Jsonb
    from raw_parser import ArchiveMember, ParsedRecord
    pending = []
    member_rows = 0

    def flush():
        if not pending:
            return
        cur.executemany("""
            INSERT INTO raw.raw_records(file_id,member_id,record_locator,record_kind,raw_data)
            VALUES (%s,%s,%s,%s,%s)
        """, [(fid, member_id, rec.record_locator, rec.record_kind, Jsonb(rec.raw_data))
              for rec in pending])
        pending.clear()

    while True:
        item = next(events, None)
        if item is None or isinstance(item, ArchiveMember):
            if cur is not None:
                flush()
            return item, member_rows
        if not isinstance(item, ParsedRecord):
            raise RuntimeError("Unknown TAR parser event")
        if (item.member_index != member.member_index or
                item.member_path != member.member_path or item.record_kind != "tar_json"):
            raise RuntimeError("TAR record belongs to wrong archive member")
        member_rows += 1
        if cur is not None:
            pending.append(item)
            if len(pending) >= BATCH_ROWS:
                flush()


def commit_one_member(conn, fid: int, member, events, completed_members: int):
    """ArchiveMember, records, and checkpoint advance atomically per TAR member."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO raw.archive_members
                  (file_id,member_index,member_path,member_size_bytes,
                   member_sha256,member_format,parse_state)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING member_id
            """, (fid, member.member_index, member.member_path,
                  member.member_size_bytes, member.member_sha256,
                  member.member_format, member.parse_state))
            mid = cur.fetchone()[0]
            next_member, count = consume_member_records(events, member, cur, fid, mid)
            cur.execute("""
                UPDATE raw.raw_files
                   SET record_count=record_count+%s,
                       metadata_evidence=jsonb_set(metadata_evidence,'{committed_members}',
                                                   to_jsonb(%s::bigint),true)
                 WHERE file_id=%s AND parse_state='partial'
                       AND (metadata_evidence->>'committed_members')::bigint=%s
                RETURNING record_count
            """, (count, completed_members+1, fid, completed_members))
            result = cur.fetchone()
            if result is None:
                raise RuntimeError("Member checkpoint race or unexpected file state")
            return next_member, count, result[0]


def finalize(conn, engine, fid, sid, plan: Plan, count: int, members: int, batch_id):
    if (count, members) != (plan.records, plan.members):
        raise RuntimeError("Final parser count or member count differs from preflight")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.raw_records WHERE file_id=%s", (fid,))
            if cur.fetchone()[0] != plan.records:
                raise RuntimeError("Final DB record count differs from preflight")
            cur.execute("SELECT count(*) FROM raw.archive_members WHERE file_id=%s", (fid,))
            if cur.fetchone()[0] != plan.members:
                raise RuntimeError("Final DB member count differs from preflight")
            cur.execute("""
                UPDATE raw.raw_files SET parse_state='success'
                 WHERE file_id=%s AND parse_state='partial' AND record_count=%s
                   AND (metadata_evidence->>'committed_members')::bigint=%s
                RETURNING file_id
            """, (fid, plan.records, plan.members))
            if cur.fetchone() is None:
                raise RuntimeError("Final checkpoint is incomplete")
            engine.link_observation(cur, sid, fid, plan.entry)
            engine.success_log(cur, batch_id, sid, fid, TARGET, "INSERTED_NEW", count)


def commit(plan: Plan) -> int:
    import psycopg
    scripts = str(ROOT / "scripts")
    tools = str(ROOT / "scripts" / "tools")
    for p in (scripts, tools):
        if p not in sys.path:
            sys.path.insert(0, p)
    import batch_ingestion as engine
    from raw_parser import ArchiveMember, ParsedRecord, iter_parse_events
    if (engine.ROOT.resolve() != ROOT.resolve() or engine.REPO != REPO
            or engine.REV != REV or engine.CATALOG.resolve() != CATALOG.resolve()):
        raise RuntimeError("Pinned M3.4 engine/catalog mismatch")
    print("Mode: COMMIT (only pinned Sofascore TAR.GZ; member-atomic)", flush=True)
    batch_id = uuid.uuid4()
    with psycopg.connect(**engine.connection_parameters()) as conn:
        with conn.cursor() as cur:
            engine.db_guard(cur)
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (GLOBAL_LOCK,))
            if not cur.fetchone()[0]:
                raise RuntimeError("Another ingestion process holds the global lock")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (REPO + ":" + TARGET,))
                if not cur.fetchone()[0]:
                    raise RuntimeError("Another ingestion process holds the per-file lock")
            try:
                archive, sha, size, stored_rel, action = engine.obtain_archive(plan.entry)
                if (sha, size) != (EXPECTED_SHA, EXPECTED_SIZE):
                    raise RuntimeError("Pinned immutable source hash/size mismatch")
                print("Pinned archive:", action, "|", stored_rel, flush=True)
                fid, sid, state, checkpoint_rows, checkpoint_members, previous = register_or_resume(
                    conn, engine, plan, sha, size, stored_rel)
                print("File ID:", fid, "| state:", state,
                      "| checkpoint members:", checkpoint_members,
                      "| checkpoint rows:", checkpoint_rows, flush=True)
                if state == "success":
                    print("M3.8.3 RESULT: SKIPPED_UNCHANGED (already success)")
                    return 0
                from raw_parser import ArchiveMember
                seen_members = 0
                seen_json = 0
                seen_skipped = 0
                seen_rows = 0
                durable_rows = checkpoint_rows
                events = iter(iter_parse_events(archive, "tar.gz"))
                member = next(events, None)
                while member is not None:
                    if not isinstance(member, ArchiveMember):
                        raise RuntimeError("Expected ArchiveMember event")
                    if seen_members >= plan.members:
                        raise RuntimeError("Parser emitted more members than preflight")
                    if member.member_format == "json" and member.parse_state == "success":
                        seen_json += 1
                    elif member.member_format == "other" and member.parse_state == "skipped":
                        seen_skipped += 1
                    else:
                        raise RuntimeError("Unexpected archive-member classification")
                    if seen_members < checkpoint_members:
                        stored = previous[seen_members]
                        fields = (member.member_index, member.member_path,
                                  member.member_size_bytes, member.member_sha256,
                                  member.member_format, member.parse_state)
                        if tuple(stored) != fields:
                            raise RuntimeError("Parser member differs from stored checkpoint")
                        following, count = consume_member_records(events, member)
                    else:
                        if seen_rows != checkpoint_rows and seen_members == checkpoint_members:
                            raise RuntimeError("Existing parser row count differs from checkpoint")
                        following, count, durable_rows = commit_one_member(
                            conn, fid, member, events, seen_members)
                        print(f"COMMITTED MEMBER | {seen_members+1:,}/{plan.members:,}"
                              f" | DB records={durable_rows:,}/{plan.records:,}", flush=True)
                    seen_rows += count
                    seen_members += 1
                    if seen_rows > plan.records:
                        raise RuntimeError("Parser yielded more records than preflight")
                    if seen_members == checkpoint_members and seen_rows != checkpoint_rows:
                        raise RuntimeError("Checkpoint row boundary mismatch")
                    member = following
                if (seen_members, seen_json, seen_skipped, seen_rows) != (
                        plan.members, plan.json_members, plan.skipped_members, plan.records):
                    raise RuntimeError("Full TAR parser totals disagree with preflight")
                if durable_rows != plan.records:
                    raise RuntimeError("DB durable row total disagrees with preflight")
                finalize(conn, engine, fid, sid, plan, seen_rows, seen_members, batch_id)
                print("M3.8.3 FINAL FILE STATUS: success | snapshot observation: linked", flush=True)
                print("M3.8.3 RESULT: PASS", flush=True)
                return 0
            except Exception as exc:
                engine.log_failure(conn, batch_id, TARGET, exc)
                raise
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (REPO + ":" + TARGET,))
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (GLOBAL_LOCK,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--plan", action="store_true")
    actions.add_argument("--commit", action="store_true")
    parser.add_argument("--confirm-tar", action="store_true")
    args = parser.parse_args()
    if args.commit != args.confirm_tar:
        parser.error("--confirm-tar required for COMMIT and forbidden for PLAN")
    plan = read_plan()
    print_plan(plan)
    if args.plan:
        print("Mode: PLAN (local catalog/preflight only; no database/network/writes)")
        print("M3.8.3 PLAN: PASS")
        return 0
    return commit(plan)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M3.8.3 RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        print("Existing member checkpoints are durable; review failure before retrying.", file=sys.stderr)
        raise SystemExit(1)
