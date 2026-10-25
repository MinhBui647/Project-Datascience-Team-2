#!/usr/bin/env python3
"""M3.2: parse archived raw bytes into JSONB-compatible objects (NO DB writes).

Supported formats: csv, csv.gz, json, tar.gz.
Call iter_parse_events(path, file_format) to stream ParsedRecord/ArchiveMember events.
Original archived files are never changed. Business normalization belongs downstream.
"""
from __future__ import annotations

import csv
import codecs
import gzip
import hashlib
import io
import json
import math
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Union

MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 48 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000


class ParseError(ValueError):
    """Unsupported or malformed source structure; retain original archive."""


@dataclass(frozen=True)
class ParsedRecord:
    record_kind: str
    record_locator: str
    raw_data: dict[str, Any]
    member_index: int | None = None
    member_path: str | None = None


@dataclass(frozen=True)
class ArchiveMember:
    member_index: int
    member_path: str
    member_size_bytes: int
    member_sha256: str | None
    member_format: str  # json | other
    parse_state: str     # success | skipped


ParseEvent = Union[ParsedRecord, ArchiveMember]


def _no_nonfinite(value: str) -> Any:
    raise ParseError(f"Nonstandard JSON constant rejected: {value}")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ParseError(f"Duplicate JSON object key: {key[:80]!r}")
        obj[key] = value
    return obj


def _parse_json(content: bytes, label: str) -> Any:
    if len(content) > MAX_JSON_BYTES:
        raise ParseError(f"JSON exceeds {MAX_JSON_BYTES} bytes: {label}")
    try:
        # utf-8-sig permits UTF-8 BOM (also seen in some exported JSON).
        return json.loads(
            content.decode("utf-8-sig"),
            parse_constant=_no_nonfinite,
            object_pairs_hook=_no_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"JSON parse failed in {label}: {exc}") from exc


def _make_record(value: Any, locator: str, kind: str,
                 member_index: int | None = None,
                 member_path: str | None = None) -> ParsedRecord:
    if isinstance(value, dict):
        raw_data = value
    else:
        # PostgreSQL raw.raw_records CHECK(jsonb_typeof(raw_data)='object').
        # Preserve type of scalar/list via wrapper; original bytes remain archived.
        raw_data = {"_raw_value": value}
        kind = "json_scalar"
        if member_index is not None:
            # Schema requires tar_json for ANY record linked to an archive member.
            kind = "tar_json"
    return ParsedRecord(
        record_kind=kind, record_locator=locator, raw_data=raw_data,
        member_index=member_index, member_path=member_path,
    )


def _iter_json_value(value: Any, *, prefix: str = "",
                     member_index: int | None = None,
                     member_path: str | None = None) -> Iterator[ParsedRecord]:
    is_tar = member_index is not None
    if isinstance(value, list):
        for index, element in enumerate(value):
            kind = "tar_json" if is_tar else "json_item"
            locator = f"{prefix}item:{index}"
            yield _make_record(element, locator, kind, member_index, member_path)
    else:
        kind = "tar_json" if is_tar else "json_object"
        yield _make_record(value, f"{prefix}root", kind, member_index, member_path)


def _csv_jsonb_headers(header: list[str]) -> list[str]:
    """Make empty CSV headers addressable without dropping/merging any cells.

    Source header text and raw bytes remain available in the immutable archive.
    Generated names use zero-based column positions and avoid collisions with
    real header names. Duplicate *named* headers are still rejected to avoid
    ambiguous mapping and silent information loss.
    """
    if not header or not any(col.strip() for col in header):
        raise ParseError("CSV has no usable header names")
    named = [col for col in header if col.strip()]
    if len(named) != len(set(named)):
        raise ParseError("CSV contains duplicate header names; cannot map to JSON object safely")
    used = set(named)
    mapped: list[str] = []
    for index, col in enumerate(header):
        if col.strip():
            mapped.append(col)
            continue
        base = f"__unnamed_csv_col_{index:03d}"
        candidate = base
        suffix = 1
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        mapped.append(candidate)
    return mapped


def _iter_csv_rows(handle: io.TextIOBase) -> Iterator[ParsedRecord]:
    reader = csv.reader(handle, strict=True)
    try:
        original_header = next(reader)
    except StopIteration:
        raise ParseError("CSV is empty: no header")
    mapped_header = _csv_jsonb_headers(original_header)
    for row_num, row in enumerate(reader, start=1):
        width = len(original_header)
        if len(row) > width:
            # Some historical Football-Data CSVs append empty cells after the
            # declared header (e.g. season 2003-04: 57 headers, 62/72 cells).
            # Only disregard trailing cells when ALL are empty/whitespace.
            # The original bytes remain intact in the immutable raw archive.
            # Never drop a non-empty value that has no corresponding header.
            if any(cell.strip() for cell in row[width:]):
                raise ParseError(
                    f"CSV row {row_num}: {len(row)} cells != {width} headers; "
                    "non-empty cells beyond header"
                )
            row = row[:width]
        elif len(row) < width:
            raise ParseError(
                f"CSV row {row_num}: {len(row)} cells != {width} headers"
            )
        yield ParsedRecord("csv_row", f"row:{row_num}", dict(zip(mapped_header, row)))


