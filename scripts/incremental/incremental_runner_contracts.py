#!/usr/bin/env python3
"""M4.6.4d.6c.1 | READ-ONLY evidence and CLI contract adapters.

No write mode, subprocess.run, HF download, checkpoint publication or SQL INSERT.
Fresh CacheEvidence uses the existing DB/HF metadata loader and *offline* HF
cache inspection; PreparedContext additionally requires the two identical
immutable manifest files and content-addressed candidate archive objects.
These are evidence builders, NOT write approvals. All writers must re-check.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sys
from typing import Any, Callable

from scripts.tools.incremental_execution_context import (
    ROOT, SHA256, CandidateReceipt, PlannedContext, PreparedContext,
    RegisteredContext, build_planned, build_prepared, build_registered,
    checkpoint_digest, cli_arguments, _guard_path,
)

# Keys are *planning* modes only. The adapter can never generate --apply,
# --commit, --promote, --publish, --register, --link, or confirmations.
PLAN_MODULES = {
    "manifest_preparation": "prepare_incremental_manifest",
    "download": "download_incremental_candidates",
    "archive_promotion": "promote_incremental_archive",
    "snapshot_preparation": "incremental_snapshot_prepare",
    "snapshot_registration": "register_incremental_snapshot",
    "reuse": "populate_incremental_reuse",
    "candidate_planner": "plan_incremental_candidates",
    "candidate_ingestion": "ingest_incremental_candidates",
    "manifest_registration": "register_incremental_manifest",
    "finalization": "finalize_incremental_snapshot",
}

DEFAULT_CACHE = "reports/m4_6_content_cache"


def _cache_relative(cache_dir: str) -> str:
    """Accept project-relative reports/ directory only; no shell expansion."""
    if (not isinstance(cache_dir, str) or len(cache_dir) > 512
            or not cache_dir.startswith("reports/") or "\\" in cache_dir
            or any(x in ("", ".", "..") for x in cache_dir.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in cache_dir)):
        raise ValueError("Cache directory must be a safe project-relative reports/ path")
    return cache_dir


@dataclass(frozen=True)
class CacheEvidence:
    """Receipt metadata from separately rechecked HF cache (NOT an approval)."""
    planned_sha256: str
    receipts: tuple[CandidateReceipt, ...]
    expected_records: int
    ready_new: int
    already_archived: int

    def __post_init__(self):
        if not isinstance(self.planned_sha256, str) or not SHA256.fullmatch(self.planned_sha256):
            raise ValueError("Invalid pinned PlannedContext SHA")
        if not isinstance(self.receipts, tuple) or not self.receipts:
            raise ValueError("Missing immutable cache receipts")
        if any(not isinstance(r, CandidateReceipt) for r in self.receipts):
            raise ValueError("Invalid receipt type")
        paths = tuple(r.path for r in self.receipts)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("Receipt paths must be unique and sorted")
        if type(self.expected_records) is not int or self.expected_records <= 0:
            raise ValueError("Expected records must be strictly positive")
        if sum(r.records for r in self.receipts) != self.expected_records:
            raise ValueError("Receipt record accounting mismatch")
        if any(type(n) is not int or n < 0 for n in (self.ready_new, self.already_archived)):
            raise ValueError("Invalid candidate archive state totals")
        if self.ready_new + self.already_archived != len(self.receipts):
            raise ValueError("Candidate state totals differ from receipts")


def _check_evidence(planned: PlannedContext, evidence: CacheEvidence) -> None:
    if not isinstance(planned, PlannedContext) or not isinstance(evidence, CacheEvidence):
        raise TypeError("Pinned planned context and verified CacheEvidence required")
    if evidence.planned_sha256 != checkpoint_digest(planned):
        raise RuntimeError("Cache evidence is pinned to a different PlannedContext")
    specs = {s.path: s for s in planned.candidates}
    if (tuple(specs) != tuple(r.path for r in evidence.receipts)
            or sum(r.size_bytes for r in evidence.receipts) != planned.expected_bytes):
        raise RuntimeError("Cache evidence is incomplete or differs from pinned plan")
    for r in evidence.receipts:
        spec = specs[r.path]
        if (r.size_bytes != spec.expected_bytes
                or (spec.lfs_sha256 is not None and r.content_sha256 != spec.lfs_sha256)):
            raise RuntimeError("Cache receipt identity/size mismatch")


def receipts_from_archive_audit(planned: PlannedContext, candidates: tuple[Any, ...]) -> CacheEvidence:
    """Pure typed conversion of ArchiveCandidate results; caller must run body audit.

    This function alone does NOT verify cache or archive bytes. Use
    audit_cache_evidence() to obtain fresh audit evidence.
    """
    if not isinstance(planned, PlannedContext) or not isinstance(candidates, tuple):
        raise TypeError("PlannedContext and an audited tuple required")
    by_path = {}
    new = already = 0
    for c in candidates:
        path = getattr(c, "relative_path", None)
        if not isinstance(path, str) or path in by_path:
            raise ValueError("Duplicate/invalid archive audit path")
        digest = getattr(c, "sha256", None)
        size = getattr(c, "byte_count", None)
        records = getattr(c, "record_count", None)
        archive_rel = getattr(c, "archive_relative_path", None)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("Invalid candidate content SHA256")
        if archive_rel != f"raw-archive-v2/objects/{digest[:2]}/{digest}":
            raise ValueError("Noncanonical candidate archive destination")
        state = getattr(c, "action", None)
        if state == "READY_NEW":
            new += 1
        elif state == "ALREADY_ARCHIVED":
            already += 1
        else:
            raise ValueError("Candidate archive state cannot be trusted")
        by_path[path] = CandidateReceipt(path, size, digest, records)
    receipts = tuple(by_path[p] for p in sorted(by_path))
    result = CacheEvidence(checkpoint_digest(planned), receipts,
                           sum(x.records for x in receipts), new, already)
    _check_evidence(planned, result)
    return result


def audit_cache_evidence(planned: PlannedContext, *, cache_dir: str = DEFAULT_CACHE,
                         root: Path = ROOT,
                         context_loader: Callable | None = None,
                         cache_validator: Callable | None = None,
                         resolver: Callable | None = None,
                         auditor: Callable | None = None) -> CacheEvidence:
    """Fresh read-only DB/HF metadata + local cache JSON validation.

    Never downloads file bodies: resolver uses local_files_only=True. Dependency
    injection is solely for offline unit tests. Does not publish anything.
    """
    if not isinstance(planned, PlannedContext):
        raise TypeError("PlannedContext required")
    _cache_relative(cache_dir)
    from scripts.tools.download_incremental_candidates import load_context, validated_cache_path
    from scripts.tools.promote_incremental_archive import resolve_cached_candidates
    from scripts.tools.incremental_archive_promotion import audit_candidates

    load = context_loader or load_context
    validate_cache = cache_validator or validated_cache_path
    resolve = resolver or resolve_cached_candidates
    audit = auditor or audit_candidates
    args = argparse.Namespace(repo_id=planned.repo_id,
                              baseline_snapshot=planned.baseline_snapshot_id,
                              target_revision=planned.target_revision,
                              expected_fingerprint=planned.plan_fingerprint,
                              expected_files=planned.expected_files,
                              expected_bytes=planned.expected_bytes)
    fresh = load(args)
    reconstructed = build_planned(fresh,
        expected_baseline_files=planned.expected_baseline_files,
        expected_full_files=planned.expected_full_files)
    if reconstructed != planned:
        raise RuntimeError("Fresh DB/HF SyncPlan drifted from pinned PlannedContext")
    cache = validate_cache(cache_dir, root=root, create=False)
    paths = resolve(fresh, cache)
    archive_rows = tuple(audit(fresh.plan, fresh.target, paths, root))
    return receipts_from_archive_audit(planned, archive_rows)


def plan_command(step: str, context: PlannedContext | PreparedContext | RegisteredContext,
                 *, cache_dir: str = DEFAULT_CACHE,
                 cache_evidence: CacheEvidence | None = None) -> tuple[str, ...]:
    """Only an unexecuted python -m ... --plan argv; never side effects.

    Archive promotion accepts PlannedContext + independently audited cache
    evidence so no forged PreparedContext is needed to learn expected_records.
    """
    if step not in PLAN_MODULES:
        raise ValueError("Unknown allowlisted plan step")
    flags: tuple[str, ...]
    if step == "archive_promotion":
        if not isinstance(context, PlannedContext):
            raise ValueError("Pre-prepared archive plan requires PlannedContext")
        _check_evidence(context, cache_evidence)
        flags = (
            "--baseline-snapshot", str(context.baseline_snapshot_id),
            "--target-revision", context.target_revision,
            "--expected-fingerprint", context.plan_fingerprint,
            "--repo-id", context.repo_id,
            "--expected-files", str(context.expected_files),
            "--expected-bytes", str(context.expected_bytes),
            "--expected-records", str(cache_evidence.expected_records),
        )
    else:
        if cache_evidence is not None:
            raise ValueError("CacheEvidence is only accepted for pre-prepared archive planning")
        flags = cli_arguments(context, step)
    extra = ("--cache-dir", _cache_relative(cache_dir)) if step in (
        "download", "archive_promotion", "candidate_planner", "candidate_ingestion") else ()
    return (sys.executable, "-m", f"scripts.tools.{PLAN_MODULES[step]}",
            *flags, *extra, "--plan")


def build_prepared_from_archives(planned: PlannedContext, evidence: CacheEvidence,
                                 *, root: Path, expected_manifest_sha256: str) -> PreparedContext:
    """Rehash manifest report, immutable copy and all candidate objects.

    No publication; use only after archive promotion was independently verified.
    The expected manifest hash MUST come from a separately reviewed manifest plan.
    """
    _check_evidence(planned, evidence)
    if not isinstance(expected_manifest_sha256, str) or not SHA256.fullmatch(expected_manifest_sha256):
        raise ValueError("Pinned manifest SHA256 is required")
    report_relative = f"reports/m4_6_manifests/{planned.target_revision}/hf_manifest_v2.json"
    archive_relative = (f"raw-archive-v2/manifests/{expected_manifest_sha256[:2]}/"
                        f"{expected_manifest_sha256}.json")
    from scripts.tools.incremental_recovery_audit import (
        MAX_MANIFEST_BYTES, verified_manifest, verify_candidate_archives,
    )
    paths = (_guard_path(root, report_relative), _guard_path(root, archive_relative))
    blobs = []
    for file in paths:
        if not file.is_file() or file.stat().st_size > MAX_MANIFEST_BYTES or file.stat().st_size == 0:
            raise RuntimeError("Verified immutable manifest evidence missing or invalid")
        blobs.append(file.read_bytes())
    if blobs[0] != blobs[1] or hashlib.sha256(blobs[0]).hexdigest() != expected_manifest_sha256:
        raise RuntimeError("Manifest immutable report/archive mismatch")
    prepared = build_prepared(planned, evidence.receipts, manifest_bytes=blobs[0])
    if prepared.manifest_sha256 != expected_manifest_sha256 or verified_manifest(root, prepared) is None:
        raise RuntimeError("Full manifest could not be verified")
    missing = verify_candidate_archives(root, prepared)
    if missing:
        raise RuntimeError("Immutable candidate archives missing: " + ", ".join(missing[:3]))
    return prepared


@dataclass(frozen=True)
class RegistrationRow:
    snapshot_id: int
    repo_id: str
    revision: str
    status: str
    full_files: int
    raw_files: int
    manifest_sha: str | None
    manifest_archive: str | None
    latest_completed_id: int


def _read_registration_row(prepared: PreparedContext) -> RegistrationRow:
    """Read-only PostgreSQL transaction; never infer ID from CLI stdout."""
    import psycopg
    from scripts.tools.register_incremental_snapshot import db_parameters
    p = prepared.planned
    params = db_parameters()
    if params.get("dbname") != "epl_raw_storage_v2":
        raise RuntimeError("Wrong database configured for registration evidence")
    params["options"] = "-c default_transaction_read_only=on -c statement_timeout=600000 -c lock_timeout=5000"
    params["application_name"] = "m46_registration_evidence_read_only"
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            if cur.fetchone() != ("epl_raw_storage_v2",):
                raise RuntimeError("Database identity changed")
            cur.execute("""SELECT snapshot_id FROM raw.snapshot_runs
                           WHERE repo_id=%s AND status='completed'
                           ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1""", (p.repo_id,))
            latest = cur.fetchone()
            if latest is None:
                raise RuntimeError("No completed baseline")
            cur.execute("""SELECT snapshot_id,repo_id,hf_revision,status,
                                  manifest_file_count,raw_scope_file_count,
                                  manifest_sha256,manifest_archive_path
                           FROM raw.snapshot_runs
                           WHERE repo_id=%s AND hf_revision=%s""",
                        (p.repo_id, p.target_revision))
            rows = cur.fetchall()
            if len(rows) != 1:
                raise RuntimeError("Target registration absent or ambiguous")
            return RegistrationRow(*rows[0], latest[0])


def build_registered_from_db(prepared: PreparedContext, *,
                             reader: Callable | None = None) -> RegisteredContext:
    """Typed, independent DB evidence. Does NOT publish registered checkpoint."""
    if not isinstance(prepared, PreparedContext):
        raise TypeError("PreparedContext required")
    r = (reader or _read_registration_row)(prepared)
    if not isinstance(r, RegistrationRow):
        raise TypeError("RegistrationRow required")
    p = prepared.planned
    if type(r.latest_completed_id) is not int or r.latest_completed_id != p.baseline_snapshot_id:
        raise RuntimeError("Latest completed baseline drifted before checkpoint")
    if type(r.snapshot_id) is not int or r.snapshot_id <= 0 or r.snapshot_id == p.baseline_snapshot_id:
        raise RuntimeError("Invalid target snapshot ID")
    if any(type(n) is not int or n <= 0 for n in (r.full_files, r.raw_files)):
        raise RuntimeError("Invalid target manifest counts")
    return build_registered(prepared, target_snapshot_id=r.snapshot_id,
                            db_repo_id=r.repo_id, db_revision=r.revision,
                            db_status=r.status, db_full_files=r.full_files,
                            db_raw_files=r.raw_files,
                            db_manifest_sha256=r.manifest_sha,
                            db_manifest_archive_path=r.manifest_archive)
