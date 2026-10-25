#!/usr/bin/env python3
"""M3.9.2 | Verify all pinned V2 immutable raw archive objects against DB + HF catalog.

From raw_storage project root:
  .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\verify_archive.py --check

Database is strictly read-only. Does NOT download data, alter archive objects,
update snapshot status, or register a manifest. Writes a local JSON report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "tools"))

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
DB = "epl_raw_storage_v2"
EXPECTED_FILES = 276
REPORT = ROOT / "reports" / "m3_9_2_archive_integrity_v2.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def load_rows():
    import psycopg
    import batch_ingestion as engine

    if (engine.REPO, engine.REV, engine.ROOT.resolve()) != (REPO, REV, ROOT.resolve()):
        raise RuntimeError("Pinned engine/revision/project root mismatch")
    catalog = engine.load_catalog()
    if len(catalog) != EXPECTED_FILES:
        raise RuntimeError(f"Unexpected catalog count: {len(catalog)}")

    params = engine.connection_parameters()
    if params.get("dbname") != DB:
        raise RuntimeError(f"Refusing connection to {params.get('dbname')!r}: expected {DB}")
    params["options"] = "-c default_transaction_read_only=on -c statement_timeout=600000"
    params["autocommit"] = True

    with psycopg.connect(**params) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                engine.db_guard(cur)
                cur.execute("""
                    SELECT snapshot_id, status, raw_scope_file_count,
                           manifest_archive_path, manifest_sha256
                    FROM raw.snapshot_runs
                    WHERE repo_id=%s AND hf_revision=%s
                """, (REPO, REV))
                snapshots = cur.fetchall()
                if len(snapshots) != 1:
                    raise RuntimeError(f"Expected one pinned snapshot, found {len(snapshots)}")
                sid, status, scope_count, manifest_path, manifest_sha = snapshots[0]
                if status not in("discovered", "completed"):
                    raise RuntimeError(f"Unexpected snapshot status {status!r}; do not proceed automatically")
                if scope_count != EXPECTED_FILES:
                    raise RuntimeError(f"Expected raw_scope_file_count={EXPECTED_FILES}, got {scope_count}")
                if bool(manifest_path) != bool(manifest_sha):
                    raise RuntimeError("Inconsistent manifest metadata in snapshot")
                cur.execute("""
                    SELECT o.relative_path, o.presence_status, o.file_id,
                           f.repo_id, f.relative_path, f.file_format,
                           f.content_sha256, f.file_size_bytes, f.archive_path,
                           f.archive_state, f.parse_state
                    FROM raw.file_observations AS o
                    LEFT JOIN raw.raw_files AS f ON f.file_id=o.file_id
                    WHERE o.snapshot_id=%s
                """, (sid,))
                rows = cur.fetchall()
    # PostgreSQL connection has been closed before long filesystem SHA scans.
    return catalog, rows, int(sid), bool(manifest_path and manifest_sha)


def verified_catalog_entries(catalog, rows):
    errors = []
    mapped = {}
    for row in rows:
        (path, presence, file_id, repo_id, file_path, fmt, sha, size,
         archive_rel, archive_state, parse_state) = row
        if path in mapped:
            errors.append(f"Duplicate observation: {path}")
        mapped[path] = {
            "presence": presence, "file_id": file_id, "repo": repo_id,
            "file_path": file_path, "format": fmt,
            "sha": str(sha or "").strip().lower(), "size": size,
            "archive_path": archive_rel, "archive_state": archive_state,
            "parse_state": parse_state,
        }

    for path in sorted(set(catalog) - set(mapped)):
        errors.append(f"Missing observation: {path}")
    for path in sorted(set(mapped) - set(catalog)):
        errors.append(f"Unexpected observation: {path}")
    if len(rows) != EXPECTED_FILES:
        errors.append(f"Observation rows={len(rows)} expected={EXPECTED_FILES}")

    seen_file_ids = set()
    objects = []
    for path, cat in sorted(catalog.items()):
        item = mapped.get(path)
        if not item:
            continue
        file_id = item["file_id"]
        if file_id is None or item["presence"] != "linked":
            errors.append(f"Unlinked observation: {path}")
            continue
        if file_id in seen_file_ids:
            errors.append(f"Nonunique linked file_id={file_id}: {path}")
        seen_file_ids.add(file_id)
        if item["repo"] != REPO or item["file_path"] != path:
            errors.append(f"Repo/path mismatch: {path}")
        if item["format"] != cat["format"]:
            errors.append(f"Format mismatch: {path}")
        if item["archive_state"] != "verified" or item["parse_state"] != "success":
            errors.append(f"File state mismatch: {path}")
        try:
            expected_size = int(cat["size_bytes"])
        except (ValueError, TypeError):
            errors.append(f"Catalog size invalid: {path}")
            continue
        if item["size"] != expected_size:
            errors.append(f"DB/catalog size mismatch: {path}")
        sha = item["sha"]
        if not SHA256_PATTERN.fullmatch(sha):
            errors.append(f"Invalid DB SHA256: {path}")
            continue
        canonical_rel = f"raw-archive-v2/objects/{sha[:2]}/{sha}"
        if item["archive_path"] != canonical_rel:
            errors.append(f"Noncanonical archive path: {path}: {item['archive_path']!r}")
            continue
        lfs = (cat.get("lfs_sha256") or "").strip().lower()
        git_blob = (cat.get("git_blob_id") or "").strip().lower()
        if lfs and (not SHA256_PATTERN.fullmatch(lfs) or sha != lfs):
            errors.append(f"DB/LFS SHA256 mismatch: {path}")
        if not lfs and not SHA1_PATTERN.fullmatch(git_blob):
            errors.append(f"Missing/invalid source Git blob ID: {path}")
        objects.append({
            "relative_path": path, "archive_path": canonical_rel,
            "sha256": sha, "size_bytes": expected_size,
            "lfs_sha256": lfs, "git_blob_id": git_blob,
        })
    if len(seen_file_ids) != EXPECTED_FILES:
        errors.append(f"Unique file IDs={len(seen_file_ids)} expected={EXPECTED_FILES}")
    if len(objects) != EXPECTED_FILES:
        errors.append(f"Verifiable objects={len(objects)} expected={EXPECTED_FILES}")
    return objects, errors


def verify_bytes(item, root=ROOT):
    path = root / item["archive_path"]
    # No symlink substitution for the expected content-addressed V2 archive.
    if path.is_symlink():
        raise RuntimeError("Archive object is a symlink")
    if not path.is_file():
        raise RuntimeError("Missing/non-regular archived object")
    before = path.stat()
    expected_size = item["size_bytes"]
    if before.st_size != expected_size:
        raise RuntimeError(f"Archive byte size mismatch: {before.st_size} vs {expected_size}")
    digest = hashlib.sha256()
    # For non-LFS Git blobs, this binds bytes to the pinned tree blob ID.
    blob_digest = None
    if not item["lfs_sha256"]:
        blob_digest = hashlib.sha1()
        blob_digest.update(f"blob {expected_size}\0".encode("ascii"))
    count = 0
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
            if blob_digest is not None:
                blob_digest.update(chunk)
            count += len(chunk)
    after = path.stat()
    if count != expected_size or after.st_size != before.st_size:
        raise RuntimeError(f"Archive size changed or incomplete read: {count} bytes")
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise RuntimeError("Archive changed during verification")
    result_sha = digest.hexdigest()
    if result_sha != item["sha256"]:
        raise RuntimeError(f"Archive SHA256 mismatch: actual={result_sha} expected={item['sha256']}")
    if item["lfs_sha256"]:
        if result_sha != item["lfs_sha256"]:
            raise RuntimeError("Archive SHA256 differs from pinned HF LFS SHA256")
    elif blob_digest is not None and blob_digest.hexdigest() != item["git_blob_id"]:
        raise RuntimeError("Archive Git blob SHA1 differs from pinned catalog")
    return count


def write_report(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=REPORT.parent,
                                         prefix=".m3_9_2_", suffix=".json.tmp", delete=False) as out:
            staged = Path(out.name)
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(staged, REPORT)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def run():
    print("=== M3.9.2 | IMMUTABLE RAW ARCHIVE BYTE INTEGRITY ===", flush=True)
    print("Repo:", REPO)
    print("Revision:", REV)
    print("Database guard:", DB)
    print("Mode: CHECK (DB READ-ONLY; local archive reads; local JSON report)", flush=True)
    catalog, rows, snapshot_id, manifest_registered = load_rows()
    objects, errors = verified_catalog_entries(catalog, rows)
    verified = 0
    bytes_read = 0
    for index, item in enumerate(objects, 1):
        path = item["relative_path"]
        try:
            n = verify_bytes(item)
            verified += 1
            bytes_read += n
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
        if index % 25 == 0 or index == len(objects):
            print(f"Scanned archive objects: {index}/{len(objects)} | verified: {verified} | issues: {len(errors)}", flush=True)
    report = {
        "check": "M3.9.2", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "repo_id": REPO, "hf_revision": REV, "snapshot_id": snapshot_id,
        "expected_files": EXPECTED_FILES, "catalog_paths": len(catalog),
        "observation_rows": len(rows), "verified_file_objects": verified,
        "verified_archive_bytes": bytes_read, "mismatch_count": len(errors),
        "issues": errors, "database_writes": 0,
        "physical_archive_reads": True, "manifest_registered": manifest_registered,
    }
    write_report(report)
    print("--- ARCHIVE VERIFICATION SUMMARY ---")
    print("Catalog paths:", len(catalog))
    print("DB observation rows:", len(rows))
    print("Verified immutable objects:", verified)
    print("Verified archive bytes:", f"{bytes_read:,}")
    print("Mismatch count:", len(errors))
    for issue in errors[:20]:
        print("ISSUE:", issue)
    if len(errors) > 20:
        print(f"... {len(errors)-20} more issues in report")
    print("Report:", REPORT.relative_to(ROOT).as_posix())
    print("Manifest registered:", manifest_registered)
    print("PostgreSQL writes: NONE")
    print("Snapshot status: UNCHANGED")
    if errors or verified != EXPECTED_FILES:
        print("M3.9.2 ARCHIVE INTEGRITY: FAIL")
        return 1
    print("M3.9.2 ARCHIVE INTEGRITY: PASS (no database writes)")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="Hash all 276 archived files; read-only DB")
    args = p.parse_args()
    if not args.check:
        p.error("Specify --check; no archive changes or DB writes are supported")
    try:
        return run()
    except Exception as exc:
        print(f"M3.9.2 ARCHIVE INTEGRITY: ERROR ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
