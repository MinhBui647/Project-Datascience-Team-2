"""M4.4.6: Export one small, complete raw file for handover checkpoint test.

Read-only PostgreSQL via RawDataReader; write local JSONL and SHA256 manifest.
Does not mark any Cleaning acknowledgement or modify raw/handover tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .raw_reader import RawDataReader
else:
    from raw_reader import RawDataReader

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = "2026_2027_new/football_data_raw_2627.csv"
MAX_SAFE_ROWS = 10000
FIELDS = (
    "snapshot_id", "source_revision", "raw_record_id", "file_id",
    "record_locator", "record_kind", "source_code", "source_path",
    "source_format", "archive_member_path", "ingested_at", "raw_data",
)


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_context(reader: RawDataReader, path: str) -> dict:
    with reader.conn.cursor() as cur:
        cur.execute("""
            SELECT f.file_id, f.record_count, f.file_format, f.content_sha256::text,
                   s.hf_revision, COUNT(r.record_id) AS actual_count
            FROM raw.snapshot_runs s
            JOIN raw.file_observations o ON o.snapshot_id=s.snapshot_id
            JOIN raw.raw_files f ON f.file_id=o.file_id
            LEFT JOIN raw.raw_records r ON r.file_id=f.file_id
            WHERE s.snapshot_id=%s AND s.status='completed'
              AND o.presence_status='linked' AND f.relative_path=%s
              AND f.parse_state='success' AND f.archive_state='verified'
            GROUP BY f.file_id, f.record_count, f.file_format, f.content_sha256,
                     s.hf_revision
        """, (reader.snapshot_id, path))
        row = cur.fetchone()
    if not row:
        raise ValueError("Source path not found in completed snapshot")
    if row["record_count"] is None or row["record_count"] != row["actual_count"]:
        raise RuntimeError("Metadata / physical record count mismatch")
    if row["actual_count"] < 1 or row["actual_count"] > MAX_SAFE_ROWS:
        raise ValueError(f"For pilot only, require 1..{MAX_SAFE_ROWS} records")
    return dict(row)


def paths(snapshot_id: int, source_path: str) -> tuple[Path, Path]:
    root = ROOT / "exports" / "raw_handover"
    stem = Path(source_path).name.replace(".", "_")
    token = hashlib.sha256(source_path.encode()).hexdigest()[:12]
    base = f"snapshot_{snapshot_id}_{stem}_{token}_full_file"
    return root / f"{base}.jsonl", root / f"{base}.manifest.json"


def run(snapshot_id: int, source_path: str, batch_size: int, commit: bool) -> None:
    if snapshot_id < 1 or not source_path or source_path.startswith(("/", "\\")):
        raise ValueError("Invalid snapshot/source path")
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch-size must be 1..1000")
    with RawDataReader(snapshot_id) as reader:
        meta = file_context(reader, source_path)
        total = int(meta["actual_count"])
        dest, manifest_dest = paths(snapshot_id, source_path)
        print("=== M4.4.6 | FULL FILE JSONL HANDOVER EXPORT ===")
        print("Snapshot:", snapshot_id, "| status: completed")
        print("Path:", source_path)
        print("File ID:", meta["file_id"])
        print("Expected complete records:", total)
        print("Batch size:", batch_size)
        print("Target JSONL:", dest.relative_to(ROOT))
        print("Target manifest:", manifest_dest.relative_to(ROOT))
        if not commit:
            print("Mode: PLAN (read-only; no files/DB writes)")
            print("M4.4.6 FULL FILE EXPORT PLAN: PASS")
            return

        if dest.exists() or manifest_dest.exists():
            raise FileExistsError("Full-file output already exists; do not overwrite: " + str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".m4_4_6_", suffix=".tmp", dir=dest.parent)
        tmp = Path(tmp_name)
        n, first, last = 0, None, None
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                for batch in reader.iter_batches(source_path=source_path, limit=batch_size):
                    for row in batch:
                        rid = row["raw_record_id"]
                        if set(FIELDS).difference(row):
                            raise RuntimeError("Reader contract missing fields")
                        if (row["snapshot_id"] != snapshot_id or
                            row["file_id"] != meta["file_id"] or
                            row["source_path"] != source_path or
                            row["source_revision"] != meta["hf_revision"] or
                            row["source_format"] != meta["file_format"]):
                            raise RuntimeError("Provenance mismatch")
                        if not isinstance(row["raw_data"], (dict, list)):
                            raise RuntimeError("Unexpected JSON payload")
                        if last is not None and rid <= last:
                            raise RuntimeError("Duplicate/out-of-order raw record ID")
                        obj = {key: row[key] for key in FIELDS}
                        out.write(json.dumps(obj, ensure_ascii=False, default=json_default, separators=(",", ":")) + "\n")
                        first = rid if first is None else first
                        last = rid
                        n += 1
                        if n > total:
                            raise RuntimeError("Reader returned more than expected")
                out.flush()
                os.fsync(out.fileno())
            if n != total:
                raise RuntimeError(f"Incomplete export: {n} of {total}")
            h = sha256_file(tmp)
            m = {
                "contract_version": "raw-handover-v1",
                "scope": "full_file",
                "snapshot_id": snapshot_id,
                "hf_revision": meta["hf_revision"],
                "file_id": meta["file_id"],
                "source_path": source_path,
                "source_format": meta["file_format"],
                "source_content_sha256": meta["content_sha256"].strip(),
                "record_count": n,
                "first_raw_record_id": first,
                "last_raw_record_id": last,
                "jsonl_file": dest.name,
                "jsonl_sha256": h,
                "jsonl_bytes": tmp.stat().st_size,
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            # Verify JSONL payload integrity before publishing the files.
            with tmp.open("r", encoding="utf-8") as scan:
                if sum(1 for line in scan if json.loads(line)["file_id"] == meta["file_id"]) != n:
                    raise RuntimeError("JSONL verification failed")
            tmp.replace(dest)
            manifest_tmp = manifest_dest.with_suffix(".tmp")
            try:
                with manifest_tmp.open("x", encoding="utf-8") as fp:
                    fp.write(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
                    fp.flush()
                    os.fsync(fp.fileno())
                manifest_tmp.replace(manifest_dest)
            finally:
                manifest_tmp.unlink(missing_ok=True)
            print("Rows exported:", n)
            print("First raw_record_id:", first)
            print("Last raw_record_id:", last)
            print("JSONL SHA256:", h)
            print("Manifest scope: full_file")
            print("Database writes: NONE")
            print("M4.4.6 FULL FILE EXPORT: PASS")
        finally:
            tmp.unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(description="One-file full export for handover checkpoint pilot")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--export", action="store_true")
    p.add_argument("--confirm-full-file", action="store_true")
    p.add_argument("--snapshot-id", type=int, default=4)
    p.add_argument("--source-path", default=DEFAULT_SOURCE)
    p.add_argument("--batch-size", type=int, default=20)
    args = p.parse_args()
    if args.export and not args.confirm_full_file:
        p.error("--export requires --confirm-full-file")
    run(args.snapshot_id, args.source_path, args.batch_size, commit=args.export)


if __name__ == "__main__":
    main()
