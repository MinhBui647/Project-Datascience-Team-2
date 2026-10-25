"""M4.3.1: bounded, verifiable JSONL handover sample from completed raw snapshot.

Reads PostgreSQL using RawDataReader (never writes DB). Creates local JSONL and
SHA256 manifest. Does not alter payloads or filter by EPL/season.
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = "2026_2027_new/football_data_raw_2627.csv"
REQUIRED_KEYS = (
    "snapshot_id", "source_revision", "raw_record_id", "file_id",
    "record_locator", "record_kind", "source_code", "source_path",
    "source_format", "archive_member_path", "ingested_at", "raw_data",
)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Non-JSON-serializable type: {type(value).__name__}")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _safe_output_paths(root: Path, snapshot_id: int, source_path: str, count: int) -> tuple[Path, Path]:
    short_stem = Path(source_path).name.replace(".", "_")
    identity = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:12]
    base = f"snapshot_{snapshot_id}_{short_stem}_{identity}_first{count}"
    directory = root / "exports" / "raw_handover"
    return directory / f"{base}.jsonl", directory / f"{base}.manifest.json"


def run_export(snapshot_id: int, source_path: str, max_records: int, batch_size: int, root: Path = PROJECT_ROOT) -> tuple[Path, Path, dict[str, Any]]:
    if snapshot_id <= 0:
        raise ValueError("snapshot-id must be positive")
    if not source_path or source_path.startswith(("/", "\\")):
        raise ValueError("Provide a relative source-path")
    if not 1 <= max_records <= 10000:
        raise ValueError("max-records must be 1..10000 for a bounded handover sample")
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch-size must be 1..1000")
    output_path, manifest_path = _safe_output_paths(root, snapshot_id, source_path, max_records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() or manifest_path.exists():
        raise FileExistsError("Export or manifest already exists; verify it or choose another max-records value: " + str(output_path))

    fd, temp_name = tempfile.mkstemp(prefix=".m4_3_1_", suffix=".tmp", dir=output_path.parent)
    temp = Path(temp_name)
    count = 0
    first_id = None
    last_id = None
    source_revision = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            with RawDataReader(snapshot_id=snapshot_id, root=root) as reader:
                for batch in reader.iter_batches(source_path=source_path, limit=batch_size):
                    for row in batch:
                        if count >= max_records:
                            break
                        missing = set(REQUIRED_KEYS).difference(row)
                        if missing:
                            raise RuntimeError("Reader contract is missing columns: " + ", ".join(sorted(missing)))
                        rec_id = row["raw_record_id"]
                        if not isinstance(rec_id, int) or (last_id is not None and rec_id <= last_id):
                            raise RuntimeError("Record IDs must be strictly increasing")
                        if row["snapshot_id"] != snapshot_id or row["source_path"] != source_path:
                            raise RuntimeError("Snapshot or path mismatch in reader output")
                        if not isinstance(row["raw_data"], (dict, list)):
                            raise RuntimeError("Expected JSONB object/array payload")
                        if source_revision is None:
                            source_revision = row["source_revision"]
                        elif source_revision != row["source_revision"]:
                            raise RuntimeError("Multiple source revisions in one export")
                        export_row = {name: row[name] for name in REQUIRED_KEYS}
                        fh.write(json.dumps(export_row, ensure_ascii=False, default=_json_default, separators=(",", ":")) + "\n")
                        first_id = rec_id if first_id is None else first_id
                        last_id = rec_id
                        count += 1
                    if count >= max_records:
                        break
            fh.flush()
            os.fsync(fh.fileno())
        if count == 0:
            raise RuntimeError("No records found for the requested completed snapshot/path")
        archive_sha256 = _sha256_file(temp)
        manifest = {
            "contract_version": "raw-handover-v1",
            "snapshot_id": snapshot_id,
            "hf_revision": source_revision,
            "source_path": source_path,
            "jsonl_file": output_path.name,
            "jsonl_sha256": archive_sha256,
            "jsonl_bytes": temp.stat().st_size,
            "record_count": count,
            "first_raw_record_id": first_id,
            "last_raw_record_id": last_id,
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "bounded sample; not full snapshot",
        }
        # Validate the generated bytes before publishing output files.
        scan_count = 0
        with temp.open("r", encoding="utf-8") as fh:
            for line in fh:
                obj = json.loads(line)
                assert obj["snapshot_id"] == snapshot_id
                assert obj["source_path"] == source_path
                scan_count += 1
        if scan_count != count:
            raise RuntimeError("JSONL verification count mismatch")
        temp.replace(output_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return output_path, manifest_path, manifest
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="M4.3.1: bounded read-only raw handover export to local JSONL")
    parser.add_argument("--snapshot-id", type=int, default=4)
    parser.add_argument("--source-path", default=DEFAULT_PATH)
    parser.add_argument("--max-records", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()
    print("=== M4.3.1 | RAW JSONL HANDOVER EXPORT ===")
    print("Snapshot:", args.snapshot_id)
    print("Source path:", args.source_path)
    print("Max records:", args.max_records)
    print("Batch size:", args.batch_size)
    output_path, manifest_path, manifest = run_export(args.snapshot_id, args.source_path, args.max_records, args.batch_size)
    print("Rows exported:", manifest["record_count"])
    print("First raw_record_id:", manifest["first_raw_record_id"])
    print("Last raw_record_id:", manifest["last_raw_record_id"])
    print("JSONL:", output_path.relative_to(PROJECT_ROOT))
    print("Manifest:", manifest_path.relative_to(PROJECT_ROOT))
    print("JSONL SHA256:", manifest["jsonl_sha256"])
    print("Database writes: NONE")
    print("M4.3.1 JSONL EXPORT: PASS")


if __name__ == "__main__":
    main()
