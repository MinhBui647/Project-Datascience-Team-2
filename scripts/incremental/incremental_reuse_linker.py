#!/usr/bin/env python3
"""M4.6.3c.1: link unchanged file versions into an in-progress snapshot.

Building block only, NO CLI/commit/snapshot creation. Caller must pin HF revision,
rebuild and check SyncPlan, create target snapshot safely, and own SQL transaction.
This module does NOT ingest records, download files, archive, or finalize.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import re

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.incremental_sync import SyncPlan

SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True)
class ReuseLink:
    relative_path: str
    file_id: int
    size_bytes: int
    git_blob_id: str | None
    lfs_sha256: str | None


def build_reuse_links(plan: SyncPlan, target: Mapping[str, FileIdentity]) -> tuple[ReuseLink, ...]:
    """Pure input contract. Require every target path be covered exactly once."""
    if not isinstance(plan, SyncPlan) or not isinstance(target, Mapping):
        raise ValueError("Invalid plan or target manifest")
    if plan.target_raw_count != len(target) or len(plan.actions) != len(target):
        # This initial linker only supports zero removed paths, the current batch.
        raise ValueError("Target/plan counts disagree or removed paths present")
    all_paths = [a.path for a in plan.actions]
    if len(set(all_paths)) != len(all_paths) or set(all_paths) != set(target):
        raise ValueError("Plan and target manifest paths disagree")
    links = []
    for action in plan.actions:
        if action.category == "REMOVED":
            raise ValueError("REMOVED paths require a separate notification workflow")
        if action.kind != "REUSE_FILE_ID":
            if action.kind != "UPSERT_VERSION_AFTER_REHASH" or action.category not in ("NEW", "CHANGED"):
                raise ValueError("Unexpected plan action")
            continue
        if action.category != "UNCHANGED" or type(action.baseline_file_id) is not int or action.baseline_file_id <= 0:
            raise ValueError("Invalid reused file ID or category")
        ident = target[action.path]
        if (type(ident.size) is not int or ident.size < 0
                or ident.size != action.expected_bytes
                or ident.git_blob_id != action.git_blob_id
                or ident.lfs_sha256 != action.lfs_sha256):
            raise ValueError("Target identity differs from pinned plan")
        if (not ident.lfs_sha256 and not ident.git_blob_id):
            raise ValueError("No content identity for reused file")
        if ident.git_blob_id is not None and not SHA40.fullmatch(ident.git_blob_id):
            raise ValueError("Invalid Git blob identity")
        if ident.lfs_sha256 is not None and not SHA256.fullmatch(ident.lfs_sha256):
            raise ValueError("Invalid LFS identity")
        links.append(ReuseLink(action.path, action.baseline_file_id, ident.size,
                               ident.git_blob_id, ident.lfs_sha256))
    if len(links) != plan.count("UNCHANGED"):
        raise ValueError("Reused file count drift")
    if len({x.file_id for x in links}) != len(links):
        raise ValueError("One reused file_id linked to multiple paths")
    return tuple(sorted(links, key=lambda x: x.relative_path))


def link_reused_versions(cur, *, baseline_snapshot_id: int, target_snapshot_id: int,
                         repo_id: str, target_revision: str,
                         links: tuple[ReuseLink, ...]) -> tuple[int, int]:
    """Write observations using caller transaction, never commit. Return (inserted,reused).

    Performs fail-closed readbacks for every baseline and destination observation.
    A repeated call verifies idempotent state. No raw_files/records INSERTs.
    Empty REUSE is valid: status/baseline guards still run, and result is (0, 0).
    """
    if (type(baseline_snapshot_id) is not int or baseline_snapshot_id <= 0
            or type(target_snapshot_id) is not int or target_snapshot_id <= 0
            or baseline_snapshot_id == target_snapshot_id):
        raise ValueError("Invalid snapshot identifiers")
    if not isinstance(repo_id, str) or not repo_id.strip() or not SHA40.fullmatch(target_revision):
        raise ValueError("Invalid repo/revision")
    if not isinstance(links, tuple):
        raise ValueError("Invalid reuse link batch; expected a tuple")
    if len({l.relative_path for l in links}) != len(links) or len({l.file_id for l in links}) != len(links):
        raise ValueError("Duplicate reuse link path/file ID")
    for link in links:
        if (not isinstance(link, ReuseLink) or not link.relative_path
                or type(link.file_id) is not int or link.file_id <= 0
                or type(link.size_bytes) is not int or link.size_bytes < 0):
            raise ValueError("Invalid reuse link")

    cur.execute("SELECT current_database()")
    if cur.fetchone() != ("epl_raw_storage_v2",):
        raise RuntimeError("Refusing non-V2 database")
    # Lock target snapshot row for the whole caller-owned transaction.
    cur.execute("""SELECT status, hf_revision FROM raw.snapshot_runs
                   WHERE snapshot_id=%s AND repo_id=%s FOR UPDATE""",
                (target_snapshot_id, repo_id))
    if cur.fetchone() != ("in_progress", target_revision):
        raise RuntimeError("Target snapshot not in_progress at pinned revision")
    cur.execute("""SELECT status FROM raw.snapshot_runs
                   WHERE snapshot_id=%s AND repo_id=%s""",
                (baseline_snapshot_id, repo_id))
    if cur.fetchone() != ("completed",):
        raise RuntimeError("Baseline snapshot not completed")

    inserted = reused = 0
    for link in links:
        # Defend against stale baseline or a file version no longer usable.
        cur.execute("""SELECT o.file_id, o.presence_status, f.file_size_bytes,
                              f.archive_state, f.parse_state, o.git_blob_id,
                              o.lfs_sha256, f.content_sha256
                       FROM raw.file_observations o
                       JOIN raw.raw_files f ON f.file_id=o.file_id
                       WHERE o.snapshot_id=%s AND o.repo_id=%s AND o.relative_path=%s""",
                    (baseline_snapshot_id, repo_id, link.relative_path))
        old = cur.fetchone()
        if old is None or old[:5] != (link.file_id, "linked", link.size_bytes,
                                        "verified", "success"):
            raise RuntimeError("Baseline file version drift: " + link.relative_path)
        old_blob, old_lfs, old_content_sha = old[5:]
        # Re-use is safe only when matching an actual comparable remote object
        # identity (LFS SHA preferred); never assume matching path/size suffices.
        if link.lfs_sha256 is not None and old_lfs is not None:
            same_identity = old_lfs == link.lfs_sha256 == old_content_sha
        elif link.git_blob_id is not None and old_blob is not None:
            same_identity = old_blob == link.git_blob_id
        else:
            same_identity = False
        if not same_identity:
            raise RuntimeError("Baseline and target content identities differ: " + link.relative_path)
        cur.execute("""INSERT INTO raw.file_observations
                       (snapshot_id,repo_id,relative_path,file_id,presence_status,
                        manifest_size_bytes,git_blob_id,lfs_sha256)
                       VALUES (%s,%s,%s,%s,'linked',%s,%s,%s)
                       ON CONFLICT(snapshot_id,relative_path) DO NOTHING""",
                    (target_snapshot_id, repo_id, link.relative_path, link.file_id,
                     link.size_bytes, link.git_blob_id, link.lfs_sha256))
        if cur.rowcount not in (0, 1):
            raise RuntimeError("Unexpected observation insert rowcount")
        inserted += cur.rowcount
        reused += 1 - cur.rowcount
        cur.execute("""SELECT file_id,presence_status,manifest_size_bytes,git_blob_id,lfs_sha256
                       FROM raw.file_observations
                       WHERE snapshot_id=%s AND repo_id=%s AND relative_path=%s""",
                    (target_snapshot_id, repo_id, link.relative_path))
        if cur.fetchone() != (link.file_id, "linked", link.size_bytes,
                              link.git_blob_id, link.lfs_sha256):
            raise RuntimeError("Target observation collision: " + link.relative_path)
    return inserted, reused
