#!/usr/bin/env python3
"""M4.6.3d.2 | Generic, read-only incremental candidate ingestion planner.

Runs AFTER Snapshot Registration and REUSE Population. Requires one explicitly
pinned, in_progress target snapshot and a complete set of verified REUSE links.
Audits cached files, immutable archive bytes, JSON parsing, previously written
candidate versions, and target observation coverage. Safe for partial retries.

Does not register/finalize snapshots, ingest records, download file bodies,
modify archive objects or write PostgreSQL. This PLAN is NOT commit approval.
Requires M4.6.3c.1 populate_incremental_reuse.py to be installed first.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.populate_incremental_reuse import (
    ReuseRequest, inspect_db, verify_plan, read_only_args,
)
from scripts.tools.incremental_writer_core import (
    PreparedJsonVersion, prepare_json_version, prepare_csv_version,
    prepared_file_format, verify_existing_version, check_observation,
)

REPO = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VALID_STATES = frozenset({
    "LINKED_VERIFIED", "VERSION_EXISTS_LINK_PENDING", "READY_INSERT_NEW_VERSION"
})


@dataclass(frozen=True)
class CandidateStatus:
    relative_path: str
    file_id: int | None
    action: str
    record_count: int
    size_bytes: int


def validate_observation_coverage(observations, links, prepared, *, req: ReuseRequest):
    """Check FULL target observations; REUSE complete, candidates may be partial.

    Observations are new six-field rows from M4.6.3c.1 inspect_db():
    (path, file_id, presence, manifest_size, git_blob, lfs_sha256).
    Candidate file_id proof is completed by audit_candidate_db().
    """
    req.validate()
    reg = req.registration
    if not isinstance(prepared, Mapping) or len(prepared) != reg.candidate_files:
        raise RuntimeError("Candidate preparation coverage mismatch")
    if len(links) != reg.reuse_files:
        raise RuntimeError("REUSE count differs from pinned plan")
    reuse = {link.relative_path: link for link in links}
    if (len(reuse) != len(links) or len({link.file_id for link in links}) != len(links)
            or set(reuse).intersection(prepared)
            or len(reuse) + len(prepared) != reg.raw_files):
        raise RuntimeError("Duplicate/incomplete target REUSE/candidate paths")
    seen = set()
    reuse_linked = candidate_linked = 0
    for row in observations:
        if not isinstance(row, (tuple, list)) or len(row) != 6:
            raise RuntimeError("Invalid target observation structure")
        path, file_id, presence, size, blob, lfs = row
        if (path in seen or type(file_id) is not int or file_id <= 0
                or presence != "linked"):
            raise RuntimeError("Duplicate/invalid target observation: " + str(path))
        seen.add(path)
        if path in reuse:
            link = reuse[path]
            if (file_id, size, blob, lfs) != (
                link.file_id, link.size_bytes, link.git_blob_id, link.lfs_sha256
            ):
                raise RuntimeError("REUSE observation identity changed: " + path)
            reuse_linked += 1
        elif path in prepared:
            version = prepared[path]
            if (size, blob, lfs) != (
                version.size_bytes, version.git_blob_id, version.lfs_sha256
            ):
                raise RuntimeError("Candidate observation identity changed: " + path)
            candidate_linked += 1
        else:
            raise RuntimeError("Unexpected observation outside pinned target: " + str(path))
    if reuse_linked != reg.reuse_files:
        raise RuntimeError("REUSE observations not fully linked")
    return reuse_linked, candidate_linked


def audit_candidate_db(cur, *, snapshot_id: int, repo_id: str,
                       prepared: Mapping[str, PreparedJsonVersion]) -> tuple[CandidateStatus, ...]:
    """Only SELECTs; verify immutable version and any existing observation."""
    if type(snapshot_id) is not int or snapshot_id <= 0 or repo_id != REPO:
        raise RuntimeError("Invalid candidate audit target identity")
    statuses = []
    for path in sorted(prepared):
        version = prepared[path]
        if (version.relative_path != path or version.repo_id != repo_id):
            raise RuntimeError("Prepared version path/repo mismatch: " + path)
        cur.execute("""SELECT f.file_id,f.file_format,f.file_size_bytes,f.archive_path,
                              f.archive_state,f.parse_state,f.record_count,s.source_code
                       FROM raw.raw_files f
                       JOIN raw.raw_sources s ON s.source_id=f.source_id
                       WHERE f.repo_id=%s AND f.relative_path=%s
                         AND f.content_sha256=%s""",
                    (repo_id, path, version.content_sha256))
        versions = cur.fetchall()
        if len(versions) > 1:
            raise RuntimeError("Duplicate immutable version: " + path)
        fid = None
        if versions:
            fid, _ = verify_existing_version(versions[0], version)
            cur.execute("SELECT COUNT(*) FROM raw.raw_records WHERE file_id=%s", (fid,))
            if cur.fetchone() != (len(version.records),):
                raise RuntimeError("Physical record count mismatch: " + path)
        cur.execute("""SELECT file_id,presence_status,manifest_size_bytes,
                              git_blob_id,lfs_sha256
                       FROM raw.file_observations
                       WHERE snapshot_id=%s AND repo_id=%s AND relative_path=%s""",
                    (snapshot_id, repo_id, path))
        observations = cur.fetchall()
        if len(observations) > 1:
            raise RuntimeError("Duplicate candidate observation: " + path)
        if observations:
            if fid is None:
                raise RuntimeError("Orphan candidate observation: " + path)
            check_observation(observations[0], file_id=fid, version=version)
            decision = "LINKED_VERIFIED"
        elif fid is not None:
            decision = "VERSION_EXISTS_LINK_PENDING"
        else:
            decision = "READY_INSERT_NEW_VERSION"
        statuses.append(CandidateStatus(path, fid, decision,
                                        len(version.records), version.size_bytes))
    return tuple(statuses)


def validate_candidate_plan(*, req: ReuseRequest, plan, target_snapshot_state,
                            actions, archived, prepared, statuses,
                            linked_reuse: int, linked_candidates: int, excluded: int):
    """Cross-check every identity/count; return projected linked raw record refs."""
    req.validate()
    r = req.registration
    if (not SHA256.fullmatch(r.fingerprint) or plan.fingerprint != r.fingerprint
            or plan.count("REMOVED") or plan.count("REVIEW")
            or plan.target_raw_count != r.raw_files
            or plan.count("UNCHANGED") != r.reuse_files
            or plan.count("NEW") + plan.count("CHANGED") != r.candidate_files
            or plan.baseline_records != req.expected_baseline_records
            or plan.reused_records != req.expected_reused_records
            or plan.candidate_bytes != r.candidate_bytes):
        raise RuntimeError("Pinned candidate plan fingerprint/counts changed")
    state = target_snapshot_state
    if (state.repo_id, state.revision, state.status,
        state.manifest_files, state.raw_files, state.manifest_sha, state.manifest_path) != (
        r.repo_id, r.target_revision, "in_progress", r.manifest_files,
        r.raw_files, None, None
    ):
        raise RuntimeError("Target snapshot identity/status/manifest changed")
    if type(excluded) is not int or excluded < 0 or r.raw_files + excluded != r.manifest_files:
        raise RuntimeError("HF manifest non-Raw count mismatch")
    if (len(actions) != r.candidate_files or len(archived) != r.candidate_files
            or len(prepared) != r.candidate_files or len(statuses) != r.candidate_files):
        raise RuntimeError("Candidate coverage/count drift")
    action_paths = {a.path for a in actions}
    archive_paths = {a.relative_path for a in archived}
    status_paths = {s.relative_path for s in statuses}
    if (len(action_paths) != len(actions) or len(archive_paths) != len(archived)
            or len(status_paths) != len(statuses)
            or set(prepared) != action_paths or set(prepared) != archive_paths
            or set(prepared) != status_paths):
        raise RuntimeError("Candidate paths are missing/duplicated")
    # candidate_actions() is a cache helper; its DTO contract exposes at least
    # path and source_code, but may not expose SyncAction.kind/category.
    pinned_actions = {a.path: a for a in plan.actions
                      if a.category in ("NEW", "CHANGED")}
    action_map = {a.path: a for a in actions}
    if set(pinned_actions) != set(prepared):
        raise RuntimeError("Pinned SyncPlan candidate paths mismatch")
    archive_map = {a.relative_path: a for a in archived}
    status_map = {s.relative_path: s for s in statuses}
    if sum(s.size_bytes for s in statuses) != r.candidate_bytes:
        raise RuntimeError("Candidate byte budget differs from plan")
    if sum(s.record_count for s in statuses) != r.candidate_records:
        raise RuntimeError("Candidate parsed record count differs from plan")
    for path, v in prepared.items():
        cache_action, archived_item, s = action_map[path], archive_map[path], status_map[path]
        a = pinned_actions[path]
        if (a.category not in ("NEW", "CHANGED")
                or a.kind != "UPSERT_VERSION_AFTER_REHASH"
                or a.file_format != prepared_file_format(v) or a.source_code != v.source_code
                or cache_action.source_code != v.source_code
                or a.expected_bytes != v.size_bytes
                or a.git_blob_id != v.git_blob_id or a.lfs_sha256 != v.lfs_sha256
                or archived_item.action != "ALREADY_ARCHIVED"
                or archived_item.sha256 != v.content_sha256
                or archived_item.byte_count != v.size_bytes
                or archived_item.record_count != len(v.records)
                or archived_item.archive_relative_path != v.archive_relative_path
                or v.repo_id != r.repo_id or v.relative_path != path
                or v.pinned_revision != r.target_revision
                or s.action not in VALID_STATES
                or s.record_count != len(v.records) or s.size_bytes != v.size_bytes
                or (s.action == "READY_INSERT_NEW_VERSION" and s.file_id is not None)
                or (s.action in ("LINKED_VERIFIED", "VERSION_EXISTS_LINK_PENDING")
                    and (type(s.file_id) is not int or s.file_id <= 0))):
            raise RuntimeError("Candidate content/DB audit conflict: " + path)
    if linked_reuse != r.reuse_files:
        raise RuntimeError("REUSE coverage incomplete")
    if linked_candidates != sum(s.action == "LINKED_VERIFIED" for s in statuses):
        raise RuntimeError("Target observations disagree with candidate DB audit")
    # An empty parser result is NOT supported by incremental_writer_core V1.
    if any(len(v.records) == 0 for v in prepared.values()):
        raise RuntimeError("Empty JSON records require Writer Core policy update")
    return req.expected_reused_records + r.candidate_records


def build_request(args: argparse.Namespace) -> ReuseRequest:
    r = Registration(args.repo_id, args.baseline_snapshot, args.target_revision,
                     args.expected_fingerprint, args.expected_baseline_files,
                     args.expected_full_files, args.expected_raw_files,
                     args.expected_reuse, args.expected_files,
                     args.expected_bytes, args.expected_records)
    req = ReuseRequest(r, args.target_snapshot_id,
                       args.expected_baseline_records, args.expected_reused_records)
    req.validate()
    return req


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-snapshot", type=int, required=True)
    ap.add_argument("--target-snapshot-id", type=int, required=True)
    ap.add_argument("--target-revision", required=True)
    ap.add_argument("--expected-fingerprint", required=True)
    ap.add_argument("--repo-id", default=REPO)
    ap.add_argument("--expected-baseline-files", type=int, required=True)
    ap.add_argument("--expected-full-files", type=int, required=True)
    ap.add_argument("--expected-raw-files", type=int, required=True)
    ap.add_argument("--expected-reuse", type=int, required=True)
    ap.add_argument("--expected-files", type=int, required=True)
    ap.add_argument("--expected-bytes", type=int, required=True)
    ap.add_argument("--expected-records", type=int, required=True)
    ap.add_argument("--expected-baseline-records", type=int, required=True)
    ap.add_argument("--expected-reused-records", type=int, required=True)
    ap.add_argument("--cache-dir", default="reports/m4_5_content_cache")
    ap.add_argument("--plan", action="store_true", required=True)
    ap.add_argument("--allow-csv-candidate-plan-only", action="store_true",
                    help="Read-only candidate audit for JSON+CSV; no write authorization")
    return ap


def main() -> int:
    args = make_parser().parse_args()
    req = build_request(args)
    r = req.registration
    print("=== M4.6.3d.2 | GENERIC CANDIDATE INGESTION PLAN ===", flush=True)
    print("Mode: READ-ONLY | target snapshot:", req.target_snapshot_id, flush=True)
    from huggingface_hub import hf_hub_download
    import psycopg
    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.incremental_cache_gate import candidate_actions, cache_directory
    from scripts.tools.incremental_archive_promotion import audit_candidates
    from scripts.tools.raw_sync_preflight import read_existing_source_codes

    base_revision, baseline = load_baseline(r.baseline_snapshot, r.repo_id)
    target_revision, target, excluded = load_target(r.repo_id, r.target_revision)
    if (target_revision != r.target_revision or base_revision == r.target_revision
            or len(target) != r.raw_files or len(target) + excluded != r.manifest_files):
        raise RuntimeError("Pinned baseline/target HF metadata drift")
    source_codes = read_existing_source_codes()
    cache = cache_directory(args.cache_dir)
    with psycopg.connect(**read_only_args()) as conn:
        with conn.cursor() as cur:
            db_rev, existing, state = inspect_db(cur, req)
            if db_rev != base_revision:
                raise RuntimeError("Baseline revision changed between DB reads")
            plan, links = (verify_plan(req, baseline, target, excluded,
                                       existing, source_codes, allow_csv_planning=True)
                           if args.allow_csv_candidate_plan_only else
                           verify_plan(req, baseline, target, excluded, existing, source_codes))
            actions = tuple(candidate_actions(plan, target, allow_csv=True)
                            if args.allow_csv_candidate_plan_only else
                            candidate_actions(plan, target))
            if len(actions) != r.candidate_files:
                raise RuntimeError("Candidate action count changed")
            cached = {}
            for action in actions:
                fp = Path(hf_hub_download(repo_id=r.repo_id,
                        filename=action.path, repo_type="dataset", revision=r.target_revision,
                        cache_dir=str(cache), local_files_only=True))
                fp = fp.resolve(strict=True)
                if not fp.is_file() or not fp.is_relative_to(cache):
                    raise RuntimeError("Cached HF file outside trusted cache directory")
                cached[action.path] = fp
            archived = tuple(audit_candidates(
                plan, target, cached, ROOT, expected_records=r.candidate_records,
                allow_csv=True
            ) if args.allow_csv_candidate_plan_only else audit_candidates(
                plan, target, cached, ROOT, expected_records=r.candidate_records
            ))
            archives_by_path = {x.relative_path: x for x in archived}
            if len(archives_by_path) != len(archived):
                raise RuntimeError("Duplicate archived candidate path")
            prepared = {}
            for action in actions:
                item = archives_by_path.get(action.path)
                if item is None:
                    raise RuntimeError("Missing archived candidate: " + action.path)
                if args.allow_csv_candidate_plan_only and action.file_format == "csv":
                    parser = prepare_csv_version
                else:
                    parser = prepare_json_version
                v = parser(
                    r.repo_id, action.path, r.target_revision, action.source_code,
                    ROOT / item.archive_relative_path, target[action.path], root=ROOT
                )
                prepared[action.path] = v
            reused, candidates = validate_observation_coverage(
                state.observations, links, prepared, req=req
            )
            statuses = audit_candidate_db(
                cur, snapshot_id=req.target_snapshot_id, repo_id=r.repo_id,
                prepared=prepared
            )
            projected = validate_candidate_plan(
                req=req, plan=plan, target_snapshot_state=state,
                actions=actions, archived=archived, prepared=prepared,
                statuses=statuses, linked_reuse=reused,
                linked_candidates=candidates, excluded=excluded
            )
    print("Repository:", r.repo_id)
    print("Baseline:", r.baseline_snapshot, "completed")
    print("Target snapshot:", req.target_snapshot_id, "in_progress")
    print("Target revision:", r.target_revision)
    print("Plan fingerprint:", r.fingerprint)
    print("Full manifest / Raw:", r.manifest_files, r.raw_files)
    print("REUSE observations:", reused)
    print("Candidate observations:", candidates, "/", r.candidate_files)
    print("Candidate bytes:", r.candidate_bytes, "| records:", r.candidate_records)
    for status in statuses:
        print(f"[{status.action:28}] {status.record_count:>5} records | "
              f"{status.size_bytes:>10} bytes | {status.relative_path}")
    print("Projected snapshot record references:", projected)
    print("CSV read-only candidate audit:", "OPT-IN" if args.allow_csv_candidate_plan_only else "DISABLED")
    print("Database writes: NONE | Archive writes: NONE | HF body downloads: NONE")
    print("M4.6.3d.2 CANDIDATE INGESTION PLAN: PASS (NOT APPROVAL TO COMMIT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
