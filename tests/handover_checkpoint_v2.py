"""M4.4.4: checkpoint contract, read-only plan, and pure in-memory simulation.

This module intentionally does NOT create/update tables. Installation/migration
is a separate explicit step after the PLAN has been reviewed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE = "epl_raw_storage_v2"
ALLOWED = {
    "PENDING": {"EXPORTED"},
    "EXPORTED": {"RECEIVED", "FAILED"},
    "RECEIVED": {"PROCESSED", "FAILED"},
    "FAILED": {"EXPORTED"},
    "PROCESSED": set(),
}


@dataclass
class SimCheckpoint:
    consumer: str
    file_version: tuple[str, str]
    status: str = "PENDING"
    export_checksum: str | None = None
    receipt_evidence: str | None = None
    processing_evidence: str | None = None

    def transition(self, to: str, *, evidence: str = "") -> None:
        if to not in ALLOWED[self.status]:
            raise ValueError(f"Forbidden transition: {self.status} -> {to}")
        if to == "EXPORTED" and not evidence:
            raise ValueError("EXPORTED requires a verified export manifest digest")
        if to == "RECEIVED" and not evidence:
            raise ValueError("RECEIVED requires consumer receipt evidence")
        if to == "PROCESSED" and not evidence:
            raise ValueError("PROCESSED requires explicit consumer cleaning acknowledgement")
        if to == "EXPORTED":
            self.export_checksum = evidence
        if to == "RECEIVED":
            self.receipt_evidence = evidence
        if to == "PROCESSED":
            self.processing_evidence = evidence
        self.status = to


def simulate() -> None:
    print("=== M4.4.4 | CHECKPOINT STATE SIMULATION ===")
    item = SimCheckpoint("cleaning_member_2", ("__simulation__/new_file.json", "a" * 64))
    assert item.status == "PENDING"
    try:
        item.transition("PROCESSED", evidence="not allowed")
    except ValueError:
        pass
    else:
        raise AssertionError("PENDING -> PROCESSED should be forbidden")
    item.transition("EXPORTED", evidence="b" * 64)
    assert item.status == "EXPORTED" and item.processing_evidence is None
    item.transition("RECEIVED", evidence="member2-receipt")
    item.transition("PROCESSED", evidence="member2-cleaning-result")
    assert item.status == "PROCESSED"
    try:
        item.transition("EXPORTED", evidence="b" * 64)
    except ValueError:
        pass
    else:
        raise AssertionError("Processed file should not be re-exported")

    changed = SimCheckpoint("cleaning_member_2", ("2026_2027_new/football_data_raw_2627.csv", "c" * 64))
    changed.transition("EXPORTED", evidence="d" * 64)
    changed.transition("FAILED")
    changed.transition("EXPORTED", evidence="e" * 64)
    assert changed.status == "EXPORTED" and changed.processing_evidence is None
    assert changed.file_version != item.file_version
    print("NEW: PENDING -> EXPORTED -> RECEIVED -> PROCESSED: PASS")
    print("CHANGED: PENDING -> EXPORTED -> FAILED -> EXPORTED: PASS")
    print("Illegal transition rejection: PASS")
    print("Distinct file versions: PASS")
    print("Database writes: NONE")
    print("M4.4.4 STATE SIMULATION: PASS")


def plan(snapshot_id: int) -> None:
    import psycopg
    from dotenv import dotenv_values

    cfg = {
        **dotenv_values(PROJECT_ROOT / ".env", encoding="utf-8-sig"),
        **dotenv_values(PROJECT_ROOT / ".env.v2", encoding="utf-8-sig"),
    }
    keys = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise RuntimeError(f"Missing database configuration: {', '.join(missing)}")
    if cfg["DB_NAME"] != DATABASE:
        raise RuntimeError("Database guard failed")

    with psycopg.connect(
        host=cfg["DB_HOST"], port=int(cfg["DB_PORT"]), dbname=DATABASE,
        user=cfg["DB_USER"], password=cfg["DB_PASSWORD"],
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == DATABASE
            cur.execute("""
                SELECT status, hf_revision FROM raw.snapshot_runs
                WHERE snapshot_id = %s
            """, (snapshot_id,))
            snap = cur.fetchone()
            if snap is None or snap[0] != "completed":
                raise RuntimeError("Baseline snapshot missing or not completed")
            cur.execute("""
                SELECT COUNT(*) FROM raw.file_observations
                WHERE snapshot_id = %s AND presence_status = 'linked'
                AND file_id IS NOT NULL
            """, (snapshot_id,))
            file_count = cur.fetchone()[0]
            cur.execute("""
                SELECT to_regclass('handover.delivery_checkpoints')::text,
                       to_regclass('handover.delivery_events')::text
            """)
            existing = cur.fetchone()

    print("=== M4.4.4 | HANDOVER CHECKPOINT PLAN ===")
    print("Database:", DATABASE)
    print(f"Baseline snapshot: {snapshot_id} | completed | {snap[1][:12]}")
    print("Linked file versions:", file_count)
    print("Existing checkpoint table:", existing[0] or "NOT INSTALLED")
    print("Existing event table:", existing[1] or "NOT INSTALLED")
    print("Proposed schema: handover (separate from raw)")
    print("Identity: (consumer_code, file_id), where file_id identifies a file version")
    print("States: PENDING -> EXPORTED -> RECEIVED -> PROCESSED")
    print("Failure path: EXPORTED/RECEIVED -> FAILED -> EXPORTED")
    print("PROCESSED requires explicit Cleaning acknowledgement")
    print("Partial sample exports DO NOT imply full-file EXPORTED")
    print("Initial snapshot #4 NOT automatically acknowledged")
    print("Mode: PLAN (read-only; no migrations, no writes)")
    print("M4.4.4 CHECKPOINT PLAN: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--simulate", action="store_true")
    parser.add_argument("--baseline-snapshot", type=int, default=4)
    args = parser.parse_args()
    if args.plan:
        plan(args.baseline_snapshot)
    else:
        simulate()


if __name__ == "__main__":
    main()
