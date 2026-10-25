"""Independent read-only audit for a completed incremental Raw snapshot."""
from __future__ import annotations

from pathlib import Path

from scripts.tools.incremental_execution_context import ROOT, RegisteredContext, candidate_format
from scripts.tools.incremental_recovery_audit import verified_manifest
from scripts.tools.finalize_incremental_snapshot import db_parameters, scan_archives
from scripts.tools.incremental_completed_snapshot_evidence import (
    CompletionObserved,
    expected_from_registered,
    validate_completed_evidence,
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def norm(value):
    return (value or "").strip().lower()


def audit_completed_snapshot(registered: RegisteredContext, *, root: Path = ROOT) -> str:
    """Collect fresh DB/archive evidence and validate it without writing."""
    import psycopg

    require(isinstance(registered, RegisteredContext),
            "Verified RegisteredContext required")
    require(isinstance(root, Path) and root.is_dir() and not root.is_symlink(),
            "Real project root required")

    root = root.resolve(strict=True)
    prepared = registered.prepared
    plan = prepared.planned

    raw = verified_manifest(root, prepared)
    require(raw is not None, "Verified manifest unavailable")

    expected = expected_from_registered(
        registered,
        archive_bytes=sum(m["size_bytes"] for m in raw.values()),
    )

    candidate_specs = {c.path: c for c in plan.candidates}
    receipts = {r.path: r for r in prepared.candidate_receipts}

    require(set(candidate_specs) == set(receipts),
            "Candidate checkpoint coverage mismatch")

    params = db_parameters(readonly=True)
    params["application_name"] = "m46_completed_snapshot_audit"

    objects = []
    versions = {}
    candidate_ids = []
    reuse_count = 0
    candidate_count = 0
    reused_records = 0
    candidate_records = 0

    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_database(),
                       current_setting('transaction_read_only')
            """)
            require(cur.fetchone() == ("epl_raw_storage_v2", "on"),
                    "Wrong database or transaction not read-only")

            cur.execute("""
                SELECT snapshot_id,repo_id,hf_revision,status,
                       manifest_file_count,raw_scope_file_count,
                       manifest_sha256,manifest_archive_path
                FROM raw.snapshot_runs
                WHERE snapshot_id IN (%s,%s)
            """, (plan.baseline_snapshot_id, registered.target_snapshot_id))

            snapshot_rows = {row[0]: row for row in cur.fetchall()}
            require(set(snapshot_rows) ==
                    {plan.baseline_snapshot_id, registered.target_snapshot_id},
                    "Baseline/target snapshot missing")

            baseline_row = snapshot_rows[plan.baseline_snapshot_id]
            target_row = snapshot_rows[registered.target_snapshot_id]

            require(
                baseline_row[:4] ==
                (plan.baseline_snapshot_id, plan.repo_id,
                 plan.baseline_revision, "completed"),
                "Baseline snapshot identity/status mismatch"
            )
            require(
                target_row ==
                (registered.target_snapshot_id, plan.repo_id,
                 plan.target_revision, "completed",
                 plan.expected_full_files, plan.expected_raw_files,
                 prepared.manifest_sha256, prepared.manifest_archive),
                "Target completed identity/manifest mismatch"
            )

            cur.execute("""
                SELECT o.relative_path,o.file_id,f.record_count,
                       o.repo_id,o.presence_status
                FROM raw.file_observations o
                LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                WHERE o.snapshot_id=%s
            """, (plan.baseline_snapshot_id,))

            base_rows = cur.fetchall()
            require(len(base_rows) == plan.expected_baseline_files,
                    "Baseline observation count mismatch")

            baseline_files = {}
            baseline_records = 0

            for path, fid, count, repo, presence in base_rows:
                require(
                    path not in baseline_files
                    and type(fid) is int and fid > 0
                    and type(count) is int and count >= 0
                    and repo == plan.repo_id and presence == "linked",
                    "Invalid baseline observation"
                )
                baseline_files[path] = fid
                baseline_records += count

            require(len(set(baseline_files.values())) == len(baseline_files),
                    "Duplicate baseline file version ID")
            require(baseline_records == plan.expected_baseline_records,
                    "Baseline raw record accounting mismatch")

            cur.execute("""
                SELECT o.relative_path,o.file_id,o.repo_id,
                       o.presence_status,o.manifest_size_bytes,
                       o.git_blob_id,o.lfs_sha256,
                       f.repo_id,f.relative_path,f.file_size_bytes,
                       f.content_sha256,f.archive_path,
                       f.archive_state,f.parse_state,
                       f.record_count,f.file_format,s.source_code
                FROM raw.file_observations o
                LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                LEFT JOIN raw.raw_sources s ON s.source_id=f.source_id
                WHERE o.snapshot_id=%s
            """, (registered.target_snapshot_id,))

            rows = cur.fetchall()
            require(len(rows) == plan.expected_raw_files,
                    "Target observation count mismatch")

            seen_paths = set()

            for row in rows:
                (path, fid, obs_repo, presence, obs_size,
                 git, lfs, file_repo, file_path, file_size,
                 sha, archive, archive_state, parse_state,
                 nrecords, fmt, source_code) = row

                require(path in raw and path not in seen_paths,
                        "Extra or duplicate Raw observation")
                seen_paths.add(path)

                m = raw[path]

                require(
                    type(fid) is int and fid > 0
                    and fid not in versions
                    and type(nrecords) is int and nrecords >= 0,
                    "Invalid/duplicate physical file version ID"
                )

                require(
                    obs_repo == plan.repo_id
                    and file_repo == plan.repo_id
                    and file_path == path
                    and presence == "linked"
                    and obs_size == m["size_bytes"]
                    and file_size == m["size_bytes"]
                    and fmt == m["format"]
                    and archive_state == "verified"
                    and parse_state == "success"
                    and isinstance(source_code, str) and bool(source_code),
                    "Raw observation/file metadata mismatch: " + path
                )

                require(
                    norm(git) == norm(m.get("git_blob_id"))
                    and norm(lfs) == norm(m.get("lfs_sha256")),
                    "Manifest Git/LFS identity mismatch: " + path
                )

                require(
                    isinstance(sha, str)
                    and len(sha) == 64
                    and archive == f"raw-archive-v2/objects/{sha[:2]}/{sha}",
                    "Invalid content-addressed archive: " + path
                )

                versions[fid] = {
                    "path": path,
                    "record_count": nrecords,
                    "format": fmt,
                }

                if path in candidate_specs:
                    spec = candidate_specs[path]
                    receipt = receipts[path]

                    require(
                        fmt == candidate_format(path)
                        and fmt in ("json", "csv")
                        and source_code == spec.source_code
                        and sha == receipt.content_sha256
                        and nrecords == receipt.records,
                        "Candidate version/receipt mismatch: " + path
                    )

                    candidate_ids.append(fid)
                    candidate_count += 1
                    candidate_records += nrecords
                else:
                    require(
                        baseline_files.get(path) == fid,
                        "REUSE version differs from baseline: " + path
                    )
                    reuse_count += 1
                    reused_records += nrecords

                objects.append({
                    "relative_path": path,
                    "archive_path": archive,
                    "sha256": sha,
                    "size_bytes": m["size_bytes"],
                    "git_blob_id": norm(m.get("git_blob_id")),
                    "lfs_sha256": norm(m.get("lfs_sha256")),
                })

            require(seen_paths == set(raw),
                    "Raw manifest/DB path coverage mismatch")

            ids = sorted(versions)
            cur.execute("""
                SELECT file_id,record_kind,
                       (member_id IS NOT NULL) AS has_member,
                       COUNT(*)
                FROM raw.raw_records
                WHERE file_id=ANY(%s::bigint[])
                GROUP BY file_id,record_kind,(member_id IS NOT NULL)
            """, (ids,))

            counts = dict.fromkeys(ids, 0)

            for fid, kind, has_member, count in cur.fetchall():
                require(fid in versions, "Unexpected physical file ID")
                fmt = versions[fid]["format"]

                valid_kinds = (
                    ("csv_row",) if fmt.startswith("csv")
                    else ("tar_json",) if fmt == "tar.gz"
                    else ("json_item", "json_object", "json_scalar")
                )

                require(
                    kind in valid_kinds
                    and bool(has_member) == (fmt == "tar.gz"),
                    "Physical record kind/member mismatch"
                )

                counts[fid] += count

            for fid, metadata in versions.items():
                require(counts[fid] == metadata["record_count"],
                        "Physical record count mismatch: " + metadata["path"])

            physical_records = sum(counts.values())

            if candidate_ids:
                cur.execute("""
                    SELECT file_id,COUNT(*),
                           COUNT(DISTINCT record_locator),
                           COUNT(*) FILTER (WHERE member_id IS NOT NULL)
                    FROM raw.raw_records
                    WHERE file_id=ANY(%s::bigint[])
                    GROUP BY file_id
                """, (candidate_ids,))

                candidate_physical = cur.fetchall()

                require(
                    len(candidate_physical) == len(candidate_ids)
                    and all(
                        fid in candidate_ids
                        and total == versions[fid]["record_count"]
                        and distinct == total
                        and has_member == 0
                        for fid,total,distinct,has_member in candidate_physical
                    ),
                    "Candidate physical record locator integrity mismatch"
                )

            tar_ids = [
                fid for fid,meta in versions.items()
                if meta["format"] == "tar.gz"
            ]

            tar_counts = dict.fromkeys(tar_ids, 0)

            if tar_ids:
                cur.execute("""
                    SELECT file_id,parse_state,COUNT(*)
                    FROM raw.archive_members
                    WHERE file_id=ANY(%s::bigint[])
                    GROUP BY file_id,parse_state
                """, (tar_ids,))

                for fid,state,count in cur.fetchall():
                    require(
                        fid in tar_counts and state in ("success", "skipped"),
                        "Invalid TAR member state"
                    )
                    tar_counts[fid] += count

                for fid in tar_ids:
                    require(
                        tar_counts[fid] == versions[fid]["record_count"],
                        "TAR member/record coverage mismatch"
                    )

            tar_members = sum(tar_counts.values())

    require(
        (reuse_count,candidate_count,candidate_records,reused_records,
         physical_records) ==
        (plan.expected_reuse,plan.expected_files,
         prepared.expected_records,plan.expected_reused_records,
         prepared.expected_record_references),
        "Pinned snapshot accounting mismatch"
    )

    print(
        f"DB audit PASS: observations={len(rows)} "
        f"REUSE={reuse_count} candidates={candidate_count} "
        f"records={physical_records} TAR_members={tar_members}",
        flush=True
    )

    ordered = tuple(sorted(objects, key=lambda x: x["relative_path"]))

    print(f"Rehashing {len(ordered)} completed-snapshot archives...", flush=True)
    receipt = scan_archives(ordered, root=root)

    require(
        receipt.objects == ordered
        and receipt.total_bytes == expected.archive_bytes,
        "Independent archive scan receipt mismatch"
    )

    # Fresh final status read after the potentially long archive rehash.
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT snapshot_id,repo_id,hf_revision,status,
                       manifest_file_count,raw_scope_file_count,
                       manifest_sha256,manifest_archive_path
                FROM raw.snapshot_runs WHERE snapshot_id=%s
            """, (registered.target_snapshot_id,))
            require(cur.fetchone() == target_row,
                    "Snapshot changed during archive audit")

    observed = CompletionObserved(
        baseline_row=(
            baseline_row[0],baseline_row[1],
            baseline_row[2],baseline_row[3],baseline_row[5]
        ),
        target_row=target_row,
        observations=len(rows),
        reused_files=reuse_count,
        candidate_files=candidate_count,
        candidate_records=candidate_records,
        reused_records=reused_records,
        physical_records=physical_records,
        tar_members=tar_members,
        archive_objects=len(receipt.objects),
        archive_bytes=receipt.total_bytes,
        manifest_hash_verified=True,
        archive_hashes_verified=True,
    )

    return validate_completed_evidence(expected, observed)
