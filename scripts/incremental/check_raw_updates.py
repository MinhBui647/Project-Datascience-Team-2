"""M4.5 | Read-only Hugging Face Raw update detector.

Compare a completed PostgreSQL baseline snapshot against one pinned HF revision.
This module is deliberately a *planner*, never an ingestion runner.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Mapping

REPO_ID = "Toibinguz/epl-premier-league-dataset"
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")
CATEGORIES = ("UNCHANGED", "NEW", "CHANGED", "REMOVED", "REVIEW")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class FileIdentity:
    size: int | None
    git_blob_id: str | None
    lfs_sha256: str | None


def normalize_id(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def in_raw_scope(path: str) -> bool:
    return path.startswith(RAW_PREFIXES)


def classify(old: FileIdentity | None, new: FileIdentity | None) -> str:
    if old is None and new is None:
        raise ValueError("Two absent identities cannot be compared")
    if old is None:
        return "NEW"
    if new is None:
        return "REMOVED"

    # Conflicting metadata within a single commit needs manual inspection.
    if old.size is not None and new.size is not None and old.size != new.size:
        if old.lfs_sha256 and old.lfs_sha256 == new.lfs_sha256:
            return "REVIEW"
        if old.git_blob_id and old.git_blob_id == new.git_blob_id:
            return "REVIEW"
        return "CHANGED"

    if old.lfs_sha256 and new.lfs_sha256:
        return "UNCHANGED" if old.lfs_sha256 == new.lfs_sha256 else "CHANGED"
    if old.git_blob_id and new.git_blob_id:
        return "UNCHANGED" if old.git_blob_id == new.git_blob_id else "CHANGED"
    # Same size alone is not proof of unchanged bytes.
    return "REVIEW"


def compare_snapshots(
    baseline: Mapping[str, FileIdentity], target: Mapping[str, FileIdentity]
) -> dict[str, list[str]]:
    out = {name: [] for name in CATEGORIES}
    if any(not in_raw_scope(p) for p in baseline) or any(not in_raw_scope(p) for p in target):
        raise ValueError("Non-Raw path present in comparison input")
    for path in sorted(baseline.keys() | target.keys()):
        category = classify(baseline.get(path), target.get(path))
        out[category].append(path)
    return out


@dataclass(frozen=True)
class BaselineSelection:
    snapshot_id: int
    hf_revision: str
    manifest_file_count: int
    raw_scope_file_count: int
    manifest_sha256: str
    manifest_archive_path: str


def validate_baseline_selection(row: tuple | None) -> BaselineSelection:
    """Pure, fail-closed validation of the newest completed snapshot row.

    The caller supplies the newest completed row ordered by observed_at,
    snapshot_id.  Never silently fall back to an older completed snapshot.
    """
    if row is None:
        raise RuntimeError("No completed baseline snapshot exists for this repository")
    (sid, revision, status, manifest_count, raw_count, sha, archive) = row
    if type(sid) is not int or sid <= 0:
        raise RuntimeError("Invalid completed baseline snapshot_id")
    if status != "completed":
        raise RuntimeError(f"Selected baseline is not completed: {status!r}")
    if not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise RuntimeError("Invalid completed baseline pinned HF revision")
    if type(manifest_count) is not int or manifest_count <= 0:
        raise RuntimeError("Invalid completed baseline manifest_file_count")
    if type(raw_count) is not int or raw_count <= 0 or raw_count > manifest_count:
        raise RuntimeError("Invalid completed baseline raw_scope_file_count")
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise RuntimeError("Missing or invalid completed baseline manifest SHA256")
    canonical = f"raw-archive-v2/manifests/{sha[:2]}/{sha}.json"
    if archive != canonical:
        raise RuntimeError("Completed baseline manifest archive path is noncanonical")
    return BaselineSelection(sid, revision, manifest_count, raw_count, sha, archive)


def select_latest_completed_baseline(repo_id: str) -> BaselineSelection:
    """Find the most recently observed completed baseline (PostgreSQL read-only).

    observed_at is ingestion observation time, NOT guaranteed HF commit order.
    Snapshot ancestry checks belong to a later target/revision gate.
    """
    from dotenv import dotenv_values
    import psycopg

    cfg = {
        **dotenv_values(".env", encoding="utf-8-sig"),
        **dotenv_values(".env.v2", encoding="utf-8-sig"),
    }
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise RuntimeError(f"Missing PostgreSQL environment keys: {', '.join(missing)}")
    if cfg["DB_NAME"] != "epl_raw_storage_v2":
        raise RuntimeError("Refusing unexpected database in environment configuration")
    with psycopg.connect(
        host=cfg["DB_HOST"], port=int(cfg["DB_PORT"]),
        dbname=cfg["DB_NAME"], user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] != "epl_raw_storage_v2":
                raise RuntimeError("Unexpected database; refusing automatic baseline selection")
            cur.execute("""
                SELECT snapshot_id, hf_revision, status, manifest_file_count,
                       raw_scope_file_count, manifest_sha256, manifest_archive_path
                FROM raw.snapshot_runs
                WHERE repo_id = %s AND status = 'completed'
                ORDER BY observed_at DESC, snapshot_id DESC
                LIMIT 1
            """, (repo_id,))
            row = cur.fetchone()
    return validate_baseline_selection(row)



@dataclass(frozen=True)
class TargetSnapshotState:
    snapshot_id: int
    status: str


def classify_target_state(
    baseline_snapshot_id: int,
    baseline_revision: str,
    target_revision: str,
    target_state: TargetSnapshotState | None,
) -> str:
    """Pure fail-closed target-state decision; never authorizes a write.

    An already registered revision must NEVER become a new ingestion target.
    """
    if not SHA40.fullmatch(baseline_revision) or not SHA40.fullmatch(target_revision):
        raise RuntimeError("Baseline/target revision must be pinned full SHA40")
    if type(baseline_snapshot_id) is not int or baseline_snapshot_id <= 0:
        raise RuntimeError("Invalid baseline snapshot ID")
    if target_state is not None:
        if type(target_state.snapshot_id) is not int or target_state.snapshot_id <= 0:
            raise RuntimeError("Invalid target snapshot ID")
        if not isinstance(target_state.status, str):
            raise RuntimeError("Invalid target snapshot status")
    if target_revision == baseline_revision:
        if (target_state is None or
                target_state.snapshot_id != baseline_snapshot_id or
                target_state.status != "completed"):
            raise RuntimeError("Current baseline revision has inconsistent target state")
        return "SKIP_ALREADY_CURRENT"
    if target_state is None:
        return "PLAN_NEW_REVISION"
    if target_state.status == "completed":
        return "SKIP_ALREADY_PROCESSED"
    if target_state.status == "in_progress":
        return "RECOVERY_REQUIRED"
    return "BLOCKED_EXISTING_TARGET_STATE"


def read_target_snapshot_state(repo_id: str, revision: str) -> TargetSnapshotState | None:
    """PostgreSQL-only snapshot lookup; read-only even if credentials permit writes."""
    if not SHA40.fullmatch(revision):
        raise RuntimeError("Target state lookup needs a pinned 40-character revision")
    from dotenv import dotenv_values
    import psycopg

    cfg = {
        **dotenv_values(".env", encoding="utf-8-sig"),
        **dotenv_values(".env.v2", encoding="utf-8-sig"),
    }
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not cfg.get(name)]
    if missing:
        raise RuntimeError(f"Missing PostgreSQL environment keys: {', '.join(missing)}")
    if cfg["DB_NAME"] != "epl_raw_storage_v2":
        raise RuntimeError("Refusing unexpected database in target-state lookup")
    with psycopg.connect(
        host=cfg["DB_HOST"], port=int(cfg["DB_PORT"]),
        dbname=cfg["DB_NAME"], user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] != "epl_raw_storage_v2":
                raise RuntimeError("Unexpected database; refusing target state lookup")
            cur.execute("""
                SELECT snapshot_id, status
                FROM raw.snapshot_runs
                WHERE repo_id=%s AND hf_revision=%s
            """, (repo_id, revision))
            rows = cur.fetchall()
    if len(rows) > 1:
        raise RuntimeError("Multiple snapshot rows for one repository/revision")
    if not rows:
        return None
    sid, status = rows[0]
    if type(sid) is not int or sid <= 0 or not isinstance(status, str):
        raise RuntimeError("Invalid snapshot state returned from database")
    return TargetSnapshotState(sid, status)


def resolve_target_revision(repo_id: str, revision_arg: str) -> str:
    """Resolve an HF ref once; all downstream planning uses the pinned commit."""
    from huggingface_hub import HfApi

    revision = str(HfApi().repo_info(
        repo_id=repo_id, repo_type="dataset", revision=revision_arg
    ).sha)
    if not SHA40.fullmatch(revision):
        raise RuntimeError("Hugging Face returned invalid pinned SHA40")
    return revision


def load_baseline(snapshot_id: int, repo_id: str) -> tuple[str, dict[str, FileIdentity]]:
    from dotenv import dotenv_values
    import psycopg

    cfg = {
        **dotenv_values(".env", encoding="utf-8-sig"),
        **dotenv_values(".env.v2", encoding="utf-8-sig"),
    }
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not cfg.get(name)]
    if missing:
        raise RuntimeError(f"Missing PostgreSQL environment keys: {', '.join(missing)}")
    files: dict[str, FileIdentity] = {}
    with psycopg.connect(
        host=cfg["DB_HOST"], port=int(cfg["DB_PORT"]),
        dbname=cfg["DB_NAME"], user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone()[0] != "epl_raw_storage_v2":
                raise RuntimeError("Unexpected database; refusing plan")
            cur.execute("""SELECT hf_revision, status, raw_scope_file_count
                           FROM raw.snapshot_runs
                           WHERE snapshot_id=%s AND repo_id=%s""", (snapshot_id, repo_id))
            row = cur.fetchone()
            if not row or row[1] != "completed":
                raise RuntimeError("Baseline snapshot missing or not completed")
            revision, _, expected_count = row
            cur.execute("""SELECT relative_path, manifest_size_bytes, git_blob_id, lfs_sha256
                           FROM raw.file_observations
                           WHERE snapshot_id=%s AND repo_id=%s AND file_id IS NOT NULL""",
                        (snapshot_id, repo_id))
            for path, size, git_blob, lfs in cur.fetchall():
                if not in_raw_scope(path):
                    raise RuntimeError(f"Out-of-scope path in baseline: {path}")
                if path in files:
                    raise RuntimeError(f"Duplicate baseline path: {path}")
                files[path] = FileIdentity(size, normalize_id(git_blob), normalize_id(lfs))
            if len(files) != expected_count:
                raise RuntimeError(f"Baseline count mismatch: linked={len(files)}, expected={expected_count}")
    return str(revision), files


def load_target(repo_id: str, revision_arg: str, *, already_resolved: bool = False) -> tuple[str, dict[str, FileIdentity], int]:
    from huggingface_hub import HfApi
    from huggingface_hub.hf_api import RepoFile

    api = HfApi()
    if already_resolved:
        if not SHA40.fullmatch(revision_arg):
            raise RuntimeError("Expected pinned SHA40 for already_resolved target")
        revision = revision_arg
    else:
        revision = str(api.repo_info(
            repo_id=repo_id, repo_type="dataset", revision=revision_arg
        ).sha)
    files: dict[str, FileIdentity] = {}
    excluded = 0
    for item in api.list_repo_tree(
        repo_id=repo_id, repo_type="dataset", revision=revision, recursive=True
    ):
        if not isinstance(item, RepoFile):
            continue
        path = item.path
        if not in_raw_scope(path):
            excluded += 1
            continue
        if path in files:
            raise RuntimeError(f"Duplicate target path: {path}")
        lfs = getattr(item, "lfs", None) or {}
        lfs_hash = lfs.get("sha256") if isinstance(lfs, dict) else None
        files[path] = FileIdentity(item.size, normalize_id(getattr(item, "blob_id", None)), normalize_id(lfs_hash))
    return revision, files, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-snapshot", type=int, default=None,
                        help="Optional explicit baseline; omitted = newest completed")
    parser.add_argument("--repo-id", default=REPO_ID)
    parser.add_argument("--target-revision", default="main", help="Prefer pinned commit SHA for repeatable results")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--baseline-only", action="store_true",
                      help="Verify completed baseline in PostgreSQL only; no HF request")
    mode.add_argument("--target-state-only", action="store_true",
                      help="Inspect one pinned target revision in PostgreSQL only; no HF request")
    parser.add_argument("--plan", action="store_true", help="Read-only; default behavior")
    args = parser.parse_args()
    if args.baseline_snapshot is not None and args.baseline_snapshot <= 0:
        parser.error("--baseline-snapshot must be positive")
    if args.target_state_only and not SHA40.fullmatch(args.target_revision):
        parser.error("--target-state-only requires full pinned SHA40 in --target-revision")

    selection = None
    if args.baseline_snapshot is None:
        selection = select_latest_completed_baseline(args.repo_id)
        snapshot_id = selection.snapshot_id
        selection_mode = "AUTO_LATEST_COMPLETED"
    else:
        snapshot_id = args.baseline_snapshot
        selection_mode = "EXPLICIT_OVERRIDE"

    old_revision, baseline = load_baseline(snapshot_id, args.repo_id)
    if selection is not None:
        if old_revision != selection.hf_revision or len(baseline) != selection.raw_scope_file_count:
            raise RuntimeError("Baseline changed between selector and observation read; retry safely")
    if args.baseline_only:
        print("=== M4.6.2c | BASELINE SELECTOR (DB READ-ONLY) ===")
        print("Selection mode:", selection_mode)
        print("Repository:", args.repo_id)
        print("Selected snapshot:", snapshot_id, "| completed")
        print("Baseline revision:", old_revision)
        print("Linked Raw files:", len(baseline))
        print("Hugging Face requests: NONE | Database writes: NONE")
        print("M4.6.2c BASELINE SELECTOR: PASS")
        return 0

    if args.target_state_only:
        target_revision = args.target_revision
    else:
        target_revision = resolve_target_revision(args.repo_id, args.target_revision)
    target_state = read_target_snapshot_state(args.repo_id, target_revision)
    decision = classify_target_state(snapshot_id, old_revision, target_revision, target_state)
    print("=== M4.6.2d.2 | TARGET REVISION STATE GATE (READ-ONLY) ===", flush=True)
    print("Repository:", args.repo_id)
    print("Baseline snapshot:", snapshot_id, "|", selection_mode)
    print("Target revision:", target_revision)
    print("Target snapshot:", "ABSENT" if target_state is None else
          f"#{target_state.snapshot_id} | {target_state.status}")
    print("Target decision:", decision)
    if args.target_state_only:
        print("Hugging Face requests: NONE | Database writes: NONE")
        print("M4.6.2d.2 TARGET STATE GATE:", "PASS" if decision.startswith("SKIP_") or decision == "PLAN_NEW_REVISION" else "BLOCKED")
        return 0 if decision.startswith("SKIP_") or decision == "PLAN_NEW_REVISION" else 2
    if decision != "PLAN_NEW_REVISION":
        print("Hugging Face file-tree requests: NONE | Database writes: NONE")
        print("Mode: PLAN (no new snapshot, ingestion or archive writes)")
        if decision.startswith("SKIP_"):
            print("M4.6.2d.2 TARGET STATE GATE: PASS")
            return 0
        print("M4.6.2d.2 TARGET STATE GATE: BLOCKED")
        return 2

    print("=== M4.6.2c | RAW UPDATE DETECTOR PLAN ===", flush=True)
    loaded_revision, target, excluded = load_target(args.repo_id, target_revision, already_resolved=True)
    if loaded_revision != target_revision:
        raise RuntimeError("Target revision changed between resolution and manifest lookup")
    result = compare_snapshots(baseline, target)
    print("Repository:", args.repo_id)
    print("Baseline snapshot:", snapshot_id, "|", selection_mode)
    print("Baseline revision:", old_revision)
    print("Target revision (pinned):", target_revision)
    print("Baseline Raw files:", len(baseline))
    print("Target Raw files:", len(target))
    print("Excluded non-Raw files:", excluded)
    for category in CATEGORIES:
        print(f"{category}: {len(result[category])}")
        if category != "UNCHANGED":
            for path in result[category]:
                print(f"  [{category}] {path}")
    handover = len(result["NEW"]) + len(result["CHANGED"])
    print("Files requiring ingestion if validated:", handover)
    print("Metadata reviews required:", len(result["REVIEW"]))
    if handover == 0 and not result["REMOVED"] and not result["REVIEW"]:
        print("Decision: NO_RAW_CHANGES (revision changed outside Raw scope)")
    elif result["REVIEW"]:
        print("Decision: BLOCKED_FOR_METADATA_REVIEW")
    elif handover or result["REMOVED"]:
        print("Decision: RAW_CHANGES_FOUND (ingestion NOT started)")
    else:
        print("Decision: NO_RAW_CHANGES")
    print("Mode: PLAN (read-only; no DB writes, file-body downloads, or local file writes)")
    print("M4.5.2b RAW UPDATE DETECTOR: " + ("REVIEW REQUIRED" if result["REVIEW"] else "PASS"))
    return 2 if result["REVIEW"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
