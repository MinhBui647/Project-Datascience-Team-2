"""Restore missing immutable objects from an existing local V2 archive copy.

Used once when moving a checkout while retaining the PostgreSQL database.
Only paths and content hashes referenced by a completed snapshot are accepted.
Never changes PostgreSQL or overwrites an existing object. Preview by default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REPO = "Toibinguz/epl-premier-league-dataset"


def snapshot_objects(snapshot_id: int | None = None) -> tuple[int, list[tuple]]:
    import psycopg
    from scripts.incremental.register_incremental_snapshot import db_parameters
    params = db_parameters()
    params["options"] = "-c default_transaction_read_only=on -c statement_timeout=60000"
    with psycopg.connect(**params) as conn:
        if conn.execute("SELECT current_database(),current_setting('transaction_read_only')").fetchone() != (
                "epl_raw_storage_v2", "on"):
            raise RuntimeError("Wrong database or non-readonly connection")
        if snapshot_id is None:
            row = conn.execute("SELECT snapshot_id FROM raw.snapshot_runs WHERE repo_id=%s "
                               "AND status='completed' ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1",
                               (REPO,)).fetchone()
            if row is None:
                raise RuntimeError("No completed snapshot available")
            snapshot_id = row[0]
        row = conn.execute("SELECT status FROM raw.snapshot_runs WHERE snapshot_id=%s AND repo_id=%s",
                           (snapshot_id, REPO)).fetchone()
        if row != ("completed",):
            raise RuntimeError("Archive restore requires a completed snapshot")
        objects = conn.execute("SELECT DISTINCT f.archive_path, f.content_sha256, f.file_size_bytes "
                               "FROM raw.file_observations o JOIN raw.raw_files f ON f.file_id=o.file_id "
                               "WHERE o.snapshot_id=%s AND o.repo_id=%s AND o.presence_status='linked'",
                               (snapshot_id, REPO)).fetchall()
    if not objects:
        raise RuntimeError("Completed snapshot has no archived objects")
    return snapshot_id, objects


def _path(root: Path, relative: str) -> Path:
    current = root
    for part in relative.split("/"):
        if part in ("", ".", "..") or "\\" in part:
            raise ValueError("Unsafe archive path")
        current /= part
        if current.is_symlink():
            raise ValueError("Archive path traverses a symlink")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("Archive path escapes root")
    return current


def _verify(path: Path, sha: str, size: int):
    if not path.is_file() or path.stat().st_size != size:
        raise RuntimeError("Archive object is missing or has wrong size: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != sha:
        raise RuntimeError("Archive SHA256 mismatch: " + str(path))


def restore_objects(source_root: Path, target_root: Path, objects: list[tuple], *, restore=False) -> dict:
    for directory in (source_root, target_root):
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("Source and destination must be real project directories")
    pending = []
    existing = []
    total = 0
    for relative, sha, size in objects:
        if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
                or relative != f"raw-archive-v2/objects/{sha[:2]}/{sha}"
                or type(size) is not int or size < 0):
            raise ValueError("Invalid database archive identity")
        destination = _path(target_root, relative)
        total += size
        if destination.exists():
            existing.append((destination, sha, size))
        else:
            source = _path(source_root, relative)
            if not source.is_file() or source.stat().st_size != size:
                raise RuntimeError("Source archive missing or wrong size: " + relative)
            pending.append((source, destination, sha, size))
    result = dict(objects=len(objects), bytes=total, already_present=len(existing),
                  missing_objects=len(pending), bytes_to_copy=sum(row[3] for row in pending), restored=0)
    if restore:
        for path, sha, size in existing:
            _verify(path, sha, size)
        # Validate the full source set before publishing anything.
        for source, _, sha, size in pending:
            _verify(source, sha, size)
        for source, destination, sha, size in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".restore-", delete=False) as out:
                    staged = Path(out.name)
                shutil.copyfile(source, staged)
                _verify(staged, sha, size)
                with staged.open("r+b") as handle:
                    os.fsync(handle.fileno())
                try:
                    os.link(staged, destination)
                except FileExistsError:
                    pass
                _verify(destination, sha, size)
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
            result["restored"] += 1
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args(argv)
    snapshot, objects = snapshot_objects(args.snapshot_id)
    result = restore_objects(args.source_root.resolve(), ROOT, objects, restore=args.restore)
    print(json.dumps(dict(snapshot_id=snapshot, mode="restore" if args.restore else "preview", **result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
