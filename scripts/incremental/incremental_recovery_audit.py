#!/usr/bin/env python3
"""M4.6.4d.4b | Fail-closed, READ-ONLY recovery audit for incremental EPL Raw.

Run from project root as a module:
  .\\.venv_v2\\Scripts\\python.exe -m scripts.tools.incremental_recovery_audit \
      --target-revision <FULL_SHA40> --plan

Never downloads HF bodies, creates checkpoints, promotes archives, writes SQL, or
invokes execution subprocesses. Checkpoints provide evidence, NOT authority.
An in_progress target WITHOUT an intact registered checkpoint is BLOCKED.
All decisions require fresh checks by each separately confirmed write module.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from scripts.incremental.incremental_execution_context import (
    ROOT, SHA40, PlannedContext, PreparedContext, RegisteredContext,
    read_checkpoint, _guard_path,
)
from scripts.incremental.incremental_execution_context import candidate_format

DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class SnapshotEvidence:
    snapshot_id: int
    repo_id: str
    revision: str
    status: str
    full_files: int
    raw_files: int
    manifest_sha: str | None
    manifest_path: str | None


@dataclass(frozen=True)
class ObservationEvidence:
    path: str
    repo_id: str
    presence: str
    size: int
    git: str | None
    lfs: str | None
    file_id: int | None
    file_repo: str | None
    file_path: str | None
    file_format: str | None
    file_size: int | None
    content_sha: str | None
    archive_path: str | None
    archive_state: str | None
    parse_state: str | None
    record_count: int | None
    source_code: str | None
    baseline_file_id: int | None
    baseline_size: int | None
    baseline_git: str | None
    baseline_lfs: str | None


@dataclass(frozen=True)
class PhysicalEvidence:
    file_id: int
    total: int
    unique_locators: int
    nonnull_members: int


@dataclass(frozen=True)
class DatabaseEvidence:
    latest_baseline_id: int
    latest_baseline_revision: str
    baseline: SnapshotEvidence
    target: SnapshotEvidence
    observations: tuple[ObservationEvidence, ...]
    candidate_physical: tuple[PhysicalEvidence, ...]
    baseline_record_references: int


@dataclass(frozen=True)
class RecoveryAssessment:
    recovery_state: str
    target_snapshot_id: int | None
    expected_reuse: int
    verified_reuse: int
    expected_candidates: int
    verified_candidates: int
    next_step: str
    write_authorized: bool = False
    blockers: tuple[str, ...] = ()

    def __post_init__(self):
        if self.write_authorized:
            raise ValueError("Recovery audit can never authorize writes")


def assessment(state: str, planned: PlannedContext | None, sid: int | None = None,
               reuse: int = 0, candidates: int = 0, *, next_step: str = "MANUAL_REVIEW",
               blockers: tuple[str, ...] = ()) -> RecoveryAssessment:
    return RecoveryAssessment(state, sid,
                              planned.expected_reuse if planned else 0, reuse,
                              planned.expected_files if planned else 0, candidates,
                              next_step, False, blockers)


def _local_path(root: Path, relative: str) -> Path:
    """Check all ancestors for symlinks; never create a path in audit mode."""
    return _guard_path(root, relative)


def read_checkpoints(root: Path, revision: str) -> tuple[PlannedContext | None, PreparedContext | None, RegisteredContext | None]:
    """Missing stage is allowed; malformed, unsafe, or gapped chain is BLOCKED."""
    result: list[Any] = []
    missing = False
    for stage in ("planned", "prepared", "registered"):
        path = _local_path(root, f"reports/m4_6_runs/{revision}/{stage}.json")
        if not path.exists():
            missing = True
            result.append(None)
            continue
        if missing:
            raise RuntimeError("Checkpoint chain contains a missing predecessor")
        result.append(read_checkpoint(root, revision, stage))
    return tuple(result)  # type: ignore[return-value]


def _read_file(root: Path, relative: str, *, max_bytes: int) -> bytes | None:
    p = _local_path(root, relative)
    if not p.exists():
        return None
    if not p.is_file() or p.stat().st_size > max_bytes or p.stat().st_size <= 0:
        raise RuntimeError("Invalid local evidence file: " + relative)
    return p.read_bytes()


def verified_manifest(root: Path, prepared: PreparedContext) -> dict[str, dict] | None:
    """Both original report and immutable archive must have EXACT pinned bytes."""
    report = _read_file(root, prepared.manifest_report, max_bytes=MAX_MANIFEST_BYTES)
    archive = _read_file(root, prepared.manifest_archive, max_bytes=MAX_MANIFEST_BYTES)
    if report is None or archive is None:
        return None
    if (report != archive or hashlib.sha256(report).hexdigest() != prepared.manifest_sha256):
        raise RuntimeError("Full manifest report/archive SHA256 or bytes disagree")
    data = json.loads(report)
    p = prepared.planned
    if not isinstance(data, dict) or (data.get("repo_id"), data.get("repo_type"),
           data.get("resolved_commit"), data.get("file_count")) != (
            p.repo_id, "dataset", p.target_revision, p.expected_full_files):
        raise RuntimeError("Pinned manifest identity/count mismatch")
    files = data.get("files")
    if not isinstance(files, list) or len(files) != p.expected_full_files:
        raise RuntimeError("Full manifest file list missing")
    raw: dict[str, dict] = {}
    all_paths: set[str] = set()
    for row in files:
        if not isinstance(row, dict):
            raise RuntimeError("Malformed manifest row")
        path = row.get("relative_path")
        if (not isinstance(path, str) or len(path) > 1024 or not path
                or path.startswith("/") or "\\" in path
                or any(x in ("", ".", "..") for x in path.split("/"))
                or any(ord(x) < 32 or ord(x) == 127 for x in path)
                or path in all_paths
                or row.get("hf_revision") != p.target_revision
                or type(row.get("size_bytes")) is not int or row["size_bytes"] < 0):
            raise RuntimeError("Unsafe or duplicate manifest path/identity")
        all_paths.add(path)
        if path.startswith(RAW_PREFIXES):
            raw[path] = row
        elif not (path.startswith("clean/") or path in ("README.md", ".gitattributes")):
            raise RuntimeError("Unexpected non-Raw manifest path")
    if len(raw) != p.expected_raw_files:
        raise RuntimeError("Manifest Raw path count mismatch")
    for spec in p.candidates:
        row = raw.get(spec.path)
        if (row is None or row.get("format") != candidate_format(spec.path)
            or (row["size_bytes"], _norm(row.get("git_blob_id")),
                _norm(row.get("lfs_sha256"))) !=
              (spec.expected_bytes, _norm(spec.git_blob_id), _norm(spec.lfs_sha256))):
            raise RuntimeError("Candidate manifest metadata drift: " + spec.path)
    if len(set(raw) - {c.path for c in p.candidates}) != p.expected_reuse:
        raise RuntimeError("Manifest REUSE/candidate count mismatch")
    return raw


def _norm(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def verify_candidate_archives(root: Path, prepared: PreparedContext) -> tuple[str, ...]:
    """Only candidate objects (small JSON V1); missing => incomplete, corrupt => BLOCK."""
    missing = []
    for rec in prepared.candidate_receipts:
        rel = f"raw-archive-v2/objects/{rec.content_sha256[:2]}/{rec.content_sha256}"
        p = _local_path(root, rel)
        if not p.exists():
            missing.append(rec.path)
            continue
        if not p.is_file() or p.stat().st_size != rec.size_bytes:
            raise RuntimeError("Candidate archive object size mismatch: " + rec.path)
        digest = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != rec.content_sha256:
            raise RuntimeError("Candidate immutable archive content corrupted: " + rec.path)
    return tuple(missing)


def classify_registered(registered: RegisteredContext, evidence: DatabaseEvidence,
                        manifest_raw: dict[str, dict]) -> RecoveryAssessment:
    """Pure reconciliation. Never infer REUSE from candidate file_id equality."""
    prepared = registered.prepared
    plan = prepared.planned
    sid = registered.target_snapshot_id
    s = evidence.target
    b = evidence.baseline
    if (type(evidence.baseline_record_references) is not int
            or evidence.baseline_record_references != plan.expected_baseline_records
            or evidence.latest_baseline_id != plan.baseline_snapshot_id
            or evidence.latest_baseline_revision != plan.baseline_revision
            or (b.snapshot_id, b.repo_id, b.revision, b.status, b.raw_files) !=
               (plan.baseline_snapshot_id, plan.repo_id, plan.baseline_revision, "completed",
                plan.expected_baseline_files)
            or b.full_files < b.raw_files
            or (s.snapshot_id, s.repo_id, s.revision, s.status, s.full_files, s.raw_files) !=
               (sid, plan.repo_id, plan.target_revision, "in_progress",
                plan.expected_full_files, plan.expected_raw_files)):
        raise RuntimeError("Baseline/latest or registered target row changed")
    if (s.manifest_sha is None) != (s.manifest_path is None):
        raise RuntimeError("Partial PostgreSQL manifest identity")
    if s.manifest_sha is not None and (s.manifest_sha, s.manifest_path) != (
            prepared.manifest_sha256, prepared.manifest_archive):
        raise RuntimeError("Registered manifest identity differs from checkpoint")
    paths = {o.path for o in evidence.observations}
    if len(paths) != len(evidence.observations) or not paths.issubset(manifest_raw):
        raise RuntimeError("Duplicate or foreign observation path")
    specs = {c.path: c for c in plan.candidates}
    receipts = {c.path: c for c in prepared.candidate_receipts}
    physical = {r.file_id: r for r in evidence.candidate_physical}
    if len(physical) != len(evidence.candidate_physical):
        raise RuntimeError("Duplicate candidate physical evidence")
    reuse_paths = set(manifest_raw) - set(specs)
    reused = 0
    candidate_count = 0
    candidate_file_ids: set[int] = set()
    for o in evidence.observations:
        m = manifest_raw[o.path]
        if (o.repo_id, o.presence, o.size, _norm(o.git), _norm(o.lfs)) != (
             plan.repo_id, "linked", m["size_bytes"], _norm(m.get("git_blob_id")),
             _norm(m.get("lfs_sha256"))):
            raise RuntimeError("Target observation differs from full HF manifest: " + o.path)
        if (type(o.file_id) is not int or o.file_id <= 0
                or o.file_repo != plan.repo_id or o.file_path != o.path
                or o.file_size != o.size
                or o.archive_state != "verified" or o.parse_state != "success"
                or not o.source_code
                or type(o.record_count) is not int or o.record_count < 0
                or not SHA256.fullmatch(_norm(o.content_sha))
                or o.archive_path != f"raw-archive-v2/objects/{_norm(o.content_sha)[:2]}/{_norm(o.content_sha)}"):
            raise RuntimeError("Invalid linked immutable file version: " + o.path)
        if o.path in reuse_paths:
            if (o.baseline_file_id != o.file_id
                    or o.baseline_size != o.size
                    or (_norm(o.baseline_git), _norm(o.baseline_lfs)) != (_norm(o.git), _norm(o.lfs))
                    or o.file_format != m.get("format")):
                raise RuntimeError("REUSE observation is not identical to completed baseline: " + o.path)
            reused += 1
        else:
            spec = specs[o.path]
            rec = receipts[o.path]
            if (o.file_format != candidate_format(spec.path) or o.source_code != spec.source_code
                    or o.content_sha != rec.content_sha256
                    or o.record_count != rec.records):
                raise RuntimeError("Candidate version/receipt mismatch: " + o.path)
            if o.file_id in candidate_file_ids:
                raise RuntimeError("Two distinct candidates linked to same file_id")
            candidate_file_ids.add(o.file_id)
            phy = physical.get(o.file_id)
            if phy is None or (phy.total, phy.unique_locators, phy.nonnull_members) != (
                    rec.records, rec.records, 0):
                raise RuntimeError("Candidate physical raw_records integrity mismatch: " + o.path)
            candidate_count += 1
    if set(physical) != candidate_file_ids:
        raise RuntimeError("Unexpected candidate physical evidence")
    if reused > plan.expected_reuse or candidate_count > plan.expected_files:
        raise RuntimeError("Excess linked observations")
    if candidate_count and reused < plan.expected_reuse:
        raise RuntimeError("Candidates linked before REUSE completeness")
    if s.manifest_sha and (reused != plan.expected_reuse or candidate_count != plan.expected_files):
        raise RuntimeError("Manifest registered before all observations completed")
    if s.manifest_sha:
        return assessment("MANIFEST_REGISTERED", plan, sid, reused, candidate_count,
                          next_step="FINALIZATION_PLAN_REVIEW")
    if reused < plan.expected_reuse:
        return assessment("REGISTERED_PENDING_REUSE", plan, sid, reused, candidate_count,
                          next_step="REUSE_PLAN_REVIEW")
    if candidate_count == 0:
        return assessment("REUSE_COMPLETE", plan, sid, reused, 0,
                          next_step="CANDIDATE_PLAN_REVIEW")
    if candidate_count < plan.expected_files:
        return assessment("CANDIDATES_PARTIAL", plan, sid, reused, candidate_count,
                          next_step="CANDIDATE_PLAN_REVIEW")
    return assessment("CANDIDATES_COMPLETE", plan, sid, reused, candidate_count,
                      next_step="MANIFEST_REGISTRATION_PLAN_REVIEW")


def read_db_evidence(registered: RegisteredContext) -> DatabaseEvidence:
    """All reads use one PostgreSQL READ ONLY transaction; candidate rows only."""
    import psycopg
    from scripts.tools.register_incremental_snapshot import db_parameters

    p = registered.prepared.planned
    params = db_parameters()
    if params.get("dbname") != "epl_raw_storage_v2":
        raise RuntimeError("Wrong database configured for Recovery Audit")
    params["options"] = "-c default_transaction_read_only=on -c statement_timeout=600000 -c lock_timeout=5000"
    params["application_name"] = "m46_recovery_read_only"
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone() != ("epl_raw_storage_v2",):
                raise RuntimeError("Wrong database in Recovery Audit")
            cur.execute("""SELECT snapshot_id,hf_revision FROM raw.snapshot_runs
                           WHERE repo_id=%s AND status='completed'
                           ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1""", (p.repo_id,))
            latest = cur.fetchone()
            if latest is None:
                raise RuntimeError("No completed baseline found")
            cur.execute("""SELECT snapshot_id,repo_id,hf_revision,status,manifest_file_count,
                                  raw_scope_file_count,manifest_sha256,manifest_archive_path
                           FROM raw.snapshot_runs WHERE snapshot_id IN (%s,%s)""",
                        (p.baseline_snapshot_id, registered.target_snapshot_id))
            snapshots = {r[0]: SnapshotEvidence(*r) for r in cur.fetchall()}
            if len(snapshots) != 2:
                raise RuntimeError("Baseline/target snapshot row missing")
            cur.execute("""SELECT COUNT(*),COUNT(DISTINCT o.relative_path),
                                  COUNT(*) FILTER (WHERE o.presence_status='linked'
                                       AND f.archive_state='verified' AND f.parse_state='success'),
                                  COALESCE(SUM(f.record_count),0)::bigint
                           FROM raw.file_observations o
                           LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                           WHERE o.snapshot_id=%s AND o.repo_id=%s""",
                        (p.baseline_snapshot_id,p.repo_id))
            base_count,base_unique,base_linked,base_records = cur.fetchone()
            if (base_count != p.expected_baseline_files
                    or base_unique != p.expected_baseline_files
                    or base_linked != p.expected_baseline_files
                    or base_records != p.expected_baseline_records):
                raise RuntimeError("Completed baseline observation/record references drift")
            cur.execute("""SELECT o.relative_path,o.repo_id,o.presence_status,o.manifest_size_bytes,
                                  o.git_blob_id,o.lfs_sha256,
                                  f.file_id,f.repo_id,f.relative_path,f.file_format,f.file_size_bytes,
                                  f.content_sha256,f.archive_path,f.archive_state,f.parse_state,
                                  f.record_count,s.source_code,
                                  b.file_id,b.manifest_size_bytes,b.git_blob_id,b.lfs_sha256
                           FROM raw.file_observations o
                           LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                           LEFT JOIN raw.raw_sources s ON s.source_id=f.source_id
                           LEFT JOIN raw.file_observations b
                             ON b.snapshot_id=%s AND b.repo_id=o.repo_id AND b.relative_path=o.relative_path
                           WHERE o.snapshot_id=%s""",
                        (p.baseline_snapshot_id, registered.target_snapshot_id))
            observations = tuple(ObservationEvidence(*r) for r in cur.fetchall())
            ids = sorted({o.file_id for o in observations
                          if o.path in {c.path for c in p.candidates} and type(o.file_id) is int})
            physical: tuple[PhysicalEvidence, ...] = ()
            if ids:
                cur.execute("""SELECT file_id,COUNT(*),COUNT(DISTINCT record_locator),
                                      COUNT(*) FILTER(WHERE member_id IS NOT NULL)
                               FROM raw.raw_records WHERE file_id = ANY(%s::bigint[])
                               GROUP BY file_id""", (ids,))
                physical = tuple(PhysicalEvidence(*r) for r in cur.fetchall())
            return DatabaseEvidence(latest[0], latest[1],
                                    snapshots[p.baseline_snapshot_id], snapshots[registered.target_snapshot_id],
                                    observations, physical, base_records)


def audit_recovery(*, repo_id: str, target_revision: str, root: Path = ROOT,
                   state_reader: Callable | None = None,
                   evidence_reader: Callable | None = None) -> RecoveryAssessment:
    """No writes. All unexpected cases are BLOCKED, never silently recovered."""
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 300:
        raise ValueError("Invalid repository ID")
    if not isinstance(target_revision, str) or not SHA40.fullmatch(target_revision):
        raise ValueError("Pinned lowercase full SHA40 required")
    if state_reader is None:
        from scripts.tools.check_raw_updates import read_target_snapshot_state
        state_reader = read_target_snapshot_state
    if evidence_reader is None:
        evidence_reader = read_db_evidence
    target = state_reader(repo_id, target_revision)
    if target is not None and target.status == "completed":
        return assessment("COMPLETED", None, target.snapshot_id,
                          next_step="SKIP_ALREADY_PROCESSED")
    if target is not None and target.status != "in_progress":
        return assessment("BLOCKED_REVIEW", None, target.snapshot_id,
                          blockers=("Unsupported existing target snapshot status",))
    try:
        planned, prepared, registered = read_checkpoints(root, target_revision)
        if planned is not None and planned.repo_id != repo_id:
            raise RuntimeError("Checkpoint repository differs from target")
        if target is None:
            if registered is not None:
                raise RuntimeError("Registered checkpoint exists, PostgreSQL target absent")
            if planned is None:
                return assessment("NO_REGISTERED_SNAPSHOT", None,
                                  next_step="NEW_REVISION_PLAN_REVIEW")
            if prepared is None:
                return assessment("PREPARATION_PARTIAL", planned,
                                  next_step="PREPARATION_PLAN_REVIEW")
            raw = verified_manifest(root, prepared)
            missing = verify_candidate_archives(root, prepared)
            if raw is None or missing:
                return assessment("PREPARATION_PARTIAL", planned,
                                  next_step="PREPARATION_PLAN_REVIEW")
            return assessment("PREPARED_READY", planned,
                              next_step="SNAPSHOT_REGISTRATION_PLAN_REVIEW")
        # A target already in DB without the exact registered checkpoint is
        # blocked: NO attempt to recreate or register that snapshot.
        if not isinstance(registered, RegisteredContext):
            raise RuntimeError("in_progress target lacks an intact registered checkpoint")
        if target.snapshot_id != registered.target_snapshot_id:
            raise RuntimeError("Checkpoint target ID differs from PostgreSQL target")
        raw = verified_manifest(root, registered.prepared)
        if raw is None:
            raise RuntimeError("Registered snapshot is missing verified full manifest")
        missing = verify_candidate_archives(root, registered.prepared)
        if missing:
            raise RuntimeError("Registered snapshot missing candidate archives: " + ", ".join(missing[:3]))
        evidence = evidence_reader(registered)
        if (evidence.target.snapshot_id, evidence.target.status,
                evidence.target.repo_id, evidence.target.revision) != (
                    target.snapshot_id, "in_progress", repo_id, target_revision):
            raise RuntimeError("PostgreSQL target changed during recovery audit")
        return classify_registered(registered, evidence, raw)
    except (RuntimeError, ValueError, FileNotFoundError, KeyError, OSError, TypeError,
            json.JSONDecodeError) as exc:
        return assessment("BLOCKED_REVIEW", None, target.snapshot_id if target else None,
                          blockers=(f"{type(exc).__name__}: {exc}",))


def format_report(result: RecoveryAssessment) -> str:
    lines = ["=== M4.6.4d.4b | READ-ONLY RECOVERY AUDIT ===",
             f"Recovery state: {result.recovery_state}",
             f"Target snapshot: {'ABSENT' if result.target_snapshot_id is None else '#' + str(result.target_snapshot_id)}",
             f"REUSE observations verified: {result.verified_reuse}/{result.expected_reuse}",
             f"Candidate observations verified: {result.verified_candidates}/{result.expected_candidates}",
             f"Next step for MANUAL review: {result.next_step}",
             "Write authorized: NO | Subprocesses: NONE | HF file-body downloads: NONE",
             "PostgreSQL writes: NONE | Cache/archive writes: NONE | Checkpoints written: NONE"]
    for blocker in result.blockers:
        lines.append("BLOCKER: " + blocker)
    lines.append("M4.6.4d.4b RECOVERY AUDIT: " +
                 ("BLOCKED (NO WRITES)" if result.recovery_state == "BLOCKED_REVIEW"
                  else "PASS (NOT WRITE APPROVAL)"))
    return "\n".join(lines)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--repo-id", default=DEFAULT_REPO)
    p.add_argument("--plan", action="store_true", required=True)
    a = p.parse_args(argv)
    if not SHA40.fullmatch(a.target_revision):
        p.error("Target revision must be pinned lowercase SHA40")
    if not a.repo_id.strip():
        p.error("Repository cannot be empty")
    return a


def main(argv=None):
    a = parse_args(argv)
    result = audit_recovery(repo_id=a.repo_id, target_revision=a.target_revision)
    print(format_report(result))
    return 2 if result.recovery_state == "BLOCKED_REVIEW" else 0


if __name__ == "__main__":
    raise SystemExit(main())
