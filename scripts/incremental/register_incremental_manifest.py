#!/usr/bin/env python3
"""M4.6.3e.2 | Generic guarded registration of an EXISTING immutable HF manifest.

Run AFTER snapshot registration, REUSE linking, candidate ingestion, and after a
separate earlier step has created both the local JSON report and its identical
SHA256-addressed immutable archive. --plan is read-only. --commit changes ONLY
manifest_sha256 + manifest_archive_path in raw.snapshot_runs within one guarded
transaction. No HF request, no archive creation, no raw data writes, no finalize.

All expected-* arguments must match the SAME pinned M4.6 execution plan used by
the other modules. An already-registered manifest is NOT overwritten/resumed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.check_raw_updates import FileIdentity, normalize_id
from scripts.tools.populate_incremental_reuse import (
    ReuseRequest, inspect_db, verify_plan, read_only_args,
)
from scripts.tools.register_incremental_snapshot import Registration, db_parameters
from scripts.tools.incremental_execution_context import candidate_format

REPO = 'Toibinguz/epl-premier-league-dataset'
DB = 'epl_raw_storage_v2'
RAW_PREFIXES = ('raw_data/', '2026_2027_new/')
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ManifestRequest:
    reuse: ReuseRequest
    report_relative_path: str
    manifest_sha256: str

    @property
    def archive_relative_path(self) -> str:
        return (f'raw-archive-v2/manifests/{self.manifest_sha256[:2]}/'
                f'{self.manifest_sha256}.json')

    def validate(self) -> None:
        self.reuse.validate()
        if not isinstance(self.manifest_sha256, str) or not SHA256.fullmatch(self.manifest_sha256):
            raise RuntimeError('Manifest SHA256 must be a pinned lowercase hex digest')
        if not checked_path(self.report_relative_path) or not (
            self.report_relative_path.startswith('reports/') and
            self.report_relative_path.endswith('.json')
        ):
            raise RuntimeError('Manifest report must be a safe relative JSON path under reports/')


@dataclass(frozen=True)
class AuditCounts:
    observations: int
    reuse: int
    candidates: int
    candidate_records: int
    reused_records: int
    record_references: int


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline-snapshot', type=int, required=True)
    ap.add_argument('--target-snapshot-id', type=int, required=True)
    ap.add_argument('--target-revision', required=True)
    ap.add_argument('--expected-fingerprint', required=True)
    ap.add_argument('--repo-id', default=REPO)
    ap.add_argument('--expected-baseline-files', type=int, required=True)
    ap.add_argument('--expected-full-files', type=int, required=True)
    ap.add_argument('--expected-raw-files', type=int, required=True)
    ap.add_argument('--expected-reuse', type=int, required=True)
    ap.add_argument('--expected-files', type=int, required=True)
    ap.add_argument('--expected-bytes', type=int, required=True)
    ap.add_argument('--expected-records', type=int, required=True)
    ap.add_argument('--expected-baseline-records', type=int, required=True)
    ap.add_argument('--expected-reused-records', type=int, required=True)
    ap.add_argument('--manifest-report', required=True,
                    help='Existing report under reports/, relative to repository root')
    ap.add_argument('--manifest-sha256', required=True,
                    help='SHA256 of both report JSON and archived immutable manifest')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--commit', action='store_true')
    ap.add_argument('--confirm-manifest-only', action='store_true')
    ap.add_argument('--allow-csv-manifest-registration-plan-only', action='store_true',
                    help='Opt in to CSV SyncPlan validation for --plan only')
    ap.add_argument('--allow-csv-manifest-registration-write', action='store_true',
                    help='Explicit CSV manifest registration opt-in; requires --commit and --confirm-manifest-only')
    return ap


def check_mode(a: argparse.Namespace, p: argparse.ArgumentParser) -> None:
    if bool(a.commit) != bool(a.confirm_manifest_only):
        p.error('--commit requires --confirm-manifest-only; --plan forbids confirmation')
    if getattr(a, 'allow_csv_manifest_registration_plan_only', False) and not a.plan:
        p.error('CSV manifest registration opt-in is read-only: --commit blocked')
    if getattr(a, 'allow_csv_manifest_registration_write', False) and (
            not a.commit or not a.confirm_manifest_only or
            getattr(a, 'allow_csv_manifest_registration_plan_only', False)):
        p.error('CSV manifest write requires --commit --confirm-manifest-only and forbids CSV plan-only')


def build_request(a: argparse.Namespace) -> ManifestRequest:
    reg = Registration(a.repo_id, a.baseline_snapshot, a.target_revision,
                       a.expected_fingerprint, a.expected_baseline_files,
                       a.expected_full_files, a.expected_raw_files,
                       a.expected_reuse, a.expected_files,
                       a.expected_bytes, a.expected_records)
    req = ManifestRequest(ReuseRequest(reg, a.target_snapshot_id,
                        a.expected_baseline_records, a.expected_reused_records),
                          a.manifest_report, a.manifest_sha256)
    req.validate()
    return req


def checked_path(path: str) -> bool:
    return (isinstance(path, str) and 0 < len(path) <= 1024
            and not path.startswith('/') and '\\' not in path
            and all(part not in ('', '.', '..') for part in path.split('/'))
            and all(ord(c) >= 32 and ord(c) != 127 for c in path))


def check_manifest(data: dict, req: ManifestRequest) -> dict[str, dict]:
    """Validate FULL local manifest; never silently ignore new out-of-scope paths."""
    req.validate()
    r = req.reuse.registration
    if not isinstance(data, dict) or (
        data.get('repo_id'), data.get('repo_type'), data.get('resolved_commit'), data.get('file_count')
    ) != (r.repo_id, 'dataset', r.target_revision, r.manifest_files):
        raise RuntimeError('Manifest repository/revision/full count mismatch')
    files = data.get('files')
    if not isinstance(files, list) or len(files) != r.manifest_files:
        raise RuntimeError('Full-manifest entries missing or count drift')
    raw, excluded, seen = {}, set(), set()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError('Malformed manifest entry')
        path = item.get('relative_path')
        if not checked_path(path) or path in seen:
            raise RuntimeError(f'Unsafe or duplicate manifest path: {path!r}')
        seen.add(path)
        sz = item.get('size_bytes')
        if item.get('hf_revision') != r.target_revision or type(sz) is not int or sz < 0:
            raise RuntimeError('Manifest file revision/size invalid: ' + path)
        git, lfs = normalize_id(item.get('git_blob_id')), normalize_id(item.get('lfs_sha256'))
        if (not git and not lfs) or (git and not SHA40.fullmatch(git)) or (
            lfs and not SHA256.fullmatch(lfs)
        ):
            raise RuntimeError('Manifest file identity missing/invalid: ' + path)
        if path.startswith(RAW_PREFIXES):
            if item.get('format') not in ('json', 'csv', 'csv.gz', 'tar.gz'):
                raise RuntimeError('Unsupported Raw manifest format: ' + path)
            raw[path] = item
        else:
            if not (path.startswith('clean/') or path in ('.gitattributes', 'README.md')):
                raise RuntimeError('Unexpected non-Raw manifest path: ' + path)
            excluded.add(path)
    if len(raw) != r.raw_files or len(excluded) != r.manifest_files - r.raw_files:
        raise RuntimeError('Raw/non-Raw manifest scope drift')
    return raw


def _safe_file(root: Path, relative: str, prefix: str) -> Path:
    if not checked_path(relative) or not relative.startswith(prefix):
        raise RuntimeError('Unsafe manifest file location')
    root = root.resolve()
    trusted = (root / prefix.rstrip('/')).resolve()
    if not trusted.is_relative_to(root) or trusted == root:
        raise RuntimeError('Trusted manifest directory resolves outside repository')
    result = root / relative
    if result.is_symlink() or not result.is_file() or not result.resolve(strict=True).is_relative_to(trusted):
        raise RuntimeError('Manifest file missing, symlink, or outside trusted directory')
    return result


def load_verified_manifest(req: ManifestRequest, *, root: Path = ROOT) -> tuple[dict[str, dict], int]:
    """Exactly identical report/archive bytes and digest, bounded in memory."""
    req.validate()
    report = _safe_file(root, req.report_relative_path, 'reports/')
    archive = _safe_file(root, req.archive_relative_path, 'raw-archive-v2/manifests/')
    if (report.stat().st_size > MAX_MANIFEST_BYTES or
            archive.stat().st_size > MAX_MANIFEST_BYTES):
        raise RuntimeError('Manifest exceeds safety byte budget')
    original, immutable = report.read_bytes(), archive.read_bytes()
    if (original != immutable or
            hashlib.sha256(original).hexdigest() != req.manifest_sha256):
        raise RuntimeError('Manifest report/archive bytes or SHA256 mismatch')
    try:
        parsed = json.loads(original)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('Invalid JSON manifest') from exc
    return check_manifest(parsed, req), len(original)


def manifest_identities(raw: dict[str, dict]) -> dict[str, FileIdentity]:
    return {p: FileIdentity(item['size_bytes'], normalize_id(item.get('git_blob_id')),
                            normalize_id(item.get('lfs_sha256')))
            for p, item in raw.items()}


def validate_observations(rows, raw: dict[str, dict], plan, links, req: ManifestRequest):
    """Classify by pinned ACTION, never by baseline file_id inequality."""
    r = req.reuse.registration
    if len(rows) != r.raw_files:
        raise RuntimeError('Manifest observation coverage incomplete')
    reuse_links = {x.relative_path: x for x in links}
    actions = {a.path: a for a in plan.actions}
    if (len(reuse_links) != r.reuse_files or len(actions) != r.raw_files or
            set(actions) != set(raw)):
        raise RuntimeError('Pinned plan paths/count invalid')
    seen_paths, seen_ids = set(), set()
    candidate_counts = {}
    reused_records = candidate_records = 0
    for row in rows:
        if len(row) != 18:
            raise RuntimeError('Malformed observation DB row')
        (path, obs_repo, presence, obs_size, obs_git, obs_lfs, fid, file_repo,
         file_path, file_size, content_sha, archive_path, archive_state, parse_state,
         record_count, source_id, source_code, fmt) = row
        if (path not in raw or path in seen_paths or type(fid) is not int or fid <= 0
                or fid in seen_ids):
            raise RuntimeError('Missing/duplicate observation path or file_id: ' + str(path))
        seen_paths.add(path)
        seen_ids.add(fid)
        entry, a = raw[path], actions[path]
        git, lfs = normalize_id(entry.get('git_blob_id')), normalize_id(entry.get('lfs_sha256'))
        sha = normalize_id(content_sha)
        if (obs_repo, presence, file_repo, file_path, obs_size, file_size,
                archive_state, parse_state, fmt) != (
                r.repo_id, 'linked', r.repo_id, path, entry['size_bytes'],
                entry['size_bytes'], 'verified', 'success', entry['format']):
            raise RuntimeError('Observation/file version metadata mismatch: ' + path)
        if (normalize_id(obs_git), normalize_id(obs_lfs)) != (git, lfs):
            raise RuntimeError('Observation/HF remote identity mismatch: ' + path)
        if (not sha or not SHA256.fullmatch(sha) or
                archive_path != f'raw-archive-v2/objects/{sha[:2]}/{sha}' or
                (lfs and lfs != sha)):
            raise RuntimeError('Noncanonical or conflicting immutable object: ' + path)
        if (type(source_id) is not int or source_id <= 0 or
                not source_code or type(record_count) is not int or record_count < 0):
            raise RuntimeError('Invalid source or record metadata: ' + path)
        if path in reuse_links:
            link = reuse_links[path]
            if a.kind != 'REUSE_FILE_ID' or (
                fid, obs_size, normalize_id(obs_git), normalize_id(obs_lfs)
            ) != (link.file_id, link.size_bytes, link.git_blob_id, link.lfs_sha256):
                raise RuntimeError('REUSE path/version mismatch: ' + path)
            reused_records += record_count
        else:
            if (a.kind != 'UPSERT_VERSION_AFTER_REHASH' or
                    a.category not in ('NEW', 'CHANGED') or
                    a.file_format not in ('json', 'csv') or
                    a.file_format != candidate_format(path) or
                    fmt != a.file_format or
                    a.source_code != source_code or
                    a.expected_bytes != file_size or
                    a.git_blob_id != git or a.lfs_sha256 != lfs or
                    record_count < 1):
                raise RuntimeError('Candidate path/source/record mismatch: ' + path)
            candidate_counts[fid] = record_count
            candidate_records += record_count
    if (seen_paths != set(raw) or len(candidate_counts) != r.candidate_files or
            candidate_records != r.candidate_records or
            reused_records != req.reuse.expected_reused_records):
        raise RuntimeError('Manifest candidate/REUSE coverage or record count drift')
    return candidate_counts, AuditCounts(len(rows), len(reuse_links),
                                         len(candidate_counts), candidate_records,
                                         reused_records, reused_records + candidate_records)


def audit_physical_candidates(cur, counts: dict[int, int], expected_records: int) -> None:
    """Read-only physical row count + JSON locator/member audit for every candidate."""
    ids = sorted(counts)
    cur.execute('''SELECT file_id,COUNT(*),COUNT(DISTINCT record_locator),
                          COUNT(*) FILTER (WHERE member_id IS NOT NULL)
                   FROM raw.raw_records WHERE file_id=ANY(%s::bigint[])
                   GROUP BY file_id''', (ids,))
    found = cur.fetchall()
    if (len(found) != len(counts) or sum(n for _, n, _, _ in found) != expected_records
            or any(fid not in counts or n != counts[fid] or unique != n or members != 0
                   for fid, n, unique, members in found)):
        raise RuntimeError('Physical candidate raw record integrity mismatch')


def audit_database(cur, req: ManifestRequest, raw: dict[str, dict],
                   baseline: dict[str, FileIdentity], sources: set[str], *,
                   allow_csv_planning: bool = False) -> AuditCounts:
    """Rebuild the original plan inside a caller-owned DB transaction."""
    req.validate()
    r = req.reuse.registration
    if type(allow_csv_planning) is not bool:
        raise ValueError('Invalid CSV manifest audit permission')
    cur.execute('SELECT current_database()')
    if cur.fetchone() != (DB,):
        raise RuntimeError('Wrong PostgreSQL database')
    baseline_revision, existing, state = inspect_db(cur, req.reuse)
    if baseline_revision == r.target_revision or set(baseline) != set(existing):
        raise RuntimeError('Pinned baseline metadata drift')
    if (state.repo_id, state.revision, state.status, state.manifest_files,
            state.raw_files, state.manifest_sha, state.manifest_path) != (
            r.repo_id, r.target_revision, 'in_progress', r.manifest_files,
            r.raw_files, None, None):
        raise RuntimeError('Snapshot not manifest-registration-ready')
    target = manifest_identities(raw)
    if allow_csv_planning:
        plan, links = verify_plan(req.reuse, baseline, target,
                                  r.manifest_files - r.raw_files, existing, sources,
                                  allow_csv_planning=True)
    else:
        plan, links = verify_plan(req.reuse, baseline, target,
                                  r.manifest_files - r.raw_files, existing, sources)
    if plan.fingerprint != r.fingerprint:
        raise RuntimeError('Reconstructed plan fingerprint drift')
    # Snapshot-only WHERE would catch extra observations even with wrong repo_id.
    cur.execute('''SELECT o.relative_path,o.repo_id,o.presence_status,o.manifest_size_bytes,
                          o.git_blob_id,o.lfs_sha256,
                          f.file_id,f.repo_id,f.relative_path,f.file_size_bytes,
                          f.content_sha256,f.archive_path,f.archive_state,f.parse_state,
                          f.record_count,s.source_id,s.source_code,f.file_format
                   FROM raw.file_observations o
                   LEFT JOIN raw.raw_files f ON f.file_id=o.file_id
                   LEFT JOIN raw.raw_sources s ON s.source_id=f.source_id
                   WHERE o.snapshot_id=%s''', (req.reuse.target_snapshot_id,))
    candidate_counts, summary = validate_observations(cur.fetchall(), raw, plan, links, req)
    audit_physical_candidates(cur, candidate_counts, r.candidate_records)
    return summary


def register_in_transaction(cur, req: ManifestRequest, raw: dict[str, dict],
                            baseline: dict[str, FileIdentity], sources: set[str],
                            *, root: Path = ROOT,
                            allow_csv_planning: bool = False) -> AuditCounts:
    """Caller owns commit/rollback. Lock, rehash both manifest files, audit, update TWO fields."""
    if type(allow_csv_planning) is not bool:
        raise ValueError('Invalid manifest write-mode CSV permission')
    req.validate()
    r = req.reuse.registration
    cur.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (r.repo_id,))
    if cur.fetchone() != (True,):
        raise RuntimeError('Concurrent repository writer/registrar holds lock')
    cur.execute('''SELECT snapshot_id FROM raw.snapshot_runs
                   WHERE snapshot_id=%s AND repo_id=%s FOR UPDATE''',
                (req.reuse.target_snapshot_id, r.repo_id))
    if cur.fetchone() != (req.reuse.target_snapshot_id,):
        raise RuntimeError('Target snapshot row lock failed')
    checked_raw, _ = load_verified_manifest(req, root=root)
    if checked_raw != raw:
        raise RuntimeError('Manifest archive changed before registration')
    if allow_csv_planning:
        summary = audit_database(cur, req, checked_raw, baseline, sources,
                                 allow_csv_planning=True)
    else:
        summary = audit_database(cur, req, checked_raw, baseline, sources)
    cur.execute('''UPDATE raw.snapshot_runs SET manifest_sha256=%s,manifest_archive_path=%s
                   WHERE snapshot_id=%s AND repo_id=%s AND hf_revision=%s
                     AND status='in_progress' AND manifest_file_count=%s
                     AND raw_scope_file_count=%s AND manifest_sha256 IS NULL
                     AND manifest_archive_path IS NULL
                   RETURNING snapshot_id,status,manifest_sha256,manifest_archive_path''',
                (req.manifest_sha256, req.archive_relative_path,
                 req.reuse.target_snapshot_id, r.repo_id, r.target_revision,
                 r.manifest_files, r.raw_files))
    if cur.fetchone() != (req.reuse.target_snapshot_id, 'in_progress',
                          req.manifest_sha256, req.archive_relative_path):
        raise RuntimeError('Manifest registration conflict or unexpected row update')
    return summary


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    check_mode(args, parser)
    req = build_request(args)
    r = req.reuse.registration
    raw, byte_count = load_verified_manifest(req)
    # Source registry and baseline observations are only read; manifest target
    # identities come entirely from the existing SHA256-verified local report.
    from scripts.tools.check_raw_updates import load_baseline
    from scripts.tools.raw_sync_preflight import read_existing_source_codes
    base_rev, baseline = load_baseline(r.baseline_snapshot, r.repo_id)
    if base_rev == r.target_revision:
        raise RuntimeError('Target revision equals baseline revision')
    sources = read_existing_source_codes()
    import psycopg
    if args.plan:
        with psycopg.connect(**read_only_args()) as conn:
            with conn.cursor() as cur:
                if args.allow_csv_manifest_registration_plan_only:
                    summary = audit_database(cur, req, raw, baseline, sources,
                                             allow_csv_planning=True)
                else:
                    summary = audit_database(cur, req, raw, baseline, sources)
        print('=== M4.6.3e.2 | GENERIC MANIFEST REGISTRATION PLAN ===')
        print('Target snapshot:', req.reuse.target_snapshot_id, '| revision:', r.target_revision)
        print('Verified local immutable manifest:', req.archive_relative_path)
        print('Verified manifest bytes:', byte_count)
        print('REUSE:', summary.reuse, '| candidates:', summary.candidates)
        print('Candidate raw records:', summary.candidate_records)
        print('Projected record references:', summary.record_references)
        print('Database writes: NONE | Snapshot status unchanged')
        print('M4.6.3e.2 MANIFEST REGISTRATION PLAN: PASS (NOT APPROVAL TO COMMIT)')
        return 0
    params = db_parameters()
    params['application_name'] = 'm46_manifest_registration'
    with psycopg.connect(**params) as conn:
        if conn.autocommit:
            raise RuntimeError('Autocommit is unsafe')
        with conn.cursor() as cur:
            if args.allow_csv_manifest_registration_write:
                summary = register_in_transaction(cur, req, raw, baseline, sources,
                                                  allow_csv_planning=True)
            else:
                summary = register_in_transaction(cur, req, raw, baseline, sources)
    print('Manifest fields registered: 2 | target snapshot:', req.reuse.target_snapshot_id)
    print('Observations:', summary.observations, '| candidate records:', summary.candidate_records)
    print('Snapshot status remains in_progress; raw data writes: NONE')
    print('M4.6.3e.2 MANIFEST REGISTRATION: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
