"""A5.2 | Bounded, snapshot-scoped, read-only PostgreSQL JSONB access.

Usage (from repository root):
  python -B -m scripts.access.raw_data_access --list-files --snapshot-id 367 --limit 10
  python -B -m scripts.access.raw_data_access --records --snapshot-id 367 --path '...' --limit 2

No DDL, DML, file exports, database mutations or Hugging Face traffic.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from typing import Callable, Any

EXPECTED_DB = "epl_raw_storage_v2"
MAX_PAGE = 50
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_PATH_CHARS = 1024
SHA40 = re.compile(r"[0-9a-f]{40}\Z")


class AccessBlocked(RuntimeError):
    """Access is not verified read-only or a contract check failed."""


def validate_snapshot_id(snapshot_id: int) -> None:
    if type(snapshot_id) is not int or snapshot_id <= 0:
        raise AccessBlocked("Positive integer snapshot ID required")


def validate_page_size(limit: int) -> None:
    if type(limit) is not int or not (1 <= limit <= MAX_PAGE):
        raise AccessBlocked(f"Page size must be between 1 and {MAX_PAGE}")


def validate_relative_path(path: str) -> None:
    if (not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS
            or path.startswith("/") or "\\" in path
            or any(component in ("", ".", "..") for component in path.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise AccessBlocked("Unsafe snapshot file path")


def connect_readonly():
    """Use the *same* read-only DB parameters already validated by A5.1."""
    import psycopg
    from scripts.tools.incremental_completed_snapshot_audit import db_parameters

    params = db_parameters(readonly=True)
    if not isinstance(params, dict):
        raise AccessBlocked("Read-only database parameter source returned unexpected data")
    params = dict(params)
    params["application_name"] = "epl_a5_raw_data_handover_readonly"
    return psycopg.connect(**params)


def verify_readonly(cur: Any) -> None:
    cur.execute("SELECT current_database(), current_setting('transaction_read_only')")
    item = cur.fetchone()
    if item != (EXPECTED_DB, "on"):
        raise AccessBlocked("Wrong PostgreSQL database or transaction is NOT read-only")


def get_snapshot(cur: Any, snapshot_id: int) -> dict:
    validate_snapshot_id(snapshot_id)
    cur.execute("""
        SELECT snapshot_id, status, hf_revision,
               manifest_file_count, raw_scope_file_count
        FROM raw.snapshot_runs WHERE snapshot_id = %s
    """, (snapshot_id,))
    row = cur.fetchone()
    if row is None:
        raise AccessBlocked("Snapshot does not exist")
    sid, status, revision, full, raw = row
    if (sid != snapshot_id or status != "completed"
            or not isinstance(revision, str) or not SHA40.fullmatch(revision)
            or type(full) is not int or type(raw) is not int
            or full < raw or raw <= 0):
        raise AccessBlocked("Snapshot must be completed with consistent identity/counts")
    return {"snapshot_id": sid, "status": status, "hf_revision": revision,
            "manifest_file_count": full, "raw_scope_file_count": raw}


def list_files_page(cur: Any, *, snapshot_id: int, limit: int = 20,
                    after_path: str | None = None) -> dict:
    """Keyset page of file observations; no scan of 8.5M record bodies."""
    validate_page_size(limit)
    if after_path is not None:
        validate_relative_path(after_path)
    snap = get_snapshot(cur, snapshot_id)
    cur.execute("""
        SELECT o.relative_path, o.file_id, f.file_format, f.record_count
        FROM raw.file_observations AS o
        JOIN raw.raw_files AS f ON f.file_id = o.file_id
        WHERE o.snapshot_id = %s AND o.relative_path > %s
        ORDER BY o.relative_path ASC
        LIMIT %s
    """, (snapshot_id, after_path or "", limit + 1))
    fetched = cur.fetchall()
    if len(fetched) > limit + 1:
        raise AccessBlocked("Database returned more rows than the requested page bound")
    more = len(fetched) > limit
    rows = fetched[:limit]
    files = [dict(relative_path=p, file_id=fid, file_format=fmt, record_count=count)
             for p, fid, fmt, count in rows]
    if any(type(x["file_id"]) is not int or x["file_id"] <= 0 for x in files):
        raise AccessBlocked("Invalid file version in snapshot")
    return {"state": "VERIFIED_READONLY", "snapshot": snap, "files": files,
            "returned_files": len(files),
            "next_after_path": rows[-1][0] if more else None,
            "write_authorized": False, "write_executed": False}


def records_page(cur: Any, *, snapshot_id: int, relative_path: str,
                 limit: int = 5, after_record_id: int = 0) -> dict:
    """Return a bounded page of JSONB rows for exactly one observed file version.

    Sort/keyset by record_id; reused raw_files remain visible in newer snapshots.
    """
    validate_page_size(limit)
    validate_relative_path(relative_path)
    if type(after_record_id) is not int or after_record_id < 0:
        raise AccessBlocked("Cursor record ID must be a non-negative integer")
    snap = get_snapshot(cur, snapshot_id)
    cur.execute("""
        SELECT file_id FROM raw.file_observations
        WHERE snapshot_id = %s AND relative_path = %s
        LIMIT 2
    """, (snapshot_id, relative_path))
    identities = cur.fetchall()
    if len(identities) != 1 or type(identities[0][0]) is not int:
        raise AccessBlocked("Exactly one observed file version required")
    file_id = identities[0][0]
    cur.execute("""
        SELECT record_id, member_id, record_locator, record_kind, raw_data,
               league_code, season, event_time, metadata_evidence, ingested_at
        FROM raw.raw_records
        WHERE file_id = %s AND record_id > %s
        ORDER BY record_id ASC
        LIMIT %s
    """, (file_id, after_record_id, limit + 1))
    fetched = cur.fetchall()
    if len(fetched) > limit + 1:
        raise AccessBlocked("Database returned more records than bounded page")
    more = len(fetched) > limit
    rows = fetched[:limit]
    records = []
    prev = after_record_id
    for row in rows:
        rid, member, locator, kind, payload, league, season, event, metadata, ingested = row
        if type(rid) is not int or rid <= prev:
            raise AccessBlocked("Record IDs must be strictly increasing")
        prev = rid
        records.append({"record_id": rid, "file_id": file_id, "member_id": member,
                        "record_locator": locator, "record_kind": kind,
                        "raw_data": payload, "league_code": league, "season": season,
                        "event_time": event, "metadata_evidence": metadata,
                        "ingested_at": ingested})
    return {"state": "VERIFIED_READONLY", "snapshot": snap,
            "relative_path": relative_path, "file_id": file_id,
            "records": records, "returned_records": len(records),
            "next_after_record_id": rows[-1][0] if more else None,
            "write_authorized": False, "write_executed": False}


def json_default(obj: Any):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Unsupported JSON value type: {type(obj).__name__}")


def encode_bounded(result: dict) -> str:
    payload = json.dumps(result, ensure_ascii=False, indent=2,
                         default=json_default, allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise AccessBlocked("Page output exceeds 8 MiB budget; reduce --limit")
    return payload


def run(*, snapshot_id: int, mode: str, limit: int,
        path: str | None = None, after_path: str | None = None,
        after_record_id: int = 0, connector: Callable[[], Any] = connect_readonly) -> dict:
    validate_snapshot_id(snapshot_id)
    validate_page_size(limit)
    if mode not in ("files", "records"):
        raise AccessBlocked("Unknown read-only operation")
    if mode == "files" and (path is not None or after_record_id != 0):
        raise AccessBlocked("File list cannot accept record options")
    if mode == "records" and (path is None or after_path is not None):
        raise AccessBlocked("Record access requires one file path")
    # Forbid making any DB connection for invalid paths/cursors.
    if path is not None:
        validate_relative_path(path)
    if after_path is not None:
        validate_relative_path(after_path)
    if type(after_record_id) is not int or after_record_id < 0:
        raise AccessBlocked("Invalid record cursor")
    with connector() as conn:
        with conn.cursor() as cur:
            verify_readonly(cur)
            if mode == "files":
                return list_files_page(cur, snapshot_id=snapshot_id,
                                       limit=limit, after_path=after_path)
            return records_page(cur, snapshot_id=snapshot_id,
                                relative_path=path, limit=limit,
                                after_record_id=after_record_id)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-files", action="store_true")
    mode.add_argument("--records", action="store_true")
    p.add_argument("--snapshot-id", type=int, required=True)
    p.add_argument("--path", help="Exact relative_path returned by --list-files")
    p.add_argument("--after-path", help="File-list continuation cursor")
    p.add_argument("--after-record-id", type=int, default=0)
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args(argv)
    if args.list_files and args.path is not None:
        p.error("--path only valid with --records")
    if args.records and args.path is None:
        p.error("--records requires --path")
    if args.records and args.after_path is not None:
        p.error("--after-path only valid with --list-files")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(snapshot_id=args.snapshot_id,
                     mode="files" if args.list_files else "records",
                     limit=args.limit, path=args.path, after_path=args.after_path,
                     after_record_id=args.after_record_id)
        print(encode_bounded(result))
        return 0
    except Exception as exc:
        # Do not dump SQL DSN/credentials/traceback to user-facing logs.
        print(json.dumps({"state": "BLOCKED", "reason_type": type(exc).__name__,
                          "write_authorized": False, "write_executed": False}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
