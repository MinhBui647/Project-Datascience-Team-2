#!/usr/bin/env python3
r"""M4.6.4c.8a | Plan/publish one deterministic, pinned FULL Hugging Face manifest.

Run BEFORE snapshot registration, with the SAME baseline, target revision and
SyncPlan expectations used by Candidate Download. Examples (PowerShell):

  .\.venv_v2\Scripts\python.exe scripts\tools\prepare_incremental_manifest.py `
      --baseline-snapshot ID --target-revision SHA40 --expected-fingerprint SHA256 `
      --expected-full-files FULL --expected-raw-files RAW `
      --expected-files CANDIDATES --expected-bytes BYTES --plan

  # Review SHA256 emitted by --plan. Repeat the same args, replacing --plan with:
      --publish --confirm-manifest-archive-only --expected-manifest-sha256 SHA256

--plan: DB reads + HF metadata queries only; NO local file or PostgreSQL writes.
--publish: validates the SAME plan and HF metadata, creates ONLY these files:
    reports/m4_6_manifests/<target SHA40>/hf_manifest_v2.json
    raw-archive-v2/manifests/<SHA256 prefix>/<SHA256>.json
Both objects are create-only (atomic hardlink); existing bytes are reverified,
never overwritten. A partial publication may be rerun with the same digest.

No file BODY downloads, cache writes, candidate archives, DB writes, snapshot
registration, or finalization. The JSON intentionally omits a wall-clock
'generated_at_utc' field to guarantee deterministic bytes for a pinned tree.
The original hf_manifest_v2.py used for exploratory reports is NOT changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.download_incremental_candidates import load_context
from scripts.tools.hf_manifest_v2 import detect_format, season_from_path, source_from_path

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RAW_PREFIXES = ("raw_data/", "2026_2027_new/")
ALLOWED_RAW_FORMATS = frozenset({"json", "csv", "csv.gz", "tar.gz"})
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--expected-fingerprint", required=True)
    p.add_argument("--expected-full-files", type=int, required=True)
    p.add_argument("--expected-raw-files", type=int, required=True)
    p.add_argument("--expected-files", type=int, required=True,
                   help="Number of NEW/CHANGED JSON candidate files")
    p.add_argument("--expected-bytes", type=int, required=True,
                   help="Sum of candidate JSON bytes")
    p.add_argument("--repo-id", default="Toibinguz/epl-premier-league-dataset")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--publish", action="store_true")
    p.add_argument("--confirm-manifest-archive-only", action="store_true")
    p.add_argument('--allow-csv-manifest-plan-only', action='store_true',
                   help='CSV read-only manifest preview; forbidden with --publish')
    p.add_argument('--allow-csv-manifest-publish', action='store_true',
                   help='Explicit CSV manifest publication opt-in; requires --publish and pinned SHA256')
    p.add_argument("--expected-manifest-sha256",
                   help="Required for --publish; take digest from --plan output")
    a = p.parse_args(argv)
    if a.publish != a.confirm_manifest_archive_only:
        p.error("--publish requires --confirm-manifest-archive-only; --plan forbids it")
    if a.allow_csv_manifest_plan_only and not a.plan:
        p.error('CSV manifest opt-in is read-only; --publish is not authorized')
    if a.allow_csv_manifest_publish and (not a.publish or not a.confirm_manifest_archive_only
                                         or a.allow_csv_manifest_plan_only):
        p.error('CSV manifest publication requires --publish --confirm-manifest-archive-only and forbids CSV plan-only')
    if not SHA40.fullmatch(a.target_revision) or not SHA256.fullmatch(a.expected_fingerprint):
        p.error("Target SHA40 and plan fingerprint SHA256 must be lowercase and complete")
    if any(type(n) is not int or n <= 0 for n in (
        a.baseline_snapshot, a.expected_full_files, a.expected_raw_files,
        a.expected_files, a.expected_bytes,
    )) or a.expected_raw_files > a.expected_full_files:
        p.error("Expected counts must be positive and RAW <= FULL")
    if not isinstance(a.repo_id, str) or not a.repo_id.strip():
        p.error("Repository cannot be blank")
    if a.publish and (not a.expected_manifest_sha256 or
                      not SHA256.fullmatch(a.expected_manifest_sha256)):
        p.error("--publish requires a pinned --expected-manifest-sha256 from --plan")
    if a.plan and a.expected_manifest_sha256 is not None:
        p.error("--plan does not accept --expected-manifest-sha256")
    return a


def safe_path(path: object) -> bool:
    return (isinstance(path, str) and 0 < len(path) <= 1024
            and not path.startswith("/") and "\\" not in path
            and all(seg not in ("", ".", "..") for seg in path.split("/"))
            and all(32 <= ord(ch) != 127 for ch in path))


def normalized_identity(value: object, regex: re.Pattern) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise RuntimeError("Malformed Git/LFS identity")
    value = value.strip().lower()
    if not regex.fullmatch(value):
        raise RuntimeError("Malformed Git/LFS identity")
    return value


def normalized_row(info: Any, revision: str) -> dict:
    """Convert a RepoFile into the existing full-manifest file entry schema."""
    path = getattr(info, "path", None)
    if not safe_path(path):
        raise RuntimeError(f"Unsafe HF manifest path: {path!r}")
    size = getattr(info, "size", None)
    if type(size) is not int or size < 0:
        raise RuntimeError("Invalid HF manifest file size: " + path)
    lfs = getattr(info, "lfs", None) or {}
    if not isinstance(lfs, dict):
        raise RuntimeError("Invalid HF LFS metadata: " + path)
    lfs_sha = normalized_identity(lfs.get("sha256"), SHA256)
    git_sha = normalized_identity(getattr(info, "blob_id", None), SHA40)
    if not git_sha and not lfs_sha:
        raise RuntimeError("HF manifest file lacks verifiable remote identity: " + path)
    fmt = detect_format(path)
    if path.startswith(RAW_PREFIXES):
        if fmt not in ALLOWED_RAW_FORMATS:
            raise RuntimeError("Unsupported Raw file format: " + path)
    elif not (path.startswith("clean/") or path in ("README.md", ".gitattributes")):
        raise RuntimeError("Unexpected file outside Raw scope: " + path)
    return {
        "relative_path": path,
        "format": fmt,
        "size_bytes": size,
        "source_hint": source_from_path(path),
        "season_hint": season_from_path(path) or "",
        "league_hint": "EPL (repository scope; not row-verified)",
        "group": path.split("/", 1)[0] if "/" in path else "<root>",
        "lfs_sha256": lfs_sha,
        "git_blob_id": git_sha,
        "hf_revision": revision,
    }


def build_manifest(context: Any, entries: Iterable, *, expected_full_files: int,
                   expected_raw_files: int) -> dict:
    """Pure function: canonical FULL manifest with strict Raw/SyncPlan agreement."""
    if not SHA40.fullmatch(context.target_revision):
        raise RuntimeError("Target revision is not pinned SHA40")
    if context.plan.count("REMOVED") or context.plan.count("REVIEW"):
        raise RuntimeError("Blocked SyncPlan categories")
    rows = [normalized_row(info, context.target_revision) for info in entries]
    rows.sort(key=lambda r: r["relative_path"])
    paths = [r["relative_path"] for r in rows]
    if len(set(paths)) != len(paths):
        raise RuntimeError("Duplicate HF manifest path")
    if len(rows) != expected_full_files:
        raise RuntimeError("FULL manifest file count differs from pinned expectation")
    raw = {r["relative_path"]: r for r in rows if r["relative_path"].startswith(RAW_PREFIXES)}
    if (len(raw) != expected_raw_files or len(raw) != len(context.target)
            or len(rows) - len(raw) != context.excluded
            or set(raw) != set(context.target)
            or context.plan.target_raw_count != len(raw)):
        raise RuntimeError("HF manifest/SyncPlan Raw paths or scope count drift")
    for path, row in raw.items():
        identity = context.target[path]
        expected_git = normalized_identity(identity.git_blob_id, SHA40)
        expected_lfs = normalized_identity(identity.lfs_sha256, SHA256)
        if (row["size_bytes"], row["git_blob_id"], row["lfs_sha256"]) != (
            identity.size, expected_git, expected_lfs
        ):
            raise RuntimeError("Manifest/SyncPlan content identity drift: " + path)
    return {
        "repo_id": context.repo_id,
        "repo_type": "dataset",
        "requested_revision": context.target_revision,
        "resolved_commit": context.target_revision,
        "file_count": len(rows),
        "total_size_bytes_metadata": sum(r["size_bytes"] for r in rows),
        "group_counts": dict(sorted(Counter(r["group"] for r in rows).items())),
        "format_counts": dict(sorted(Counter(r["format"] for r in rows).items())),
        "source_hint_counts": dict(sorted(Counter(r["source_hint"] for r in rows).items())),
        "unclassified_paths": [r["relative_path"] for r in rows
                               if r["source_hint"].startswith(("unknown", "unclassified"))],
        "notes": [
            "Deterministic full revision-pinned metadata manifest; no generated_at_utc field.",
            "File bodies are NOT downloaded; local Raw SHA256 cannot be derived here.",
            "Git blob IDs and remote LFS SHA256 come from Hugging Face metadata.",
            "Source/season/league fields are hints and not authoritative row labels.",
        ],
        "files": rows,
    }


def canonical_bytes(manifest: dict) -> bytes:
    """Stable UTF-8 bytes; no timestamps, filesystem paths, or unstable field order."""
    payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if not 0 < len(payload) <= MAX_MANIFEST_BYTES:
        raise RuntimeError("FULL HF manifest exceeds safety byte budget")
    return payload


def fetch_full_tree(context: Any, *, api=None, file_type=None) -> list:
    """Metadata only. Do not download any repository file bodies."""
    if api is None:
        from huggingface_hub import HfApi
        api = HfApi(token=os.getenv("HF_TOKEN") or None)
    if file_type is None:
        from huggingface_hub.hf_api import RepoFile
        file_type = RepoFile
    info = api.repo_info(repo_id=context.repo_id, repo_type="dataset",
                         revision=context.target_revision)
    if str(info.sha) != context.target_revision:
        raise RuntimeError("HF repo_info commit differs from pinned SHA40")
    entries = api.list_repo_tree(repo_id=context.repo_id, repo_type="dataset",
                                 revision=context.target_revision, recursive=True)
    return [x for x in entries if isinstance(x, file_type)]


def manifest_paths(revision: str, digest: str) -> tuple[str, str]:
    if not SHA40.fullmatch(revision) or not SHA256.fullmatch(digest):
        raise RuntimeError("Invalid manifest archive identity")
    return (f"reports/m4_6_manifests/{revision}/hf_manifest_v2.json",
            f"raw-archive-v2/manifests/{digest[:2]}/{digest}.json")


def guarded_destination(root: Path, relative: str, *, create_parents: bool = False) -> Path:
    """Forbid traversal, symlinks (including ancestors), and non-directory parents."""
    if not safe_path(relative):
        raise RuntimeError("Unsafe manifest destination path")
    root = Path(root).resolve(strict=True)
    current = root
    parts = relative.split("/")
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise RuntimeError("Unsafe directory component in manifest path")
        if create_parents and not current.exists():
            current.mkdir()  # never create in --plan mode
        if current.is_symlink():
            raise RuntimeError("Manifest directory became a symlink")
    dest = current / parts[-1]
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        raise RuntimeError("Unsafe manifest destination file")
    if not dest.resolve(strict=False).is_relative_to(root):
        raise RuntimeError("Manifest destination escapes project root")
    return dest


def verify_existing(dest: Path, payload: bytes) -> bool:
    if dest.is_symlink():
        raise RuntimeError("Existing manifest cannot be a symlink")
    if not dest.exists():
        return False
    if not dest.is_file() or dest.stat().st_size != len(payload):
        raise RuntimeError("Existing manifest differs from pinned bytes")
    if dest.read_bytes() != payload:
        raise RuntimeError("Existing manifest differs from pinned bytes")
    return True


def inspect_manifest_outputs(root: Path, revision: str, digest: str,
                             payload: bytes) -> tuple[str, str, tuple[str, str]]:
    if hashlib.sha256(payload).hexdigest() != digest:
        raise RuntimeError("Manifest payload SHA256 differs from pinned digest")
    report_rel, archive_rel = manifest_paths(revision, digest)
    report = guarded_destination(root, report_rel)
    archive = guarded_destination(root, archive_rel)
    statuses = tuple("ALREADY_VERIFIED" if verify_existing(p, payload) else "READY_NEW"
                     for p in (report, archive))
    return report_rel, archive_rel, statuses


def publish_create_only(root: Path, relative: str, payload: bytes) -> str:
    """Content verified before and after atomic create-only hardlink publication."""
    dest = guarded_destination(root, relative, create_parents=True)
    if verify_existing(dest, payload):
        return "ALREADY_VERIFIED"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=dest.parent, prefix=".manifest-stage-",
                                         delete=False) as out:
            staged = Path(out.name)
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        if staged.stat().st_size != len(payload) or staged.read_bytes() != payload:
            raise RuntimeError("Staged manifest bytes changed before publication")
        # No overwrite, not even on a retry or concurrent writer.
        dest = guarded_destination(root, relative)
        try:
            os.link(staged, dest)
            outcome = "CREATED_NEW"
        except FileExistsError:
            outcome = "ALREADY_VERIFIED"
        if not verify_existing(dest, payload):
            raise RuntimeError("Manifest publication verification failed")
        return outcome
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def publish_manifest(root: Path, revision: str, digest: str, payload: bytes) -> tuple[str, str, str, str]:
    """No DB writes. Partial (report-only) publication is safe to retry."""
    report_rel, archive_rel, _ = inspect_manifest_outputs(root, revision, digest, payload)
    report_action = publish_create_only(root, report_rel, payload)
    archive_action = publish_create_only(root, archive_rel, payload)
    _, _, statuses = inspect_manifest_outputs(root, revision, digest, payload)
    if statuses != ("ALREADY_VERIFIED", "ALREADY_VERIFIED"):
        raise RuntimeError("Manifest report/archive reconciliation failed")
    return report_rel, archive_rel, report_action, archive_action


def main(argv=None) -> int:
    args = parse_args(argv)
    # Keep JSON's original loader call unchanged for existing tests/callers.
    if args.allow_csv_manifest_plan_only or args.allow_csv_manifest_publish:
        context = load_context(argparse.Namespace(**vars(args), allow_csv_cache_only=True))
    else:
        context = load_context(args)  # PostgreSQL read-only + pinned HF metadata
    if (context.plan.target_raw_count != args.expected_raw_files or
            len(context.target) + context.excluded != args.expected_full_files):
        raise RuntimeError("Pinned full/raw manifest expectations changed")
    manifest = build_manifest(context, fetch_full_tree(context),
                              expected_full_files=args.expected_full_files,
                              expected_raw_files=args.expected_raw_files)
    payload = canonical_bytes(manifest)
    digest = hashlib.sha256(payload).hexdigest()
    report_rel, archive_rel, states = inspect_manifest_outputs(ROOT, args.target_revision,
                                                                digest, payload)
    print("=== M4.6.4c.8a | IMMUTABLE FULL MANIFEST PREPARATION ===")
    print("Mode:", "PLAN (read-only)" if args.plan else "PUBLISH (local immutable files only)")
    print("Repository:", args.repo_id)
    print("Baseline snapshot:", args.baseline_snapshot)
    print("Target revision:", args.target_revision)
    print("SyncPlan fingerprint:", args.expected_fingerprint)
    print("Full manifest paths:", args.expected_full_files)
    print("Raw-scope paths:", args.expected_raw_files)
    print("Manifest SHA256:", digest)
    print("Manifest bytes:", len(payload))
    print("Report:", report_rel, "|", states[0])
    print("Archive:", archive_rel, "|", states[1])
    if args.plan:
        print("Manifest writes: NONE | PostgreSQL writes: NONE | HF file-body downloads: NONE")
        print("M4.6.4c.8a MANIFEST PLAN: PASS (NOT APPROVAL TO PUBLISH)")
        return 0
    if digest != args.expected_manifest_sha256:
        raise RuntimeError("Manifest digest changed since --plan; refusing publication")
    _, _, report_action, archive_action = publish_manifest(ROOT, args.target_revision,
                                                           digest, payload)
    print("Published report:", report_action, "| immutable archive:", archive_action)
    print("PostgreSQL writes: NONE | Snapshot state unchanged | Raw archives unchanged")
    print("M4.6.4c.8a MANIFEST PUBLICATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
