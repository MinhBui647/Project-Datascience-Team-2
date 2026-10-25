#!/usr/bin/env python3
"""M3.9.1: PostgreSQL-only, pinned-catalog reconciliation for EPL Raw Storage V2.

From raw_storage root (Windows PowerShell):
.\\.venv_v2\\Scripts\\python.exe scripts\\tools\\reconcile_db.py --check

No DML, no archive I/O, no network, no modification of snapshot status.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "tools"))

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
DB = "epl_raw_storage_v2"
EXPECTED_FILES = 276
EXPECTED_TAR = {
    "raw_data/understat/understat_matches_cache.tar.gz": (4616, 4616),
    "raw_data/sofascore/sofascore_matches_cache.tar.gz": (6137, 6137),
}


def reconcile():
    import psycopg
    import batch_ingestion as engine

    if (engine.REPO, engine.REV, engine.ROOT.resolve()) != (REPO, REV, ROOT.resolve()):
        raise RuntimeError("Pinned ingestion engine/revision/path mismatch")
    catalog = engine.load_catalog()
    if len(catalog) != EXPECTED_FILES:
        raise RuntimeError(f"Pinned catalog must have {EXPECTED_FILES} entries")

    params = engine.connection_parameters()
    if params["dbname"] != DB:
        raise RuntimeError("V2 database guard failed")
    params["options"] = "-c default_transaction_read_only=on -c statement_timeout=600000"
    params["autocommit"] = True

    errors: list[str] = []
    actual_by_path: dict[str, dict] = {}
    db_status = None
    sid = None
    physical_total = 0
    members_total = 0
    kind_errors = 0

    print("=== M3.9.1 | PINNED RAW DB RECONCILIATION (READ-ONLY) ===", flush=True)
    print("Repo:", REPO)
    print("Revision:", REV)
    print("Database guard:", DB)
    print("Catalog files:", len(catalog))
    print("Mode: CHECK (PostgreSQL read-only, no network/archive reads/writes)", flush=True)

    with psycopg.connect(**params) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                engine.db_guard(cur)
                cur.execute("""
                    SELECT snapshot_id, status, raw_scope_file_count, manifest_file_count,
                           manifest_sha256, manifest_archive_path
                    FROM raw.snapshot_runs WHERE repo_id=%s AND hf_revision=%s
                """, (REPO, REV))
                snapshot = cur.fetchall()
                if len(snapshot) != 1:
                    raise RuntimeError(f"Expected exactly one pinned snapshot, found {len(snapshot)}")
                sid, db_status, scope_n, manifest_n, manifest_sha, manifest_path = snapshot[0]
                if scope_n != EXPECTED_FILES:
                    errors.append(f"snapshot.raw_scope_file_count={scope_n!r}, expected 276")
                if manifest_n is not None and manifest_n != 294:
                    errors.append(f"snapshot.manifest_file_count={manifest_n}, expected 294")

                cur.execute("""
                    SELECT o.relative_path, o.repo_id, o.presence_status, o.file_id,
                           o.manifest_size_bytes, o.lfs_sha256, o.git_blob_id,
                           f.repo_id, f.relative_path, f.file_format,
                           f.file_size_bytes, f.content_sha256, f.archive_path,
                           f.archive_state, f.parse_state, f.record_count
                    FROM raw.file_observations o
                    LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                    WHERE o.snapshot_id=%s
                """, (sid,))
                obs = cur.fetchall()
                for row in obs:
                    (path, o_repo, pres, fid, m_size, lfs_sha, git_blob,
                     f_repo, f_path, fmt, f_size, f_sha, archive_path,
                     archive_state, parse_state, n_records) = row
                    if path in actual_by_path:
                        errors.append(f"Duplicate observation path: {path}")
                    actual_by_path[path] = {
                        "repo": o_repo, "presence": pres, "fid": fid,
                        "manifest_size": m_size, "lfs": lfs_sha,
                        "blob": git_blob, "f_repo": f_repo, "f_path": f_path,
                        "format": fmt, "size": f_size, "sha": f_sha,
                        "archive_path": archive_path,
                        "archive_state": archive_state,
                        "parse_state": parse_state, "record_count": n_records,
                    }
                print("Snapshot ID:", sid, "| status:", db_status)
                print("Snapshot observations:", len(obs), flush=True)
                for path in sorted(set(catalog) - set(actual_by_path)):
                    errors.append(f"MISSING observation: {path}")
                for path in sorted(set(actual_by_path) - set(catalog)):
                    errors.append(f"OUT-OF-SCOPE observation: {path}")

                selected_ids = []
                fmt_by_id: dict[int, str] = {}
                counts_by_id: dict[int, int] = {}
                for path, cat in catalog.items():
                    x = actual_by_path.get(path)
                    if x is None:
                        continue
                    if x["repo"] != REPO or x["f_repo"] != REPO or x["f_path"] != path:
                        errors.append(f"Repository/path FK mismatch: {path}")
                    if x["presence"] != "linked" or x["fid"] is None:
                        errors.append(f"NOT LINKED: {path} (status={x['presence']})")
                        continue
                    fid = int(x["fid"])
                    selected_ids.append(fid)
                    fmt_by_id[fid] = cat["format"]
                    if x["format"] != cat["format"]:
                        errors.append(f"FORMAT mismatch: {path}")
                    expected_size = int(cat["size_bytes"])
                    if x["size"] != expected_size or x["manifest_size"] != expected_size:
                        errors.append(f"SIZE mismatch: {path}")
                    expected_sha = (cat.get("lfs_sha256") or "").strip().lower()
                    if expected_sha and (str(x["sha"]).strip().lower() != expected_sha
                                         or str(x["lfs"] or "").strip().lower() != expected_sha):
                        errors.append(f"LFS SHA mismatch: {path}")
                    expected_blob = (cat.get("git_blob_id") or "").strip()
                    if expected_blob and (x["blob"] or "") != expected_blob:
                        errors.append(f"Git blob ID mismatch: {path}")
                    if x["archive_state"] != "verified" or x["parse_state"] != "success":
                        errors.append(f"FILE STATE mismatch: {path} archive={x['archive_state']} parse={x['parse_state']}")
                    if type(x["record_count"]) is not int or x["record_count"] < 0:
                        errors.append(f"INVALID record_count: {path}")
                    else:
                        counts_by_id[fid] = x["record_count"]
                    if not x["sha"] or not x["archive_path"]:
                        errors.append(f"MISSING archive metadata: {path}")
                    else:
                        sha = str(x["sha"]).strip().lower()
                        expected_path = f"raw-archive-v2/objects/{sha[:2]}/{sha}"
                        if str(x["archive_path"]).replace("\\", "/") != expected_path:
                            errors.append(f"ARCHIVE PATH mismatch: {path}")

                if len(selected_ids) != EXPECTED_FILES or len(set(selected_ids)) != EXPECTED_FILES:
                    errors.append(f"Linked file IDs={len(selected_ids)}; unique={len(set(selected_ids))}; expected 276")

                print("Counting actual raw_records across pinned linked file IDs...", flush=True)
                record_counts: Counter = Counter()
                record_kinds: dict[int, Counter] = defaultdict(Counter)
                if selected_ids:
                    cur.execute("""
                        SELECT file_id, record_kind, (member_id IS NOT NULL) AS has_member, count(*)
                        FROM raw.raw_records
                        WHERE file_id = ANY(%s::bigint[])
                        GROUP BY file_id, record_kind, (member_id IS NOT NULL)
                    """, (selected_ids,))
                    for fid, kind, has_member, n in cur.fetchall():
                        record_counts[fid] += n
                        record_kinds[fid][(kind, has_member)] += n
                physical_total = sum(record_counts.values())
                for path, cat in catalog.items():
                    x = actual_by_path.get(path)
                    if not x or not x["fid"]:
                        continue
                    fid = int(x["fid"])
                    expected_n = counts_by_id.get(fid)
                    actual_n = record_counts.get(fid, 0)
                    if expected_n is None or actual_n != expected_n:
                        errors.append(f"RECORD COUNT mismatch: {path}: file_metadata={expected_n}, physical={actual_n}")
                    fmt = cat["format"]
                    kind_allow = ("csv_row",) if fmt.startswith("csv") else (("tar_json",) if fmt == "tar.gz" else ("json_item", "json_object", "json_scalar"))
                    for (kind, has_member), n in record_kinds[fid].items():
                        if kind not in kind_allow or has_member != (fmt == "tar.gz"):
                            kind_errors += n
                            errors.append(f"RECORD KIND mismatch: {path}: kind={kind}, has_member={has_member}, n={n}")

                print("Checking physical TAR members and member states...", flush=True)
                tar_ids = [int(actual_by_path[p]["fid"]) for p in EXPECTED_TAR if actual_by_path.get(p, {}).get("fid")]
                member_counts: Counter = Counter()
                bad_member_states: Counter = Counter()
                if tar_ids:
                    cur.execute("""
                        SELECT file_id, parse_state, count(*)
                        FROM raw.archive_members
                        WHERE file_id = ANY(%s::bigint[])
                        GROUP BY file_id, parse_state
                    """, (tar_ids,))
                    for fid, state, n in cur.fetchall():
                        member_counts[fid] += n
                        if state not in ("success", "skipped"):
                            bad_member_states[fid] += n
                members_total = sum(member_counts.values())
                for path, (expected_members, expected_records) in EXPECTED_TAR.items():
                    x = actual_by_path.get(path)
                    if not x or not x["fid"]:
                        continue
                    fid = int(x["fid"])
                    if member_counts[fid] != expected_members or record_counts[fid] != expected_records:
                        errors.append(f"TAR COUNT mismatch: {path}: members={member_counts[fid]}/{expected_members}, records={record_counts[fid]}/{expected_records}")
                    if bad_member_states[fid]:
                        errors.append(f"TAR member states NOT complete: {path}: bad={bad_member_states[fid]}")

    print("--- RECONCILIATION SUMMARY ---")
    print(f"Catalog paths: {len(catalog)}")
    print(f"Observation paths: {len(actual_by_path)}")
    print(f"Linked file IDs: {sum(1 for x in actual_by_path.values() if x['presence'] == 'linked')}")
    print(f"Success files (observed): {sum(1 for x in actual_by_path.values() if x['parse_state'] == 'success')}")
    print(f"Metadata records (linked): {sum(counts_by_id.values()):,}")
    print(f"Actual raw_records (linked): {physical_total:,}")
    print(f"TAR members (two files): {members_total:,}")
    print(f"Unexpected record kinds: {kind_errors:,}")
    print(f"Mismatch count: {len(errors)}")
    for issue in errors[:30]:
        print("ISSUE:", issue)
    if len(errors) > 30:
        print(f"... {len(errors) - 30} additional issues not printed")
    print("Snapshot status unchanged:", db_status)
    print("Manifest archive registered:", bool(manifest_sha and manifest_path))
    if errors:
        print("M3.9.1 DB RECONCILIATION: FAIL (no database writes)")
        return 1
    print("M3.9.1 DB RECONCILIATION: PASS (no database writes)")
    print("Note: physical archive-byte verification and manifest finalization are separate checkpoints.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Read-only pinned PostgreSQL raw reconciliation")
    p.add_argument("--check", action="store_true", help="Run read-only DB consistency checks")
    args = p.parse_args()
    if not args.check:
        p.error("Explicit --check is required")
    try:
        return reconcile()
    except Exception as exc:
        print(f"M3.9.1 DB RECONCILIATION: ERROR | {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
