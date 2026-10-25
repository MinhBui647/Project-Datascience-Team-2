#!/usr/bin/env python3
"""M4.CSV.4f.2 | Read-only mixed CSV/JSON snapshot preparation gate.

Install at scripts/tools/csv_mixed_snapshot_preflight.py in the EPL project.
Requires existing project's modules and authenticated read-only access to the
pinned HF metadata and PostgreSQL. NEVER inserts/updates SQL, publishes files,
or registers/finalizes snapshots. Reads ONLY cached candidate bodies and
immutable archive objects; fail closed on missing or conflicting evidence.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = "Toibinguz/epl-premier-league-dataset"
REV_RE = re.compile(r"[a-f0-9]{40}\Z")
DIGEST_RE = re.compile(r"[a-f0-9]{64}\Z")
EXPECTED_FORMATS = {"csv": 1, "json": 2}
MAX_CSV_FILE_BYTES = 64 * 1024 * 1024  # match production CSV Writer Core
MAX_JSON_FILE_BYTES = 16 * 1024 * 1024


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-reuse", type=int, required=True)
    p.add_argument("--expected-files", type=int, required=True)
    p.add_argument("--expected-bytes", type=int, required=True)
    p.add_argument("--expected-records", type=int, required=True)
    p.add_argument("--expected-raw-files", type=int, required=True)
    p.add_argument("--cache-dir", default="reports/m4_6_content_cache")
    p.add_argument("--plan", required=True, action="store_true")
    return p


def file_hashes(path: Path) -> tuple[int, str, str]:
    size = path.stat().st_size
    sha = hashlib.sha256()
    git = hashlib.sha1(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
            git.update(chunk)
    return size, sha.hexdigest(), git.hexdigest()


def assert_hf_identity(identity, *, size: int, sha: str, blob: str, path: str) -> None:
    if size != identity.size:
        raise RuntimeError(f"HF size mismatch: {path}")
    if identity.lfs_sha256:
        if sha != identity.lfs_sha256:
            raise RuntimeError(f"HF LFS digest mismatch: {path}")
    elif identity.git_blob_id:
        if blob != identity.git_blob_id:
            raise RuntimeError(f"HF git blob digest mismatch: {path}")
    else:
        raise RuntimeError(f"No authoritative HF content identity: {path}")


def archive_location(sha: str) -> tuple[Path, str]:
    rel = f"raw-archive-v2/objects/{sha[:2]}/{sha}"
    parts = ("raw-archive-v2", "objects", sha[:2], sha)
    walk = ROOT
    for part in parts:
        walk = walk / part
        if walk.is_symlink():
            raise RuntimeError("Symlink in immutable archive path: " + rel)
    path = ROOT / rel
    if not path.is_file() or path.resolve(strict=True) != path:
        raise RuntimeError("Missing/untrusted immutable archive: " + rel)
    return path, rel


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if (args.baseline_snapshot <= 0 or not REV_RE.fullmatch(args.target_revision)
            or not DIGEST_RE.fullmatch(args.expected_fingerprint)):
        raise ValueError("Invalid baseline/revision/fingerprint")
    if (args.expected_reuse < 0 or args.expected_files != 3
            or args.expected_bytes <= 0 or args.expected_records <= 0
            or args.expected_raw_files != args.expected_reuse + args.expected_files):
        raise ValueError("Expected counts inconsistent with pinned 3-file mixed batch")
    if not args.plan:
        raise RuntimeError("Only --plan is accepted")

    # Import project modules only after strictly validating the pinned CLI.
    from huggingface_hub import hf_hub_download
    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.incremental_sync import assemble_plan
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.raw_sync_write_plan import read_db_state
    from scripts.tools.incremental_reuse_linker import build_reuse_links
    from scripts.tools.incremental_snapshot_prepare import (
        read_target_db_state, validate_preparation,
    )
    from scripts.tools.incremental_archive_promotion import ArchiveCandidate
    from scripts.tools.incremental_writer_core import (
        prepare_json_version, prepare_csv_version,
    )
    from scripts.tools.download_incremental_candidates import validated_cache_path

    base_rev, baseline = load_baseline(args.baseline_snapshot, REPO)
    revision, target, excluded = load_target(REPO, args.target_revision)
    db_revision, existing = read_db_state(args.baseline_snapshot, REPO, revision)
    if (base_rev != db_revision or revision != args.target_revision
            or base_rev == revision or len(baseline) != args.expected_raw_files
            or len(target) != args.expected_raw_files):
        raise RuntimeError("Pinned baseline/target or DB state drift")
    plan = assemble_plan(baseline, target, existing, read_existing_source_codes(),
                         allow_csv_planning=True,
                         max_csv_bytes=MAX_CSV_FILE_BYTES)
    if (plan.fingerprint != args.expected_fingerprint or
            plan.target_raw_count != args.expected_raw_files or
            plan.count("REMOVED") or plan.count("REVIEW") or
            plan.count("UNCHANGED") != args.expected_reuse or
            plan.count("NEW") + plan.count("CHANGED") != args.expected_files or
            plan.candidate_bytes != args.expected_bytes):
        raise RuntimeError("Pinned mixed-format SyncPlan mismatch")

    links = build_reuse_links(plan, target)
    cache = validated_cache_path(args.cache_dir, root=ROOT, create=False)
    actions = tuple(sorted((x for x in plan.actions
                            if x.category in ("NEW", "CHANGED")),
                           key=lambda x: x.path))
    seen_formats = {"csv": 0, "json": 0}
    archives = []
    parsed_total = 0
    for a in actions:
        if (a.kind != "UPSERT_VERSION_AFTER_REHASH"
                or a.file_format not in seen_formats
                or a.source_code is None or a.path not in target):
            raise RuntimeError("Unsupported candidate action: " + a.path)
        remote = target[a.path]
        if (a.expected_bytes != remote.size or
                (a.git_blob_id, a.lfs_sha256) !=
                (remote.git_blob_id, remote.lfs_sha256)):
            raise RuntimeError("Pinned candidate metadata changed: " + a.path)
        local = Path(hf_hub_download(repo_id=REPO, filename=a.path,
                        repo_type="dataset", revision=revision,
                        cache_dir=str(cache), local_files_only=True))
        if not local.resolve(strict=True).is_relative_to(cache.resolve(strict=True)):
            raise RuntimeError("Candidate cache path outside trusted directory")
        size, sha, blob = file_hashes(local)
        assert_hf_identity(remote, size=size, sha=sha, blob=blob, path=a.path)
        stored, rel = archive_location(sha)
        if file_hashes(stored) != (size, sha, blob):
            raise RuntimeError("Archive differs from pinned cached body: " + a.path)
        prepare = prepare_csv_version if a.file_format == "csv" else prepare_json_version
        prepared = prepare(REPO, a.path, revision, a.source_code, stored,
                           remote, root=ROOT)
        count = len(prepared.records)
        if (prepared.content_sha256 != sha or prepared.size_bytes != size
                or prepared.archive_relative_path != rel or count <= 0):
            raise RuntimeError("Prepared Writer/Core record mismatch: " + a.path)
        archives.append(ArchiveCandidate(
            relative_path=a.path, sha256=sha, byte_count=size,
            record_count=count, archive_relative_path=rel,
            action="ALREADY_ARCHIVED",
        ))
        seen_formats[a.file_format] += 1
        parsed_total += count
        print(f"[PASS] {a.file_format.upper():4} {count:>5} records "
              f"{size:>9} bytes | {a.path}", flush=True)

    if seen_formats != EXPECTED_FORMATS or parsed_total != args.expected_records:
        raise RuntimeError("Mixed-format distribution or record count drift")
    existing_versions = read_target_db_state(REPO, revision, archives)
    result = validate_preparation(
        plan, excluded=excluded, links=links, archives=tuple(archives),
        existing_versions=existing_versions,
        expected_fingerprint=args.expected_fingerprint,
        expected_reuse=args.expected_reuse,
        expected_candidates=args.expected_files,
        expected_bytes=args.expected_bytes,
        expected_records=args.expected_records,
    )
    print("--- M4.CSV.4f.2 | MIXED SNAPSHOT PREPARATION ---")
    print("Baseline snapshot:", args.baseline_snapshot)
    print("Target revision:", revision)
    print("Plan fingerprint:", plan.fingerprint)
    print("Target Raw files:", result.raw_files)
    print("REUSE verified:", result.reused)
    print("Candidate CSV / JSON:", seen_formats["csv"], "/", seen_formats["json"])
    print("Candidate archives verified:", result.candidates)
    print("Candidate bytes:", result.candidate_bytes)
    print("Candidate parsed records:", result.candidate_records)
    print("Projected record references:", result.projected_records)
    print("Already registered candidate versions:", result.already_registered_versions)
    print("PostgreSQL writes: NONE | Archive writes: NONE | Snapshot registered: NO")
    print("M4.CSV.4f.2 MIXED SNAPSHOT PREPARATION PLAN: PASS (NOT WRITE APPROVAL)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"M4.CSV.4f.2 BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
