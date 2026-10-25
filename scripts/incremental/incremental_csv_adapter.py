"""Read-only verification for an archived incremental CSV candidate.

This module does not authorize ingestion or snapshot publication.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from scripts.raw_parser import ParsedRecord, iter_parse_events


MAX_CSV_FILE_BYTES = 128 * 1024 * 1024
MAX_CSV_RECORDS = 2_000_000

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class CsvArchiveInspection:
    file_size_bytes: int
    content_sha256: str
    record_count: int
    verification_method: str
    write_authorized: bool = False


def _hash_file(path: Path, size: int) -> tuple[str, str]:
    """Compute content SHA256 and Git blob SHA1 while streaming."""
    content_hash = hashlib.sha256()
    git_hash = hashlib.sha1(
        b"blob " + str(size).encode("ascii") + b"\0"
    )

    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            content_hash.update(block)
            git_hash.update(block)

    return content_hash.hexdigest(), git_hash.hexdigest()


def inspect_csv_archive(
    path: str | Path,
    *,
    expected_size: int,
    expected_lfs_sha256: str | None = None,
    expected_git_blob_id: str | None = None,
    file_format: str = "csv",
    max_file_bytes: int = MAX_CSV_FILE_BYTES,
    max_records: int = MAX_CSV_RECORDS,
) -> CsvArchiveInspection:
    """Verify immutable CSV bytes and parse every row, without writes.

    At least one independently supplied content identity is required.
    The caller remains responsible for authenticating HF metadata and
    preserving the immutable archive.
    """
    if file_format != "csv":
        raise ValueError("This adapter accepts CSV only")

    for label, value in (
        ("expected_size", expected_size),
        ("max_file_bytes", max_file_bytes),
        ("max_records", max_records),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(label + " must be a positive integer")

    if expected_lfs_sha256 is not None:
        if (
            not isinstance(expected_lfs_sha256, str)
            or not SHA256_PATTERN.fullmatch(expected_lfs_sha256)
        ):
            raise ValueError("Invalid LFS SHA256")

    if expected_git_blob_id is not None:
        if (
            not isinstance(expected_git_blob_id, str)
            or not SHA1_PATTERN.fullmatch(expected_git_blob_id)
        ):
            raise ValueError("Invalid Git blob SHA1")

    if expected_lfs_sha256 is None and expected_git_blob_id is None:
        raise ValueError("Missing verifiable file identity")

    source = Path(path)

    if source.is_symlink():
        raise ValueError("Symbolic links are not accepted")

    if not source.is_file():
        raise FileNotFoundError("Archived CSV file is missing")

    initial = source.stat()

    if initial.st_size != expected_size:
        raise ValueError("CSV size mismatch")

    if initial.st_size > max_file_bytes:
        raise ValueError("CSV exceeds per-file byte budget")

    sha256, git_sha1 = _hash_file(source, initial.st_size)

    if expected_lfs_sha256 is not None:
        if sha256 != expected_lfs_sha256:
            raise ValueError("CSV LFS SHA256 mismatch")
        method = "LFS_SHA256"
    else:
        if git_sha1 != expected_git_blob_id:
            raise ValueError("CSV Git blob SHA1 mismatch")
        method = "GIT_BLOB_SHA1"

    count = 0

    for event in iter_parse_events(source, "csv"):
        if not isinstance(event, ParsedRecord):
            raise RuntimeError("Unexpected CSV parser event")

        if not isinstance(event.raw_data, dict):
            raise RuntimeError("CSV record must be a JSON object")

        count += 1

        if count > max_records:
            raise RuntimeError("CSV record limit exceeded")

    if count == 0:
        raise RuntimeError("CSV contains no data records")

    final = source.stat()

    before = (
        initial.st_dev,
        initial.st_ino,
        initial.st_size,
        initial.st_mtime_ns,
    )
    after = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
    )

    if before != after:
        raise RuntimeError("CSV file changed during inspection")

    final_sha256, _ = _hash_file(source, final.st_size)

    if final_sha256 != sha256:
        raise RuntimeError("CSV content changed during inspection")

    return CsvArchiveInspection(
        file_size_bytes=initial.st_size,
        content_sha256=sha256,
        record_count=count,
        verification_method=method,
        write_authorized=False,
    )
