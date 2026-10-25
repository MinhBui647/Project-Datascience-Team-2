#!/usr/bin/env python3
"""M4.6.3d.3 | Guarded incremental JSON/CSV candidate ingestion coordinator.

Requires an ALREADY REGISTERED, pinned in_progress target snapshot and completed
REUSE linking. --plan delegates to the read-only M4.6.3d.2 planner. Only an
explicit --apply --confirm-candidate-write can write JSON. CSV additionally requires
--allow-csv-candidate-write. Each candidate is written in one transaction
at a time. Never creates snapshots, downloads HF file bodies, modifies immutable
archives, registers manifests, or finalizes snapshots.

Run from the project root, using the same expected-* flags as the planner.
No default snapshot/revision/fingerprint exists. A failed transaction for a
candidate rolls back; successful previous candidate transactions remain and
may be audited on retry.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.plan_incremental_candidates import (
    build_request, validate_observation_coverage, audit_candidate_db,
    validate_candidate_plan,
)
from scripts.tools.populate_incremental_reuse import (
    ReuseRequest, inspect_db, verify_plan, read_only_args,
)
from scripts.tools.register_incremental_snapshot import db_parameters, ReadOnlyPreflightFailure

PLAN_MARKER = "M4.6.3d.2 CANDIDATE INGESTION PLAN: PASS (NOT APPROVAL TO COMMIT)"


@dataclass(frozen=True)
class CandidateBundle:
    request: ReuseRequest
    baseline_revision: str
    baseline: Mapping
    target: Mapping
    source_codes: frozenset[str]
    plan: object
    prepared: Mapping
    links: tuple
    actions: tuple
    archived: tuple
    excluded: int
    allow_csv: bool = False

    def __post_init__(self):
        if type(self.allow_csv) is not bool:
            raise ValueError("Invalid CSV bundle authorization state")


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-snapshot-id", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--repo-id", default="Toibinguz/epl-premier-league-dataset")
    p.add_argument("--expected-baseline-files", type=int, required=True)
    p.add_argument("--expected-full-files", type=int, required=True)
    p.add_argument("--expected-raw-files", type=int, required=True)
    p.add_argument("--expected-reuse", type=int, required=True)
    p.add_argument("--expected-files", type=int, required=True)
    p.add_argument("--expected-bytes", type=int, required=True)
    p.add_argument("--expected-records", type=int, required=True)
    p.add_argument("--expected-baseline-records", type=int, required=True)
    p.add_argument("--expected-reused-records", type=int, required=True)
    p.add_argument("--cache-dir", default="reports/m4_5_content_cache")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-candidate-write", action="store_true")
    p.add_argument("--allow-csv-candidate-plan-only", action="store_true",
                   help="Only permitted with --plan; read-only CSV candidate audit")
    p.add_argument("--allow-csv-candidate-write", action="store_true",
                   help="Only with --apply --confirm-candidate-write for already registered snapshot")
    return p


def check_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if bool(args.apply) != bool(args.confirm_candidate_write):
        parser.error("--apply requires --confirm-candidate-write; --plan forbids confirmation")
    if getattr(args, "allow_csv_candidate_plan_only", False) and not args.plan:
        parser.error("CSV plan-only flag cannot authorize --apply")
    if getattr(args, "allow_csv_candidate_write", False) and not (
        args.apply and args.confirm_candidate_write
        and not getattr(args, "allow_csv_candidate_plan_only", False)
    ):
        parser.error("CSV writing needs --apply --confirm-candidate-write and the CSV write opt-in")


def planner_command(req: ReuseRequest, cache_dir: str, *,
                    allow_csv_candidate_plan_only: bool = False) -> list[str]:
    req.validate()
    r = req.registration
    if type(allow_csv_candidate_plan_only) is not bool:
        raise ValueError("Invalid CSV candidate planning switch")
    return [
        sys.executable, str(ROOT / "scripts/tools/plan_incremental_candidates.py"),
        "--baseline-snapshot", str(r.baseline_snapshot),
        "--target-snapshot-id", str(req.target_snapshot_id),
        "--target-revision", r.target_revision,
        "--expected-fingerprint", r.fingerprint,
        "--repo-id", r.repo_id,
        "--expected-baseline-files", str(r.baseline_files),
        "--expected-full-files", str(r.manifest_files),
        "--expected-raw-files", str(r.raw_files),
        "--expected-reuse", str(r.reuse_files),
        "--expected-files", str(r.candidate_files),
        "--expected-bytes", str(r.candidate_bytes),
        "--expected-records", str(r.candidate_records),
        "--expected-baseline-records", str(req.expected_baseline_records),
        "--expected-reused-records", str(req.expected_reused_records),
        "--cache-dir", cache_dir,
        *(["--allow-csv-candidate-plan-only"] if allow_csv_candidate_plan_only else []),
        "--plan",
    ]


def run_existing_plan(req: ReuseRequest, cache_dir: str, *,
                      allow_csv_candidate_plan_only: bool = False) -> None:
    """Required read-only gate for both --plan and --apply."""
    result = subprocess.run(
        (planner_command(req, cache_dir, allow_csv_candidate_plan_only=True)
         if allow_csv_candidate_plan_only else planner_command(req, cache_dir)),
        cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    if result.returncode != 0:
        raise ReadOnlyPreflightFailure("Read-only candidate planner", result.returncode)
    if PLAN_MARKER not in result.stdout.splitlines():
        raise RuntimeError("Read-only candidate planner failed; refusing candidate writes")


def build_verified_bundle(req: ReuseRequest, cache_dir: str, *,
                          allow_csv: bool = False) -> CandidateBundle:
    """Independently rebuild pinned plan and prepare verified immutable JSON/CSV.

    No database writes or HF file-body downloads. A full independent recheck is
    required even after the subprocess planner passed.
    """
    req.validate()
    if type(allow_csv) is not bool:
        raise ValueError("Invalid guarded CSV write switch")
    r = req.registration
    import psycopg
    from huggingface_hub import hf_hub_download
    from scripts.tools.check_raw_updates import load_baseline, load_target
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    from scripts.tools.incremental_cache_gate import cache_directory, candidate_actions
    from scripts.tools.incremental_archive_promotion import audit_candidates
    from scripts.tools.incremental_writer_core import prepare_json_version, prepare_csv_version

    baseline_revision, baseline = load_baseline(r.baseline_snapshot, r.repo_id)
    target_revision, target, excluded = load_target(r.repo_id, r.target_revision)
    if (target_revision != r.target_revision or baseline_revision == target_revision
            or len(target) != r.raw_files or len(target) + excluded != r.manifest_files):
        raise RuntimeError("Pinned HF/baseline metadata changed")
    sources = frozenset(read_existing_source_codes())
    cache = cache_directory(cache_dir)
    with psycopg.connect(**read_only_args()) as conn:
        with conn.cursor() as cur:
            db_rev, existing, state = inspect_db(cur, req)
            if db_rev != baseline_revision:
                raise RuntimeError("Baseline revision drift between DB reads")
            plan, links = (verify_plan(req, baseline, target, excluded,
                                       existing, sources, allow_csv_planning=True)
                           if allow_csv else
                           verify_plan(req, baseline, target, excluded, existing, sources))
            actions = tuple(candidate_actions(plan, target, allow_csv=True)
                            if allow_csv else candidate_actions(plan, target))
            if len(actions) != r.candidate_files:
                raise RuntimeError("Candidate action count drift")
            cached = {}
            for action in actions:
                fp = Path(hf_hub_download(
                    repo_id=r.repo_id, filename=action.path, repo_type="dataset",
                    revision=r.target_revision, cache_dir=str(cache),
                    local_files_only=True,
                ))
                fp = fp.resolve(strict=True)
                if not fp.is_file() or not fp.is_relative_to(cache):
                    raise RuntimeError("Candidate cache path escaped trusted cache directory")
                cached[action.path] = fp
            archived = tuple(audit_candidates(
                plan, target, cached, ROOT, expected_records=r.candidate_records,
                allow_csv=True,
            ) if allow_csv else audit_candidates(
                plan, target, cached, ROOT, expected_records=r.candidate_records
            ))
            archive_by_path = {a.relative_path: a for a in archived}
            if len(archive_by_path) != len(archived):
                raise RuntimeError("Duplicate candidate archive path")
            prepared = {}
            for action in actions:
                item = archive_by_path.get(action.path)
                if item is None or action.path in prepared:
                    raise RuntimeError("Candidate archive coverage conflict: " + action.path)
                if allow_csv and action.file_format == "csv":
                    prepare = prepare_csv_version
                elif action.file_format == "json":
                    prepare = prepare_json_version
                else:
                    raise RuntimeError("Unsupported candidate file format: " + action.path)
                prepared[action.path] = prepare(
                    r.repo_id, action.path, r.target_revision, action.source_code,
                    ROOT / item.archive_relative_path, target[action.path], root=ROOT,
                )
            reused, candidates = validate_observation_coverage(
                state.observations, links, prepared, req=req
            )
            statuses = audit_candidate_db(
                cur, snapshot_id=req.target_snapshot_id,
                repo_id=r.repo_id, prepared=prepared,
            )
            validate_candidate_plan(
                req=req, plan=plan, target_snapshot_state=state,
                actions=actions, archived=archived, prepared=prepared,
                statuses=statuses, linked_reuse=reused,
                linked_candidates=candidates, excluded=excluded,
            )
    return CandidateBundle(req, baseline_revision, baseline, target, sources, plan, prepared,
                           tuple(links), actions, archived, excluded, allow_csv=allow_csv)


def audit_state(cur, bundle: CandidateBundle):
    """Read-only SELECT audit (no locks); caller controls transaction/locks."""
    req = bundle.request
    r = req.registration
    db_rev, existing, state = inspect_db(cur, req)
    if (db_rev != bundle.baseline_revision or
            not bundle.baseline or not isinstance(bundle.source_codes, frozenset)):
        raise RuntimeError("Baseline/plan context invalid")
    plan, links = (verify_plan(req, bundle.baseline, bundle.target,
                               bundle.excluded, existing, bundle.source_codes,
                               allow_csv_planning=True) if bundle.allow_csv else
                   verify_plan(req, bundle.baseline, bundle.target,
                               bundle.excluded, existing, bundle.source_codes))
    if plan != bundle.plan or tuple(links) != bundle.links:
        raise RuntimeError("Baseline/plan drift after preparation")
    reused, candidates = validate_observation_coverage(
        state.observations, bundle.links, bundle.prepared, req=req
    )
    statuses = audit_candidate_db(
        cur, snapshot_id=req.target_snapshot_id,
        repo_id=r.repo_id, prepared=bundle.prepared,
    )
    validate_candidate_plan(
        req=req, plan=bundle.plan, target_snapshot_state=state,
        actions=bundle.actions, archived=bundle.archived, prepared=bundle.prepared,
        statuses=statuses, linked_reuse=reused,
        linked_candidates=candidates, excluded=bundle.excluded,
    )
    return {s.relative_path: s for s in statuses}


def audit_locked_state(cur, bundle: CandidateBundle):
    """Validate snapshot after row lock within EACH candidate transaction."""
    req = bundle.request
    r = req.registration
    cur.execute("""SELECT repo_id,hf_revision,status,manifest_file_count,
                          raw_scope_file_count,manifest_sha256,manifest_archive_path
                   FROM raw.snapshot_runs WHERE snapshot_id=%s FOR UPDATE""",
                (req.target_snapshot_id,))
    expected = (r.repo_id, r.target_revision, "in_progress", r.manifest_files,
                r.raw_files, None, None)
    if cur.fetchone() != expected:
        raise RuntimeError("Target snapshot identity/status/manifest changed")
    return audit_state(cur, bundle)


def ingest_single(path: str, bundle: CandidateBundle) -> tuple[str, int]:
    """One candidate per DB transaction; fail closed before/after writing."""
    req = bundle.request
    r = req.registration
    if path not in bundle.prepared:
        raise RuntimeError("Candidate path not present in pinned bundle")
    from scripts.tools.incremental_writer_core import write_prepared_version
    import psycopg

    params = db_parameters()
    params["application_name"] = "m46_generic_candidate_writer"
    with psycopg.connect(**params) as conn:
        if conn.autocommit:
            raise RuntimeError("Autocommit must be disabled")
        with conn.cursor() as cur:
            # Lock order: repository, candidate path, target snapshot row.
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (r.repo_id,))
            if cur.fetchone() != (True,):
                raise RuntimeError("Another incremental registrar/writer holds repository lock")
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (r.repo_id + ":" + path,))
            if cur.fetchone() != (True,):
                raise RuntimeError("Another candidate writer holds path lock")
            statuses = audit_locked_state(cur, bundle)
            status = statuses[path]
            if status.action == "LINKED_VERIFIED":
                return "ALREADY_LINKED", 0
            if status.action not in ("READY_INSERT_NEW_VERSION", "VERSION_EXISTS_LINK_PENDING"):
                raise RuntimeError("Unexpected candidate state: " + status.action)
            action, file_id, rows = write_prepared_version(
                cur, req.target_snapshot_id, bundle.prepared[path], root=ROOT,
            )
            expected_action = ("INSERTED_NEW" if status.action == "READY_INSERT_NEW_VERSION"
                               else "REUSED_EXISTING")
            expected_rows = (len(bundle.prepared[path].records) if expected_action == "INSERTED_NEW" else 0)
            if action != expected_action or rows != expected_rows:
                raise RuntimeError("Writer outcome disagrees with prewrite DB audit")
            updated = audit_locked_state(cur, bundle)
            if updated[path].action != "LINKED_VERIFIED" or updated[path].file_id != file_id:
                raise RuntimeError("Candidate postwrite audit failed; rolling back")
        # Successful context exit commits this candidate's verified transaction.
    return action, rows


def final_read_only_audit(bundle: CandidateBundle) -> tuple[int, int]:
    """Independent read-only completion check; does NOT finalize snapshot."""
    import psycopg
    with psycopg.connect(**read_only_args()) as conn:
        with conn.cursor() as cur:
            statuses = audit_state(cur, bundle)
            if len(statuses) != bundle.request.registration.candidate_files or any(
                    s.action != "LINKED_VERIFIED" for s in statuses.values()):
                raise RuntimeError("Candidate observations incomplete after ingestion")
    r = bundle.request.registration
    return r.reuse_files, r.candidate_files


def setup_error_log(snapshot_id: int) -> logging.Logger:
    """Only used in --apply; no log files created by --plan."""
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    logger_name = f"m46_candidate_{snapshot_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    log_path = report_dir / f"m46_candidate_{snapshot_id}_errors.log"
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    check_mode(args, parser)
    req = build_request(args)
    print("=== M4.6.3d.3 | GENERIC CANDIDATE COORDINATOR ===", flush=True)
    print("Baseline:", req.registration.baseline_snapshot,
          "| Target snapshot:", req.target_snapshot_id, flush=True)
    if args.allow_csv_candidate_plan_only or args.allow_csv_candidate_write:
        run_existing_plan(req, args.cache_dir, allow_csv_candidate_plan_only=True)
    else:
        run_existing_plan(req, args.cache_dir)
    if args.plan:
        print("COORDINATOR PLAN: PASS | Database writes: NONE | Snapshot unchanged")
        return 0

    bundle = (build_verified_bundle(req, args.cache_dir, allow_csv=True)
              if args.allow_csv_candidate_write else build_verified_bundle(req, args.cache_dir))
    logger = setup_error_log(req.target_snapshot_id)
    new_versions = linked_versions = already_linked = inserted_records = 0
    for path in sorted(bundle.prepared):
        try:
            action, count = ingest_single(path, bundle)
        except Exception:
            logger.exception("Candidate failure: snapshot=%s path=%s", req.target_snapshot_id, path)
            print("ERROR: candidate transaction rolled back: " + path, file=sys.stderr)
            raise
        if action == "INSERTED_NEW":
            new_versions += 1
        elif action == "REUSED_EXISTING":
            linked_versions += 1
        elif action == "ALREADY_LINKED":
            already_linked += 1
        else:
            raise RuntimeError("Unexpected candidate action: " + action)
        inserted_records += count
        print(f"[{action:15}] records inserted={count:5} | {path}", flush=True)
    reused, candidates = final_read_only_audit(bundle)
    print(f"DONE: new_versions={new_versions} reused_versions={linked_versions} "
          f"already_linked={already_linked} raw_records_inserted={inserted_records}")
    print(f"Final observations: {reused} REUSE + {candidates} candidates = {reused+candidates}")
    print("Snapshot remains in_progress | No manifest registration or finalization")
    print("M4.6.3d.3 CANDIDATE INGESTION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
