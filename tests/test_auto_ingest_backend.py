"""Offline integration coverage for automatic M4 preparation and dispatch."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.orchestration.auto_ingest_backend import Backend, CACHE_DIR
from scripts.incremental.check_raw_updates import FileIdentity
from scripts.incremental.download_incremental_candidates import DownloadContext
from scripts.incremental.incremental_archive_promotion import audit_candidates
from scripts.incremental.incremental_cache_gate import CandidateCheck
from scripts.incremental.incremental_execution_context import (
    CandidateSpec, PlannedContext, RegisteredContext,
)
from scripts.incremental.prepare_incremental_manifest import publish_manifest
from scripts.tools.incremental_sync import SyncAction, SyncPlan


REPO = "Toibinguz/epl-premier-league-dataset"
REVISION = "a" * 40


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.backend = Backend(self.root, timeout_seconds=1)

    def fixture(self):
        payloads = {
            "2026_2027_new/football_data_raw_2627.csv": b"id,name\n1,Alpha\n",
            "raw_data/fpl/sample.json": b'[{"id":7}]',
        }
        actions, specs, checked, target, local = [], [], [], {}, {}
        cache = self.root / "reports" / "fixture-cache"
        cache.mkdir(parents=True)
        for index, (path, payload) in enumerate(sorted(payloads.items())):
            digest = hashlib.sha256(payload).hexdigest()
            source = "football_data" if path.endswith(".csv") else "fpl"
            fmt = "csv" if path.endswith(".csv") else "json"
            target[path] = FileIdentity(len(payload), None, digest)
            specs.append(CandidateSpec(path, "NEW", len(payload), source, None, digest))
            actions.append(SyncAction("UPSERT_VERSION_AFTER_REHASH", path, "NEW", None,
                                      source, fmt, len(payload), digest, None))
            checked.append(CandidateCheck(path, "NEW", len(payload), digest, 1))
            local[path] = cache / str(index)
            local[path].write_bytes(payload)
        total = sum(len(value) for value in payloads.values())
        plan = SyncPlan(tuple(actions), 1, 0, 1, 2, total, "b" * 64)
        planned = PlannedContext(REPO, 1, "c" * 40, REVISION, plan.fingerprint,
                                 1, 2, 2, 0, 2, total, 1, 0, tuple(specs))
        context = DownloadContext(REPO, 1, planned.baseline_revision, REVISION,
                                  plan, target, tuple(actions), 0, True)
        manifest = {
            "repo_id": REPO, "repo_type": "dataset", "resolved_commit": REVISION,
            "file_count": 2,
            "files": [
                {"relative_path": spec.path, "format": "csv" if spec.path.endswith(".csv") else "json",
                 "hf_revision": REVISION, "size_bytes": spec.expected_bytes,
                 "git_blob_id": "", "lfs_sha256": spec.lfs_sha256}
                for spec in specs
            ],
        }
        payload = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        publish_manifest(self.root, REVISION, digest, payload)
        return planned, context, tuple(checked), local, digest

    def prepare_fixture(self):
        planned, context, checked, local, digest = self.fixture()
        with patch.object(self.backend, "_context", return_value=context), \
             patch("scripts.incremental.download_incremental_candidates.download_candidates",
                   return_value=(checked, 2)) as download, \
             patch("scripts.incremental.promote_incremental_archive.resolve_cached_candidates",
                   return_value=local), \
             patch("scripts.incremental.incremental_archive_promotion.audit_candidates",
                   wraps=audit_candidates) as audit:
            prepared = self.backend.prepare(planned, digest)
        self.assertTrue(all(call.kwargs["allow_csv"] is True for call in audit.call_args_list))
        self.assertEqual(download.call_args.args[0], context)
        return prepared

    def test_prepare_mixed_csv_json_rehashes_archives_and_parses_writer(self):
        prepared = self.prepare_fixture()
        self.assertEqual(prepared.expected_records, 2)
        self.assertEqual(len(prepared.candidate_receipts), 2)
        for receipt in prepared.candidate_receipts:
            archive = (self.root / "raw-archive-v2" / "objects"
                       / receipt.content_sha256[:2] / receipt.content_sha256)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), receipt.content_sha256)
        self.assertIsNone(self.backend.read_chain(REVISION)[1])

    def test_prepare_wrong_manifest_hash_blocks_before_registration(self):
        planned, context, checked, local, _ = self.fixture()
        with patch.object(self.backend, "_context", return_value=context), \
             patch("scripts.incremental.download_incremental_candidates.download_candidates",
                   return_value=(checked, 2)), \
             patch("scripts.incremental.promote_incremental_archive.resolve_cached_candidates",
                   return_value=local):
            with self.assertRaises(RuntimeError):
                self.backend.prepare(planned, "f" * 64)

    def test_writer_argv_all_stages_pin_revision_and_csv_opt_in(self):
        prepared = self.prepare_fixture()
        registered = RegisteredContext(prepared, 2)
        stages = (
            "SNAPSHOT_REGISTRATION", "REUSE_LINKING", "CANDIDATE_INGESTION",
            "MANIFEST_REGISTRATION", "FINALIZATION",
        )
        for stage in stages:
            context = prepared if stage == "SNAPSHOT_REGISTRATION" else registered
            argv = self.backend.writer_argv(stage, context)
            self.assertEqual(argv[0], sys.executable)
            self.assertNotIn("--plan", argv)
            self.assertEqual(argv[argv.index("--target-revision") + 1], REVISION)
            self.assertTrue(any(flag.startswith("--allow-csv-") for flag in argv))
            if stage in ("SNAPSHOT_REGISTRATION", "CANDIDATE_INGESTION"):
                self.assertEqual(argv[argv.index("--cache-dir") + 1], CACHE_DIR)
        with self.assertRaises(ValueError):
            self.backend.writer_argv("SHELL", prepared)

    def test_registration_verification_absent_and_ambiguous_target(self):
        prepared = self.prepare_fixture()
        with patch.object(self.backend, "target", return_value=None):
            self.assertFalse(self.backend.verify("SNAPSHOT_REGISTRATION", prepared))
        with patch.object(self.backend, "target", return_value=SimpleNamespace(status="in_progress")):
            with self.assertRaisesRegex(RuntimeError, "reconcile"):
                self.backend.verify("SNAPSHOT_REGISTRATION", prepared)

    def test_blocked_verifier_never_becomes_incomplete(self):
        registered = RegisteredContext(self.prepare_fixture(), 2)
        result = SimpleNamespace(status="BLOCKED", blockers=("corrupt archive",))
        with patch.object(self.backend, "target", return_value=SimpleNamespace(status="in_progress")), \
             patch("scripts.incremental.incremental_post_execution_verifier.verify_read_only", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "corrupt archive"):
                self.backend.verify("CANDIDATE_INGESTION", registered)

    def test_failed_preflight_never_launches_writer(self):
        prepared = self.prepare_fixture()
        with patch.object(self.backend, "_run", side_effect=RuntimeError("preflight blocked")) as run:
            with self.assertRaisesRegex(RuntimeError, "preflight blocked"):
                self.backend.write("SNAPSHOT_REGISTRATION", prepared, self.root / "reports" / "logs")
        self.assertEqual(run.call_count, 1)
        self.assertTrue(run.call_args.kwargs["readonly"])
        self.assertIn("--plan", run.call_args.args[0])

    def test_cli_preflight_is_readonly_and_shell_disabled(self):
        log_dir = self.backend._log_directory(self.root / "reports" / "logs")
        with patch("subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 0
            self.backend._run((sys.executable, "-m", "example"), log_dir / "plan.log", readonly=True)
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertIn("default_transaction_read_only=on", popen.call_args.kwargs["env"]["PGOPTIONS"])

    def test_child_timeout_stops_process_and_does_not_retry(self):
        log_dir = self.backend._log_directory(self.root / "reports" / "logs")
        with self.assertRaises(subprocess.TimeoutExpired):
            self.backend._run((sys.executable, "-c", "import time; time.sleep(10)"),
                              log_dir / "timeout.log", readonly=False)

    def test_logs_cannot_escape_reports(self):
        with self.assertRaises(ValueError):
            self.backend._log_directory(self.root / "other")

    def test_plan_blocks_unrelated_in_progress_revision(self):
        with patch("scripts.incremental.register_incremental_snapshot.db_parameters", return_value={}), \
             patch("psycopg.connect") as connect, \
             patch("scripts.incremental.plan_incremental_orchestrator.plan_read_only") as plan:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("epl_raw_storage_v2", "on")
            cursor.fetchall.return_value = [(20, "e" * 40)]
            with self.assertRaisesRegex(RuntimeError, "Another revision"):
                self.backend.plan(REPO, REVISION)
        plan.assert_not_called()

    def reuse_fixture(self):
        planned, context, _, _, _ = self.fixture()
        planned = replace(planned, expected_full_files=3, expected_raw_files=3,
                          expected_reuse=1, expected_reused_records=1)
        path = "raw_data/understat/reused.csv"
        payload = b"id\n1\n"
        digest = hashlib.sha256(payload).hexdigest()
        target = dict(context.target)
        target[path] = FileIdentity(len(payload), None, digest)
        context = replace(context, target=target)
        archive = f"raw-archive-v2/objects/{digest[:2]}/{digest}"
        row = (path, REPO, "linked", len(payload), None, digest, 5, REPO, path,
               len(payload), digest, archive, "verified", "success", 1)
        return planned, context, row, self.root / archive, payload

    def test_reuse_archive_missing_blocks_before_registration(self):
        planned, context, row, archive, _ = self.reuse_fixture()
        with patch.object(self.backend, "_reuse_rows", return_value=(row,)):
            with self.assertRaisesRegex(RuntimeError, "missing or corrupt before snapshot registration"):
                self.backend.verify_reuse_archives(planned, context)
        self.assertFalse(archive.exists())

    def test_reuse_archive_corruption_is_detected_by_actual_hash_scan(self):
        planned, context, row, archive, payload = self.reuse_fixture()
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"X" * len(payload))
        with patch.object(self.backend, "_reuse_rows", return_value=(row,)):
            with self.assertRaisesRegex(RuntimeError, "missing or corrupt before snapshot registration"):
                self.backend.verify_reuse_archives(planned, context)

    def test_reuse_archive_success_requires_metadata_and_hashes(self):
        planned, context, row, archive, payload = self.reuse_fixture()
        archive.parent.mkdir(parents=True)
        archive.write_bytes(payload)
        with patch.object(self.backend, "_reuse_rows", return_value=(row,)):
            receipt = self.backend.verify_reuse_archives(planned, context)
        self.assertEqual(receipt, {"verified": True, "objects": 1, "bytes": len(payload)})
        invalid = list(row)
        invalid[12] = "pending"
        with patch.object(self.backend, "_reuse_rows", return_value=(tuple(invalid),)):
            with self.assertRaisesRegex(RuntimeError, "Invalid baseline REUSE"):
                self.backend.verify_reuse_archives(planned, context)

    def test_reuse_preflight_failure_prevents_candidate_download(self):
        planned, context, _, _, digest = self.fixture()
        with patch.object(self.backend, "_context", return_value=context), \
             patch.object(self.backend, "verify_reuse_archives", side_effect=RuntimeError("missing baseline object")), \
             patch("scripts.incremental.download_incremental_candidates.download_candidates") as download:
            with self.assertRaisesRegex(RuntimeError, "missing baseline object"):
                self.backend.prepare(planned, digest)
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
