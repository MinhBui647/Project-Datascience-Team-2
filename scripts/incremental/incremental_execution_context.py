#!/usr/bin/env python3
"""M4.6.4d.2 | Immutable, versioned incremental Execution Context (stdlib only).

Three stages: Planned -> Prepared -> Registered. Pure constructors validate
cross-stage invariants. publish_checkpoint() alone creates local, immutable
JSON checkpoints under reports/m4_6_runs/<revision>/; it never overwrites.
--verify-checkpoints is strictly read-only. No HF, PostgreSQL, cache, archive,
or ingestion operations are performed by this module.

IMPORTANT: checkpoints are coordination records, NOT write authorization.
Each downstream execution module must independently redo its own DB/HF/content
checks and require explicit per-operation confirmation flags.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")
SUPPORTED_CANDIDATE_FORMATS = frozenset(("json", "csv"))
VERSION = 1
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024
STAGES = ("planned", "prepared", "registered")


def _posint(value: Any, name: str, *, allow_zero: bool = False) -> None:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'} integer")


def _sha(value: Any, pattern: re.Pattern, name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid {name}")


def candidate_format(path: str) -> str | None:
    """Infer from a verified Raw path without changing v1 checkpoint DTO fields."""
    if not isinstance(path, str):
        return None
    if path.endswith(".json"):
        return "json"
    if path.endswith(".csv"):
        return "csv"
    return None


def _safe_raw_path(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 1024
            and value.startswith(RAW_PREFIXES)
            and candidate_format(value) in SUPPORTED_CANDIDATE_FORMATS
            and "\\" not in value and not value.startswith("/")
            and all(part not in ("", ".", "..") for part in value.split("/"))
            and all(32 <= ord(char) != 127 for char in value))


@dataclass(frozen=True)
class CandidateSpec:
    path: str
    category: str
    expected_bytes: int
    source_code: str
    git_blob_id: str | None
    lfs_sha256: str | None

    def __post_init__(self) -> None:
        if not _safe_raw_path(self.path) or self.category not in ("NEW", "CHANGED"):
            raise ValueError("Invalid NEW/CHANGED JSON/CSV candidate")
        _posint(self.expected_bytes, "candidate bytes")
        if not isinstance(self.source_code, str) or not SOURCE.fullmatch(self.source_code):
            raise ValueError("Invalid source_code")
        if self.git_blob_id is None and self.lfs_sha256 is None:
            raise ValueError("Candidate has no Git/LFS content identity")
        if self.git_blob_id is not None:
            _sha(self.git_blob_id, SHA40, "Git blob ID")
        if self.lfs_sha256 is not None:
            _sha(self.lfs_sha256, SHA256, "LFS SHA256")


@dataclass(frozen=True)
class CandidateReceipt:
    """An output of a separately run, verified candidate cache/archive gate."""
    path: str
    size_bytes: int
    content_sha256: str
    records: int

    def __post_init__(self) -> None:
        if not _safe_raw_path(self.path):
            raise ValueError("Invalid candidate receipt path")
        _posint(self.size_bytes, "candidate receipt bytes")
        _posint(self.records, "candidate receipt records")
        _sha(self.content_sha256, SHA256, "candidate content SHA256")


@dataclass(frozen=True)
class PlannedContext:
    repo_id: str
    baseline_snapshot_id: int
    baseline_revision: str
    target_revision: str
    plan_fingerprint: str
    expected_baseline_files: int
    expected_full_files: int
    expected_raw_files: int
    expected_reuse: int
    expected_files: int
    expected_bytes: int
    expected_baseline_records: int
    expected_reused_records: int
    candidates: tuple[CandidateSpec, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.repo_id, str) or not self.repo_id.strip() or len(self.repo_id) > 300:
            raise ValueError("Invalid repo_id")
        _posint(self.baseline_snapshot_id, "baseline snapshot ID")
        _sha(self.baseline_revision, SHA40, "baseline revision")
        _sha(self.target_revision, SHA40, "target revision")
        _sha(self.plan_fingerprint, SHA256, "SyncPlan fingerprint")
        if self.target_revision == self.baseline_revision:
            raise ValueError("Target must differ from baseline")
        for name in ("expected_baseline_files", "expected_full_files", "expected_raw_files",
                     "expected_files", "expected_bytes", "expected_baseline_records"):
            _posint(getattr(self, name), name)
        for name in ("expected_reuse", "expected_reused_records"):
            _posint(getattr(self, name), name, allow_zero=True)
        if (self.expected_full_files < self.expected_raw_files
                or self.expected_reuse + self.expected_files != self.expected_raw_files
                or self.expected_reused_records > self.expected_baseline_records):
            raise ValueError("Inconsistent snapshot file/record accounting")
        if type(self.candidates) is not tuple or len(self.candidates) != self.expected_files:
            raise ValueError("Candidate collection/count mismatch")
        if any(not isinstance(item, CandidateSpec) for item in self.candidates):
            raise ValueError("Invalid candidate specification")
        paths = [x.path for x in self.candidates]
        if paths != sorted(set(paths)):
            raise ValueError("Candidate paths must be unique and sorted")
        if sum(x.expected_bytes for x in self.candidates) != self.expected_bytes:
            raise ValueError("Candidate byte total mismatch")


@dataclass(frozen=True)
class PreparedContext:
    planned: PlannedContext
    candidate_receipts: tuple[CandidateReceipt, ...]
    expected_records: int
    expected_record_references: int
    manifest_sha256: str
    manifest_report: str
    manifest_archive: str

    def __post_init__(self) -> None:
        if not isinstance(self.planned, PlannedContext):
            raise ValueError("Prepared stage requires planned context")
        _posint(self.expected_records, "candidate raw records")
        _posint(self.expected_record_references, "target record references")
        _sha(self.manifest_sha256, SHA256, "manifest SHA256")
        expected_report = f"reports/m4_6_manifests/{self.planned.target_revision}/hf_manifest_v2.json"
        expected_archive = ("raw-archive-v2/manifests/"
                            f"{self.manifest_sha256[:2]}/{self.manifest_sha256}.json")
        if (self.manifest_report, self.manifest_archive) != (expected_report, expected_archive):
            raise ValueError("Manifest paths are not canonical")
        if type(self.candidate_receipts) is not tuple:
            raise ValueError("Candidate receipts must be immutable tuple")
        spec = {c.path: c for c in self.planned.candidates}
        receipts = self.candidate_receipts
        paths = [c.path for c in receipts]
        if paths != sorted(set(spec)) or len(receipts) != self.planned.expected_files:
            raise ValueError("Candidate receipts do not cover pinned plan")
        if any(not isinstance(r, CandidateReceipt) or r.size_bytes != spec[r.path].expected_bytes
               or (spec[r.path].lfs_sha256 is not None
                   and r.content_sha256 != spec[r.path].lfs_sha256)
               for r in receipts):
            raise ValueError("Candidate receipt identity/byte mismatch")
        if (sum(r.records for r in receipts) != self.expected_records
                or self.expected_record_references !=
                self.planned.expected_reused_records + self.expected_records):
            raise ValueError("Candidate/target record accounting mismatch")


@dataclass(frozen=True)
class RegisteredContext:
    prepared: PreparedContext
    target_snapshot_id: int
    target_status: str = "in_progress"

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedContext):
            raise ValueError("Registered stage requires prepared context")
        _posint(self.target_snapshot_id, "target snapshot ID")
        if self.target_snapshot_id == self.prepared.planned.baseline_snapshot_id:
            raise ValueError("Target snapshot ID equals baseline")
        if self.target_status != "in_progress":
            raise ValueError("Registration checkpoint requires in_progress")


Context = PlannedContext | PreparedContext | RegisteredContext


def build_planned(download_context: Any, *, expected_baseline_files: int,
                  expected_full_files: int, allow_csv_context: bool = False) -> PlannedContext:
    """Adapt existing DownloadContext/SyncPlan, without DB/HF calls.

    expected_baseline_files must come from a separately verified baseline read;
    this adapter does not infer it from target changes.
    """
    if type(allow_csv_context) is not bool:
        raise ValueError("Invalid CSV context opt-in flag")
    plan = download_context.plan
    if plan.count("REMOVED") or plan.count("REVIEW"):
        raise ValueError("REMOVED/REVIEW actions blocked by incremental context")
    actions = tuple(plan.actions)
    picked = tuple(sorted((a for a in actions if a.category in ("NEW", "CHANGED")),
                          key=lambda a: a.path))
    if (len({a.path for a in actions}) != len(actions)
            or len(actions) != plan.target_raw_count
            or plan.count("UNCHANGED") + len(picked) != plan.target_raw_count
            or {a.path for a in picked} != {a.path for a in download_context.actions}
            or len(download_context.target) != plan.target_raw_count
            or plan.baseline_records - plan.reused_records != plan.superseded_records
            or plan.candidate_bytes != sum(a.expected_bytes for a in picked)):
        raise ValueError("Invalid or incomplete SyncPlan/DownloadContext")
    specs = []
    for action in picked:
        identity = download_context.target[action.path]
        fmt = candidate_format(action.path)
        if (action.kind != "UPSERT_VERSION_AFTER_REHASH"
                or fmt not in SUPPORTED_CANDIDATE_FORMATS
                or action.file_format != fmt
                or (fmt == "csv" and not allow_csv_context)
                or action.expected_bytes != identity.size
                or action.git_blob_id != identity.git_blob_id
                or action.lfs_sha256 != identity.lfs_sha256):
            raise ValueError("Candidate metadata differs from pinned HF tree")
        specs.append(CandidateSpec(action.path, action.category, action.expected_bytes,
                                   action.source_code, action.git_blob_id,
                                   action.lfs_sha256))
    if expected_full_files != len(download_context.target) + download_context.excluded:
        raise ValueError("FULL HF file count does not match target metadata")
    return PlannedContext(
        repo_id=download_context.repo_id,
        baseline_snapshot_id=download_context.baseline_snapshot,
        baseline_revision=download_context.baseline_revision,
        target_revision=download_context.target_revision,
        plan_fingerprint=plan.fingerprint,
        expected_baseline_files=expected_baseline_files,
        expected_full_files=expected_full_files,
        expected_raw_files=plan.target_raw_count,
        expected_reuse=plan.count("UNCHANGED"),
        expected_files=len(picked), expected_bytes=plan.candidate_bytes,
        expected_baseline_records=plan.baseline_records,
        expected_reused_records=plan.reused_records,
        candidates=tuple(specs),
    )


def build_prepared(planned: PlannedContext, verified_receipts: tuple[CandidateReceipt, ...],
                   *, manifest_bytes: bytes) -> PreparedContext:
    """Bind independently validated candidate evidence to an exact FULL manifest.

    Does not validate candidate file bodies itself: downstream gates MUST rehash
    cache/archive and recheck DB. Manifest is re-parsed and cross-checked here.
    """
    if not isinstance(planned, PlannedContext) or not isinstance(manifest_bytes, bytes):
        raise ValueError("Prepared context requires planned context and manifest bytes")
    if not 0 < len(manifest_bytes) <= 64 * 1024 * 1024:
        raise ValueError("Manifest byte budget exceeded")
    try:
        manifest = json.loads(manifest_bytes)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid manifest JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be JSON object")
    if (manifest.get("repo_id"), manifest.get("repo_type"),
            manifest.get("resolved_commit"), manifest.get("file_count")) != (
            planned.repo_id, "dataset", planned.target_revision, planned.expected_full_files):
        raise ValueError("Manifest repository/revision/file count drift")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != planned.expected_full_files:
        raise ValueError("Full manifest file rows missing")
    paths: set[str] = set()
    raw_count = 0
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("relative_path"), str):
            raise ValueError("Invalid manifest file row")
        path = row["relative_path"]
        if (not path or path.startswith("/") or "\\" in path or len(path) > 1024
                or any(seg in ("", ".", "..") for seg in path.split("/"))
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or path in paths or row.get("hf_revision") != planned.target_revision
                or type(row.get("size_bytes")) is not int or row["size_bytes"] < 0):
            raise ValueError("Duplicate/unsafe/inconsistent full manifest row")
        paths.add(path)
        if path.startswith(RAW_PREFIXES):
            raw_count += 1
    if raw_count != planned.expected_raw_files:
        raise ValueError("Manifest Raw file scope mismatch")
    spec = {x.path: x for x in planned.candidates}
    by_path = {x["relative_path"]: x for x in files}
    for path, item in spec.items():
        m = by_path.get(path)
        if (m is None or m.get("format") != candidate_format(path) or
                (m["size_bytes"], (m.get("git_blob_id") or "").lower(),
                 (m.get("lfs_sha256") or "").lower()) !=
                (item.expected_bytes, item.git_blob_id or "", item.lfs_sha256 or "")):
            raise ValueError("Manifest candidate identity differs from plan")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    return PreparedContext(planned, verified_receipts,
                           sum(x.records for x in verified_receipts),
                           planned.expected_reused_records + sum(x.records for x in verified_receipts),
                           digest, f"reports/m4_6_manifests/{planned.target_revision}/hf_manifest_v2.json",
                           f"raw-archive-v2/manifests/{digest[:2]}/{digest}.json")


def build_registered(prepared: PreparedContext, *, target_snapshot_id: int,
                     db_repo_id: str, db_revision: str, db_status: str,
                     db_full_files: int, db_raw_files: int,
                     db_manifest_sha256: str | None = None,
                     db_manifest_archive_path: str | None = None) -> RegisteredContext:
    """Use values independently read from the PostgreSQL target snapshot row."""
    if not isinstance(prepared, PreparedContext):
        raise ValueError("Expected PreparedContext")
    plan = prepared.planned
    if ((db_repo_id, db_revision, db_status, db_full_files, db_raw_files,
         db_manifest_sha256, db_manifest_archive_path) !=
            (plan.repo_id, plan.target_revision, "in_progress", plan.expected_full_files,
             plan.expected_raw_files, None, None)):
        raise ValueError("Target snapshot DB row does not match pinned prepared context")
    return RegisteredContext(prepared, target_snapshot_id)


def cli_arguments(context: Context, step: str) -> tuple[str, ...]:
    """Return verified expectation flags only; never return --commit/--apply flags."""
    if not isinstance(context, (PlannedContext, PreparedContext, RegisteredContext)):
        raise ValueError("Invalid Execution Context")
    prepared = (context.prepared if isinstance(context, RegisteredContext)
                else context if isinstance(context, PreparedContext) else None)
    plan = prepared.planned if prepared else context
    registered = context if isinstance(context, RegisteredContext) else None
    supported = {
        "download", "archive_promotion", "manifest_preparation", "snapshot_preparation",
        "snapshot_registration", "reuse", "candidate_planner", "candidate_ingestion",
        "manifest_registration", "finalization",
    }
    if step not in supported:
        raise ValueError("Unknown execution step")
    if step in ("archive_promotion", "snapshot_preparation", "snapshot_registration", "reuse", "candidate_planner",
                "candidate_ingestion", "manifest_registration", "finalization") and prepared is None:
        raise ValueError("Step requires PreparedContext")
    if step in ("reuse", "candidate_planner", "candidate_ingestion",
                "manifest_registration", "finalization") and registered is None:
        raise ValueError("Step requires RegisteredContext")
    flags = ["--baseline-snapshot", str(plan.baseline_snapshot_id),
             "--target-revision", plan.target_revision,
             "--expected-fingerprint", plan.plan_fingerprint,
             "--repo-id", plan.repo_id]
    if step != "snapshot_preparation":
        if step not in ("download", "archive_promotion", "manifest_preparation"):
            flags += ["--expected-baseline-files", str(plan.expected_baseline_files)]
        if step not in ("download", "archive_promotion"):
            flags += ["--expected-full-files", str(plan.expected_full_files),
                      "--expected-raw-files", str(plan.expected_raw_files)]
    if step in ("snapshot_preparation", "snapshot_registration", "reuse", "candidate_planner",
                "candidate_ingestion", "manifest_registration", "finalization"):
        flags += ["--expected-reuse", str(plan.expected_reuse)]
    flags += ["--expected-files", str(plan.expected_files),
              "--expected-bytes", str(plan.expected_bytes)]
    if prepared is not None and step not in ("manifest_preparation",):
        if step in ("archive_promotion", "snapshot_preparation", "snapshot_registration", "reuse", "candidate_planner",
                    "candidate_ingestion", "manifest_registration", "finalization"):
            flags += ["--expected-records", str(prepared.expected_records)]
    if step in ("reuse", "candidate_planner", "candidate_ingestion",
                "manifest_registration", "finalization"):
        flags += ["--target-snapshot-id", str(registered.target_snapshot_id),
                  "--expected-baseline-records", str(plan.expected_baseline_records),
                  "--expected-reused-records", str(plan.expected_reused_records)]
    if step in ("manifest_registration", "finalization"):
        flags += ["--manifest-report", prepared.manifest_report,
                  "--manifest-sha256", prepared.manifest_sha256]
    return tuple(flags)


def _payload(context: Context) -> dict:
    return asdict(context)


def _stage(context: Context) -> str:
    if isinstance(context, RegisteredContext):
        return "registered"
    if isinstance(context, PreparedContext):
        return "prepared"
    if isinstance(context, PlannedContext):
        return "planned"
    raise TypeError("Unsupported context type")


def _parent(context: Context) -> Context | None:
    return context.prepared if isinstance(context, RegisteredContext) else (
        context.planned if isinstance(context, PreparedContext) else None)


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def checkpoint_envelope(context: Context) -> dict:
    parent = _parent(context)
    contents = {"schema_version": VERSION, "stage": _stage(context),
                "parent_sha256": checkpoint_digest(parent) if parent is not None else None,
                "payload": _payload(context)}
    digest = hashlib.sha256(_canonical(contents)).hexdigest()
    return {**contents, "checkpoint_sha256": digest}


def checkpoint_digest(context: Context) -> str:
    return checkpoint_envelope(context)["checkpoint_sha256"]


def _checkpoint_rel(revision: str, stage: str) -> str:
    _sha(revision, SHA40, "target revision")
    if stage not in STAGES:
        raise ValueError("Unknown checkpoint stage")
    return f"reports/m4_6_runs/{revision}/{stage}.json"


def _guard_path(root: Path, relative: str, *, create_parents: bool = False) -> Path:
    root = Path(root).resolve(strict=True)
    current = root
    for component in relative.split("/")[:-1]:
        current = current / component
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise RuntimeError("Unsafe checkpoint directory or symlink")
        if create_parents and not current.exists():
            current.mkdir()
        if current.is_symlink():
            raise RuntimeError("Checkpoint path changed to symlink")
    dest = current / relative.split("/")[-1]
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        raise RuntimeError("Unsafe checkpoint file or symlink")
    if not dest.resolve(strict=False).is_relative_to(root):
        raise RuntimeError("Checkpoint escaped project root")
    return dest


def _decode_exact(cls, obj: Any, **overrides):
    if not isinstance(obj, dict) or set(obj) != {f.name for f in fields(cls)}:
        raise ValueError(f"Invalid {cls.__name__} checkpoint payload fields")
    return cls(**{**obj, **overrides})


def _decode(stage: str, payload: Any) -> Context:
    if stage == "planned":
        items = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Candidate specs missing")
        return _decode_exact(PlannedContext, payload,
                             candidates=tuple(_decode_exact(CandidateSpec, x) for x in items))
    if stage == "prepared":
        if not isinstance(payload, dict) or not isinstance(payload.get("planned"), dict):
            raise ValueError("Missing planned payload")
        receipts = payload.get("candidate_receipts")
        if not isinstance(receipts, list):
            raise ValueError("Missing candidate receipts")
        return _decode_exact(PreparedContext, payload,
                             planned=_decode("planned", payload["planned"]),
                             candidate_receipts=tuple(_decode_exact(CandidateReceipt, x) for x in receipts))
    if stage == "registered":
        if not isinstance(payload, dict) or not isinstance(payload.get("prepared"), dict):
            raise ValueError("Missing prepared payload")
        return _decode_exact(RegisteredContext, payload,
                             prepared=_decode("prepared", payload["prepared"]))
    raise ValueError("Unsupported checkpoint stage")


def read_checkpoint(root: Path, revision: str, stage: str) -> Context:
    """Read + cryptographically check contents AND the entire parent checkpoint chain."""
    dest = _guard_path(root, _checkpoint_rel(revision, stage))
    data = dest.read_bytes()
    if not 0 < len(data) <= MAX_CHECKPOINT_BYTES:
        raise RuntimeError("Checkpoint too large or empty")
    try:
        envelope = json.loads(data)
        if (not isinstance(envelope, dict) or set(envelope) !=
                {"schema_version", "stage", "parent_sha256", "payload", "checkpoint_sha256"}):
            raise ValueError("Checkpoint envelope shape mismatch")
        if envelope["schema_version"] != VERSION or envelope["stage"] != stage:
            raise ValueError("Checkpoint schema version or stage mismatch")
        context = _decode(stage, envelope["payload"])
        if (context.prepared.planned.target_revision if isinstance(context, RegisteredContext)
            else context.planned.target_revision if isinstance(context, PreparedContext)
            else context.target_revision) != revision:
            raise ValueError("Checkpoint revision directory mismatch")
        if _canonical(checkpoint_envelope(context)) != data:
            raise ValueError("Checkpoint is noncanonical, corrupt or tampered")
        if stage != "planned":
            parent_stage = STAGES[STAGES.index(stage) - 1]
            parent_context = read_checkpoint(root, revision, parent_stage)
            if (envelope["parent_sha256"] != checkpoint_digest(parent_context)
                    or _parent(context) != parent_context):
                raise ValueError("Checkpoint parent digest/payload mismatch")
        elif envelope["parent_sha256"] is not None:
            raise ValueError("Planned checkpoint may not have a parent")
        return context
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Invalid checkpoint: " + str(dest)) from exc


def publish_checkpoint(root: Path, context: Context) -> tuple[str, str]:
    """Create one checkpoint atomically, never replacing existing content.

    For Prepared/Registered stages, verifies the exact previous checkpoint first.
    Caller must provide independently validated evidence; a persisted checkpoint
    does not bypass write guards of any execution module.
    """
    stage = _stage(context)
    plan = context.prepared.planned if isinstance(context, RegisteredContext) else (
        context.planned if isinstance(context, PreparedContext) else context)
    revision = plan.target_revision
    if stage != "planned":
        parent = _parent(context)
        stored = read_checkpoint(root, revision, STAGES[STAGES.index(stage) - 1])
        if stored != parent:
            raise RuntimeError("Cannot publish checkpoint against a changed predecessor")
    rel = _checkpoint_rel(revision, stage)
    data = _canonical(checkpoint_envelope(context))
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise RuntimeError("Checkpoint exceeds size budget")
    dest = _guard_path(root, rel, create_parents=True)
    if dest.exists():
        if dest.read_bytes() != data:
            raise RuntimeError("Existing checkpoint differs; refusing overwrite")
        read_checkpoint(root, revision, stage)
        return rel, "ALREADY_VERIFIED"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".context-stage-",
                                         mode="wb", delete=False) as output:
            staged = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if staged.read_bytes() != data:
            raise RuntimeError("Staged checkpoint changed")
        dest = _guard_path(root, rel)
        try:
            os.link(staged, dest)
            result = "CREATED_NEW"
        except FileExistsError:
            result = "ALREADY_VERIFIED"
        if dest.read_bytes() != data:
            raise RuntimeError("Checkpoint collision or corruption")
        read_checkpoint(root, revision, stage)
        return rel, result
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-checkpoints", action="store_true", required=True,
                        help="Read-only verification of existing local checkpoints")
    parser.add_argument("--target-revision", required=True)
    args = parser.parse_args(argv)
    _sha(args.target_revision, SHA40, "target revision")
    for stage in STAGES:
        rel = _checkpoint_rel(args.target_revision, stage)
        if not _guard_path(ROOT, rel).is_file():
            print(f"{stage.upper()}: NOT CREATED | subsequent stages not checked")
            break
        context = read_checkpoint(ROOT, args.target_revision, stage)
        print(f"{stage.upper()}: VERIFIED | SHA256={checkpoint_digest(context)}")
    print("HF requests: NONE | PostgreSQL writes: NONE | Archive writes: NONE")
    print("M4.6.4d.2 EXECUTION CONTEXT CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
