"""M5.5.4b: fail-closed CSV/JSON DB-stage writer command intentions.

A WriterIntent is NOT write authorization. No execution, filesystem, DB,
Hugging Face or checkpoint mutation is performed by this module.

Call only with an argv tuple independently validated by the M4 read-only
Execution Contract. The future coordinator must separately re-audit its
stage, pinned context, evidence and explicit single-stage authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from typing import Sequence

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")

# EXACT existing M4 tools/flags. No new ingestion or SQL implementation.
# stage -> (plan module, write mode, confirmation, CSV opt-in)
STAGE_WRITERS: dict[str, tuple[str, str, str, str]] = {
    "SNAPSHOT_REGISTRATION": (
        "register_incremental_snapshot", "--register", "--confirm-snapshot-only",
        "--allow-csv-registration-write",
    ),
    "REUSE_LINKING": (
        "populate_incremental_reuse", "--link", "--confirm-reuse-only",
        "--allow-csv-reuse-link",
    ),
    "CANDIDATE_INGESTION": (
        "ingest_incremental_candidates", "--apply", "--confirm-candidate-write",
        "--allow-csv-candidate-write",
    ),
    "MANIFEST_REGISTRATION": (
        "register_incremental_manifest", "--commit", "--confirm-manifest-only",
        "--allow-csv-manifest-registration-write",
    ),
    "FINALIZATION": (
        "finalize_incremental_snapshot", "--commit", "--confirm-complete",
        "--allow-csv-finalization-write",
    ),
}

# Mirrors the option/value allowlist of M4's read-only executor.
ALLOWED_PLAN_KEYS = frozenset({
    "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
    "--repo-id", "--expected-files", "--expected-bytes",
    "--expected-records", "--expected-baseline-files", "--expected-full-files",
    "--expected-raw-files", "--expected-reuse", "--target-snapshot-id",
    "--expected-baseline-records", "--expected-reused-records",
    "--manifest-report", "--manifest-sha256", "--cache-dir",
})
REQUIRED_COMMON = frozenset({
    "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
    "--repo-id", "--expected-files", "--expected-bytes",
    "--expected-records", "--expected-baseline-files", "--expected-full-files",
    "--expected-raw-files", "--expected-reuse",
})
REQUIRES_TARGET = frozenset({
    "REUSE_LINKING", "CANDIDATE_INGESTION", "MANIFEST_REGISTRATION", "FINALIZATION",
})
REQUIRES_RECORD_COUNTS = REQUIRES_TARGET
REQUIRES_MANIFEST = frozenset({"MANIFEST_REGISTRATION", "FINALIZATION"})


@dataclass(frozen=True)
class WriterIntent:
    stage: str
    module: str
    argv: tuple[str, ...]
    csv_flag: str
    write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.write_authorized:
            raise ValueError("WriterIntent may never grant write authorization")


def build_writer_intent(stage: str, read_only_argv: Sequence[str]) -> WriterIntent:
    """Convert a VERIFIED read-only preview shape to an *unapproved* write intent.

    The caller must first validate the entire StageReview with the existing
    M4 validate_read_only_commands(). That upstream validation and an
    independent write permission check remain mandatory before any use.
    """
    if stage not in STAGE_WRITERS:
        raise ValueError("Unsupported/not-yet-integrated DB write stage")
    if not isinstance(read_only_argv, (tuple, list)) or any(
        not isinstance(v, str) for v in read_only_argv
    ):
        raise TypeError("Read-only argv must be a sequence of strings")
    module, mode, confirmation, csv_flag = STAGE_WRITERS[stage]
    argv = tuple(read_only_argv)
    if (len(argv) < 6 or argv[:3] != (sys.executable, "-m", f"scripts.tools.{module}")
            or argv[-1] != "--plan" or argv.count("--plan") != 1):
        raise ValueError("Unexpected or non-read-only M4 module invocation")
    fields = argv[3:-1]
    if len(fields) % 2:
        raise ValueError("Expected pairs of named CLI options and values")
    flags = fields[::2]
    values = fields[1::2]
    if (len(flags) != len(set(flags)) or
            not set(flags).issubset(ALLOWED_PLAN_KEYS) or
            not REQUIRED_COMMON.issubset(flags) or
            any(not value or value.startswith("-") for value in values)):
        raise ValueError("Missing/duplicate/unsafe pinned M4 plan arguments")
    opts = dict(zip(flags, values))
    if not SHA40.fullmatch(opts["--target-revision"]):
        raise ValueError("Target revision must be pinned SHA40")
    if not SHA256.fullmatch(opts["--expected-fingerprint"]):
        raise ValueError("Fingerprint must be SHA256")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", opts["--repo-id"]):
        raise ValueError("Invalid repository ID")
    if not opts["--baseline-snapshot"].isdigit() or int(opts["--baseline-snapshot"]) <= 0:
        raise ValueError("Invalid baseline snapshot")
    if stage in REQUIRES_TARGET:
        if ("--target-snapshot-id" not in opts or
                not opts["--target-snapshot-id"].isdigit() or
                int(opts["--target-snapshot-id"]) <= 0):
            raise ValueError("Stage requires pinned target snapshot ID")
    if stage in REQUIRES_RECORD_COUNTS:
        if not {"--expected-baseline-records", "--expected-reused-records"}.issubset(opts):
            raise ValueError("Stage requires baseline and reused record totals")
    if stage == "CANDIDATE_INGESTION" and "--cache-dir" not in opts:
        raise ValueError("Candidate ingestion requires an explicit pinned cache-dir")
    if stage in REQUIRES_MANIFEST:
        if not {"--manifest-report", "--manifest-sha256"}.issubset(opts):
            raise ValueError("Stage requires pinned immutable manifest")
        if not SHA256.fullmatch(opts["--manifest-sha256"]):
            raise ValueError("Manifest SHA256 malformed")
        manifest = opts["--manifest-report"].replace("\\", "/")
        if (not manifest.startswith("reports/") or ".." in manifest.split("/")
                or not manifest.endswith(".json")):
            raise ValueError("Unsafe manifest report path")
    # We only form an immutable proposed argv, NEVER spawn a process.
    write_argv = argv[:-1] + (mode, confirmation, csv_flag)
    return WriterIntent(stage, f"scripts.tools.{module}", write_argv, csv_flag)
