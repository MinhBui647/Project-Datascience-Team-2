#!/usr/bin/env python3
"""M4.5.4a | Read-only write-readiness contract for incremental Raw Storage snapshots.

A new snapshot should link existing completed raw file versions and ingest only
NEW/CHANGED file versions. No PostgreSQL, local archive or HF file-body writes.

Example (from the project root):
  python scripts/tools/raw_sync_write_plan.py --baseline-snapshot 4 \
      --target-revision 0216641d6081234e9e35a3de65c138830c77963a --plan

This is NOT a commit/ingestion script. It intentionally has no --commit flag.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Mapping
import re

# Two import contexts: package and direct script invocation.
if __package__:
    from .check_raw_updates import (
        REPO_ID, FileIdentity, compare_snapshots, load_baseline, load_target,
    )
else:
    from check_raw_updates import (
        REPO_ID, FileIdentity, compare_snapshots, load_baseline, load_target,
    )

REQUIRED_CONSTRAINTS = frozenset({
    "uq_snapshot_repo_revision", "uq_file_version",
    "uq_observation_path", "uq_record_locator",
})
SHA = re.compile(r"[0-9a-f]{40}\Z")

@dataclass(frozen=True)
class ExistingFile:
    """A completed Raw file version referenced by the baseline snapshot."""
    file_id: int
    sha256: str
    size: int
    record_count: int
    archive_state: str = "verified"
    parse_state: str = "success"
    observation_state: str = "linked"

@dataclass(frozen=True)
class WritePlan:
    reuse: tuple[str, ...]
    new: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    baseline_linked_record_count: int
    reused_record_count: int
    superseded_record_count: int
    reused_file_ids: tuple[int, ...]


def build_plan(
    baseline: Mapping[str, FileIdentity],
    target: Mapping[str, FileIdentity],
    existing: Mapping[str, ExistingFile],
) -> WritePlan:
    """Pure deterministic planner, used by offline regression tests."""
    groups = compare_snapshots(baseline, target)
    if groups["REVIEW"]:
        raise RuntimeError("Metadata REVIEW cases block writing")
    if set(baseline) != set(existing):
        raise RuntimeError("Baseline observations and raw file links disagree")
    reused = []
    reused_ids = []
    used_ids = set()
    total = shared = replaced = 0
    for path in sorted(baseline):
        item = existing[path]
        if (type(item.file_id) is not int or item.file_id <= 0
                or item.file_id in used_ids):
            raise RuntimeError(f"Invalid or duplicate baseline file_id: {path}")
        used_ids.add(item.file_id)
        if (item.archive_state, item.parse_state, item.observation_state) != (
            "verified", "success", "linked"
        ):
            raise RuntimeError(f"Baseline file version not completed: {path}")
        if type(item.record_count) is not int or item.record_count < 0:
            raise RuntimeError(f"Missing baseline record count: {path}")
        if (type(item.size) is not int or item.size < 0
                or item.size != baseline[path].size):
            raise RuntimeError(f"Baseline file size mismatch: {path}")
        if baseline[path].lfs_sha256 and item.sha256 != baseline[path].lfs_sha256:
            raise RuntimeError(f"Baseline LFS sha256 mismatch: {path}")
        total += item.record_count
        if path in groups["UNCHANGED"]:
            reused.append(path)
            reused_ids.append(item.file_id)
            shared += item.record_count
        else:
            # Old versions remain immutable, even if superseded/removed.
            replaced += item.record_count

    if len(reused) + len(groups["CHANGED"]) + len(groups["REMOVED"]) != len(baseline):
        raise RuntimeError("Baseline membership accounting mismatch")
    if len(reused) + len(groups["CHANGED"]) + len(groups["NEW"]) != len(target):
        raise RuntimeError("Target membership accounting mismatch")

    return WritePlan(
        reuse=tuple(reused), new=tuple(groups["NEW"]),
        changed=tuple(groups["CHANGED"]), removed=tuple(groups["REMOVED"]),
        baseline_linked_record_count=total,
        reused_record_count=shared,
        superseded_record_count=replaced,
        reused_file_ids=tuple(reused_ids),
    )


def read_db_state(snapshot_id: int, repo_id: str, target_rev: str):
    """Read-only DB guards. A target snapshot already present requires review."""
    from dotenv import dotenv_values
    import psycopg

    cfg = {
        **dotenv_values(".env", encoding="utf-8-sig"),
        **dotenv_values(".env.v2", encoding="utf-8-sig"),
    }
    keys = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in keys if not cfg.get(key)]
    if missing:
        raise RuntimeError(f"Missing database environment fields: {', '.join(missing)}")

    existing: dict[str, ExistingFile] = {}
    with psycopg.connect(
        host=cfg["DB_HOST"], port=int(cfg["DB_PORT"]),
        dbname=cfg["DB_NAME"], user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] != "epl_raw_storage_v2":
                raise RuntimeError("Incorrect PostgreSQL database")

            cur.execute("""
                SELECT snapshot_id, status FROM raw.snapshot_runs
                WHERE repo_id = %s AND hf_revision = %s
            """, (repo_id, target_rev))
            target_rows = cur.fetchall()
            if target_rows:
                raise RuntimeError(
                    f"Target revision already registered in snapshot_runs: {target_rows!r}. "
                    "Stop and inspect instead of creating or rewriting snapshots."
                )

            cur.execute("""
                SELECT status, hf_revision, raw_scope_file_count
                FROM raw.snapshot_runs
                WHERE snapshot_id=%s AND repo_id=%s
            """, (snapshot_id, repo_id))
            baseline_row = cur.fetchone()
            if baseline_row is None or baseline_row[0] != "completed":
                raise RuntimeError("Baseline snapshot is missing or not completed")

            cur.execute("""
                SELECT conname FROM pg_constraint
                WHERE connamespace = 'raw'::regnamespace
                  AND conname = ANY(%s)
            """, (list(REQUIRED_CONSTRAINTS),))
            found = {row[0] for row in cur.fetchall()}
            if found != REQUIRED_CONSTRAINTS:
                raise RuntimeError(
                    f"Missing uniqueness constraints: {sorted(REQUIRED_CONSTRAINTS - found)}"
                )

            cur.execute("""
                SELECT o.relative_path, o.presence_status,
                       f.file_id, f.content_sha256, f.file_size_bytes,
                       f.record_count, f.archive_state, f.parse_state
                FROM raw.file_observations o
                JOIN raw.raw_files f ON f.file_id=o.file_id
                WHERE o.snapshot_id=%s AND o.repo_id=%s
            """, (snapshot_id, repo_id))
            for (path, observed, fid, sha, size, count, archive, parse) in cur.fetchall():
                if path in existing:
                    raise RuntimeError(f"Duplicate baseline path: {path}")
                existing[path] = ExistingFile(
                    file_id=fid, sha256=sha.strip(), size=size,
                    record_count=count, archive_state=archive,
                    parse_state=parse, observation_state=observed,
                )
            if len(existing) != baseline_row[2]:
                raise RuntimeError("DB baseline observation/file count mismatch")
    return baseline_row[1], existing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-snapshot", type=int, required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--repo-id", default=REPO_ID)
    parser.add_argument("--plan", action="store_true", help="Required; read-only mode")
    args = parser.parse_args()
    if not args.plan:
        parser.error("--plan required; this tool never writes")
    if not SHA.fullmatch(args.target_revision):
        parser.error("--target-revision must be a pinned 40-character SHA")

    print("=== M4.5.4a | INCREMENTAL WRITE READINESS PLAN ===", flush=True)
    baseline_rev, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    target_rev, target, excluded = load_target(args.repo_id, args.target_revision)
    if target_rev != args.target_revision or baseline_rev == target_rev:
        raise RuntimeError("Target SHA is different from pinned SHA or equals baseline")
    db_rev, existing = read_db_state(args.baseline_snapshot, args.repo_id, target_rev)
    if db_rev != baseline_rev:
        raise RuntimeError("Baseline revision changed between database reads")
    plan = build_plan(baseline, target, existing)

    print("Repository:", args.repo_id)
    print("Baseline snapshot:", args.baseline_snapshot, "| status: completed")
    print("Baseline revision:", baseline_rev)
    print("Target revision:", target_rev)
    print("Excluded non-Raw files:", excluded)
    print("Baseline linked files:", len(baseline))
    print("Target Raw files:", len(target))
    print("REUSE existing file_id (no record reinsertion):", len(plan.reuse))
    print("NEW candidate paths (insert/reuse by SHA):", len(plan.new))
    print("CHANGED candidate versions (insert/reuse by SHA):", len(plan.changed))
    print("REMOVED paths (notification only):", len(plan.removed))
    print("Baseline linked record count:", plan.baseline_linked_record_count)
    print("Record count across reused files:", plan.reused_record_count)
    print("Record count across superseded/removed baseline files:", plan.superseded_record_count)
    print("New version record count: NOT PERSISTED YET")
    print("Target snapshot status: ABSENT (safe to prepare; not created)")
    print("Target snapshot id: NOT ASSUMED (assigned by PostgreSQL when created)")
    print("Next write design: immutable file versions + per-file transactions + idempotent observations")
    print("Manifest/final reconciliation: REQUIRED BEFORE COMPLETED")
    print("Content hashes: MUST BE REVALIDATED IMMEDIATELY BEFORE INGEST")
    print("Database writes: NONE")
    print("Raw archive modifications: NONE")
    print("Local files modified: NONE")
    print("M4.5.4a WRITE READINESS PLAN: PASS (not approval to commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
