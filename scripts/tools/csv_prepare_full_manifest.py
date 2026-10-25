#!/usr/bin/env python3
"""M4.CSV.4g.1a | Guarded full-manifest preparation for pinned mixed CSV/JSON batch.

Run from EPL project root using `python -B -m scripts.tools.csv_prepare_full_manifest`.
--plan: read-only PostgreSQL and revision-pinned HF metadata / immutable archive reads.
--publish: AFTER reviewing the --plan digest, create ONLY the full manifest report
and immutable manifest archive; never overwrite existing files.

Important: legacy prepare_incremental_manifest.main() uses a JSON-only loader.
This adapter deliberately reuses only its tested PURE manifest constructors and
create-only publication helpers while constructing a mixed-format SyncPlan.
No new snapshots, no PostgreSQL modifications or raw-object/cache writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PREFLIGHT_MARKER = "M4.CSV.4f.2 MIXED SNAPSHOT PREPARATION PLAN: PASS (NOT WRITE APPROVAL)"
MAX_CSV_WRITER_BYTES = 64 * 1024 * 1024


def arguments(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-snapshot", type=int, required=True)
    ap.add_argument("--target-revision", required=True)
    ap.add_argument("--expected-fingerprint", required=True)
    ap.add_argument("--expected-raw-files", type=int, required=True)
    ap.add_argument("--expected-reuse", type=int, required=True)
    ap.add_argument("--expected-files", type=int, required=True)
    ap.add_argument("--expected-bytes", type=int, required=True)
    ap.add_argument("--expected-records", type=int, required=True)
    ap.add_argument("--expected-references", type=int, required=True)
    ap.add_argument("--cache-dir", default="reports/m4_6_content_cache")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--publish", action="store_true")
    ap.add_argument("--confirm-manifest-archive-only", action="store_true")
    ap.add_argument("--expected-manifest-sha256")
    a = ap.parse_args(argv)
    if (a.baseline_snapshot != 366 or not SHA40.fullmatch(a.target_revision)
            or not SHA256.fullmatch(a.expected_fingerprint)):
        ap.error("Invalid baseline (requires #366), target SHA40 or fingerprint SHA256")
    if (a.expected_raw_files != 296 or a.expected_reuse != 293
            or a.expected_files != 3 or a.expected_bytes != 2916818
            or a.expected_records != 72 or a.expected_references != 8502614):
        ap.error("Pinned mixed CSV/JSON batch counts differ from reviewed 4f.2 receipt")
    if (not isinstance(a.cache_dir, str) or not a.cache_dir.startswith("reports/")
            or "\\" in a.cache_dir or any(part in ("", ".", "..")
                    for part in a.cache_dir.split("/"))):
        ap.error("Unsafe cache path")
    if a.publish:
        if (not a.confirm_manifest_archive_only or
                not a.expected_manifest_sha256 or
                not SHA256.fullmatch(a.expected_manifest_sha256)):
            ap.error("--publish requires --confirm-manifest-archive-only and --expected-manifest-sha256")
    elif a.confirm_manifest_archive_only or a.expected_manifest_sha256:
        ap.error("--plan forbids publication confirmation/digest flags")
    return a


def run_independent_mixed_gate(a):
    command = [sys.executable, "-B", "-m", "scripts.tools.csv_mixed_snapshot_preflight",
               "--baseline-snapshot", str(a.baseline_snapshot),
               "--target-revision", a.target_revision,
               "--expected-fingerprint", a.expected_fingerprint,
               "--expected-reuse", str(a.expected_reuse),
               "--expected-files", str(a.expected_files),
               "--expected-bytes", str(a.expected_bytes),
               "--expected-records", str(a.expected_records),
               "--expected-raw-files", str(a.expected_raw_files),
               "--cache-dir", a.cache_dir, "--plan"]
    run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                         encoding="utf-8", errors="replace", timeout=900)
    lines = set(run.stdout.splitlines())
    expected = {PREFLIGHT_MARKER,
                f"Projected record references: {a.expected_references}",
                f"Candidate parsed records: {a.expected_records}",
                "Candidate CSV / JSON: 1 / 2",
                "Already registered candidate versions: 0"}
    if run.returncode != 0 or not expected.issubset(lines):
        raise RuntimeError("Independent mixed preflight failed or drifted: "
                           + (run.stderr.strip() or run.stdout[-1500:]))
    print("Independent CSV/JSON archive and writer preflight: PASS", flush=True)


def rebuild_mixed_context(a):
    """Reconstruct pinned mixed SyncPlan without JSON-only candidate helper."""
    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.incremental_sync import assemble_plan
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.raw_sync_write_plan import read_db_state

    baseline_revision, baseline = load_baseline(a.baseline_snapshot, REPO)
    revision, target, excluded = load_target(REPO, a.target_revision)
    db_revision, existing = read_db_state(a.baseline_snapshot, REPO, revision)
    if (revision != a.target_revision or revision == baseline_revision
            or db_revision != baseline_revision or len(baseline) != a.expected_raw_files
            or len(target) != a.expected_raw_files or type(excluded) is not int
            or excluded < 0):
        raise RuntimeError("Pinned baseline/target revision or Raw scope drift")
    plan = assemble_plan(baseline, target, existing, read_existing_source_codes(),
                         allow_csv_planning=True, max_csv_bytes=MAX_CSV_WRITER_BYTES)
    candidates = tuple(x for x in plan.actions if x.category in ("NEW", "CHANGED"))
    formats = Counter(x.file_format for x in candidates)
    if (plan.fingerprint != a.expected_fingerprint
            or plan.count("REMOVED") or plan.count("REVIEW")
            or plan.target_raw_count != a.expected_raw_files
            or plan.count("UNCHANGED") != a.expected_reuse
            or len(candidates) != a.expected_files
            or plan.candidate_bytes != a.expected_bytes
            or formats != {"csv": 1, "json": 2}
            or set(x.path for x in plan.actions) != set(target)
            or len(set(x.path for x in plan.actions)) != a.expected_raw_files):
        raise RuntimeError("Pinned mixed-format SyncPlan fingerprint/counts/path coverage drift")
    return SimpleNamespace(repo_id=REPO, target_revision=revision, target=target,
                           excluded=excluded, plan=plan), len(target) + excluded


def main(argv=None):
    a = arguments(argv)
    print("=== M4.CSV.4g.1a | PINNED MIXED FULL MANIFEST ===", flush=True)
    print("Mode:", "PLAN (NO WRITES)" if a.plan else "PUBLISH (MANIFEST FILES ONLY)")
    run_independent_mixed_gate(a)
    context, full_files = rebuild_mixed_context(a)

    # Reuse the same deterministic schema/bytes and create-only publication as M4.6.
    # Do NOT call legacy prepare_incremental_manifest.main()/load_context (JSON-only).
    from scripts.tools.prepare_incremental_manifest import (
        build_manifest, fetch_full_tree, canonical_bytes, inspect_manifest_outputs,
        publish_manifest,
    )
    tree = fetch_full_tree(context)  # pinned revision; metadata only
    manifest = build_manifest(context, tree,
                              expected_full_files=full_files,
                              expected_raw_files=a.expected_raw_files)
    payload = canonical_bytes(manifest)
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    report, archive, states = inspect_manifest_outputs(ROOT, a.target_revision, digest, payload)
    print("Repository:", REPO)
    print("Baseline:", a.baseline_snapshot, "| target revision:", a.target_revision)
    print("SyncPlan fingerprint:", context.plan.fingerprint)
    print("Full manifest files:", full_files, "| Raw:", a.expected_raw_files,
          "| Non-Raw:", context.excluded)
    print("Candidate formats: CSV=1 JSON=2 | candidate records:", a.expected_records)
    print("Manifest bytes:", len(payload))
    print("Manifest SHA256:", digest)
    print("Report:", report, "|", states[0])
    print("Archive:", archive, "|", states[1])
    if a.plan:
        print("PostgreSQL writes: NONE | Manifest writes: NONE | Raw cache/archive writes: NONE")
        print("M4.CSV.4g.1a MANIFEST PLAN: PASS (NOT APPROVAL TO PUBLISH)")
        return 0

    if digest != a.expected_manifest_sha256:
        raise RuntimeError("Manifest SHA256 changed after read-only plan; publication refused")
    _, _, report_action, archive_action = publish_manifest(ROOT, a.target_revision,
                                                           digest, payload)
    _, _, rechecked = inspect_manifest_outputs(ROOT, a.target_revision, digest, payload)
    if rechecked != ("ALREADY_VERIFIED", "ALREADY_VERIFIED"):
        raise RuntimeError("Manifest publication readback mismatch")
    print("Report publication:", report_action)
    print("Archive publication:", archive_action)
    print("PostgreSQL writes: NONE | Snapshot created: NO | Candidate archives unchanged")
    print("M4.CSV.4g.1a MANIFEST PUBLICATION: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"M4.CSV.4g.1a BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
