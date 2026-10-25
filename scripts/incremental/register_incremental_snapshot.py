#!/usr/bin/env python3
"""M4.6.3b.2: guarded snapshot registration for a pinned incremental plan.

--plan reruns the archive-verified Preparation gate (no PostgreSQL writes).
--register --confirm-snapshot-only reruns that gate and inserts ONLY one
raw.snapshot_runs row as in_progress inside a guarded transaction.

The coordinator never downloads candidate bodies, inserts raw records,
creates observations, writes archive objects, or finalizes snapshots.
If a revision is already registered, it MUST be handled by separate recovery.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_DB = "epl_raw_storage_v2"
EXPECTED_REPO = "Toibinguz/epl-premier-league-dataset"


class ReadOnlyPreflightFailure(RuntimeError):
    """A read-only child exited unsuccessfully before the caller could write.

    Keep this distinct from a successful child whose output fails the pinned
    contract. The automation backend can reconcile the child's terminal
    diagnostic without treating arbitrary earlier exceptions as retry proof.
    Raw child output remains in the process log, never in this exception.
    """

    def __init__(self, gate: str, returncode: int):
        if gate not in ("Preparation gate", "Read-only candidate planner"):
            raise ValueError("Unknown read-only preflight gate")
        if type(returncode) is not int or returncode == 0:
            raise ValueError("Read-only preflight failure needs a nonzero exit")
        self.returncode = returncode
        super().__init__(f"{gate} failed; read-only child exited with status {returncode}")


@dataclass(frozen=True)
class Registration:
    repo_id: str
    baseline_snapshot: int
    target_revision: str
    fingerprint: str
    baseline_files: int
    manifest_files: int
    raw_files: int
    reuse_files: int
    candidate_files: int
    candidate_bytes: int
    candidate_records: int

    def validate(self) -> None:
        if self.repo_id != EXPECTED_REPO:
            raise RuntimeError("Unexpected Hugging Face repository")
        if type(self.baseline_snapshot) is not int or self.baseline_snapshot <= 0:
            raise RuntimeError("Invalid baseline snapshot")
        if not SHA40.fullmatch(self.target_revision):
            raise RuntimeError("Target revision must be pinned SHA40")
        if not SHA256.fullmatch(self.fingerprint):
            raise RuntimeError("Invalid plan fingerprint")
        # Keep this contract aligned with incremental_snapshot_prepare.validate_expected_counts.
        from scripts.tools.incremental_snapshot_prepare import validate_expected_counts
        validate_expected_counts(reuse=self.reuse_files, candidates=self.candidate_files,
                                 candidate_bytes=self.candidate_bytes,
                                 candidate_records=self.candidate_records)
        for name in ("baseline_files", "manifest_files", "raw_files"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise RuntimeError(f"{name} must be a positive integer")
        if self.reuse_files > self.baseline_files:
            raise RuntimeError("REUSE count exceeds baseline Raw file count")
        if not self.manifest_files >= self.raw_files == self.reuse_files + self.candidate_files:
            raise RuntimeError("Target manifest coverage mismatch")



def check_csv_registration_mode(*, allow_csv: bool, plan: bool) -> None:
    """Never use the CSV preview switch to authorize registration."""
    if type(allow_csv) is not bool or type(plan) is not bool:
        raise ValueError("Invalid CSV snapshot registration mode")
    if allow_csv and not plan:
        raise ValueError("CSV registration preview is read-only; --register remains blocked")


def preflight_command(
    req: Registration,
    *,
    cache_dir: str = "reports/m4_6_content_cache",
    allow_csv_preflight_only: bool = False,
) -> list[str]:

    """Re-run the complete verified-archive preflight immediately before writing."""
    req.validate()
    if type(allow_csv_preflight_only) is not bool:
        raise ValueError("CSV preflight switch must be bool")
    if (
        not isinstance(cache_dir, str)
        or not cache_dir.startswith("reports/")
        or "\\" in cache_dir
        or any(part in ("", ".", "..") for part in cache_dir.split("/"))
        or any(ord(c) < 32 or ord(c) == 127 for c in cache_dir)
    ):
        raise ValueError("Invalid project-relative cache directory")

    return [sys.executable,
            str(ROOT / "scripts/tools/incremental_snapshot_prepare.py"),
            "--baseline-snapshot", str(req.baseline_snapshot),
            "--target-revision", req.target_revision,
            "--repo-id", req.repo_id,
            "--expected-fingerprint", req.fingerprint,
            "--expected-reuse", str(req.reuse_files),
            "--expected-files", str(req.candidate_files),
            "--expected-bytes", str(req.candidate_bytes),

            "--expected-records", str(req.candidate_records),
            "--cache-dir", cache_dir,
            *(["--allow-csv-preflight-only"] if allow_csv_preflight_only else []),
            "--plan"]


def run_preflight(
    req: Registration,
    *,
    cache_dir: str = "reports/m4_6_content_cache",
    allow_csv_preflight_only: bool = False,
) -> None:
    if type(allow_csv_preflight_only) is not bool:
        raise ValueError("Invalid CSV preflight mode")
    command = (preflight_command(req, cache_dir=cache_dir,
                                 allow_csv_preflight_only=True)
               if allow_csv_preflight_only else preflight_command(req, cache_dir=cache_dir))

    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=300)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    expected_lines = (
        "M4.5.4c.3i SNAPSHOT PREPARATION PLAN: PASS (NOT APPROVAL TO COMMIT)",
        f"Full HF manifest file count: {req.manifest_files}",
        f"Target Raw files: {req.raw_files}",
        f"REUSE file IDs: {req.reuse_files}",
        f"Candidate archive objects VERIFIED: {req.candidate_files}",
        f"Archive verified bytes: {req.candidate_bytes}",
        f"Candidate parser records: {req.candidate_records}",
        "Target snapshot: ABSENT; ID will be assigned by PostgreSQL",
    )
    lines = set(result.stdout.splitlines())
    if result.returncode != 0:
        raise ReadOnlyPreflightFailure("Preparation gate", result.returncode)
    if not all(line in lines for line in expected_lines):
        raise RuntimeError("Preparation gate failed or expected totals drifted; refusing registration")
    # The Preparation gate checks candidate hashes, source/archive identities,
    # REUSE links and absence of existing/concurrently active target snapshots.


def register_in_transaction(cur, req: Registration) -> int:
    """Actual SQL inside caller-owned transaction. Never commits or finalizes."""
    req.validate()
    cur.execute("SELECT current_database()")
    if cur.fetchone() != (EXPECTED_DB,):
        raise RuntimeError("Wrong database: refusing snapshot registration")

    # Cooperating incremental registrations serialize on repository identity.
    cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (req.repo_id,))
    if cur.fetchone() != (True,):
        raise RuntimeError("Another incremental registrar holds the repository lock")

    cur.execute("""SELECT snapshot_id,repo_id,hf_revision,status,raw_scope_file_count,
                          manifest_sha256,manifest_archive_path
                   FROM raw.snapshot_runs WHERE snapshot_id=%s FOR SHARE""",
                (req.baseline_snapshot,))
    base = cur.fetchone()
    if (base is None or base[1] != req.repo_id or base[3] != "completed"
            or base[4] != req.baseline_files
            or not isinstance(base[5], str) or not SHA256.fullmatch(base[5])
            or base[6] != f"raw-archive-v2/manifests/{base[5][:2]}/{base[5]}.json"):
        raise RuntimeError("Baseline snapshot is missing, incomplete or lacks registered manifest")
    if base[2] == req.target_revision:
        raise RuntimeError("Target revision equals baseline revision")

    # Stop if another completed snapshot became latest since the plan was pinned.
    cur.execute("""SELECT snapshot_id FROM raw.snapshot_runs
                   WHERE repo_id=%s AND status='completed'
                   ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1""", (req.repo_id,))
    if cur.fetchone() != (req.baseline_snapshot,):
        raise RuntimeError("Baseline is no longer the latest completed snapshot")

    # Validate all observations, including linked file states, not only a count
    # of non-NULL file IDs. This avoids accepting a partially damaged baseline.
    cur.execute("""SELECT COUNT(*), COUNT(DISTINCT o.relative_path),
                          COUNT(DISTINCT o.file_id),
                          COUNT(*) FILTER (WHERE o.presence_status='linked'
                                             AND f.archive_state='verified'
                                             AND f.parse_state='success')
                   FROM raw.file_observations AS o
                   LEFT JOIN raw.raw_files AS f ON f.file_id=o.file_id
                   WHERE o.snapshot_id=%s AND o.repo_id=%s""",
                (req.baseline_snapshot, req.repo_id))
    if cur.fetchone() != (req.baseline_files,) * 4:
        raise RuntimeError("Baseline observations or immutable file states changed")

    cur.execute("""SELECT snapshot_id,status FROM raw.snapshot_runs
                   WHERE repo_id=%s AND hf_revision=%s FOR UPDATE""",
                (req.repo_id, req.target_revision))
    if cur.fetchone() is not None:
        raise RuntimeError("Target revision already registered: stop and assess resume")

    cur.execute("""SELECT snapshot_id,hf_revision FROM raw.snapshot_runs
                   WHERE repo_id=%s AND status='in_progress' FOR UPDATE""",
                (req.repo_id,))
    if cur.fetchall():
        raise RuntimeError("Another snapshot already in_progress; stop")

    cur.execute("""INSERT INTO raw.snapshot_runs
                      (repo_id,hf_revision,manifest_file_count,raw_scope_file_count,status)
                   VALUES (%s,%s,%s,%s,'in_progress') RETURNING snapshot_id""",
                (req.repo_id, req.target_revision, req.manifest_files, req.raw_files))
    inserted = cur.fetchone()
    if inserted is None or type(inserted[0]) is not int or inserted[0] <= 0:
        raise RuntimeError("Failed to obtain new snapshot ID")
    return inserted[0]


def db_parameters() -> dict:
    from dotenv import dotenv_values
    cfg = {**dotenv_values(ROOT / ".env", encoding="utf-8-sig"),
           **dotenv_values(ROOT / ".env.v2", encoding="utf-8-sig")}
    if cfg.get("DB_NAME") != EXPECTED_DB:
        raise RuntimeError(".env configuration references unexpected database")
    return {"host": cfg.get("DB_HOST"), "port": int(cfg.get("DB_PORT") or 5432),
            "dbname": cfg.get("DB_NAME"), "user": cfg.get("DB_USER"),
            "password": cfg.get("DB_PASSWORD"), "connect_timeout": 15,
            "options": "-c statement_timeout=120000 -c lock_timeout=5000"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--repo-id", default=EXPECTED_REPO)

    p.add_argument(
        "--cache-dir",
        default="reports/m4_6_content_cache",
    )

    p.add_argument("--expected-baseline-files", type=int, required=True)
    p.add_argument("--expected-full-files", type=int, required=True)
    p.add_argument("--expected-raw-files", type=int, required=True)
    p.add_argument("--expected-reuse", type=int, required=True)
    p.add_argument("--expected-files", type=int, required=True)
    p.add_argument("--expected-bytes", type=int, required=True)
    p.add_argument("--expected-records", type=int, required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--register", action="store_true")
    p.add_argument("--confirm-snapshot-only", action="store_true")
    p.add_argument("--allow-csv-registration-plan-only", action="store_true",
                   help="CSV preflight preview only; cannot register an in_progress snapshot")
    p.add_argument("--allow-csv-registration-write", action="store_true",
                   help="Explicit CSV snapshot-registration opt-in; requires --register and --confirm-snapshot-only")
    args = p.parse_args()
    if args.register and not args.confirm_snapshot_only:
        p.error("Registration requires --confirm-snapshot-only")
    if args.plan and args.confirm_snapshot_only:
        p.error("--confirm-snapshot-only is only for --register")
    if args.allow_csv_registration_write and (not args.register or not args.confirm_snapshot_only
                                              or args.allow_csv_registration_plan_only):
        p.error("CSV snapshot write requires --register --confirm-snapshot-only and forbids CSV plan-only")
    try:
        check_csv_registration_mode(allow_csv=args.allow_csv_registration_plan_only,
                                    plan=args.plan)
    except ValueError as exc:
        p.error(str(exc))

    req = Registration(args.repo_id, args.baseline_snapshot, args.target_revision,
                       args.expected_fingerprint, args.expected_baseline_files, args.expected_full_files,
                       args.expected_raw_files, args.expected_reuse, args.expected_files,
                       args.expected_bytes, args.expected_records)
    req.validate()
    print("=== M4.6.3b.2 | INCREMENTAL SNAPSHOT REGISTRATION ===", flush=True)
    print("Repository:", req.repo_id)
    print("Target revision:", req.target_revision)
    print("Plan fingerprint:", req.fingerprint)
    print("Expected baseline/full/Raw files:", req.baseline_files, req.manifest_files, req.raw_files)
    print("Candidate bytes/records:", req.candidate_bytes, req.candidate_records)
    print("REUSE/Candidate files:", req.reuse_files, req.candidate_files, flush=True)

    if args.allow_csv_registration_plan_only or args.allow_csv_registration_write:
        run_preflight(req, cache_dir=args.cache_dir, allow_csv_preflight_only=True)
    else:
        run_preflight(req, cache_dir=args.cache_dir)


    if args.plan:
        print("Registration result: READY; target snapshot still ABSENT")
        print("Database writes: NONE | Snapshot created: NO")
        print("M4.6.3b.2 SNAPSHOT REGISTRATION PLAN: PASS (NOT APPROVAL TO COMMIT)")
        return 0

    import psycopg
    # The advisory lock and all SQL checks execute together before INSERT.
    # The previous gate runs before the transaction; this second DB check
    # closes concurrent-registration races (but not HF upstream changes,
    # because all HF fetches are pinned to the immutable commit SHA).
    with psycopg.connect(**db_parameters()) as conn:
        with conn.cursor() as cur:
            new_id = register_in_transaction(cur, req)
        # Context manager performs COMMIT iff no exception occurred.
    print("Registered snapshot ID:", new_id)
    print("Status: in_progress | manifest SHA/path: not registered")
    print("Observations inserted: 0 | Raw file versions inserted: 0 | Raw records inserted: 0")
    print("M4.6.3b.2 SNAPSHOT REGISTRATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
