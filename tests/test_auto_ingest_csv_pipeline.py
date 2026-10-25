"""Mixed CSV/JSON automation integration without a live PostgreSQL connection.

HF transport and SQL storage are replaced; manifest bytes, cache validation,
archive publication, parsing, writer SQL, checkpoints and journals are real.
"""
from contextlib import nullcontext
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.orchestration import auto_ingest
from scripts.orchestration.auto_ingest_backend import Backend
from scripts.incremental.check_raw_updates import FileIdentity
from scripts.incremental.download_incremental_candidates import DownloadContext
from scripts.incremental.incremental_execution_context import (
    CandidateSpec, PlannedContext, RegisteredContext,
)
from scripts.incremental.incremental_writer_core import (
    prepare_csv_version, prepare_json_version, write_prepared_version,
)
from scripts.tools.incremental_sync import SyncAction, SyncPlan


REPO = auto_ingest.DEFAULT_REPO
REVISION = "a" * 40
CSV_PATH = "2026_2027_new/football_data_raw_2627.csv"
JSON_PATH = "raw_data/fpl/sample.json"
CSV_BODY = b'HomeTeam,AwayTeam,Score\nArsenal,Chelsea,2-1\nChelsea,Arsenal,0-0\n'
JSON_BODY = b'[{"id":7,"finished":true},{"id":8,"finished":false}]'


class MemoryCursor:
    """Persistent SQL storage for the real single-file transactional writer."""

    def __init__(self, backend):
        self.backend = backend
        self.nextrow = None
        self.rowcount = 0

    def execute(self, sql, args=None):
        statement = " ".join(sql.split())
        self.rowcount = 0
        store = self.backend
        if "SELECT current_database()" in statement:
            self.nextrow = ("epl_raw_storage_v2",)
        elif "pg_advisory_xact_lock" in statement:
            self.nextrow = (True,)
        elif "SELECT status FROM raw.snapshot_runs" in statement:
            self.nextrow = (store.status,)
        elif "SELECT source_id FROM raw.raw_sources" in statement:
            self.nextrow = ({"football_data": 1, "fpl": 2}[args[0]],)
        elif "SELECT f.file_id,f.file_format" in statement:
            self.nextrow = store.files.get(tuple(args))
        elif "INSERT INTO raw.raw_files" in statement:
            repo, path, source_id, fmt, sha, size, archive, _ = args
            fid = 100 + len(store.files)
            source = {1: "football_data", 2: "fpl"}[source_id]
            store.files[(repo, path, sha)] = (fid, fmt, size, archive, "verified", "pending", None, source)
            self.nextrow = (fid,)
        elif "UPDATE raw.raw_files SET parse_state='success'" in statement:
            count, fid = args
            for key, row in list(store.files.items()):
                if row[0] == fid:
                    store.files[key] = row[:5] + ("success", count, row[7])
                    self.rowcount = 1
        elif "SELECT COUNT(*) FROM raw.raw_records" in statement:
            self.nextrow = (len(store.records.get(args[0], ())),)
        elif "INSERT INTO raw.file_observations" in statement:
            snapshot, _, path, fid, size, git, lfs = args
            store.observations.setdefault((snapshot, path), (fid, "linked", size, git, lfs))
        elif "SELECT file_id,presence_status" in statement:
            self.nextrow = store.observations.get(tuple(args))
        else:
            raise AssertionError("Unexpected writer SQL: " + statement)
        return self

    def executemany(self, sql, rows):
        if "INSERT INTO raw.raw_records" not in sql:
            raise AssertionError("Unexpected batch SQL")
        for fid, locator, kind, data in rows:
            records = self.backend.records.setdefault(fid, [])
            if any(existing[0] == locator for existing in records):
                raise AssertionError("Duplicate record locator would be inserted")
            records.append((locator, kind, data.obj))
            self.backend.records_inserted += 1

    def fetchone(self):
        return self.nextrow


