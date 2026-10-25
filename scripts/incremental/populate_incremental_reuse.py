#!/usr/bin/env python3
"""M4.6.3c.1: generic, guarded REUSE observation coordinator.

An existing, pinned in_progress snapshot is REQUIRED. Plan reads DB/HF metadata;
--link --confirm-reuse-only inserts ONLY raw.file_observations (no Raw records,
archive writes, snapshots, or finalization). Inputs are explicit immutable
execution expectations. Neither mode creates/resumes a snapshot automatically.

Important: this tool must run BEFORE candidate observations are inserted;
partial pre-existing REUSE links are permitted and checked for idempotence.
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

from scripts.tools.check_raw_updates import load_baseline, load_target
from scripts.tools.incremental_sync import assemble_plan
from scripts.tools.incremental_reuse_linker import build_reuse_links, link_reused_versions
from scripts.tools.raw_sync_write_plan import ExistingFile
from scripts.tools.raw_sync_preflight import read_existing_source_codes
from scripts.tools.register_incremental_snapshot import Registration, db_parameters, SHA40

DB = "epl_raw_storage_v2"
REPO = "Toibinguz/epl-premier-league-dataset"
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True)
class ReuseRequest:
    registration: Registration
    target_snapshot_id: int
    expected_baseline_records: int
    expected_reused_records: int

    def validate(self):
        self.registration.validate()
        if type(self.target_snapshot_id) is not int or self.target_snapshot_id <= 0:
            raise RuntimeError("Target snapshot ID must be a positive integer")
        if self.target_snapshot_id == self.registration.baseline_snapshot:
            raise RuntimeError("Target snapshot ID equals baseline snapshot ID")
        for field in ("expected_baseline_records", "expected_reused_records"):
            v = getattr(self, field)
            if type(v) is not int or v < 0:
                raise RuntimeError(f"{field} must be a nonnegative integer")
        if self.expected_reused_records > self.expected_baseline_records:
            raise RuntimeError("REUSE records exceed baseline records")
        if self.registration.reuse_files == 0 and self.expected_reused_records != 0:
            raise RuntimeError("Zero REUSE file count requires zero REUSE records")


@dataclass(frozen=True)
class TargetState:
    repo_id: str
    revision: str
    status: str
    manifest_files: int
    raw_files: int
    manifest_sha: str | None
    manifest_path: str | None
    observations: tuple[tuple[str, int | None, str, int | None, str | None, str | None], ...]


def validate_target_state(state: TargetState, links, req: ReuseRequest):
    """Fail closed on completed target, candidate obs or identity drift."""
    req.validate()
    r = req.registration
    if not isinstance(state, TargetState):
        raise RuntimeError("Invalid target snapshot state")
    expected = (r.repo_id, r.target_revision, "in_progress", r.manifest_files, r.raw_files)
    actual = (state.repo_id, state.revision, state.status, state.manifest_files, state.raw_files)
    if actual != expected or state.manifest_sha is not None or state.manifest_path is not None:
        raise RuntimeError("Target snapshot state differs from pinned pre-candidate REUSE phase")
    if len(links) != r.reuse_files:
        raise RuntimeError("REUSE link count differs from pinned plan")
    identity = {x.relative_path: x for x in links}
    if len(identity) != len(links) or len({x.file_id for x in links}) != len(links):
        raise RuntimeError("Duplicate REUSE path/file ID")
    seen = set()
    for path, fid, presence, size, blob, lfs in state.observations:
        link = identity.get(path)
        if (path in seen or link is None or
                (fid, presence, size, blob, lfs) !=
                (link.file_id, "linked", link.size_bytes, link.git_blob_id, link.lfs_sha256)):
            raise RuntimeError("Unexpected or conflicting target observation: " + str(path))
        seen.add(path)
    return len(links) - len(seen), len(seen)


def inspect_db(cur, req: ReuseRequest):
    """Read baseline+target in one caller-owned transaction; caller locks target first for writes."""
    req.validate()
    r = req.registration
    cur.execute("SELECT current_database()")
    if cur.fetchone() != (DB,):
        raise RuntimeError("Wrong PostgreSQL database")
    cur.execute("""SELECT repo_id,hf_revision,status,raw_scope_file_count,
                          manifest_sha256,manifest_archive_path
                   FROM raw.snapshot_runs WHERE snapshot_id=%s""", (r.baseline_snapshot,))
    b = cur.fetchone()
    if (b is None or b[0] != r.repo_id or b[2] != "completed" or
            not isinstance(b[1], str) or not SHA40.fullmatch(b[1]) or
            b[3] != r.baseline_files or not isinstance(b[4], str) or
            not SHA256.fullmatch(b[4]) or
            b[5] != f"raw-archive-v2/manifests/{b[4][:2]}/{b[4]}.json"):
        raise RuntimeError("Baseline snapshot missing, incomplete or manifest inconsistent")
    baseline_revision = b[1]
    if baseline_revision == r.target_revision:
        raise RuntimeError("Target revision equals baseline revision")
    cur.execute("""SELECT snapshot_id FROM raw.snapshot_runs
                   WHERE repo_id=%s AND status='completed'
                   ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1""", (r.repo_id,))
    if cur.fetchone() != (r.baseline_snapshot,):
        raise RuntimeError("Baseline no longer latest completed snapshot")

    cur.execute("""SELECT o.relative_path,o.presence_status,f.file_id,
                          f.content_sha256,f.file_size_bytes,f.record_count,
                          f.archive_state,f.parse_state
                   FROM raw.file_observations o
                   LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                   WHERE o.snapshot_id=%s AND o.repo_id=%s""",
                (r.baseline_snapshot, r.repo_id))
    existing = {}
    file_ids = set()
    record_total = 0
    for path, observed, fid, sha, size, records, archive, parse in cur.fetchall():
        if (path in existing or type(fid) is not int or fid <= 0 or fid in file_ids or
                not isinstance(sha, str) or not SHA256.fullmatch(sha) or
                observed != "linked" or archive != "verified" or parse != "success" or
                type(size) is not int or size < 0 or
                type(records) is not int or records < 0):
            raise RuntimeError("Invalid/duplicate baseline observation: " + str(path))
        file_ids.add(fid)
        existing[path] = ExistingFile(fid, sha, size, records, archive, parse, observed)
        record_total += records
    if len(existing) != r.baseline_files or record_total != req.expected_baseline_records:
        raise RuntimeError("Baseline file/record counts changed")
    cur.execute("""SELECT repo_id,hf_revision,status,manifest_file_count,
                          raw_scope_file_count,manifest_sha256,manifest_archive_path
                   FROM raw.snapshot_runs WHERE snapshot_id=%s""", (req.target_snapshot_id,))
    target_row = cur.fetchone()
    if target_row is None:
        raise RuntimeError("Target snapshot ID not found; registration required")
    cur.execute("""SELECT relative_path,file_id,presence_status,
                          manifest_size_bytes,git_blob_id,lfs_sha256
                   FROM raw.file_observations
                   WHERE snapshot_id=%s AND repo_id=%s""",
                (req.target_snapshot_id, r.repo_id))
    obs = tuple(cur.fetchall())
    return baseline_revision, existing, TargetState(*target_row, obs)


def verify_plan(req: ReuseRequest, baseline, target, excluded, existing, source_codes, *,
                allow_csv_planning: bool = False):
    """Reconstruct the SAME fingerprint and expected counts used at registration."""
    req.validate()
    r = req.registration
    if type(allow_csv_planning) is not bool:
        raise ValueError("Invalid CSV planning switch")
    plan = (assemble_plan(baseline, target, existing, source_codes,
                          allow_csv_planning=True) if allow_csv_planning
            else assemble_plan(baseline, target, existing, source_codes))
    if (type(excluded) is not int or excluded < 0 or
            plan.fingerprint != r.fingerprint or
            plan.target_raw_count != r.raw_files or
            len(target) + excluded != r.manifest_files or
            plan.count("UNCHANGED") != r.reuse_files or
            plan.count("NEW") + plan.count("CHANGED") != r.candidate_files or
            plan.count("REMOVED") or plan.count("REVIEW") or
            plan.candidate_bytes != r.candidate_bytes or
            plan.baseline_records != req.expected_baseline_records or
            plan.reused_records != req.expected_reused_records):
        raise RuntimeError("Pinned REUSE plan/fingerprint/counts changed")
    links = build_reuse_links(plan, target)
    if len(links) != r.reuse_files:
        raise RuntimeError("REUSE builder count drift")
    return plan, links


def read_only_args():
    args = db_parameters()
    args["options"] += " -c default_transaction_read_only=on"
    args["application_name"] = "m46_reuse_plan"
    return args


def print_plan(req, state, plan, pending, done):
    r = req.registration
    print("=== M4.6.3c.1 | INCREMENTAL REUSE POPULATION ===")
    print("Baseline:", r.baseline_snapshot, "completed")
    print("Target snapshot:", req.target_snapshot_id, state.status)
    print("Target revision:", r.target_revision)
    print("Plan fingerprint:", plan.fingerprint)
    print("Full manifest / Raw:", state.manifest_files, state.raw_files)
    print("REUSE eligible:", r.reuse_files)
    print("REUSE already linked:", done)
    print("REUSE pending:", pending)
    print("Candidate files (not touched):", r.candidate_files)
    print("Reused raw record references:", plan.reused_records)
    print("Raw versions / raw records inserted: 0 / 0")


def check_csv_reuse_mode(*, allow_csv: bool, plan: bool) -> None:
    """CSV REUSE preview cannot authorize linking observations."""
    if type(allow_csv) is not bool or type(plan) is not bool:
        raise ValueError("Invalid CSV REUSE mode")
    if allow_csv and not plan:
        raise ValueError("CSV REUSE preview is read-only; --link remains blocked")


def main() -> int:
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
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--link", action="store_true")
    ap.add_argument("--confirm-reuse-only", action="store_true")
    ap.add_argument("--allow-csv-reuse-plan-only", action="store_true",
                    help="CSV REUSE SyncPlan preview only; cannot --link")
    ap.add_argument("--allow-csv-reuse-link", action="store_true",
                    help="Explicit CSV REUSE link opt-in; requires --link --confirm-reuse-only")
    a = ap.parse_args()
    if (a.link and not a.confirm_reuse_only) or (a.plan and a.confirm_reuse_only):
        ap.error("--link requires --confirm-reuse-only (not valid with --plan)")
    if a.allow_csv_reuse_link and (not a.link or not a.confirm_reuse_only
                                   or a.allow_csv_reuse_plan_only):
        ap.error("CSV REUSE link requires --link --confirm-reuse-only and forbids CSV plan-only")
    try:
        check_csv_reuse_mode(allow_csv=a.allow_csv_reuse_plan_only, plan=a.plan)
    except ValueError as exc:
        ap.error(str(exc))
    reg = Registration(a.repo_id, a.baseline_snapshot, a.target_revision,
                       a.expected_fingerprint, a.expected_baseline_files,
                       a.expected_full_files, a.expected_raw_files, a.expected_reuse,
                       a.expected_files, a.expected_bytes, a.expected_records)
    req = ReuseRequest(reg, a.target_snapshot_id, a.expected_baseline_records,
                       a.expected_reused_records)
    req.validate()

    import psycopg
    # Only the pinned target's HF metadata is requested; never a floating main ref.
    baseline_revision, baseline = load_baseline(reg.baseline_snapshot, reg.repo_id)
    remote_rev, target, excluded = load_target(reg.repo_id, reg.target_revision)
    if remote_rev != reg.target_revision or baseline_revision == reg.target_revision:
        raise RuntimeError("HF pinned baseline/target revision changed")
    sources = read_existing_source_codes()
    if a.plan:
        with psycopg.connect(**read_only_args()) as conn:
            with conn.cursor() as cur:
                db_rev, existing, state = inspect_db(cur, req)
                if db_rev != baseline_revision:
                    raise RuntimeError("Baseline revision drift between PostgreSQL reads")
                plan, links = (verify_plan(req, baseline, target, excluded, existing, sources,
                                           allow_csv_planning=True)
                               if a.allow_csv_reuse_plan_only else
                               verify_plan(req, baseline, target, excluded, existing, sources))
                pending, done = validate_target_state(state, links, req)
                print_plan(req, state, plan, pending, done)
        print("Database writes: NONE | Snapshot created: NO | Raw records inserted: 0")
        print("M4.6.3c.1 REUSE PLAN: PASS (NOT APPROVAL TO COMMIT)")
        return 0

    params = db_parameters()
    params["application_name"] = "m46_reuse_commit"
    with psycopg.connect(**params) as conn:
        if conn.autocommit:
            raise RuntimeError("Autocommit unsafe")
        with conn.cursor() as cur:
            cur.execute("""SELECT snapshot_id FROM raw.snapshot_runs
                           WHERE snapshot_id=%s AND repo_id=%s FOR UPDATE""",
                        (req.target_snapshot_id, reg.repo_id))
            if cur.fetchone() != (req.target_snapshot_id,):
                raise RuntimeError("Target row lock failed")
            db_rev, existing, state = inspect_db(cur, req)
            if db_rev != baseline_revision:
                raise RuntimeError("Baseline revision drift between PostgreSQL reads")
            plan, links = (verify_plan(req, baseline, target, excluded, existing, sources,
                                       allow_csv_planning=True) if a.allow_csv_reuse_link else
                           verify_plan(req, baseline, target, excluded, existing, sources))
            pending, done = validate_target_state(state, links, req)
            print_plan(req, state, plan, pending, done)
            inserted, already = link_reused_versions(
                cur, baseline_snapshot_id=reg.baseline_snapshot,
                target_snapshot_id=req.target_snapshot_id, repo_id=reg.repo_id,
                target_revision=reg.target_revision, links=links)
            if (inserted, already) != (pending, done):
                raise RuntimeError("REUSE linker result differs from pinned readback")
            _, _, new_state = inspect_db(cur, req)
            remaining, total_done = validate_target_state(new_state, links, req)
            if (remaining, total_done) != (0, reg.reuse_files):
                raise RuntimeError("Post-link observation counts incorrect")
    print("REUSE inserted:", inserted, "| previously linked:", already)
    print("Snapshot status: in_progress | Raw versions/records inserted: 0")
    print("M4.6.3c.1 REUSE POPULATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
