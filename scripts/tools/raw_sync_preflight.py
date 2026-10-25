"""M4.5.3a | Read-only incremental Raw synchronization preflight.

Audits metadata, source mapping, and supported formats for files modified
between a completed PostgreSQL snapshot and a pinned Hugging Face revision.
Does NOT download contents, write files, or create/update database records.

Example (run at project root):
  .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\raw_sync_preflight.py \\
    --baseline-snapshot 4 --target-revision 0216641d6081234e9e35a3de65c138830c77963a --plan
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

if __package__:
    from .check_raw_updates import (
        REPO_ID, FileIdentity, load_baseline, load_target, compare_snapshots,
    )
    from .hf_manifest_v2 import source_from_path, detect_format
else:
    from check_raw_updates import (
        REPO_ID, FileIdentity, load_baseline, load_target, compare_snapshots,
    )
    from hf_manifest_v2 import source_from_path, detect_format

SUPPORTED_FORMATS = frozenset({"csv", "csv.gz", "json", "tar.gz"})
UNCLASSIFIED_SOURCES = frozenset({"unknown", "unclassified_2026_2027"})
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class EntryPreflight:
    path: str
    category: str
    source_code: str
    file_format: str
    size: int | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_path(path: str) -> list[str]:
    parts = path.split("/")
    errs: list[str] = []
    if (not path or path.startswith("/") or "\\" in path or "//" in path
            or any(x in ("", ".", "..") for x in parts)):
        errs.append("unsafe-relative-path")
    if any(ord(c) < 32 for c in path):
        errs.append("control-character-in-path")
    return errs


def inspect_entry(path: str, category: str, identity: FileIdentity) -> EntryPreflight:
    """Pure function; safe to unit-test without Hugging Face or PostgreSQL."""
    errors = validate_path(path)
    warnings: list[str] = []
    fmt = detect_format(path)
    source = source_from_path(path)
    if category not in {"NEW", "CHANGED"}:
        errors.append("unexpected-ingest-category")
    if fmt not in SUPPORTED_FORMATS:
        errors.append(f"unsupported-format:{fmt}")
    if source in UNCLASSIFIED_SOURCES or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", source):
        errors.append(f"unclassified-source:{source}")
    if type(identity.size) is not int or identity.size <= 0:
        errors.append("missing-or-nonpositive-size")
    if identity.lfs_sha256 is not None and not SHA256.fullmatch(identity.lfs_sha256):
        errors.append("invalid-lfs-sha256")
    if identity.git_blob_id is not None and not GIT_SHA1.fullmatch(identity.git_blob_id):
        errors.append("invalid-git-blob-id")
    if not identity.lfs_sha256 and not identity.git_blob_id:
        errors.append("missing-content-identity")
    if isinstance(identity.size, int) and identity.size > 256 * 1024 * 1024 and fmt == "json":
        warnings.append("large-json-check-parser-memory-before-ingestion")
    return EntryPreflight(path, category, source, fmt, identity.size, tuple(errors), tuple(warnings))


def read_existing_source_codes() -> set[str]:
    """Optional read-only registry check; never create source rows."""
    from dotenv import dotenv_values
    import psycopg
    cfg = {**dotenv_values('.env', encoding='utf-8-sig'),
           **dotenv_values('.env.v2', encoding='utf-8-sig')}
    with psycopg.connect(
        host=cfg['DB_HOST'], port=int(cfg['DB_PORT']),
        dbname=cfg['DB_NAME'], user=cfg['DB_USER'], password=cfg['DB_PASSWORD'],
        options='-c default_transaction_read_only=on',
    ) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            if cur.fetchone()[0] != 'epl_raw_storage_v2':
                raise RuntimeError('Wrong database; refusing preflight')
            cur.execute('SELECT source_code FROM raw.raw_sources')
            return {row[0] for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-snapshot', type=int, required=True)
    parser.add_argument('--target-revision', required=True,
                        help='Use full 40-character pinned SHA, never moving main')
    parser.add_argument('--repo-id', default=REPO_ID)
    parser.add_argument('--plan', action='store_true', help='Required; read-only')
    args = parser.parse_args()
    if not args.plan:
        parser.error('--plan is required; this tool cannot ingest or write')
    if not re.fullmatch(r'[0-9a-f]{40}', args.target_revision):
        parser.error('--target-revision must be a full 40-character commit SHA')

    print('=== M4.5.3a | INCREMENTAL RAW PREFLIGHT ===', flush=True)
    baseline_rev, baseline = load_baseline(args.baseline_snapshot, args.repo_id)
    target_rev, target, excluded = load_target(args.repo_id, args.target_revision)
    if target_rev != args.target_revision:
        raise RuntimeError('Pinned revision mismatch')
    groups = compare_snapshots(baseline, target)
    entries = [inspect_entry(path, category, target[path])
               for category in ('NEW', 'CHANGED') for path in groups[category]]
    registry = read_existing_source_codes()

    blockers = []
    for entry in entries:
        blockers.extend(f'{entry.path}: {problem}' for problem in entry.errors)
        if entry.source_code not in registry:
            blockers.append(f'{entry.path}: source {entry.source_code!r} absent from raw.raw_sources')

    print('Repository:', args.repo_id)
    print('Baseline snapshot:', args.baseline_snapshot)
    print('Baseline revision:', baseline_rev)
    print('Target pinned revision:', target_rev)
    print('Raw baseline files:', len(baseline))
    print('Raw target files:', len(target))
    print('Excluded non-Raw files:', excluded)
    for category in ('UNCHANGED', 'NEW', 'CHANGED', 'REMOVED', 'REVIEW'):
        print(f'{category}: {len(groups[category])}')
    print('\n--- INCREMENTAL INGESTION CANDIDATES ---')
    for entry in entries:
        status = 'BLOCKED' if (entry.errors or entry.source_code not in registry) else 'METADATA_OK'
        print(f'[{status}] {entry.category:7} | {entry.file_format:6} | '
              f'{str(entry.size):>10} bytes | source={entry.source_code:13} | {entry.path}')
        for warning in entry.warnings:
            print('  WARNING:', warning)
    if groups['REVIEW']:
        blockers.append(f'{len(groups["REVIEW"])} file(s) require metadata review')
    print('\n--- NEXT-STAGE SAFETY GUARDS ---')
    print('Candidate files:', len(entries))
    print('Metadata/source blockers:', len(blockers))
    for blocker in blockers:
        print('BLOCKER:', blocker)
    print('Legacy ingestion runners pinned to snapshot #4: NOT reused for writes')
    print('Parser and actual file-content validation: NOT YET PERFORMED')
    print('Snapshot #5 creation: NOT PERFORMED')
    print('Database writes: NONE')
    print('File contents downloaded: NONE')
    print('Local files modified: NONE')
    if blockers:
        print('M4.5.3a PREFLIGHT: REVIEW REQUIRED')
        return 2
    print('M4.5.3a METADATA PREFLIGHT: PASS (not approval to ingest)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
