"""M4.2.2: Read-only, snapshot-pinned Raw Storage reader.

Physical schema: raw.snapshot_runs -> raw.file_observations -> raw.raw_files
                 -> raw.raw_records (+ raw_sources, archive_members).
No changes to raw tables, snapshot, or source JSONB payloads.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterator

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_BATCH_SIZE = 1_000


def load_db_config(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    env = {
        **dotenv_values(root / ".env", encoding="utf-8-sig"),
        **dotenv_values(root / ".env.v2", encoding="utf-8-sig"),
    }
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise RuntimeError("Missing database config keys: " + ", ".join(missing))
    if env["DB_NAME"] != "epl_raw_storage_v2":
        raise RuntimeError("Database guard failed: DB_NAME must be epl_raw_storage_v2")
    return {
        "host": env["DB_HOST"],
        "port": int(env["DB_PORT"]),
        "dbname": env["DB_NAME"],
        "user": env["DB_USER"],
        "password": env["DB_PASSWORD"],
    }


class RawDataReader:
    """Snapshot-scoped batch reader; suitable for direct Cleaning or JSONL export."""

    def __init__(self, snapshot_id: int, root: Path = PROJECT_ROOT):
        if snapshot_id <= 0:
            raise ValueError("snapshot_id must be positive")
        self.snapshot_id = snapshot_id
        self.root = root
        self.conn: psycopg.Connection | None = None

    def __enter__(self) -> "RawDataReader":
        self.conn = psycopg.connect(
            **load_db_config(self.root),
            row_factory=dict_row,
            autocommit=True,
            options="-c default_transaction_read_only=on -c statement_timeout=120000",
        )
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT current_database() AS db")
                if cur.fetchone()["db"] != "epl_raw_storage_v2":
                    raise RuntimeError("Connected to wrong database")
                cur.execute(
                    "SELECT status FROM raw.snapshot_runs WHERE snapshot_id = %s",
                    (self.snapshot_id,),
                )
                snapshot = cur.fetchone()
                if not snapshot:
                    raise RuntimeError(f"Snapshot #{self.snapshot_id} does not exist")
                if snapshot["status"] != "completed":
                    raise RuntimeError(
                        f"Snapshot #{self.snapshot_id} is not completed: {snapshot['status']}"
                    )
            return self
        except Exception:
            self.conn.close()
            self.conn = None
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def fetch_batch(
        self,
        *,
        after_record_id: int = 0,
        limit: int = 100,
        source_code: str | None = None,
        source_path: str | None = None,
        file_format: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.conn is None:
            raise RuntimeError("Use RawDataReader inside a 'with' block")
        if after_record_id < 0:
            raise ValueError("after_record_id must be >= 0")
        if not 1 <= limit <= MAX_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}")

        sql = """
            SELECT
                o.snapshot_id,
                sn.hf_revision AS source_revision,
                r.record_id AS raw_record_id,
                r.file_id,
                r.record_locator,
                r.record_kind,
                s.source_code,
                f.relative_path AS source_path,
                f.file_format AS source_format,
                m.member_path AS archive_member_path,
                r.ingested_at,
                r.raw_data
            FROM raw.raw_records AS r
            JOIN raw.raw_files AS f ON f.file_id = r.file_id
            JOIN raw.file_observations AS o ON o.file_id = f.file_id
            JOIN raw.snapshot_runs AS sn ON sn.snapshot_id = o.snapshot_id
            LEFT JOIN raw.raw_sources AS s ON s.source_id = f.source_id
            LEFT JOIN raw.archive_members AS m
                ON m.member_id = r.member_id AND m.file_id = r.file_id
            WHERE o.snapshot_id = %s
              AND sn.status = 'completed'
              AND o.presence_status = 'linked'
              AND f.parse_state = 'success'
              AND f.archive_state = 'verified'
              AND r.record_id > %s
        """
        params: list[Any] = [self.snapshot_id, after_record_id]
        if source_code is not None:
            sql += " AND s.source_code = %s"
            params.append(source_code)
        if source_path is not None:
            sql += " AND f.relative_path = %s"
            params.append(source_path)
        if file_format is not None:
            sql += " AND f.file_format = %s"
            params.append(file_format)
        sql += " ORDER BY r.record_id ASC LIMIT %s"
        params.append(limit)
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def iter_batches(
        self,
        *,
        after_record_id: int = 0,
        limit: int = 100,
        source_code: str | None = None,
        source_path: str | None = None,
        file_format: str | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Keyset pagination. Cursor is the last raw_record_id in each batch."""
        while True:
            batch = self.fetch_batch(
                after_record_id=after_record_id,
                limit=limit,
                source_code=source_code,
                source_path=source_path,
                file_format=file_format,
            )
            if not batch:
                return
            yield batch
            after_record_id = batch[-1]["raw_record_id"]
            if len(batch) < limit:
                return


def main() -> None:
    parser = argparse.ArgumentParser(description="M4.2.2: snapshot-pinned read-only Raw Reader")
    parser.add_argument("--snapshot-id", type=int, default=4)
    parser.add_argument("--limit", type=int, default=3, help="Preview records, 1..1000")
    parser.add_argument("--after-record-id", type=int, default=0)
    parser.add_argument("--source-code")
    parser.add_argument("--source-path")
    parser.add_argument("--file-format")
    args = parser.parse_args()

    print("=== M4.2.2 | RAW DATA READER SAMPLE ===")
    with RawDataReader(args.snapshot_id) as reader:
        rows = reader.fetch_batch(
            after_record_id=args.after_record_id,
            limit=args.limit,
            source_code=args.source_code,
            source_path=args.source_path,
            file_format=args.file_format,
        )
    print("Snapshot:", args.snapshot_id, "| status: completed")
    print("Rows fetched:", len(rows))
    for row in rows:
        payload = row["raw_data"]
        print(
            "record_id={raw_record_id} | file_id={file_id} | kind={record_kind} | "
            "locator={record_locator} | source={source_code} | path={source_path} | "
            "member={archive_member_path} | payload_type={payload_type}".format(
                **{k: row[k] for k in (
                    "raw_record_id", "file_id", "record_kind", "record_locator",
                    "source_code", "source_path", "archive_member_path"
                )},
                payload_type=type(payload).__name__,
            )
        )
    if rows:
        print("Next cursor (after_record_id):", rows[-1]["raw_record_id"])
    print("Database writes: NONE")
    print("M4.2.2 READER SAMPLE: PASS")


if __name__ == "__main__":
    main()
