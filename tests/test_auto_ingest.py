"""Offline automation behavior with real immutable checkpoints and attempt journals.

Only source, archive and PostgreSQL operations are replaced by an in-memory
backend. No network requests, database connections or real M4 writers run.
"""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.orchestration import auto_ingest as mod
from scripts.incremental.incremental_execution_context import (
    CandidateReceipt, CandidateSpec, PlannedContext, PreparedContext,
    RegisteredContext, checkpoint_digest, publish_checkpoint,
)
from scripts.incremental.incremental_recovery_audit import read_checkpoints
from scripts.orchestration.durable_attempt_recovery_audit import audit_attempt
from scripts.orchestration.unified_incremental_runner import local_process_lock
from scripts.orchestration.write_attempt_guard import reserve_write_attempt


REPO = mod.DEFAULT_REPO
REVISION = "a" * 40
BASELINE = "1" * 40
NEXT_REVISION = "b" * 40
MANIFEST = "c" * 64


def contexts(revision=REVISION):
    candidate = CandidateSpec("2026_2027_new/football_data_raw_2627.csv", "CHANGED",
                              12, "football_data", "d" * 40, None)
    planned = PlannedContext(
        repo_id=REPO, baseline_snapshot_id=367, baseline_revision=BASELINE,
        target_revision=revision, plan_fingerprint="e" * 64,
        expected_baseline_files=2, expected_full_files=3, expected_raw_files=2,
        expected_reuse=1, expected_files=1, expected_bytes=12,
        expected_baseline_records=30, expected_reused_records=20,
        candidates=(candidate,),
    )
    prepared = PreparedContext(
        planned, (CandidateReceipt(candidate.path, 12, "f" * 64, 2),), 2, 22,
        MANIFEST, f"reports/m4_6_manifests/{revision}/hf_manifest_v2.json",
        f"raw-archive-v2/manifests/{MANIFEST[:2]}/{MANIFEST}.json",
    )
    return planned, prepared, RegisteredContext(prepared, 368)


class FakeBackend:
    def __init__(self, root):
        self.root = root
        self.head = REVISION
        self.calls = []
        self.write_calls = []
        self.statuses = {}
        self.verified = set()
        self.prepare_failures = 0
        self.failure_stage = None
        self.failure_after_effect = False
        self.audit_passes = True
        self.plan_decision = "PLAN_NEW_REVISION"

    def resolve(self, repo, ref):
        self.calls.append(("resolve", repo, ref))
        return self.head

    def read_chain(self, revision):
        self.calls.append(("read_chain", revision))
        return read_checkpoints(self.root, revision)

    def target(self, repo, revision):
        self.calls.append(("target", revision))
        status = self.statuses.get(revision)
        return None if status is None else SimpleNamespace(status=status, snapshot_id=368)

    def plan(self, repo, revision):
        self.calls.append(("plan", revision))
        return SimpleNamespace(decision=self.plan_decision, planned=contexts(revision)[0],
                               target_snapshot_id=None)

    def publish(self, context):
        self.calls.append(("publish", type(context).__name__))
        return publish_checkpoint(self.root, context)

    def manifest(self, planned):
        self.calls.append(("manifest", planned.target_revision))
        return MANIFEST

    def prepare(self, planned, manifest_sha):
        self.calls.append(("prepare", planned.target_revision))
        if self.prepare_failures:
            self.prepare_failures -= 1
            raise OSError("Transient source download failed")
        assert manifest_sha == MANIFEST
        return contexts(planned.target_revision)[1]

    def registered(self, prepared):
        self.calls.append(("registered", prepared.planned.target_revision))
        assert self.statuses[prepared.planned.target_revision] == "in_progress"
        return RegisteredContext(prepared, 368)

    def verify(self, stage, context):
        if isinstance(context, PreparedContext):
            assert stage == "SNAPSHOT_REGISTRATION"
            revision = context.planned.target_revision
        else:
            assert isinstance(context, RegisteredContext)
            revision = context.prepared.planned.target_revision
        self.calls.append(("verify", stage, revision))
        return (revision, stage) in self.verified

    def writer_argv(self, stage, context):
        # The real durable ledger hashes but never executes this argv.
        return ("python", "-m", "tests.fake_writer", "--stage", stage)

    def write(self, stage, context, log_dir):
        revision = (context.planned.target_revision if isinstance(context, PreparedContext)
                    else context.prepared.planned.target_revision)
        self.calls.append(("write", stage, revision))
        self.write_calls.append(stage)
        assert log_dir.is_relative_to(self.root)
        if self.failure_stage == stage and not self.failure_after_effect:
            raise RuntimeError("Writer outcome is unknown")
        if stage == "SNAPSHOT_REGISTRATION":
            assert isinstance(context, PreparedContext)
            self.statuses[revision] = "in_progress"
        if stage == "FINALIZATION":
            self.statuses[revision] = "completed"
        self.verified.add((revision, stage))
        if self.failure_stage == stage:
            raise RuntimeError("Writer committed but response was lost")

    def completed(self, registered):
        revision = registered.prepared.planned.target_revision
        self.calls.append(("completed", revision))
        return dict(verified=self.audit_passes, snapshot_id=registered.target_snapshot_id,
                    target_revision=revision, record_references=22)


class AutoIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="EPL automatic ingest ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = FakeBackend(self.root)

    def run_once(self, execute=True, **kwargs):
        return mod.run_once(root=self.root, backend=self.backend, execute=execute,
                            lock_factory=lambda: nullcontext(lambda: None), **kwargs)

    def active(self):
        return json.loads((self.root / "reports/auto_ingest/active.json").read_text(encoding="utf-8"))

    def save_active(self, revision=REVISION):
        path = self.root / "reports/auto_ingest/active.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(schema="auto-ingest-active-v1", repo_id=REPO,
                                        target_revision=revision, finished=False)), encoding="utf-8")
        return path

    def audit(self, stage):
        return audit_attempt(root=self.root, repo_id=REPO, target_revision=REVISION, stage=stage)

    def assert_no_external_writes(self):
        self.assertEqual(self.backend.write_calls, [])
        mutations = {"publish", "manifest", "prepare", "write"}
        self.assertFalse(any(call[0] in mutations for call in self.backend.calls))

    def test_completed_revision_skips_without_any_mutation(self):
        self.backend.statuses[REVISION] = "completed"
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("SKIPPED_CURRENT", 0))
        self.assertEqual(receipt["snapshot_id"], 368)
        self.assertFalse(receipt["write_executed"])
        self.assert_no_external_writes()
        self.assertFalse((self.root / "reports/auto_ingest/active.json").exists())

    def test_check_new_revision_only_plans_without_publishing_or_writing(self):
        receipt, code = self.run_once(execute=False)
        self.assertEqual((receipt["state"], code), ("READY_TO_INGEST", 0))
        self.assertFalse(receipt["write_authorized"])
        self.assertFalse(receipt["write_executed"])
        self.assertEqual(receipt["candidate_files"], 1)
        self.assertEqual(receipt["reuse_files"], 1)
        self.assert_no_external_writes()
        self.assertFalse((self.root / "reports/m4_6_runs").exists())

    def test_new_revision_completes_all_stages_and_persists_verified_journals(self):
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("COMPLETED", 0), receipt)
        self.assertEqual(self.backend.write_calls, list(mod.DB_STAGES))
        self.assertEqual([step["stage"] for step in receipt["steps"]], [
            "PLANNED", "MANIFEST_PREPARATION", "PREPARED", *mod.DB_STAGES,
            "COMPLETED_AUDIT",
        ])
        self.assertTrue(receipt["write_executed"])
        self.assertTrue(receipt["audit"]["verified"])
        self.assertEqual(read_checkpoints(self.root, REVISION), contexts())
        self.assertTrue(self.active()["finished"])
        for stage in mod.DB_STAGES:
            self.assertEqual(self.audit(stage).journal_status, "VERIFIED_AFTER_WRITE")

    def test_receipt_sharing_failure_retries_metadata_without_replaying_writer(self):
        replace = os.replace
        failed = []

        def replace_receipt(source, destination):
            if (not failed and Path(destination).name == "receipt.json"
                    and self.backend.write_calls):
                failed.append((Path(source), Path(source).read_bytes()))
                error = PermissionError(13, "Receipt is temporarily open")
                error.winerror = 5
                raise error
            if failed and Path(source) == failed[0][0]:
                self.assertEqual(Path(source).read_bytes(), failed[0][1])
            return replace(source, destination)

        metadata_os = SimpleNamespace(name="nt", replace=replace_receipt,
                                      fsync=os.fsync, environ=os.environ)
        with patch.object(mod, "os", metadata_os), patch.object(mod.time, "sleep") as sleep:
            receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("COMPLETED", 0), receipt)
        self.assertEqual(len(failed), 1)
        sleep.assert_called_once_with(0.01)
        self.assertEqual(self.backend.write_calls, list(mod.DB_STAGES))
        self.assertTrue(self.active()["finished"])

    @unittest.skipUnless(os.name == "nt", "Windows receipt sharing semantics required")
    def test_save_retries_a_real_reader_handle_until_it_closes(self):
        path = self.root / "receipt.json"
        mod._save(path, {"state": "RUNNING"})
        reader = path.open("rb")
        try:
            with patch.object(mod.time, "sleep", side_effect=lambda _delay: reader.close()) as sleep:
                mod._save(path, {"state": "COMPLETED"})
            sleep.assert_called_once_with(0.01)
        finally:
            reader.close()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"state": "COMPLETED"})
        self.assertEqual(list(self.root.glob(".auto-*")), [])

    def test_save_persistent_windows_denial_is_bounded_and_preserves_previous_receipt(self):
        path = self.root / "receipt.json"
        mod._save(path, {"state": "RUNNING"})
        previous = path.read_bytes()
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                error = PermissionError(13, "Persistent denial")
                error.winerror = code
                replace = Mock(side_effect=error)
                metadata_os = SimpleNamespace(name="nt", replace=replace, fsync=os.fsync)
                with patch.object(mod, "os", metadata_os), patch.object(mod.time, "sleep") as sleep:
                    with self.assertRaises(PermissionError) as caught:
                        mod._save(path, {"state": "COMPLETED"})
                self.assertIs(caught.exception, error)
                self.assertEqual(replace.call_count, 6)
                self.assertEqual(len({call.args[0] for call in replace.call_args_list}), 1)
                self.assertEqual([call.args[0] for call in sleep.call_args_list],
                                 [0.01, 0.02, 0.04, 0.08, 0.16])
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(self.root.glob(".auto-*")), [])

    def test_save_unrelated_filesystem_errors_are_not_retried(self):
        path = self.root / "receipt.json"
        mod._save(path, {"state": "RUNNING"})
        previous = path.read_bytes()
        windows_denial = PermissionError(13, "Not a Windows platform")
        windows_denial.winerror = 5
        cases = (("nt", PermissionError(13, "ACL denied")),
                 ("nt", FileNotFoundError(2, "Directory disappeared")),
                 ("posix", windows_denial))
        for platform, error in cases:
            with self.subTest(platform=platform, error=type(error).__name__):
                replace = Mock(side_effect=error)
                metadata_os = SimpleNamespace(name=platform, replace=replace, fsync=os.fsync)
                with patch.object(mod, "os", metadata_os), patch.object(mod.time, "sleep") as sleep:
                    with self.assertRaises(OSError) as caught:
                        mod._save(path, {"state": "COMPLETED"})
                self.assertIs(caught.exception, error)
                replace.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(self.root.glob(".auto-*")), [])

    def test_artifact_failure_resumes_pinned_plan_without_duplicate_db_writes(self):
        self.backend.prepare_failures = 1
        first, first_code = self.run_once()
        self.assertEqual((first["state"], first_code), ("BLOCKED", 2))
        self.assertEqual(first["failed_stage"], "CACHE_AND_ARCHIVE_PREPARATION")
        planned_path = self.root / f"reports/m4_6_runs/{REVISION}/planned.json"
        pinned = planned_path.read_bytes()
        self.assertEqual(read_checkpoints(self.root, REVISION)[1:], (None, None))
        self.assertFalse(self.active()["finished"])
        self.assertEqual(self.backend.write_calls, [])
        second, second_code = self.run_once()
        self.assertEqual((second["state"], second_code), ("COMPLETED", 0), second)
        self.assertTrue(second["resumed"])
        self.assertEqual(planned_path.read_bytes(), pinned)
        self.assertEqual(self.backend.write_calls, list(mod.DB_STAGES))
        self.assertEqual(len([call for call in self.backend.calls if call[0] == "plan"]), 1)

    def test_unknown_database_outcome_stays_fenced_even_if_effect_is_visible(self):
        self.backend.failure_stage = "CANDIDATE_INGESTION"
        self.backend.failure_after_effect = True
        first, first_code = self.run_once()
        self.assertEqual((first["state"], first_code), ("BLOCKED", 2))
        self.assertEqual(self.audit("CANDIDATE_INGESTION").state, "MANUAL_RECONCILIATION_REQUIRED")
        writes = list(self.backend.write_calls)
        self.backend.failure_stage = None
        second, second_code = self.run_once()
        self.assertEqual((second["state"], second_code), ("BLOCKED", 2))
        self.assertEqual(self.backend.write_calls, writes)
        self.assertFalse(second["write_executed"])
        self.assertIn("MANUAL_RECONCILIATION_REQUIRED", second["detail"])
        self.assertFalse(self.active()["finished"])

    def test_pending_revision_finishes_before_following_advanced_main(self):
        self.backend.prepare_failures = 1
        self.run_once()
        self.backend.head = NEXT_REVISION
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("COMPLETED", 0), receipt)
        self.assertEqual(receipt["target_revision"], REVISION)
        self.assertEqual(len([call for call in self.backend.calls if call[0] == "resolve"]), 1)
        self.assertFalse((self.root / f"reports/m4_6_runs/{NEXT_REVISION}").exists())

    def test_reserved_attempt_without_result_blocks_before_any_database_write(self):
        for context in contexts():
            self.backend.publish(context)
        self.backend.statuses[REVISION] = "in_progress"
        registered = contexts()[2]
        reserve_write_attempt(
            root=self.root, repo_id=REPO, target_revision=REVISION,
            stage="CANDIDATE_INGESTION", context_sha256=checkpoint_digest(registered),
            intent_sha256="0" * 64, target_snapshot_id=368,
            argv=self.backend.writer_argv("CANDIDATE_INGESTION", registered),
        )
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertEqual(self.audit("CANDIDATE_INGESTION").state, "AMBIGUOUS_ATTEMPT_BLOCKED")
        self.assertEqual(self.backend.write_calls, [])

    def test_historical_verified_journal_with_current_missing_evidence_blocks(self):
        self.run_once()
        self.save_active()
        self.backend.statuses[REVISION] = "in_progress"
        self.backend.verified.remove((REVISION, "REUSE_LINKING"))
        writes = list(self.backend.write_calls)
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertIn("conflicts with current database evidence", receipt["detail"])
        self.assertEqual(self.backend.write_calls, writes)
        self.assertFalse(receipt["write_executed"])

    def test_independently_verified_previous_stages_are_never_rewritten(self):
        for context in contexts():
            self.backend.publish(context)
        self.backend.statuses[REVISION] = "in_progress"
        self.backend.verified.update((REVISION, stage) for stage in mod.DB_STAGES[:3])
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("COMPLETED", 0), receipt)
        self.assertEqual(self.backend.write_calls, ["MANIFEST_REGISTRATION", "FINALIZATION"])
        self.assertEqual(self.audit("CANDIDATE_INGESTION").state, "NO_LOCAL_ATTEMPT")

    def test_completed_pending_run_reconciles_with_no_new_database_attempt(self):
        for context in contexts():
            self.backend.publish(context)
        self.save_active()
        self.backend.statuses[REVISION] = "completed"
        self.backend.verified.update((REVISION, stage) for stage in mod.DB_STAGES)
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("COMPLETED", 0), receipt)
        self.assertEqual(self.backend.write_calls, [])
        self.assertFalse(receipt["write_executed"])
        self.assertTrue(self.active()["finished"])

    def test_failed_completion_audit_never_marks_active_revision_finished(self):
        self.backend.audit_passes = False
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertEqual(receipt["failed_stage"], "COMPLETED_AUDIT")
        self.assertFalse(self.active()["finished"])

    def test_existing_target_without_registration_checkpoint_blocks(self):
        for context in contexts()[:2]:
            self.backend.publish(context)
        self.backend.statuses[REVISION] = "in_progress"
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertIn("without REGISTERED checkpoint", receipt["detail"])
        self.assertEqual(self.backend.write_calls, [])

    def test_second_local_worker_cannot_reach_source_or_database(self):
        lock = self.root / "reports/m5_scheduler/single_instance.lock"
        with local_process_lock(lock):
            receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(receipt["write_executed"])

    def test_database_lock_contention_blocks_before_source_resolution(self):
        @contextmanager
        def busy_lock():
            raise RuntimeError("Another database ingestion worker is active")
            yield  # pragma: no cover

        receipt, code = mod.run_once(root=self.root, backend=self.backend, execute=True,
                                     lock_factory=busy_lock)
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertEqual(self.backend.calls, [])

    def test_malformed_active_identity_blocks_before_resolving_or_writing(self):
        path = self.save_active()
        variants = ["{broken", "[]", json.dumps(dict(schema="auto-ingest-active-v1", repo_id="other/repo",
                                                    target_revision=REVISION, finished=False))]
        for content in variants:
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                receipt, code = self.run_once()
                self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
                self.assertEqual(self.backend.calls, [])
                self.assertFalse(receipt["write_executed"])

    def test_invalid_resolved_revision_is_never_published(self):
        self.backend.head = "main"
        receipt, code = self.run_once()
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assert_no_external_writes()

    def test_checkpoint_revision_drift_blocks_even_in_check_mode(self):
        wrong = contexts(NEXT_REVISION)[0]
        self.backend.read_chain = lambda revision: (wrong, None, None)
        receipt, code = self.run_once(execute=False)
        self.assertEqual((receipt["state"], code), ("BLOCKED", 2))
        self.assertIn("drift", receipt["detail"])
        self.assert_no_external_writes()


if __name__ == "__main__":
    unittest.main()
