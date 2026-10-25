#!/usr/bin/env python3
"""M4.CSV.4g.1 | Read-only manifest and snapshot-registration readiness audit.

Designed for the pinned EPL mixed CSV/JSON incremental batch after
M4.CSV.4f.2. NEVER registers a snapshot or mutates PostgreSQL, cache,
archive, manifest or checkpoint. Run from project root via:
 python -B -m scripts.tools.csv_snapshot_registration_readiness ... --plan

The legacy register_incremental_snapshot.py preflight and generic candidate
planner/ingestion coordinator remain JSON-only in the reviewed source bundle.
Passing this audit is NOT permission to invoke their --register/--apply modes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPO = "Toibinguz/epl-premier-league-dataset"
DATABASE = "epl_raw_storage_v2"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
PREPARATION_PASS = "M4.CSV.4f.2 MIXED SNAPSHOT PREPARATION PLAN: PASS (NOT WRITE APPROVAL)"


def get_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-raw-files", type=int, required=True)
    p.add_argument("--expected-reuse", type=int, required=True)
    p.add_argument("--expected-files", type=int, required=True)
    p.add_argument("--expected-bytes", type=int, required=True)
    p.add_argument("--expected-records", type=int, required=True)
    p.add_argument("--expected-references", type=int, required=True)
    p.add_argument("--cache-dir", default="reports/m4_6_content_cache")
    p.add_argument("--plan", action="store_true", required=True)
    a = p.parse_args(argv)
    if (a.baseline_snapshot <= 0 or not SHA40.fullmatch(a.target_revision)
            or not SHA256.fullmatch(a.expected_fingerprint)):
        p.error("Invalid pinned baseline/revision/fingerprint")
    if (a.expected_raw_files <= 0 or a.expected_files != 3
            or a.expected_reuse < 0 or a.expected_reuse + a.expected_files != a.expected_raw_files
            or a.expected_bytes <= 0 or a.expected_records <= 0
            or a.expected_references < a.expected_records):
        p.error("Inconsistent mixed-format expectations")
    if (not isinstance(a.cache_dir, str) or not a.cache_dir.startswith("reports/")
            or "\\" in a.cache_dir or any(part in ("", ".", "..")
               for part in a.cache_dir.split("/"))):
        p.error("Unsafe project-relative cache directory")
    return a


def safe_local(relative: str) -> Path:
    if (not isinstance(relative, str) or relative.startswith("/") or "\\" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise RuntimeError("Unsafe project-relative manifest path")
    cursor = ROOT
    for part in relative.split("/"):
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimeError("Symlink in manifest path: " + relative)
    if not cursor.is_file() or not cursor.resolve(strict=True).is_relative_to(ROOT.resolve(strict=True)):
        raise RuntimeError("Manifest file missing or unsafe: " + relative)
    return cursor


def verify_manifest(args, target, excluded: int) -> tuple[str, int]:
    report_rel = f"reports/m4_6_manifests/{args.target_revision}/hf_manifest_v2.json"
    report = safe_local(report_rel)
    size = report.stat().st_size
    if not 0 < size <= MAX_MANIFEST_BYTES:
        raise RuntimeError("Manifest report byte budget invalid")
    data = report.read_bytes()
    if len(data) != size:
        raise RuntimeError("Manifest report changed while reading")
    digest = hashlib.sha256(data).hexdigest()
    archive_rel = f"raw-archive-v2/manifests/{digest[:2]}/{digest}.json"
    archive = safe_local(archive_rel)
    if archive.stat().st_size != size or archive.read_bytes() != data:
        raise RuntimeError("Full manifest immutable report/archive mismatch")
    manifest = json.loads(data)
    if not isinstance(manifest, dict):
        raise RuntimeError("Full manifest must be a JSON object")
    entries = manifest.get("files")
    full = args.expected_raw_files + excluded
    if (manifest.get("repo_id") != REPO or manifest.get("repo_type") != "dataset"
            or manifest.get("requested_revision") != args.target_revision
            or manifest.get("resolved_commit") != args.target_revision
            or manifest.get("file_count") != full or not isinstance(entries, list)
            or len(entries) != full):
        raise RuntimeError("Full manifest identity/coverage mismatch")
    paths = [e.get("relative_path") if isinstance(e, dict) else None for e in entries]
    if any(not isinstance(p, str) or not p or "\\" in p or p.startswith("/")
           or any(x in ("", ".", "..") for x in p.split("/")) for p in paths):
        raise RuntimeError("Malformed manifest file path")
    if len(set(paths)) != full:
        raise RuntimeError("Duplicate full-manifest paths")
    raw = {e["relative_path"]: e for e in entries
           if e["relative_path"].startswith(RAW_PREFIXES)}
    if len(raw) != args.expected_raw_files or set(raw) != set(target):
        raise RuntimeError("Full manifest Raw paths differ from pinned target")
    for path, e in raw.items():
        remote = target[path]
        if (e.get("hf_revision") != args.target_revision
                or e.get("size_bytes") != remote.size
                or e.get("git_blob_id") != (remote.git_blob_id or "")
                or e.get("lfs_sha256") != (remote.lfs_sha256 or "")):
            raise RuntimeError("Manifest/HF Raw identity drift: " + path)
    formats = Counter(e.get("format") for e in raw.values())
    print("Full manifest:", full, "paths | Raw:", len(raw), "| excluded:", excluded)
    print("Manifest SHA256:", digest)
    print("Manifest archive:", archive_rel)
    print("Target Raw formats:", ", ".join(f"{k}={v}" for k, v in sorted(formats.items())))
    return digest, full


def run_mixed_preflight(a):
    argv = [sys.executable, "-B", "-m", "scripts.tools.csv_mixed_snapshot_preflight",
            "--baseline-snapshot", str(a.baseline_snapshot),
            "--target-revision", a.target_revision,
            "--expected-fingerprint", a.expected_fingerprint,
            "--expected-reuse", str(a.expected_reuse),
            "--expected-files", str(a.expected_files),
            "--expected-bytes", str(a.expected_bytes),
            "--expected-records", str(a.expected_records),
            "--expected-raw-files", str(a.expected_raw_files),
            "--cache-dir", a.cache_dir, "--plan"]
    result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=900)
    if result.returncode != 0 or PREPARATION_PASS not in result.stdout.splitlines():
        raise RuntimeError("Independent mixed-format preflight failed: "
                           + (result.stderr.strip() or result.stdout[-1600:]))
    for line in result.stdout.splitlines():
        if line.startswith(("[PASS]", "Projected record references:", "Candidate parsed records:")):
            print(line)
    expected = f"Projected record references: {a.expected_references}"
    if expected not in result.stdout.splitlines():
        raise RuntimeError("Projected record references drifted since 4f.2")
    print("Independent 4f.2 preflight: PASS")


def audit_database(a, expected_full: int):
    import psycopg
    from scripts.tools.register_incremental_snapshot import db_parameters
    params = db_parameters()
    if params.get("dbname") != DATABASE:
        raise RuntimeError("Wrong database")
    params["options"] = "-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000"
    params["application_name"] = "m4_csv_4g_registration_readiness"
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone() != (DATABASE,):
                raise RuntimeError("DB name mismatch")
            cur.execute("""SELECT repo_id,hf_revision,status,manifest_file_count,
                                  raw_scope_file_count,manifest_sha256,manifest_archive_path
                           FROM raw.snapshot_runs WHERE snapshot_id=%s""",
                        (a.baseline_snapshot,))
            row = cur.fetchone()
            if (row is None or row[0] != REPO or row[2] != "completed"
                    or row[4] != a.expected_raw_files or not isinstance(row[5], str)
                    or not SHA256.fullmatch(row[5])
                    or row[6] != f"raw-archive-v2/manifests/{row[5][:2]}/{row[5]}.json"
                    or row[1] == a.target_revision):
                raise RuntimeError("Completed baseline/registered manifest invalid")
            cur.execute("""SELECT snapshot_id FROM raw.snapshot_runs
                           WHERE repo_id=%s AND status='completed'
                           ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1""", (REPO,))
            if cur.fetchone() != (a.baseline_snapshot,):
                raise RuntimeError("A newer completed snapshot appeared")
            cur.execute("""SELECT COUNT(*) FROM raw.snapshot_runs
                           WHERE repo_id=%s AND (status='in_progress' OR hf_revision=%s)""",
                        (REPO, a.target_revision))
            if cur.fetchone() != (0,):
                raise RuntimeError("Target already exists or another run in progress")
            cur.execute("""SELECT COUNT(*), COUNT(DISTINCT o.relative_path),
                                  COUNT(DISTINCT o.file_id),
                                  COUNT(*) FILTER (WHERE o.presence_status='linked'
                                     AND f.archive_state='verified' AND f.parse_state='success')
                           FROM raw.file_observations o
                           LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                           WHERE o.snapshot_id=%s AND o.repo_id=%s""",
                        (a.baseline_snapshot, REPO))
            if cur.fetchone() != (a.expected_raw_files,)*4:
                raise RuntimeError("Baseline observations or verified file states inconsistent")
    print("Baseline database snapshot:", a.baseline_snapshot, "completed")
    print("Target database snapshot: ABSENT | in_progress: NONE")
    print("Baseline observations:", a.expected_raw_files, "verified/unique")
    print("Expected target manifest files:", expected_full)


def main(argv=None):
    args = get_args(argv)
    print("=== M4.CSV.4g.1 | REGISTRATION READINESS PLAN (READ-ONLY) ===", flush=True)
    run_mixed_preflight(args)
    from scripts.tools.check_raw_updates import load_target
    _, target, excluded = load_target(REPO, args.target_revision)
    if len(target) != args.expected_raw_files or excluded < 0:
        raise RuntimeError("Raw metadata coverage drift")
    digest, full = verify_manifest(args, target, excluded)
    audit_database(args, full)
    print("Registration inputs: pinned and independently verified")
    print("Manifest SHA256 expected:", digest)
    print("Downstream CSV candidate ingestion: NOT YET PROVEN; no registration approval")
    print("PostgreSQL writes: NONE | Snapshot created: NO | Archive writes: NONE")
    print("M4.CSV.4g.1 REGISTRATION READINESS PLAN: PASS (NOT WRITE APPROVAL)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M4.CSV.4g.1 BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