class MixedBackend(Backend):
    """Keep production preparation/writer APIs; replace external state only."""

    def __init__(self, root, csv_body=CSV_BODY):
        super().__init__(root, timeout_seconds=1)
        self.payloads = {CSV_PATH: csv_body, JSON_PATH: JSON_BODY}
        self.status = None
        self.files = {}
        self.records = {}
        self.observations = {}
        self.records_inserted = 0
        self.stage_writes = []
        actions, specs, target = [], [], {}
        for path, payload in sorted(self.payloads.items()):
            digest = hashlib.sha256(payload).hexdigest()
            category = "CHANGED" if path.endswith(".csv") else "NEW"
            source = "football_data" if path.endswith(".csv") else "fpl"
            fmt = "csv" if path.endswith(".csv") else "json"
            target[path] = FileIdentity(len(payload), None, digest)
            actions.append(SyncAction("UPSERT_VERSION_AFTER_REHASH", path, category,
                                      1 if category == "CHANGED" else None,
                                      source, fmt, len(payload), digest, None))
            specs.append(CandidateSpec(path, category, len(payload), source, None, digest))
        total = sum(len(payload) for payload in self.payloads.values())
        plan = SyncPlan(tuple(actions), 1, 0, 1, 2, total, "b" * 64)
        self.planned = PlannedContext(REPO, 367, "c" * 40, REVISION, plan.fingerprint,
                                      1, 2, 2, 0, 2, total, 1, 0, tuple(specs))
        self.download_context = DownloadContext(REPO, 367, "c" * 40, REVISION,
                                                plan, target, tuple(actions), 0, True)

    def resolve(self, repo, ref):
        return REVISION

    def target(self, repo, sha):
        return None if self.status is None else SimpleNamespace(status=self.status, snapshot_id=368)

    def plan(self, repo, sha):
        return SimpleNamespace(decision="PLAN_NEW_REVISION", planned=self.planned, target_snapshot_id=None)

    def _context(self, planned):
        if planned != self.planned:
            raise RuntimeError("Pinned plan drift")
        return self.download_context

    def remote_tree(self, context):
        return [SimpleNamespace(path=path, size=len(payload), blob_id=None,
                                lfs={"sha256": hashlib.sha256(payload).hexdigest()})
                for path, payload in self.payloads.items()]

    def download(self, *, filename, revision, cache_dir, local_files_only, **kwargs):
        if revision != REVISION:
            raise AssertionError("HF commit was not pinned")
        path = Path(cache_dir) / "fixture" / filename
        if not local_files_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.payloads[filename])
        if not path.is_file():
            raise FileNotFoundError(path)
        return str(path)

    def registered(self, prepared):
        if self.status != "in_progress":
            raise RuntimeError("Registration not committed")
        return RegisteredContext(prepared, 368)

    def preflight(self, stage, context, log_dir):
        self.writer_argv(stage, context)  # Real allowlist/context validation.

    def write(self, stage, context, log_dir, *, preflight=True, **kwargs):
        self.stage_writes.append(stage)
        if stage == "SNAPSHOT_REGISTRATION":
            if self.status is not None:
                raise AssertionError("Duplicate snapshot registration")
            self.status = "in_progress"
        elif stage == "CANDIDATE_INGESTION":
            self.write_candidates(context)
        elif stage == "MANIFEST_REGISTRATION":
            self.manifest_registered = True
        elif stage == "FINALIZATION":
            if not self.verify("CANDIDATE_INGESTION", context):
                raise RuntimeError("Candidates incomplete")
            self.status = "completed"

    def write_candidates(self, context):
        for spec in context.prepared.planned.candidates:
            receipt = next(row for row in context.prepared.candidate_receipts if row.path == spec.path)
            archive = self.root / "raw-archive-v2" / "objects" / receipt.content_sha256[:2] / receipt.content_sha256
            prepare = prepare_csv_version if spec.path.endswith(".csv") else prepare_json_version
            version = prepare(REPO, spec.path, REVISION, spec.source_code, archive,
                              self.download_context.target[spec.path], root=self.root)
            write_prepared_version(MemoryCursor(self), 368, version, root=self.root)

    def verify(self, stage, context):
        if stage == "SNAPSHOT_REGISTRATION":
            return isinstance(context, RegisteredContext) and self.status is not None
        if stage == "REUSE_LINKING":
            return self.status is not None  # Fixture has zero REUSE files.
        if stage == "CANDIDATE_INGESTION":
            prepared = context.prepared
            if len(self.observations) != len(prepared.candidate_receipts):
                return False
            for receipt in prepared.candidate_receipts:
                version = self.files.get((REPO, receipt.path, receipt.content_sha256))
                if version is None or version[5:7] != ("success", receipt.records):
                    return False
                if len(self.records.get(version[0], ())) != receipt.records:
                    return False
            return True
        if stage == "MANIFEST_REGISTRATION":
            return getattr(self, "manifest_registered", False)
        if stage == "FINALIZATION":
            return self.status == "completed"
        raise AssertionError(stage)

    def completed(self, registered):
        from scripts.incremental.finalize_incremental_snapshot import scan_archives
        prepared = registered.prepared
        items = tuple({"relative_path": row.path,
                       "archive_path": f"raw-archive-v2/objects/{row.content_sha256[:2]}/{row.content_sha256}",
                       "sha256": row.content_sha256, "size_bytes": row.size_bytes,
                       "git_blob_id": "", "lfs_sha256": row.content_sha256}
                      for row in prepared.candidate_receipts)
        scan = scan_archives(items, root=self.root)
        valid = (self.status == "completed" and self.verify("CANDIDATE_INGESTION", registered)
                 and scan.total_bytes == sum(len(body) for body in self.payloads.values()))
        return {"verified": valid, "snapshot_id": 368,
                "record_references": sum(len(rows) for rows in self.records.values()),
                "candidate_formats": sorted(row[1] for row in self.files.values())}


class MixedCsvJsonAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_pipeline(self, backend):
        with patch("scripts.incremental.prepare_incremental_manifest.fetch_full_tree",
                   side_effect=backend.remote_tree), \
             patch("huggingface_hub.hf_hub_download", side_effect=backend.download):
            return auto_ingest.run_once(root=self.root, backend=backend, execute=True,
                                        lock_factory=lambda: nullcontext(lambda: None))

    def test_mixed_csv_json_runs_real_parser_archive_writer_and_completed_pipeline(self):
        backend = MixedBackend(self.root)
        receipt, code = self.run_pipeline(backend)
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        self.assertEqual(receipt["audit"]["candidate_formats"], ["csv", "json"])
        self.assertEqual(backend.records_inserted, 4)
        all_records = [row for rows in backend.records.values() for row in rows]
        csv_records = [row for row in all_records if row[1] == "csv_row"]
        self.assertEqual([row[2]["HomeTeam"] for row in csv_records], ["Arsenal", "Chelsea"])
        self.assertEqual([row[2]["Score"] for row in csv_records], ["2-1", "0-0"])
        self.assertEqual(len([row for row in all_records if row[1] == "json_item"]), 2)
        self.assertEqual(len(backend.files), 2)
        self.assertEqual(len(backend.observations), 2)
        self.assertIn("CANDIDATE_INGESTION", backend.stage_writes)

    def test_completed_mixed_revision_and_existing_versions_never_duplicate_records(self):
        backend = MixedBackend(self.root)
        first, code = self.run_pipeline(backend)
        self.assertEqual(code, 0, first)
        _, _, registered = backend.read_chain(REVISION)
        backend.status = "in_progress"
        backend.write_candidates(registered)  # Re-run real file writer on existing versions.
        self.assertEqual(backend.records_inserted, 4)
        self.assertEqual(len(backend.observations), 2)
        backend.status = "completed"
        writes_before = list(backend.stage_writes)
        second, code = self.run_pipeline(backend)
        self.assertEqual((code, second["state"]), (0, "SKIPPED_CURRENT"), second)
        self.assertFalse(second["write_executed"])
        self.assertEqual(backend.stage_writes, writes_before)
        self.assertEqual(backend.records_inserted, 4)

    def test_malformed_csv_stops_before_snapshot_or_record_writes(self):
        backend = MixedBackend(self.root, csv_body=b"HomeTeam,HomeTeam\nArsenal,Chelsea\n")
        receipt, code = self.run_pipeline(backend)
        self.assertEqual(code, 2, receipt)
        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertFalse(receipt["write_executed"])
        self.assertIsNone(backend.status)
        self.assertEqual(backend.stage_writes, [])
        self.assertEqual(backend.files, {})
        self.assertEqual(backend.records_inserted, 0)


if __name__ == "__main__":
    unittest.main()
