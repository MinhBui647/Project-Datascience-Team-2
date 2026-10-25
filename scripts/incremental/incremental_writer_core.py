#!/usr/bin/env python3
"""M4.5.4c.3c: single-file PostgreSQL writer building blocks (JSON + CSV).

No standalone write CLI. Caller MUST (1) pin HF revision and revalidate metadata,
(2) verify downloaded JSON, (3) archive immutably and rehash archive bytes,
(4) establish a target snapshot in_progress, (5) wrap EACH call to
write_prepared_version(...) in a PostgreSQL transaction, (6) finalize separately.

This module does not create snapshots, commit, download, or finalize. Never call
its functions outside a correctly managed transaction. No configuration/secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

SHA256 = re.compile(r"[a-f0-9]{64}\Z")
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SOURCE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
VALID_KINDS = frozenset({"json_object", "json_item", "json_scalar"})
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_CSV_WRITER_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 250_000


@dataclass(frozen=True)
class PreparedJsonVersion:
    repo_id: str
    relative_path: str
    pinned_revision: str
    source_code: str
    size_bytes: int
    content_sha256: str
    git_blob_id: str | None
    lfs_sha256: str | None
    archive_relative_path: str
    records: tuple[tuple[str, str, dict], ...]


@dataclass(frozen=True)
class PreparedCsvVersion(PreparedJsonVersion):
    """Same transactional file version contract with CSV record semantics.

    This subtype preserves the existing JSON DTO and caller APIs; it does
    not by itself authorize any write.
    """


def prepared_file_format(version: PreparedJsonVersion) -> str:
    if not isinstance(version, PreparedJsonVersion):
        raise ValueError("Missing PreparedJsonVersion/PreparedCsvVersion")
    return "csv" if isinstance(version, PreparedCsvVersion) else "json"


def safe_relative_path(path: str) -> bool:
    return (isinstance(path, str) and len(path) <= 1024
            and path.startswith(("raw_data/", "2026_2027_new/"))
            and not path.startswith("/") and "\\" not in path
            and all(segment not in ("", ".", "..") for segment in path.split("/")))


def validate_prepared(version: PreparedJsonVersion) -> None:
    if not isinstance(version, PreparedJsonVersion):
        raise ValueError("Missing PreparedJsonVersion")
    if not version.repo_id or not isinstance(version.repo_id, str):
        raise ValueError("Invalid repository")
    fmt = prepared_file_format(version)
    if not safe_relative_path(version.relative_path) or not version.relative_path.endswith("." + fmt):
        raise ValueError("Unsafe or unsupported raw path")
    if not SHA40.fullmatch(version.pinned_revision):
        raise ValueError("Must pin full HF revision SHA")
    if not SOURCE.fullmatch(version.source_code):
        raise ValueError("Invalid registered source code")
    if not SHA256.fullmatch(version.content_sha256):
        raise ValueError("Invalid actual SHA256")
    budget = MAX_CSV_WRITER_BYTES if fmt == "csv" else MAX_JSON_BYTES
    if type(version.size_bytes) is not int or not 0 < version.size_bytes <= budget:
        raise ValueError("Invalid " + fmt.upper() + " byte budget")
    if version.git_blob_id is not None and not SHA40.fullmatch(version.git_blob_id):
        raise ValueError("Invalid remote git blob")
    if version.lfs_sha256 is not None:
        if not SHA256.fullmatch(version.lfs_sha256):
            raise ValueError("Invalid remote LFS SHA256")
        if version.lfs_sha256 != version.content_sha256:
            raise ValueError("LFS content mismatch")
    if version.git_blob_id is None and version.lfs_sha256 is None:
        raise ValueError("No remote content identity")
    if not version.archive_relative_path == (
        f"raw-archive-v2/objects/{version.content_sha256[:2]}/{version.content_sha256}"
    ):
        raise ValueError("Non-canonical immutable archive path")
    if not 0 < len(version.records) <= MAX_RECORDS:
        raise ValueError("Invalid record count")
    locators = set()
    for row in version.records:
        if not isinstance(row, tuple) or len(row) != 3:
            raise ValueError("Invalid parsed record")
        loc, kind, data = row
        if not isinstance(loc, str) or not loc or loc in locators:
            raise ValueError("Duplicate/invalid record locator")
        expected_kinds = frozenset({"csv_row"}) if fmt == "csv" else VALID_KINDS
        if kind not in expected_kinds or not isinstance(data, dict):
            raise ValueError("Unsupported parser record")
        locators.add(loc)


def prepare_json_version(repo_id: str, relative_path: str, revision: str,
                         source_code: str, local_archive: Path, remote_identity,
                         *, root: Path) -> PreparedJsonVersion:
    """Verify archive bytes AND parse complete JSON; no PostgreSQL changes.

    remote_identity: FileIdentity from check_raw_updates; JSON compatibility
    follows validate_raw_contents.validate_local_json. The immutable archive
    should already have been published by archive_manager.archive_immutable.
    """
    from scripts.tools.validate_raw_contents import validate_local_json, verify_bytes
    from scripts.raw_parser import ParsedRecord, iter_parse_events

    archive_root = (root.resolve() / "raw-archive-v2" / "objects").resolve()
    src = local_archive.resolve(strict=True)
    if not src.is_relative_to(archive_root):
        raise ValueError("Archive path outside immutable object store")
    if local_archive.is_symlink() or not src.is_file():
        raise ValueError("Not a regular immutable archive object")
    result = validate_local_json(src, remote_identity, max_file_bytes=MAX_JSON_BYTES)
    canonical_rel = f"raw-archive-v2/objects/{result.sha256[:2]}/{result.sha256}"
    if src != (root.resolve() / canonical_rel).resolve():
        raise ValueError("Archive object location differs from content-addressed path")
    if not isinstance(source_code, str):
        raise ValueError("Invalid source")

    parsed = []
    for event in iter_parse_events(src, "json"):
        if not isinstance(event, ParsedRecord) or event.member_index is not None:
            raise ValueError("Unexpected archive member in JSON")
        parsed.append((event.record_locator, event.record_kind, event.raw_data))
    if len(parsed) != result.record_count:
        raise ValueError("Parsed record count drifted after validation")
    # Rehash after parsing to reduce validation/use race conditions.
    end_size, end_sha, _, _ = verify_bytes(src, remote_identity, max_file_bytes=MAX_JSON_BYTES)
    if (end_size, end_sha) != (result.byte_count, result.sha256):
        raise ValueError("Archive changed during JSON parsing")

    prepared = PreparedJsonVersion(
        repo_id=repo_id, relative_path=relative_path, pinned_revision=revision,
        source_code=source_code, size_bytes=result.byte_count,
        content_sha256=result.sha256, git_blob_id=remote_identity.git_blob_id,
        lfs_sha256=remote_identity.lfs_sha256, archive_relative_path=canonical_rel,
        records=tuple(parsed),
    )
    validate_prepared(prepared)
    return prepared


def prepare_csv_version(repo_id: str, relative_path: str, revision: str,
                        source_code: str, local_archive: Path, remote_identity,
                        *, root: Path) -> PreparedCsvVersion:
    """Verify a canonical immutable CSV archive and parse all rows. NO SQL.

    Fail closed on files > 64 MiB or > 250k records; incremental batches
    are intentionally bounded until a future streaming writer is proven.
    """
    from scripts.incremental.incremental_csv_adapter import inspect_csv_archive
    from scripts.raw_parser import ParsedRecord, iter_parse_events
    from scripts.tools.validate_raw_contents import verify_bytes, check_jsonb_value

    root = root.resolve(strict=True)
    archive_root = root / "raw-archive-v2" / "objects"
    if (archive_root.is_symlink()
            or archive_root.parent.is_symlink()
            or not archive_root.is_dir()):
        raise ValueError("Untrusted immutable archive directory")
    src = Path(local_archive).resolve(strict=True)
    if not src.is_relative_to(archive_root) or Path(local_archive).is_symlink():
        raise ValueError("CSV archive outside immutable object store or symlink")
    if not src.is_file():
        raise ValueError("CSV archive must be a regular file")
    expected = src.relative_to(root).as_posix()
    if not isinstance(relative_path, str) or not relative_path.endswith(".csv"):
        raise ValueError("CSV source path must end with .csv")

    result = inspect_csv_archive(
        src, expected_size=remote_identity.size,
        expected_lfs_sha256=remote_identity.lfs_sha256,
        expected_git_blob_id=remote_identity.git_blob_id,
        max_file_bytes=MAX_CSV_WRITER_BYTES, max_records=MAX_RECORDS,
    )
    canonical_rel = f"raw-archive-v2/objects/{result.content_sha256[:2]}/{result.content_sha256}"
    if expected != canonical_rel:
        raise ValueError("Noncanonical CSV immutable archive path")
    records = []
    for event in iter_parse_events(src, "csv"):
        if (not isinstance(event, ParsedRecord)
                or event.member_index is not None or event.record_kind != "csv_row"):
            raise ValueError("Unexpected CSV parser event")
        check_jsonb_value(event.raw_data)
        records.append((event.record_locator, event.record_kind, event.raw_data))
        if len(records) > MAX_RECORDS:
            raise ValueError("CSV writer record budget exceeded")
    if len(records) != result.record_count:
        raise ValueError("CSV record count drift after verification")
    # Independent rehash after parser pass, immediately before preparing SQL input.
    after_size, after_sha, _, _ = verify_bytes(
        src, remote_identity, max_file_bytes=MAX_CSV_WRITER_BYTES,
    )
    if (after_size, after_sha) != (result.file_size_bytes, result.content_sha256):
        raise RuntimeError("CSV immutable archive changed during parsing")
    prepared = PreparedCsvVersion(
        repo_id=repo_id, relative_path=relative_path, pinned_revision=revision,
        source_code=source_code, size_bytes=result.file_size_bytes,
        content_sha256=result.content_sha256, git_blob_id=remote_identity.git_blob_id,
        lfs_sha256=remote_identity.lfs_sha256, archive_relative_path=canonical_rel,
        records=tuple(records),
    )
    validate_prepared(prepared)
    return prepared


def verify_existing_version(row, version: PreparedJsonVersion) -> tuple[int, int]:
    """Hard stop on incomplete/different DB file version; no silent repair."""
    if row is None or len(row) != 8:
        raise RuntimeError("Missing or malformed existing file")
    file_id, fmt, size, archive_path, archive_state, parse_state, count, source_code = row
    if (fmt, size, archive_path, archive_state, parse_state, count, source_code) != (
        prepared_file_format(version), version.size_bytes, version.archive_relative_path, "verified",
        "success", len(version.records), version.source_code,
    ):
        raise RuntimeError("Existing immutable version metadata conflict")
    if type(file_id) is not int or file_id <= 0:
        raise RuntimeError("Invalid existing file_id")
    return file_id, count


def check_observation(row, *, file_id: int, version: PreparedJsonVersion) -> None:
    if row is None or len(row) != 5:
        raise RuntimeError("Snapshot observation missing")
    observed_id, status, size, git_sha, lfs_sha = row
    if (observed_id, status, size, git_sha, lfs_sha) != (
        file_id, "linked", version.size_bytes,
        version.git_blob_id, version.lfs_sha256,
    ):
        raise RuntimeError("Snapshot observation mismatch (no overwrite permitted)")


def write_prepared_version(cur, snapshot_id: int, version: PreparedJsonVersion, *,
                           root: Path) -> tuple[str, int, int]:
    """Perform mutations using caller-provided transaction cursor (NO COMMIT).

    Returns (INSERTED_NEW or REUSED_EXISTING, file_id, rows_inserted).
    Caller must transaction-wrap each file and arrange separate error logging.
    """
    validate_prepared(version)
    if type(snapshot_id) is not int or snapshot_id <= 0:
        raise ValueError("Invalid target snapshot ID")
    # Revalidate archive and all derived JSON records just before the first SQL
    # write. A previous Content Validation report is NOT trusted as proof.
    from scripts.tools.check_raw_updates import FileIdentity
    identity = FileIdentity(size=version.size_bytes, git_blob_id=version.git_blob_id,
                            lfs_sha256=version.lfs_sha256)
    prepare = (prepare_csv_version if isinstance(version, PreparedCsvVersion)
               else prepare_json_version)
    rechecked = prepare(
        version.repo_id, version.relative_path, version.pinned_revision,
        version.source_code, root / version.archive_relative_path, identity, root=root,
    )
    if rechecked != version:
        raise RuntimeError("Parsed record payload drift; rejecting DB write")
    # The caller must control transaction lifetime; no conn.commit() is issued.
    from psycopg.types.json import Jsonb

    cur.execute("SELECT current_database()")
    if cur.fetchone()[0] != "epl_raw_storage_v2":
        raise RuntimeError("Wrong PostgreSQL database")
    # Advisory transaction lock prevents concurrent writers for same file path.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (version.repo_id + ":" + version.relative_path,))
    cur.fetchone()
    cur.execute("""SELECT status FROM raw.snapshot_runs
                   WHERE snapshot_id=%s AND repo_id=%s AND hf_revision=%s FOR UPDATE""",
                (snapshot_id, version.repo_id, version.pinned_revision))
    state = cur.fetchone()
    if state != ("in_progress",):
        raise RuntimeError("Target snapshot must exist and be in_progress")
    cur.execute("SELECT source_id FROM raw.raw_sources WHERE source_code=%s", (version.source_code,))
    source = cur.fetchone()
    if source is None:
        raise RuntimeError("Unregistered source")
    source_id = source[0]
    cur.execute("""SELECT f.file_id,f.file_format,f.file_size_bytes,f.archive_path,
                          f.archive_state,f.parse_state,f.record_count,s.source_code
                   FROM raw.raw_files f
                   JOIN raw.raw_sources s ON s.source_id=f.source_id
                   WHERE f.repo_id=%s AND f.relative_path=%s AND f.content_sha256=%s
                   FOR UPDATE""",
                (version.repo_id, version.relative_path, version.content_sha256))
    existing = cur.fetchone()
    if existing is None:
        evidence = {"source_hint": version.source_code, "classification": "path_hint_only",
                    "hf_revision_observed": version.pinned_revision}
        cur.execute("""INSERT INTO raw.raw_files
                    (repo_id,relative_path,source_id,file_format,content_sha256,
                     file_size_bytes,archive_path,archive_state,parse_state,
                     league_scope,season_scope,metadata_evidence)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'verified','pending',
                            'unknown','unknown',%s) RETURNING file_id""",
                    (version.repo_id, version.relative_path, source_id,
                     prepared_file_format(version), version.content_sha256, version.size_bytes,
                     version.archive_relative_path, Jsonb(evidence)))
        file_id = cur.fetchone()[0]
        # Content has already been parsed/hashed before SQL writes.
        cur.executemany("""INSERT INTO raw.raw_records
                       (file_id,member_id,record_locator,record_kind,raw_data)
                       VALUES (%s,NULL,%s,%s,%s)""",
                        [(file_id, loc, kind, Jsonb(data)) for loc, kind, data in version.records])
        cur.execute("""UPDATE raw.raw_files SET parse_state='success',record_count=%s
                       WHERE file_id=%s AND parse_state='pending'""",
                    (len(version.records), file_id))
        if cur.rowcount != 1:
            raise RuntimeError("File parse state transition failed")
        action, inserted = "INSERTED_NEW", len(version.records)
    else:
        file_id, _ = verify_existing_version(existing, version)
        inserted, action = 0, "REUSED_EXISTING"

    cur.execute("SELECT COUNT(*) FROM raw.raw_records WHERE file_id=%s", (file_id,))
    if cur.fetchone()[0] != len(version.records):
        raise RuntimeError("Raw record count mismatch")
    cur.execute("""INSERT INTO raw.file_observations
                   (snapshot_id,repo_id,relative_path,file_id,presence_status,
                    manifest_size_bytes,git_blob_id,lfs_sha256)
                   VALUES (%s,%s,%s,%s,'linked',%s,%s,%s)
                   ON CONFLICT(snapshot_id,relative_path) DO NOTHING""",
                (snapshot_id, version.repo_id, version.relative_path, file_id,
                 version.size_bytes, version.git_blob_id, version.lfs_sha256))
    cur.execute("""SELECT file_id,presence_status,manifest_size_bytes,git_blob_id,
                          lfs_sha256 FROM raw.file_observations
                   WHERE snapshot_id=%s AND relative_path=%s""",
                (snapshot_id, version.relative_path))
    check_observation(cur.fetchone(), file_id=file_id, version=version)
    return action, file_id, inserted
