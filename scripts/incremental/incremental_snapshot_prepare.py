#!/usr/bin/env python3
"""M4.6.3b.1: generic, read-only preparation gate for an incremental snapshot.

Validates a pinned HF plan, optionally zero REUSE file versions, and a nonempty
set of already-verified candidate archives before snapshot registration.
No --commit, PostgreSQL changes, archive writes, or HF file-body downloads.
Legacy M4.5.4c.3i success lines remain compatible with registration coordinator.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# External dependencies are imported by main() so pure contracts below are
# testable offline without a Hugging Face or PostgreSQL installation.
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class PreparedSnapshot:
    total_files: int
    raw_files: int
    reused: int
    candidates: int
    candidate_bytes: int
    candidate_records: int
    reused_records: int
    projected_records: int
    already_registered_versions: int


def validate_expected_counts(*, reuse: int, candidates: int,
                             candidate_bytes: int, candidate_records: int) -> None:
    """Fail closed on malformed budgets; no-change is handled before preparation.

    Zero REUSE is valid. At least one positive-size candidate is required;
    zero parser records can be valid for an empty JSON collection.
    """
    for label, value in (("expected_reuse", reuse), ("expected_files", candidates),
                         ("expected_bytes", candidate_bytes),
                         ("expected_records", candidate_records)):
        if type(value) is not int:
            raise RuntimeError(f"{label} must be an integer (not bool)")
    if reuse < 0 or candidates <= 0 or candidate_bytes <= 0 or candidate_records < 0:
        raise RuntimeError("Expected REUSE >= 0; candidates > 0; bytes > 0; records >= 0")


def validate_preparation(plan, *, excluded: int, links, archives, existing_versions,
                         expected_fingerprint: str, expected_reuse: int,
                         expected_candidates: int, expected_bytes: int,
                         expected_records: int) -> PreparedSnapshot:
    """Pure fail-closed composition; physical candidate archives must be verified."""
    validate_expected_counts(reuse=expected_reuse, candidates=expected_candidates,
                             candidate_bytes=expected_bytes, candidate_records=expected_records)
    if not isinstance(expected_fingerprint, str) or not SHA256.fullmatch(expected_fingerprint):
        raise RuntimeError("Invalid pinned plan fingerprint")
    if plan.fingerprint != expected_fingerprint:
        raise RuntimeError("Pinned plan fingerprint mismatch")
    if plan.count("REMOVED") or plan.count("REVIEW"):
        raise RuntimeError("Removed/review file requires separate workflow")
    if type(excluded) is not int or excluded < 0:
        raise RuntimeError("Invalid excluded non-Raw count")

    planned_reuse = {a.path for a in plan.actions if a.kind == "REUSE_FILE_ID"
                     and a.category == "UNCHANGED"}
    planned_candidates = {a.path for a in plan.actions
                          if a.kind == "UPSERT_VERSION_AFTER_REHASH"
                          and a.category in ("NEW", "CHANGED")}
    if (len(plan.actions) != plan.target_raw_count
            or len({a.path for a in plan.actions}) != len(plan.actions)
            or len(planned_reuse) != plan.count("UNCHANGED")
            or len(planned_candidates) != plan.count("NEW") + plan.count("CHANGED")
            or len(planned_reuse | planned_candidates) != plan.target_raw_count
            or planned_reuse & planned_candidates):
        raise RuntimeError("Unexpected/incomplete target plan actions")
    if len(links) != expected_reuse or len(links) != plan.count("UNCHANGED"):
        raise RuntimeError("REUSE link count drift")
    if len(archives) != expected_candidates or len(archives) != plan.count("NEW") + plan.count("CHANGED"):
        raise RuntimeError("Candidate count drift")
    if plan.target_raw_count != len(links) + len(archives):
        raise RuntimeError("Target Raw manifest coverage incomplete")

    linked_paths = [l.relative_path for l in links]
    archive_paths = [x.relative_path for x in archives]
    if (len(set(linked_paths)) != len(linked_paths)
            or len(set(archive_paths)) != len(archive_paths)
            or set(linked_paths) != planned_reuse
            or set(archive_paths) != planned_candidates):
        raise RuntimeError("REUSE/candidate paths differ from pinned plan")
    if any(x.action != "ALREADY_ARCHIVED" for x in archives):
        raise RuntimeError("A candidate object has not been archived and verified")
    if any(type(x.byte_count) is not int or x.byte_count <= 0
           or type(x.record_count) is not int or x.record_count < 0
           or not isinstance(x.sha256, str) or not SHA256.fullmatch(x.sha256)
           for x in archives):
        raise RuntimeError("Invalid verified archive metadata")
    if sum(x.byte_count for x in archives) != expected_bytes or plan.candidate_bytes != expected_bytes:
        raise RuntimeError("Archive byte count drift")
    if sum(x.record_count for x in archives) != expected_records:
        raise RuntimeError("Archive parser record count drift")
    archive_keys = {(x.relative_path, x.sha256) for x in archives}
    if (len(archive_keys) != len(archives)
            or set(existing_versions) != archive_keys
            or any(existing_versions[k] not in (None, "REUSE_VERIFIED") for k in archive_keys)):
        raise RuntimeError("Existing file-version audit incomplete/invalid")

    return PreparedSnapshot(
        total_files=plan.target_raw_count + excluded,
        raw_files=plan.target_raw_count,
        reused=len(links), candidates=len(archives),
        candidate_bytes=expected_bytes,
        candidate_records=expected_records,
        reused_records=plan.reused_records,
        projected_records=plan.reused_records + expected_records,
        already_registered_versions=sum(existing_versions[k] == "REUSE_VERIFIED"
                                        for k in archive_keys),
    )



def verify_writer_v1_compatibility(repo_id: str, revision: str, target,
                                   candidates, archives, *, root: Path = ROOT,
                                   allow_csv: bool = False) -> int:
    """Fail closed BEFORE snapshot registration if the writer cannot ingest a file.

    This reads immutable archive objects and parses their full JSON payloads via
    the existing production Writer Core. It never writes PostgreSQL or archives.
    Metadata/archive-level checks must have succeeded before this call.
    """
    from scripts.tools.incremental_writer_core import (
        MAX_RECORDS, prepare_json_version,
    )

    if type(allow_csv) is not bool:
        raise ValueError("Invalid CSV writer preflight opt-in")
    actions = {a.path: a for a in candidates}
    archive_map = {a.relative_path: a for a in archives}
    if (len(actions) != len(candidates) or len(archive_map) != len(archives)
            or set(actions) != set(archive_map)):
        raise RuntimeError("Candidate/immutable archive path coverage mismatch")
    total_records = 0
    for path in sorted(actions):
        action, item = actions[path], archive_map[path]
        if path not in target:
            raise RuntimeError("Candidate absent from pinned target: " + path)
        count = item.record_count
        if type(count) is not int or not 1 <= count <= MAX_RECORDS:
            raise RuntimeError(
                ((f"Writer Core V1 requires 1..{MAX_RECORDS} JSON records per candidate: "
                  if not allow_csv else
                  f"Writer Core requires 1..{MAX_RECORDS} records per candidate: ") +
                 f"{path} (records={count!r})")
            )
        if path.lower().endswith(".json"):
            prepare = prepare_json_version
        elif path.endswith(".csv") and allow_csv:
            from scripts.tools.incremental_writer_core import prepare_csv_version
            prepare = prepare_csv_version
        else:
            raise RuntimeError("Unsupported format in writer preflight: " + path)
        version = prepare(
            repo_id, path, revision, action.source_code,
            root / item.archive_relative_path, target[path], root=root,
        )
        if (version.content_sha256, version.size_bytes, len(version.records)) != (
                item.sha256, item.byte_count, count):
            raise RuntimeError("Writer Core/archive content mismatch: " + path)
        total_records += count
    return total_records


def read_target_db_state(repo_id: str, revision: str, archives):
    """Only read-only SELECTs. Block a target revision already registered or concurrent ingestion."""
    from dotenv import dotenv_values
    import psycopg

    cfg = {**dotenv_values(ROOT / ".env", encoding="utf-8-sig"),
           **dotenv_values(ROOT / ".env.v2", encoding="utf-8-sig")}
    params = {"host": cfg.get("DB_HOST"), "port": int(cfg.get("DB_PORT") or 5432),
              "dbname": cfg.get("DB_NAME"), "user": cfg.get("DB_USER"),
              "password": cfg.get("DB_PASSWORD"),
              "options": "-c default_transaction_read_only=on -c statement_timeout=120000"}
    identities = {}
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone() != ("epl_raw_storage_v2",):
                raise RuntimeError("Refusing unexpected database")
            cur.execute("""SELECT snapshot_id,status FROM raw.snapshot_runs
                           WHERE repo_id=%s AND hf_revision=%s""", (repo_id, revision))
            if cur.fetchone() is not None:
                raise RuntimeError("Target snapshot already exists; explicit resume review required")
            cur.execute("""SELECT snapshot_id,hf_revision,status FROM raw.snapshot_runs
                           WHERE repo_id=%s AND status='in_progress'""", (repo_id,))
            active = cur.fetchall()
            if active:
                raise RuntimeError(f"Another snapshot is in_progress: {active!r}")
            for item in archives:
                cur.execute("""SELECT file_id,file_size_bytes,archive_path,archive_state,
                                      parse_state,record_count
                               FROM raw.raw_files
                               WHERE repo_id=%s AND relative_path=%s AND content_sha256=%s""",
                            (repo_id, item.relative_path, item.sha256))
                found = cur.fetchall()
                if len(found) > 1:
                    raise RuntimeError("Duplicate immutable file identity")
                if not found:
                    identities[(item.relative_path, item.sha256)] = None
                    continue
                _, size, archive_path, archived, parsed, records = found[0]
                if ((size, archive_path, archived, parsed, records) !=
                    (item.byte_count, item.archive_relative_path, "verified", "success", item.record_count)):
                    raise RuntimeError("Existing file version is inconsistent: " + item.relative_path)
                identities[(item.relative_path, item.sha256)] = "REUSE_VERIFIED"
    return identities


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", required=True, type=int)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-files", required=True, type=int)
    p.add_argument("--expected-bytes", required=True, type=int)
    p.add_argument("--expected-records", required=True, type=int)
    p.add_argument("--expected-reuse", required=True, type=int)
    p.add_argument("--repo-id", default="Toibinguz/epl-premier-league-dataset")
    p.add_argument("--cache-dir", default="reports/m4_5_content_cache")
    p.add_argument("--plan", action="store_true", required=True)
    p.add_argument("--allow-csv-preflight-only", action="store_true",
                   help="CSV writer/archive verification; read-only, not snapshot registration")
    args = p.parse_args()
    if args.baseline_snapshot <= 0 or not SHA40.fullmatch(args.target_revision):
        p.error("Positive baseline and pinned SHA40 revision required")
    if not SHA256.fullmatch(args.expected_fingerprint):
        p.error("Valid SHA256 plan fingerprint required")
    try:
        validate_expected_counts(reuse=args.expected_reuse, candidates=args.expected_files,
                                 candidate_bytes=args.expected_bytes,
                                 candidate_records=args.expected_records)
    except RuntimeError as exc:
        p.error(str(exc))
    if args.repo_id != "Toibinguz/epl-premier-league-dataset":
        p.error("Repository differs from guarded EPL dataset")

    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.incremental_sync import assemble_plan
    from scripts.tools.incremental_cache_gate import cache_directory, candidate_actions
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.raw_sync_write_plan import read_db_state
    from scripts.tools.incremental_archive_promotion import audit_candidates
    from scripts.tools.incremental_reuse_linker import build_reuse_links

    print("=== M4.5.4c.3i | INCREMENTAL SNAPSHOT PREPARATION PLAN ===", flush=True)
    base_revision, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    revision, target, excluded = load_target(args.repo_id, args.target_revision)
    db_revision, existing = read_db_state(args.baseline_snapshot, args.repo_id, revision)
    if base_revision != db_revision or revision != args.target_revision or revision == base_revision:
        raise RuntimeError("Unexpected baseline/target revision")
    if args.allow_csv_preflight_only:
        plan = assemble_plan(baseline, target, existing, read_existing_source_codes(),
                             allow_csv_planning=True)
    else:
        plan = assemble_plan(baseline, target, existing, read_existing_source_codes())
    if plan.fingerprint != args.expected_fingerprint:
        raise RuntimeError("Plan fingerprint drift")
    links = build_reuse_links(plan, target)
    candidates = (candidate_actions(plan, target, allow_csv=True)
                  if args.allow_csv_preflight_only else candidate_actions(plan, target))
    cache = cache_directory(args.cache_dir)
    from huggingface_hub import hf_hub_download
    paths = {}
    for action in candidates:
        f = Path(hf_hub_download(repo_id=args.repo_id,filename=action.path,
                     repo_type="dataset",revision=revision,cache_dir=str(cache),
                     local_files_only=True))
        # HF snapshot entries may be symlinks to blobs within this cache.
        # Pass the verified blob to validators that reject archive symlinks.
        f = f.resolve(strict=True)
        if not f.is_file() or not f.is_relative_to(cache):
            raise RuntimeError("HF cache path escaped trusted reports directory")
        paths[action.path] = f
    archived = (audit_candidates(plan, target, paths, ROOT,
                                 expected_records=args.expected_records, allow_csv=True)
                if args.allow_csv_preflight_only else
                audit_candidates(plan, target, paths, ROOT,
                                 expected_records=args.expected_records))
    # Writer compatibility is checked BEFORE Snapshot Registration can INSERT.
    # Empty JSON record sets and unsupported parser payloads must not strand an
    # in_progress snapshot that the JSON-only Candidate Writer cannot complete.
    writer_records = verify_writer_v1_compatibility(
        args.repo_id, revision, target, candidates, archived, root=ROOT,
        **({"allow_csv": True} if args.allow_csv_preflight_only else {}),
    )
    if writer_records != args.expected_records:
        raise RuntimeError("Writer Core parsed record count differs from pinned plan")
    existing_versions = read_target_db_state(args.repo_id, revision, archived)
    result = validate_preparation(
        plan, excluded=excluded, links=links, archives=archived,
        existing_versions=existing_versions,
        expected_fingerprint=args.expected_fingerprint,
        expected_reuse=args.expected_reuse,
        expected_candidates=args.expected_files,
        expected_bytes=args.expected_bytes,
        expected_records=args.expected_records,
    )
    print("Repository:", args.repo_id)
    print("Baseline snapshot:", args.baseline_snapshot, "completed")
    print("Target revision:", revision)
    print("Plan fingerprint:", plan.fingerprint)
    print("Full HF manifest file count:", result.total_files)
    print("Target Raw files:", result.raw_files)
    print("Excluded non-Raw files:", excluded)
    print("REUSE file IDs:", result.reused)
    print("Candidate archive objects VERIFIED:", result.candidates)
    print("Archive verified bytes:", result.candidate_bytes)
    print("Candidate parser records:", result.candidate_records)
    if args.allow_csv_preflight_only:
        print("Writer Core compatibility: PASS | candidates:", result.candidates)
    else:
        print("Writer Core V1 JSON compatibility: PASS | candidates:", result.candidates)
    print("Already registered candidate versions:", result.already_registered_versions)
    print("Unregistered candidate versions:", result.candidates - result.already_registered_versions)
    print("Projected snapshot record references:", result.projected_records)
    print("Target snapshot: ABSENT; ID will be assigned by PostgreSQL")
    print("Next SQL step: INSERT in_progress snapshot after explicit approval")
    print("Manifest bytes/archive registration: STILL REQUIRED before completed")
    print("HF file-body downloads: NONE (local_files_only=True)")
    print("Database writes: NONE | Snapshot created: NO | Archive writes: NONE")
    print("M4.5.4c.3i SNAPSHOT PREPARATION PLAN: PASS (NOT APPROVAL TO COMMIT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
