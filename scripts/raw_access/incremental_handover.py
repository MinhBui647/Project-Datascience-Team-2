"""M4.4.3: Read-only, file-version-level incremental handover planner.

Only completed snapshots are supported for live comparisons. --simulate
creates a future snapshot IN MEMORY, based on a completed snapshot.
No changes to database, raw archive, or export files.
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_NAME = "epl_raw_storage_v2"


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    repo_id: str
    revision: str
    status: str
    paths: dict[str, str]


def load_database_config(root: Path = PROJECT_ROOT) -> dict:
    config = {
        **dotenv_values(root / ".env", encoding="utf-8-sig"),
        **dotenv_values(root / ".env.v2", encoding="utf-8-sig"),
    }
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(f"Missing database keys: {', '.join(missing)}")
    if config["DB_NAME"] != DB_NAME:
        raise RuntimeError(f"Database guard failed: expected {DB_NAME}")
    return dict(host=config["DB_HOST"], port=int(config["DB_PORT"]),
                dbname=config["DB_NAME"], user=config["DB_USER"],
                password=config["DB_PASSWORD"])


def load_completed_snapshot(conn, snapshot_id: int) -> Snapshot:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT snapshot_id, repo_id, hf_revision, status, raw_scope_file_count
            FROM raw.snapshot_runs WHERE snapshot_id = %s
        """, (snapshot_id,))
        meta = cur.fetchone()
        if meta is None:
            raise RuntimeError(f"Snapshot #{snapshot_id} does not exist")
        if meta["status"] != "completed":
            raise RuntimeError(f"Snapshot #{snapshot_id} is not completed")
        cur.execute("""
            SELECT o.relative_path, f.content_sha256
            FROM raw.file_observations AS o
            JOIN raw.raw_files AS f ON f.file_id = o.file_id
            WHERE o.snapshot_id = %s
              AND o.presence_status = 'linked'
              AND f.archive_state = 'verified'
              AND f.parse_state = 'success'
              AND o.repo_id = %s
              AND f.repo_id = %s
            ORDER BY o.relative_path
        """, (snapshot_id, meta["repo_id"], meta["repo_id"]))
        rows = cur.fetchall()
        cur.execute("""
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE presence_status = 'linked' AND file_id IS NOT NULL) AS linked
            FROM raw.file_observations WHERE snapshot_id = %s
        """, (snapshot_id,))
        observations = cur.fetchone()
    if len(rows) != observations["linked"]:
        raise RuntimeError("Linked observations do not all refer to successful/verified files")
    # A completed future snapshot may include explicit non-linked observations
    # for removed paths. Those are not live file versions and are excluded here.
    if (observations["n"] == observations["linked"]
            and meta["raw_scope_file_count"] is not None
            and len(rows) != meta["raw_scope_file_count"]):
        raise RuntimeError("Snapshot raw_scope_file_count does not match linked file count")
    paths = {row["relative_path"]: str(row["content_sha256"]).strip().lower()
             for row in rows}
    if len(paths) != len(rows) or not paths:
        raise RuntimeError("Duplicate paths or empty snapshot")
    if any(len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
           for digest in paths.values()):
        raise RuntimeError("Invalid stored SHA256 in raw.raw_files")
    return Snapshot(snapshot_id=meta["snapshot_id"], repo_id=meta["repo_id"],
                    revision=meta["hf_revision"], status=meta["status"], paths=paths)


def diff_versions(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    old_names, new_names = set(old), set(new)
    common = old_names & new_names
    return {
        "UNCHANGED": sorted(p for p in common if old[p] == new[p]),
        "NEW": sorted(new_names - old_names),
        "CHANGED": sorted(p for p in common if old[p] != new[p]),
        "REMOVED": sorted(old_names - new_names),
    }


def simulated_future(base: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    if len(base) < 2:
        raise RuntimeError("Simulation needs at least two source file paths")
    next_paths = dict(base)
    names = sorted(base)
    changed_path, removed_path = names[0], names[-1]
    new_path = "__simulation__/new_file.json"
    if new_path in base:
        raise RuntimeError("Simulation path already exists")
    changed_digest = hashlib.sha256(b"simulated_new_content").hexdigest()
    if changed_digest == base[changed_path]:
        raise RuntimeError("Unexpected SHA256 collision in simulation")
    next_paths[changed_path] = changed_digest
    next_paths.pop(removed_path)
    next_paths[new_path] = hashlib.sha256(b"simulated_new_file").hexdigest()
    return next_paths, {"changed": changed_path, "removed": removed_path, "added": new_path}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-snapshot", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true", help="In-memory future snapshot test")
    mode.add_argument("--target-snapshot", type=int, help="Existing completed snapshot")
    args = parser.parse_args()
    if args.baseline_snapshot <= 0 or (args.target_snapshot is not None and args.target_snapshot <= 0):
        parser.error("Snapshot IDs must be positive")
    if args.target_snapshot == args.baseline_snapshot:
        parser.error("Baseline and target snapshot IDs must differ")

    with psycopg.connect(
        **load_database_config(), row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=120000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS name")
            if cur.fetchone()["name"] != DB_NAME:
                raise RuntimeError("Connected to the wrong database")
        baseline = load_completed_snapshot(conn, args.baseline_snapshot)
        if args.simulate:
            current, _ = simulated_future(baseline.paths)
            mode_label = "SIMULATE (future snapshot in memory)"
            target_label = "simulation-only"
        else:
            target = load_completed_snapshot(conn, args.target_snapshot)
            if target.repo_id != baseline.repo_id:
                raise RuntimeError("Snapshots belong to different repositories")
            current = target.paths
            mode_label = "COMPARE (two real completed snapshots)"
            target_label = f"#{target.snapshot_id} / {target.revision[:12]}"

    result = diff_versions(baseline.paths, current)
    print("=== M4.4.3 | INCREMENTAL HANDOVER PLAN ===")
    print("Database:", DB_NAME)
    print("Repository:", baseline.repo_id)
    print("Baseline:", f"#{baseline.snapshot_id} / {baseline.revision[:12]}")
    print("Target:", target_label)
    print("Mode:", mode_label)
    print("Baseline files:", len(baseline.paths))
    print("Target files:", len(current))
    for key in ("UNCHANGED", "NEW", "CHANGED", "REMOVED"):
        print(f"{key}: {len(result[key])}")
        if key != "UNCHANGED":
            for path in result[key]:
                print(f"  [{key}] {path}")
    print("Files requiring handover:", len(result["NEW"]) + len(result["CHANGED"]))
    if args.simulate:
        assert tuple(map(len, (result["UNCHANGED"], result["NEW"], result["CHANGED"], result["REMOVED"]))) == (len(baseline.paths) - 2, 1, 1, 1)
    print("Changed-file policy: deliver new file version (NOT record-level delta)")
    print("Removed-file policy: notify only; do not delete old raw data")
    print("Acknowledgement checkpoint: NOT IMPLEMENTED (separate milestone)")
    print("Database writes: NONE")
    print("Snapshot status: UNCHANGED")
    print("M4.4.3 INCREMENTAL PLAN: PASS")


if __name__ == "__main__":
    main()
