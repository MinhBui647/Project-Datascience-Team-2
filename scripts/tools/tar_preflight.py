#!/usr/bin/env python3
"""M3.8: pinned, read-only PostgreSQL TAR.GZ preflight for Sofascore.

PLAN checks ONLY the local catalog; INSPECT uses HF pinned cache/download and
streams the full archive through a bounded integrity pass and the *production*
raw_parser. No DB connection, no extraction to disk, no DB writes.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'reports' / 'raw_scope_catalog_v2.csv'
REPO = 'Toibinguz/epl-premier-league-dataset'
REV = '4c6bec3a2d89f36496f40c854ba8de374a7b2b36'
TARGET = 'raw_data/sofascore/sofascore_matches_cache.tar.gz'
EXPECTED_SIZE = 96_170_246
EXPECTED_SHA = '86ed16115cc9f4d82875718b393d9461c51752b513db9a5af2f0d47eec77931b'
MAX_GZIP_EXPANSION = 1536 * 1024 * 1024  # independent safety cap; prod TAR parser has its own caps
BLOCK = 1024 * 1024


def catalog_entry():
    if not CATALOG.is_file():
        raise RuntimeError(f'Catalog not found: {CATALOG}')
    with CATALOG.open('r', encoding='utf-8-sig', newline='') as stream:
        rows = sorted(csv.DictReader(stream), key=lambda row: row['relative_path'])
    if len(rows) != 276 or len({r['relative_path'] for r in rows}) != 276:
        raise RuntimeError('Catalog must have exactly 276 unique entries')
    if any(r.get('hf_revision') != REV for r in rows):
        raise RuntimeError('Catalog contains a different HF revision')
    matches = [(i, row) for i, row in enumerate(rows) if row['relative_path'] == TARGET]
    if len(matches) != 1:
        raise RuntimeError('Sofascore TAR target missing or duplicated')
    index, row = matches[0]
    if (row['group'] != 'raw_data' or row['format'] != 'tar.gz'
            or int(row['size_bytes']) != EXPECTED_SIZE
            or (row.get('lfs_sha256') or '').strip().lower() != EXPECTED_SHA):
        raise RuntimeError('Sofascore TAR metadata mismatch with pinned scope')
    return index, row


def check_sha_and_size(path: Path):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while block := stream.read(BLOCK):
            size += len(block)
            digest.update(block)
    if size != EXPECTED_SIZE or digest.hexdigest() != EXPECTED_SHA:
        raise RuntimeError('Downloaded TAR.GZ size / LFS SHA256 mismatch')
    return size


def bounded_gzip_integrity(path: Path):
    """Read through GZIP EOF so Python validates trailer CRC32 and ISIZE."""
    total = 0
    with gzip.open(path, 'rb') as stream:
        while chunk := stream.read(BLOCK):
            total += len(chunk)
            if total > MAX_GZIP_EXPANSION:
                raise RuntimeError(f'GZIP expansion exceeds limit {MAX_GZIP_EXPANSION} bytes')
    return total


def check_production_parser(path: Path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from raw_parser import iter_parse_events, ArchiveMember, ParsedRecord

    members = 0
    json_members = 0
    skipped_members = 0
    records = 0
    member_payload_bytes = 0
    largest_member_bytes = 0
    sample_paths = []
    kinds = {}
    for event in iter_parse_events(path, 'tar.gz'):
        if isinstance(event, ArchiveMember):
            members += 1
            member_payload_bytes += event.member_size_bytes
            largest_member_bytes = max(largest_member_bytes, event.member_size_bytes)
            if len(sample_paths) < 5:
                sample_paths.append(event.member_path)
            if event.parse_state == 'success' and event.member_format == 'json':
                json_members += 1
            elif event.parse_state == 'skipped' and event.member_format == 'other':
                skipped_members += 1
            else:
                raise RuntimeError('Unexpected TAR archive member parse state')
            if members % 1000 == 0:
                print(f'  Scanned TAR members: {members:,} | records: {records:,}', flush=True)
        elif isinstance(event, ParsedRecord):
            if event.member_index is None or event.member_path is None:
                raise RuntimeError('TAR record lacks archive-member link')
            records += 1
            kinds[event.record_kind] = kinds.get(event.record_kind, 0) + 1
            if records % 250000 == 0:
                print(f'  Scanned TAR records: {records:,}', flush=True)
        else:
            raise RuntimeError(f'Unexpected parser event type: {type(event).__name__}')
    if members == 0 or json_members == 0 or records == 0:
        raise RuntimeError('No JSON records found; inspect source before attempting COMMIT')
    return {
        'archive_members': members,
        'json_members': json_members,
        'skipped_non_json_members': skipped_members,
        'member_payload_bytes': member_payload_bytes,
        'largest_member_bytes': largest_member_bytes,
        'raw_parser_records': records,
        'record_kinds': kinds,
        'sample_member_paths': sample_paths,
    }


def atomic_report(details):
    reports = ROOT / 'reports'
    reports.mkdir(exist_ok=True)
    target = reports / 'm3_8_sofascore_tar_preflight.json'
    descriptor, temp = tempfile.mkstemp(dir=reports, prefix='.m3_8_', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(details, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true', help='catalog-only; no download or DB')
    mode.add_argument('--inspect', action='store_true', help='download/cache, full-stream verify and parse; no DB')
    args = parser.parse_args()
    index, _ = catalog_entry()
    print('=== M3.8 | SOFASCORE TAR.GZ PREFLIGHT ===', flush=True)
    print('Repo:', REPO)
    print('Pinned revision:', REV)
    print('Catalog scope: 276')
    print('Index:', index)
    print('Path:', TARGET)
    print('Compressed source bytes:', EXPECTED_SIZE)
    print('Max gzip expanded bytes:', MAX_GZIP_EXPANSION)
    print('Database writes: NONE')
    if args.plan:
        print('Mode: PLAN (catalog only; no network/DB/writes)')
        print('M3.8 TAR PLAN: PASS')
        return 0

    print('Mode: INSPECT (pinned download/cache + full TAR parse; no DB)', flush=True)
    free = shutil.disk_usage(ROOT).free
    print('Local free bytes:', free, flush=True)
    if free < 3 * EXPECTED_SIZE:
        raise RuntimeError('Insufficient local disk headroom for HF cache')
    from huggingface_hub import hf_hub_download

    archive = Path(hf_hub_download(repo_id=REPO, repo_type='dataset', filename=TARGET, revision=REV))
    check_sha_and_size(archive)
    print('Pinned SHA256: VERIFIED', flush=True)
    expanded = bounded_gzip_integrity(archive)
    print(f'Gzip integrity + bounded expansion: PASS | {expanded:,} bytes', flush=True)
    counts = check_production_parser(archive)
    print('Production TAR parser: PASS |'
          f' {counts["archive_members"]:,} members |'
          f' {counts["json_members"]:,} JSON members |'
          f' {counts["raw_parser_records"]:,} records', flush=True)
    print('Skipped non-JSON members:', counts['skipped_non_json_members'], flush=True)
    receipt = {
        'repo_id': REPO,
        'revision': REV,
        'relative_path': TARGET,
        'catalog_index': index,
        'sha256': EXPECTED_SHA,
        'compressed_bytes': EXPECTED_SIZE,
        'gzip_expanded_bytes': expanded,
        **counts,
        'status': 'pass',
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'note': 'Database read/write not performed; ingestion must be planned separately',
    }
    report_path = atomic_report(receipt)
    print('Report:', report_path.relative_to(ROOT).as_posix(), flush=True)
    print('M3.8 TAR PREFLIGHT: PASS (no database writes)', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'M3.8 TAR PREFLIGHT: FAILED ({type(exc).__name__}: {exc})', file=sys.stderr)
        sys.exit(1)