def _safe_member_path(raw_name: str) -> str:
    if not raw_name or "\\" in raw_name or "\x00" in raw_name:
        raise ParseError(f"Unsafe TAR member name: {raw_name!r}")
    path = PurePosixPath(raw_name)
    if (path.is_absolute() or any(part in ("..", ".") for part in raw_name.split("/"))
            or "//" in raw_name or raw_name.startswith("/")):
        raise ParseError(f"Unsafe TAR member path: {raw_name!r}")
    return raw_name


def _iter_tar(path: Path) -> Iterator[ParseEvent]:
    total_uncompressed = 0
    seen_paths: set[str] = set()
    try:
        with tarfile.open(path, mode="r|gz") as archive:
            for member_index, member in enumerate(archive):
                if member_index >= MAX_ARCHIVE_MEMBERS:
                    raise ParseError("TAR exceeds maximum allowed member count")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ParseError(f"Unsupported TAR entry type: {member.name!r}")
                member_path = _safe_member_path(member.name)
                if member_path in seen_paths:
                    raise ParseError(f"Duplicate TAR member path: {member_path!r}")
                seen_paths.add(member_path)
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ParseError(f"TAR member too large: {member_path} ({member.size})")
                total_uncompressed += member.size
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ParseError("TAR exceeds total uncompressed-byte safety limit")
                file_handle = archive.extractfile(member)
                if file_handle is None:
                    raise ParseError(f"Could not read TAR member: {member_path}")
                with file_handle:
                    content = file_handle.read(MAX_MEMBER_BYTES + 1)
                if len(content) != member.size:
                    raise ParseError(f"TAR member size mismatch: {member_path}")
                member_hash = hashlib.sha256(content).hexdigest()
                member_format = "json" if member_path.lower().endswith(".json") else "other"
                if member_format != "json":
                    yield ArchiveMember(member_index, member_path, member.size,
                                        member_hash, "other", "skipped")
                    continue
                value = _parse_json(content, f"TAR:{member_path}")
                yield ArchiveMember(member_index, member_path, member.size,
                                    member_hash, "json", "success")
                yield from _iter_json_value(value, prefix=f"member:{member_index}:",
                                            member_index=member_index,
                                            member_path=member_path)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ParseError(f"Cannot parse TAR.GZ {path}: {exc}") from exc


def detect_csv_encoding(path: str | Path, file_format: str) -> str:
    """Select a lossless CSV decoder BEFORE yielding any parsed records.

    Historical CSVs can use Windows-1252. Decode the complete byte stream
    incrementally so an invalid UTF-8 byte late in the file cannot cause
    partial record output followed by a restart/duplication. Do not use
    errors='replace' or errors='ignore'. The immutable source is not changed.
    For csv.gz this makes an extra sequential streaming pass over the gzip.
    """
    path = Path(path)
    if file_format not in ("csv", "csv.gz"):
        raise ParseError(f"Unsupported CSV format for encoding detection: {file_format!r}")
    opener = gzip.open if file_format == "csv.gz" else open
    for encoding in ("utf-8-sig", "cp1252"):
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        try:
            with opener(path, "rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    decoder.decode(chunk, final=False)
                decoder.decode(b"", final=True)
            return encoding
        except UnicodeDecodeError:
            continue
        except (OSError, EOFError) as exc:
            raise ParseError(f"CSV byte stream could not be read in {path}: {exc}") from exc
    raise ParseError(f"CSV has bytes invalid under UTF-8 and CP1252: {path}")


def iter_parse_events(path: str | Path, file_format: str) -> Iterator[ParseEvent]:
    """Yield deterministic event locators for one *verified* archived source file.

    csv / csv.gz: one JSON object per CSV row, strings left unmodified.
    json: top-level array => per item; top-level object => 1 object.
    tar.gz: ArchiveMember event then records for each JSON member, never extract to disk.
    Caller must exhaust iterator and treat ANY exception as parse failure; if writing
    to DB, all payload insertions for a file must be in one transaction.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Archive does not exist: {path}")
    if file_format == "csv":
        # Select encoding before streaming any records into a DB transaction.
        encoding = detect_csv_encoding(path, file_format)
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                yield from _iter_csv_rows(handle)
        except (UnicodeDecodeError, csv.Error, OSError) as exc:
            raise ParseError(f"CSV parse failed in {path} ({encoding}): {exc}") from exc
    elif file_format == "csv.gz":
        encoding = detect_csv_encoding(path, file_format)
        try:
            with gzip.open(path, "rt", encoding=encoding, newline="") as handle:
                yield from _iter_csv_rows(handle)
        except (UnicodeDecodeError, csv.Error, EOFError, OSError) as exc:
            raise ParseError(f"CSV.GZ parse failed in {path} ({encoding}): {exc}") from exc
    elif file_format == "json":
        with path.open("rb") as handle:
            content = handle.read(MAX_JSON_BYTES + 1)
        yield from _iter_json_value(_parse_json(content, str(path)))
    elif file_format == "tar.gz":
        yield from _iter_tar(path)
    else:
        raise ParseError(f"Unsupported raw format: {file_format!r}")
