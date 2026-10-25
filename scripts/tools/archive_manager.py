#!/usr/bin/env python3
"""M3.1 — Archive one pinned Hugging Face raw file without PostgreSQL writes.

Run from any working directory using the Python interpreter in .venv_v2:
    .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\archive_manager.py

Requires: huggingface_hub, reports/raw_scope_catalog_v2.csv
This script only archives the one explicitly selected sample file.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO_ID = "Toibinguz/epl-premier-league-dataset"
PINNED_COMMIT = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
RELATIVE_PATH = "2026_2027_new/fpl_fixtures_latest.json"
EXPECTED_FORMAT = "json"
EXPECTED_GROUP = "2026_2027_new"
ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
ARCHIVE_DIR = ROOT / "raw-archive-v2" / "objects"
RECEIPT = ROOT / "reports" / "m3_1_archive_pilot_receipt.json"


def find_catalog_entry() -> dict[str, str]:
    if not CATALOG.is_file():
        raise RuntimeError(f"Missing catalog: {CATALOG}")
    with CATALOG.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"relative_path", "group", "format", "size_bytes", "hf_revision"}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(f"Catalog missing columns: {sorted(required - set(reader.fieldnames or []))}")
        matches = [row for row in reader if row["relative_path"] == RELATIVE_PATH]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one catalog entry for {RELATIVE_PATH}, found {len(matches)}")
    entry = matches[0]
    if entry["group"] != EXPECTED_GROUP or entry["format"] != EXPECTED_FORMAT:
        raise RuntimeError("Catalog entry has unexpected group or format")
    if entry["hf_revision"] != PINNED_COMMIT:
        raise RuntimeError("Catalog is from another Hugging Face commit; stop and verify")
    try:
        size = int(entry["size_bytes"])
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid size_bytes in catalog") from exc
    if size <= 0:
        raise RuntimeError("Catalog contains an invalid nonpositive file size")
    return entry


def sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            count += len(chunk)
    return digest.hexdigest(), count


def verify_git_blob(path: Path, byte_size: int, blob_id: str) -> None:
    """Verify Git's blob SHA-1 if file is not LFS-backed."""
    if not blob_id:
        return
    if not re.fullmatch(r"[a-fA-F0-9]{40}", blob_id):
        raise RuntimeError("Malformed git_blob_id in catalog")
    digest = hashlib.sha1()
    digest.update(f"blob {byte_size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != blob_id.lower():
        raise RuntimeError("Git blob ID mismatch: downloaded content does not match manifest")


def verify_download(path: Path, entry: dict[str, str]) -> tuple[str, int]:
    sha, count = sha256_and_size(path)
    if count != int(entry["size_bytes"]):
        raise RuntimeError(f"File size mismatch: got {count}, expected {entry['size_bytes']}")
    lfs = (entry.get("lfs_sha256") or "").strip().lower()
    if lfs:
        if not re.fullmatch(r"[0-9a-f]{64}", lfs):
            raise RuntimeError("Malformed lfs_sha256 in catalog")
        if sha != lfs:
            raise RuntimeError("LFS SHA-256 mismatch: wrong content for pinned revision")
    else:
        verify_git_blob(path, count, (entry.get("git_blob_id") or "").strip())
    return sha, count


def archive_immutable(src: Path, sha: str, count: int) -> tuple[Path, str]:
    target_dir = ARCHIVE_DIR / sha[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / sha

    if destination.exists():
        saved_sha, saved_count = sha256_and_size(destination)
        if (saved_sha, saved_count) != (sha, count):
            raise RuntimeError("Archive integrity ERROR: existing object is corrupted; refusing overwrite")
        return destination, "ALREADY_ARCHIVED"

    # Stage a complete verified object in the same filesystem as the target.
    # Publish with a hardlink (atomic, does not overwrite on collision).
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".stage-", dir=target_dir, delete=False
        ) as output:
            staged = Path(output.name)
            with src.open("rb") as input_file:
                shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())

        saved_sha, saved_count = sha256_and_size(staged)
        if (saved_sha, saved_count) != (sha, count):
            raise RuntimeError("Staged archive content failed integrity verification")

        try:
            os.link(staged, destination)
            action = "ARCHIVED_NEW"
        except FileExistsError:
            action = "ALREADY_ARCHIVED"
        # Do not fall back to replace/rename: both can overwrite existing objects.

        final_sha, final_count = sha256_and_size(destination)
        if (final_sha, final_count) != (sha, count):
            raise RuntimeError("Archive integrity ERROR after publication")
        return destination, action
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def write_receipt(entry: dict[str, str], archive: Path, sha: str, size: int, action: str) -> None:
    receipt = {
        "repo_id": REPO_ID,
        "pinned_commit": PINNED_COMMIT,
        "relative_path": RELATIVE_PATH,
        "source_hint": entry.get("source_hint"),
        "format": entry["format"],
        "sha256": sha,
        "size_bytes": size,
        "archive_path": archive.relative_to(ROOT).as_posix(),
        "archive_action": action,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_writes": 0,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    # Receipt may be refreshed; the immutable object is never overwritten.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=".receipt-", suffix=".json",
        dir=RECEIPT.parent, delete=False
    ) as output:
        temporary = Path(output.name)
        json.dump(receipt, output, indent=2, ensure_ascii=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    try:
        os.replace(temporary, RECEIPT)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    print("=== M3.1 | PINNED RAW ARCHIVE PILOT ===")
    print("Dataset:", REPO_ID)
    print("Commit:", PINNED_COMMIT)
    print("Path:", RELATIVE_PATH)
    entry = find_catalog_entry()
    print("Catalog size:", entry["size_bytes"], "bytes")

    # HF caches the downloaded file; only the copy stored by SHA-256 in
    # raw-archive-v2/ is considered the project's immutable raw archive.
    cached_file = Path(hf_hub_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        filename=RELATIVE_PATH,
        revision=PINNED_COMMIT,
    ))
    sha, size = verify_download(cached_file, entry)
    archived, action = archive_immutable(cached_file, sha, size)
    write_receipt(entry, archived, sha, size, action)

    print("Archive action:", action)
    print("SHA-256:", sha)
    print("Archived size:", size, "bytes")
    print("Archive path:", archived.relative_to(ROOT).as_posix())
    print("Receipt:", RECEIPT.relative_to(ROOT).as_posix())
    print("PostgreSQL writes: 0")
    print("M3.1 RESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"M3.1 RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        sys.exit(1)
