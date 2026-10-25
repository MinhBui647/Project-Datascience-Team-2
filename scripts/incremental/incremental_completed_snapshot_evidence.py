"""Pure fail-closed evidence validator for completed M4.6 snapshots.

This module performs no IO and never authorizes writes.
It validates evidence collected by a separate read-only auditor.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompletionExpected:
    snapshot_id: int
    baseline_id: int
    repo_id: str
    revision: str
    baseline_revision: str
    full_files: int
    raw_files: int
    baseline_raw_files: int
    reused_files: int
    candidate_files: int
    candidate_records: int
    reused_records: int
    record_references: int
    manifest_sha256: str
    manifest_archive: str
    archive_bytes: int


@dataclass(frozen=True)
class CompletionObserved:
    baseline_row: tuple
    target_row: tuple
    observations: int
    reused_files: int
    candidate_files: int
    candidate_records: int
    reused_records: int
    physical_records: int
    tar_members: int
    archive_objects: int
    archive_bytes: int
    manifest_hash_verified: bool
    archive_hashes_verified: bool


def expected_from_registered(registered, *, archive_bytes: int):
    from scripts.tools.incremental_execution_context import RegisteredContext

    if not isinstance(registered, RegisteredContext):
        raise ValueError("Verified RegisteredContext required")

    if type(archive_bytes) is not int or archive_bytes < 0:
        raise ValueError("Invalid manifest Raw archive byte total")

    prepared = registered.prepared
    planned = prepared.planned

    return CompletionExpected(
        snapshot_id=registered.target_snapshot_id,
        baseline_id=planned.baseline_snapshot_id,
        repo_id=planned.repo_id,
        revision=planned.target_revision,
        baseline_revision=planned.baseline_revision,
        full_files=planned.expected_full_files,
        raw_files=planned.expected_raw_files,
        baseline_raw_files=planned.expected_baseline_files,
        reused_files=planned.expected_reuse,
        candidate_files=planned.expected_files,
        candidate_records=prepared.expected_records,
        reused_records=planned.expected_reused_records,
        record_references=prepared.expected_record_references,
        manifest_sha256=prepared.manifest_sha256,
        manifest_archive=prepared.manifest_archive,
        archive_bytes=archive_bytes,
    )


def validate_completed_evidence(expected, observed) -> str:
    """Validate independent completed-state evidence; raise on any mismatch."""

    if not isinstance(expected, CompletionExpected):
        raise ValueError("CompletionExpected required")

    if not isinstance(observed, CompletionObserved):
        raise ValueError("CompletionObserved required")

    expected_baseline = (
        expected.baseline_id,
        expected.repo_id,
        expected.baseline_revision,
        "completed",
        expected.baseline_raw_files,
    )

    expected_target = (
        expected.snapshot_id,
        expected.repo_id,
        expected.revision,
        "completed",
        expected.full_files,
        expected.raw_files,
        expected.manifest_sha256,
        expected.manifest_archive,
    )

    if type(observed.baseline_row) is not tuple:
        raise ValueError("Malformed baseline database row")

    if type(observed.target_row) is not tuple:
        raise ValueError("Malformed target database row")

    if observed.baseline_row != expected_baseline:
        raise ValueError("Baseline snapshot identity/status/count mismatch")

    if observed.target_row != expected_target:
        raise ValueError("Completed target identity/status/manifest mismatch")

    numeric = {
        "observations": observed.observations,
        "reused_files": observed.reused_files,
        "candidate_files": observed.candidate_files,
        "candidate_records": observed.candidate_records,
        "reused_records": observed.reused_records,
        "physical_records": observed.physical_records,
        "tar_members": observed.tar_members,
        "archive_objects": observed.archive_objects,
        "archive_bytes": observed.archive_bytes,
    }

    if any(type(value) is not int or value < 0 for value in numeric.values()):
        raise ValueError("Invalid or negative completed-snapshot counts")

    comparisons = {
        "observations": expected.raw_files,
        "reused_files": expected.reused_files,
        "candidate_files": expected.candidate_files,
        "candidate_records": expected.candidate_records,
        "reused_records": expected.reused_records,
        "physical_records": expected.record_references,
        "archive_objects": expected.raw_files,
        "archive_bytes": expected.archive_bytes,
    }

    for field, value in comparisons.items():
        if numeric[field] != value:
            raise ValueError(
                f"Completed-snapshot {field} mismatch: "
                f"{numeric[field]} != {value}"
            )

    if observed.reused_files + observed.candidate_files != observed.observations:
        raise ValueError("Observation coverage inconsistent")

    if observed.reused_records + observed.candidate_records != observed.physical_records:
        raise ValueError("Raw record accounting inconsistent")

    if observed.tar_members > observed.physical_records:
        raise ValueError("Invalid TAR member count")

    if observed.manifest_hash_verified is not True:
        raise ValueError("Independent manifest hash evidence missing")

    if observed.archive_hashes_verified is not True:
        raise ValueError("Independent full archive rehash evidence missing")

    return (
        f"Completed snapshot #{expected.snapshot_id}; "
        f"observations={observed.observations}; "
        f"records={observed.physical_records}; "
        f"archive_objects={observed.archive_objects}; "
        f"archive_bytes={observed.archive_bytes}"
    )
