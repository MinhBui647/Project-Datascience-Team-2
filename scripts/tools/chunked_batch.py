#!/usr/bin/env python3
"""M3.4D: small resumable windows over a pinned 276-file manifest.

Install: raw_storage/scripts/tools/chunked_batch.py

From project root:
  .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\chunked_batch.py --plan --offset 4 --limit 10
  .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\chunked_batch.py --commit --confirm-small-batch --offset 4 --limit 10

The PLAN mode only reads the local CSV catalog; it does not connect to HF/DB.
COMMIT calls the previously tested M3.4 ingestion functions, creates one
transaction per file (autocommit=True), and leaves snapshot status unchanged.
There is NO --all flag; files larger than the cap and TAR/CSV.GZ are deferred.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
ALLOWED = {"json", "csv"}
EXPECTED_SCOPE = 276
DB_NAME = "epl_raw_storage_v2"
BATCH_LOCK = "epl_raw_storage_v2:m3_4d:single_batch_writer"


def load_catalog() -> list[dict[str, str]]:
    if not CATALOG.is_file():
        raise RuntimeError(f"Missing catalog: {CATALOG}")
    with CATALOG.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"relative_path", "group", "source_hint", "format", "size_bytes", "hf_revision"}
        if not required <= set(reader.fieldnames or []):
            raise RuntimeError(f"Missing catalog columns: {sorted(required - set(reader.fieldnames or []))}")
        rows = list(reader)
    if len(rows) != EXPECTED_SCOPE:
        raise RuntimeError(f"Expected {EXPECTED_SCOPE} raw paths, got {len(rows)}")
    seen: set[str] = set()
    for row in rows:
        path = row["relative_path"]
        parts = path.split("/")
        if (not path or path.startswith("/") or "\\" in path or "//" in path
                or any(part in ("", ".", "..") for part in parts)):
            raise RuntimeError(f"Unsafe relative_path: {path!r}")
        if path in seen:
            raise RuntimeError(f"Duplicate catalog path: {path}")
        seen.add(path)
        if row["hf_revision"] != REV:
            raise RuntimeError(f"Catalog revision mismatch: {path}")
        if row["group"] not in ("raw_data", "2026_2027_new"):
            raise RuntimeError(f"Not in raw scope: {path}")
        if row["format"] not in ("json", "csv", "csv.gz", "tar.gz"):
            raise RuntimeError(f"Unexpected format: {path}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", row["source_hint"]):
            raise RuntimeError(f"Unsafe source code: {path}")
        try:
            n = int(row["size_bytes"])
        except (ValueError, TypeError):
            raise RuntimeError(f"Bad file size: {path}") from None
        if n < 0:
            raise RuntimeError(f"Negative file size: {path}")
    return sorted(rows, key=lambda x: x["relative_path"])


def plan_window(rows: list[dict[str, str]], offset: int, limit: int, size_cap: int):
    if offset >= len(rows):
        raise RuntimeError(f"Offset {offset} exceeds catalog last index {len(rows) - 1}")
    selected = rows[offset:offset + limit]
    planned: list[tuple[int, dict[str, str]]] = []
    deferred: list[tuple[int, dict[str, str], str]] = []
    for idx, entry in enumerate(selected, start=offset):
        size = int(entry["size_bytes"])
        if entry["format"] not in ALLOWED:
            deferred.append((idx, entry, f"FORMAT_{entry['format'].upper()}"))
        elif size > size_cap:
            deferred.append((idx, entry, "LARGE_FILE"))
        else:
            planned.append((idx, entry))
    return selected, planned, deferred


def render_plan(rows, offset, limit, size_cap):
    window, planned, deferred = plan_window(rows, offset, limit, size_cap)
    print("=== M3.4D | CHUNKED BATCH WINDOW ===")
    print("Repo:", REPO)
    print("Pinned revision:", REV)
    print("Catalog scope:", len(rows))
    print(f"Window: [{offset}, {offset + len(window)}) of {len(rows)} (zero-based)")
    print("Compressed file size cap:", size_cap, "bytes")
    print("Allowlisted formats: csv, json (csv.gz and tar.gz deferred)")
    for index, entry in planned:
        print(f"PROCESS {index:03} | {entry['format']:6} | {int(entry['size_bytes']):>10} | {entry['relative_path']}")
    for index, entry, reason in deferred:
        print(f"DEFER   {index:03} | {reason:14} | {int(entry['size_bytes']):>10} | {entry['relative_path']}")
    print(f"Window entries: {len(window)} | Process: {len(planned)} | Deferred: {len(deferred)}")
    return planned, deferred


def commit_batch(planned, deferred, offset, limit):
    import psycopg  # noqa: PLC0415
    sys.path.insert(0, str(ROOT / "scripts" / "tools"))
    import batch_ingestion as engine  # noqa: PLC0415

    if (engine.ROOT.resolve() != ROOT.resolve() or engine.REPO != REPO
            or engine.REV != REV or engine.CATALOG.resolve() != CATALOG.resolve()):
        raise RuntimeError("M3.4 runner does not match this pinned project/revision")

    batch_id = uuid.uuid4()
    result = {
        "batch_id": str(batch_id), "runner": "M3.4D", "repo_id": REPO, "revision": REV,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "offset": offset, "limit": limit, "selected_count": len(planned),
        "deferred_count": len(deferred), "inserted_versions": 0, "skipped_unchanged": 0,
        "failed_files": 0, "rows_inserted": 0, "results": [],
        "deferred": [
            {"index": i, "path": r["relative_path"], "reason": reason}
            for i, r, reason in deferred
        ],
        "snapshot_status": "discovered",
    }
    # autocommit=True is deliberate: engine.ingest_file uses conn.transaction().
    # Without it, an initial SELECT could cause all files to share one outer txn.
    with psycopg.connect(**engine.connection_parameters()) as conn:
        with conn.cursor() as cur:
            engine.db_guard(cur)
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (BATCH_LOCK,))
            got_lock = cur.fetchone()[0]
        if not got_lock:
            raise RuntimeError("Another M3.4D batch is currently running; refusing concurrent commit")
        print("Connected database:", DB_NAME)
        print("Batch ID:", batch_id)
        try:
            for i, entry in planned:
                path = entry["relative_path"]
                try:
                    stored, sha, size, stored_rel, archive_action = engine.obtain_archive(entry)
                    action, inserted, total, members = engine.ingest_file(
                        conn, batch_id, entry, stored, sha, size, stored_rel
                    )
                    if action == "INSERTED_NEW":
                        result["inserted_versions"] += 1
                    elif action == "SKIPPED_UNCHANGED":
                        result["skipped_unchanged"] += 1
                    else:
                        raise RuntimeError(f"Unexpected action: {action}")
                    result["rows_inserted"] += inserted
                    result["results"].append({
                        "index": i, "path": path, "status": action,
                        "sha256": sha, "archive_action": archive_action,
                        "rows_inserted": inserted, "record_count": total,
                        "archive_member_count": members,
                    })
                    print(f"[{i:03}] {action} | +{inserted} records | total {total} | {path}")
                except Exception as exc:
                    result["failed_files"] += 1
                    result["results"].append({
                        "index": i, "path": path, "status": "FAILED",
                        "error": f"{type(exc).__name__}: {exc}"[:800],
                    })
                    print(f"[{i:03}] FAILED | {path} | {type(exc).__name__}: {exc}", file=sys.stderr)
                    engine.log_failure(conn, batch_id, path, exc)
                    # Stop at the first error for the FIRST production-like batch.
                    # Already successful files remain committed and can be skipped on retry.
                    break
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (BATCH_LOCK,))

    report = engine.write_report(result)
    print("Inserted file versions:", result["inserted_versions"])
    print("Skipped unchanged:", result["skipped_unchanged"])
    print("Failed files:", result["failed_files"])
    print("Records inserted this run:", result["rows_inserted"])
    print("Deferred in window:", len(deferred))
    print("Report:", report.relative_to(ROOT).as_posix())
    print("Snapshot remains: discovered (not complete)")
    if result["failed_files"]:
        print("M3.4D RESULT: PARTIAL_FAILURE (see report; safe to resume)")
        return 1
    print("M3.4D RESULT: PASS")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--commit", action="store_true")
    parser.add_argument("--offset", type=int, default=4, help="0-based index in sorted full catalog")
    parser.add_argument("--limit", type=int, default=10, help="1..20 catalog paths per window")
    parser.add_argument("--max-file-mib", type=int, default=5, help="compressed/source file cap: 1..10 MiB")
    parser.add_argument("--confirm-small-batch", action="store_true", help="required for --commit")
    args = parser.parse_args()
    if args.offset < 0 or not 1 <= args.limit <= 20 or not 1 <= args.max_file_mib <= 10:
        parser.error("Require --offset >=0, 1<=--limit<=20, 1<=--max-file-mib<=10")
    if args.commit and not args.confirm_small_batch:
        parser.error("--commit requires --confirm-small-batch after reviewing --plan")
    if args.plan and args.confirm_small_batch:
        parser.error("--confirm-small-batch is only valid with --commit")
    rows = load_catalog()
    planned, deferred = render_plan(rows, args.offset, args.limit, args.max_file_mib * 1024 * 1024)
    if args.plan:
        print("Mode: PLAN (offline, no DB/network/writes)")
        print("M3.4D PLAN: PASS")
        return 0
    if not planned:
        raise RuntimeError("No eligible files in this window; cannot --commit")
    print("Mode: COMMIT (per-file transactions; pinned downloads)")
    return commit_batch(planned, deferred, args.offset, args.limit)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M3.4D RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
