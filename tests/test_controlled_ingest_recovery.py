"""Controlled recovery integration with real checkpoints and durable journals."""
from contextlib import nullcontext
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.orchestration import auto_ingest
from scripts.orchestration.auto_ingest_backend import Backend, ChildProcessFailure
from scripts.orchestration.auto_recovery import RetryPolicy
from scripts.orchestration.durable_attempt_recovery_audit import audit_attempt
from scripts.orchestration.write_attempt_guard import reserve_write_attempt
from scripts.incremental.incremental_execution_context import checkpoint_digest, publish_checkpoint
from tests.test_auto_ingest import FakeBackend, contexts, REPO, REVISION


def child_error(*, transient=True, unknown=False, readonly=False):
    return ChildProcessFailure(exit_code=2 if not unknown else None,
                               log_relative="reports/fixture.log", readonly=readonly,
                               process_exited=True, retryable=transient,
                               outcome_unknown=unknown, category="database_transient")


class RecoveryBackend(FakeBackend):
    def __init__(self, root):
        super().__init__(root)
        self.resolve_failures = 0
        self.preparation_failures = 0
        self.preflight_failures = {}
        self.write_failures = {}
        self.readback_failures = {}
        self.registration_read_failures = 0
        self.preview_journals = []
        self.effects = {}

    def resolve(self, repo, ref):
        if self.resolve_failures:
            self.resolve_failures -= 1
            raise ConnectionError("Temporary source transport failure")
        return super().resolve(repo, ref)

    def prepare(self, planned, manifest_sha):
        if self.preparation_failures:
            self.preparation_failures -= 1
            raise TimeoutError("Temporary candidate download timeout")
        return super().prepare(planned, manifest_sha)

    def preflight(self, stage, context, log_dir):
        inspected = audit_attempt(root=self.root, repo_id=REPO, target_revision=REVISION, stage=stage)
        self.preview_journals.append((stage, inspected.state))
        if self.preflight_failures.get(stage, 0):
            self.preflight_failures[stage] -= 1
            raise child_error(readonly=True)

    def write(self, stage, context, log_dir, *, preflight=True):
        failures = self.write_failures.get(stage, [])
        mode = failures.pop(0) if failures else None
        if mode in ("transient_before", "unknown"):
            self.write_calls.append(stage)
            raise child_error(unknown=mode == "unknown")
        before = (REVISION, stage) in self.verified
        super().write(stage, context, log_dir)
        if not before:
            self.effects[stage] = self.effects.get(stage, 0) + 1
        if mode == "transient_after":
            raise child_error()

    def registered(self, prepared):
        if self.registration_read_failures:
            self.registration_read_failures -= 1
            raise ConnectionError("Registration evidence database temporarily unavailable")
        return super().registered(prepared)

    def verify(self, stage, context):
        if (REVISION, stage) in self.verified and self.readback_failures.get(stage, 0):
            self.readback_failures[stage] -= 1
            raise ConnectionError("Independent readback database temporarily unavailable")
        return super().verify(stage, context)


class ControlledIngestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.backend = RecoveryBackend(self.root)

    def run_once(self):
        return auto_ingest.run_once(root=self.root, backend=self.backend, execute=True,
                                    retry_policy=RetryPolicy(3, 0, 0), sleeper=lambda _: None,
                                    lock_factory=lambda: nullcontext(lambda: None))

    def test_transient_source_and_preparation_retry_finish_same_pinned_revision(self):
        self.backend.resolve_failures = 1
        self.backend.preparation_failures = 1
        receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        self.assertEqual(receipt["target_revision"], REVISION)
        self.assertEqual(receipt["recovery_status"], "RECOVERED")
        operations = {event["operation"] for event in receipt["retry_events"]}
        self.assertTrue({"RESOLVE_REVISION", "CACHE_AND_ARCHIVE_PREPARATION"}.issubset(operations))
        self.assertTrue(all(count == 1 for count in self.backend.effects.values()))

    def test_preflight_retries_before_permanent_write_claim_is_reserved(self):
        self.backend.preflight_failures["SNAPSHOT_REGISTRATION"] = 2
        receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        previews = [status for stage, status in self.backend.preview_journals if stage == "SNAPSHOT_REGISTRATION"]
        self.assertEqual(previews, ["NO_LOCAL_ATTEMPT"] * 3)
        self.assertEqual(self.backend.write_calls.count("SNAPSHOT_REGISTRATION"), 1)

    def test_real_readonly_child_transport_timeout_retries_without_reserving_early_claim(self):
        process_backend = Backend(self.root, timeout_seconds=5)
        preview = self.backend.preflight
        failures = [True]

        def preflight(stage, context, log_dir):
            preview(stage, context, log_dir)
            if stage == "SNAPSHOT_REGISTRATION" and failures:
                failures.pop()
                directory = process_backend._log_directory(log_dir)
                process_backend._run(
                    (sys.executable, "-B", "-c", "raise TimeoutError('temporary source timeout')"),
                    directory / "transport.log", readonly=True,
                )

        with patch.object(self.backend, "preflight", side_effect=preflight):
            receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        self.assertEqual(receipt["recovery_status"], "RECOVERED")
        self.assertFalse(receipt["intervention_required"])
        previews = [status for stage, status in self.backend.preview_journals
                    if stage == "SNAPSHOT_REGISTRATION"]
        self.assertEqual(previews, ["NO_LOCAL_ATTEMPT", "NO_LOCAL_ATTEMPT"])
        self.assertEqual(self.backend.write_calls.count("SNAPSHOT_REGISTRATION"), 1)
        retries = [event for event in receipt["retry_events"]
                   if event["operation"] == "SNAPSHOT_REGISTRATION_PREFLIGHT"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["category"], "network_transient")

    def test_exited_transient_writer_is_audited_and_only_missing_effect_retried(self):
        self.backend.write_failures["CANDIDATE_INGESTION"] = ["transient_before"]
        receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 2)
        self.assertEqual(self.backend.effects["CANDIDATE_INGESTION"], 1)
        self.assertTrue(any(item["action"] == "RETRY_VERIFIED_INCOMPLETE_STAGE"
                            for item in receipt["recoveries"]))

    def test_transient_exit_after_visible_effect_reconciles_without_replaying_writer(self):
        self.backend.write_failures["CANDIDATE_INGESTION"] = ["transient_after"]
        receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 1)
        self.assertEqual(self.backend.effects["CANDIDATE_INGESTION"], 1)
        self.assertTrue(any(item["action"] == "RECONCILED_WITHOUT_REWRITE"
                            for item in receipt["recoveries"]))

    def test_successful_child_with_temporary_readback_failure_resumes_without_reexecution(self):
        self.backend.readback_failures["CANDIDATE_INGESTION"] = 3
        first, code = self.run_once()
        self.assertEqual(code, 2, first)
        self.assertFalse(first["intervention_required"])
        self.assertEqual(first["recovery_status"], "RETRY_EXHAUSTED")
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 1)
        second, code = self.run_once()
        self.assertEqual((code, second["state"]), (0, "COMPLETED"), second)
        self.assertTrue(second["resumed"])
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 1)

    def test_committed_registration_recovers_missing_checkpoint_without_second_insert(self):
        self.backend.registration_read_failures = 3
        first, code = self.run_once()
        self.assertEqual(code, 2, first)
        self.assertFalse(first["intervention_required"])
        self.assertIsNone(self.backend.read_chain(REVISION)[2])
        self.assertEqual(self.backend.statuses[REVISION], "in_progress")
        second, code = self.run_once()
        self.assertEqual((code, second["state"]), (0, "COMPLETED"), second)
        self.assertIsNotNone(self.backend.read_chain(REVISION)[2])
        self.assertEqual(self.backend.write_calls.count("SNAPSHOT_REGISTRATION"), 1)

    def test_unknown_writer_outcome_requires_intervention_and_is_never_retried(self):
        self.backend.write_failures["CANDIDATE_INGESTION"] = ["unknown"]
        first, code = self.run_once()
        self.assertEqual(code, 2, first)
        self.assertTrue(first["intervention_required"])
        self.assertEqual(first["recovery_status"], "MANUAL_INTERVENTION_REQUIRED")
        second, code = self.run_once()
        self.assertEqual(code, 2, second)
        self.assertTrue(second["intervention_required"])
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 1)

    def test_persistent_writer_retry_budget_is_not_reset_by_next_run(self):
        self.backend.write_failures["CANDIDATE_INGESTION"] = ["transient_before"] * 5
        first, code = self.run_once()
        self.assertEqual(code, 2, first)
        self.assertTrue(first["intervention_required"])
        self.assertEqual(first["reason_code"], "WRITER_RETRY_BUDGET_EXHAUSTED")
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 3)
        second, code = self.run_once()
        self.assertEqual(code, 2, second)
        self.assertEqual(second["reason_code"], "WRITER_RETRY_BUDGET_EXHAUSTED")
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 3)

    def test_legacy_unverified_claim_has_no_terminal_recovery_evidence_and_stays_manual(self):
        planned, prepared, registered = contexts()
        for context in (planned, prepared, registered):
            publish_checkpoint(self.root, context)
        self.backend.statuses[REVISION] = "in_progress"
        reserve_write_attempt(root=self.root, repo_id=REPO, target_revision=REVISION,
                              stage="CANDIDATE_INGESTION", context_sha256=checkpoint_digest(registered),
                              intent_sha256="f" * 64, target_snapshot_id=368,
                              argv=self.backend.writer_argv("CANDIDATE_INGESTION", registered))
        receipt, code = self.run_once()
        self.assertEqual(code, 2, receipt)
        self.assertTrue(receipt["intervention_required"])
        self.assertEqual(self.backend.write_calls, [])

    def test_readonly_check_reports_unknown_writer_as_intervention(self):
        self.backend.write_failures["CANDIDATE_INGESTION"] = ["unknown"]
        first, _ = self.run_once()
        writes = list(self.backend.write_calls)
        checked, code = auto_ingest.run_once(root=self.root, backend=self.backend, execute=False,
                                            retry_policy=RetryPolicy(3, 0, 0), sleeper=lambda _: None)
        self.assertEqual(code, 2, checked)
        self.assertTrue(checked["intervention_required"])
        self.assertEqual(checked["failed_stage"], "RECOVERY_AUDIT")
        self.assertFalse(checked["write_executed"])
        self.assertEqual(self.backend.write_calls, writes)

    def test_local_status_requires_no_backend_or_database(self):
        self.backend.resolve_failures = 1
        receipt, _ = self.run_once()
        with patch.object(auto_ingest, "ROOT", self.root), \
                patch.object(auto_ingest, "run_once", side_effect=AssertionError("Status must not execute pipeline")), \
                redirect_stdout(io.StringIO()) as output:
            code = auto_ingest.main(["--status"])
        status = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(status["run_id"], receipt["run_id"])
        self.assertEqual(status["recovery_status"], "RECOVERED")
        self.assertIn("receipt_path", status)

    def test_safe_skip_after_retry_reports_recovered_even_on_early_return(self):
        self.backend.resolve_failures = 1
        self.backend.plan_decision = "SKIP_NO_RAW_CHANGES"
        receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "SKIPPED_NO_RAW_CHANGES"), receipt)
        self.assertEqual(receipt["recovery_status"], "RECOVERED")
        self.assertFalse(receipt["write_executed"])

    def test_active_successful_process_incomplete_evidence_never_replays(self):
        self.backend.readback_failures["CANDIDATE_INGESTION"] = 3
        self.run_once()
        self.backend.verified.remove((REVISION, "CANDIDATE_INGESTION"))
        receipt, code = self.run_once()
        self.assertEqual(code, 2, receipt)
        self.assertEqual(receipt["reason_code"], "CONTRADICTORY_VERIFIED_EVIDENCE")
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 1)

    def test_recovery_command_drift_is_blocked_before_any_replay(self):
        self.backend.readback_failures["CANDIDATE_INGESTION"] = 3
        self.run_once()
        original = self.backend.writer_argv
        self.backend.writer_argv = lambda stage, context: original(stage, context) + ("--unexpected-option",)
        writes = list(self.backend.write_calls)
        receipt, code = self.run_once()
        self.assertEqual(code, 2, receipt)
        self.assertEqual(receipt["error_type"], "RecoveryJournalError")
        self.assertTrue(receipt["intervention_required"])
        self.assertEqual(self.backend.write_calls, writes)

    def test_known_terminal_success_can_append_missing_primary_result_after_reconciliation(self):
        self.backend.readback_failures["CANDIDATE_INGESTION"] = 3
        self.run_once()
        from scripts.orchestration.durable_attempt_recovery_audit import _directory
        directory = _directory(self.root, REPO, REVISION)
        claim_bytes = (directory / "CANDIDATE_INGESTION.claim.json").read_bytes()
        # Simulate a crash before the original result file was persisted.
        (directory / "CANDIDATE_INGESTION.result.json").unlink()
        receipt, code = self.run_once()
        self.assertEqual((code, receipt["state"]), (0, "COMPLETED"), receipt)
        inspected = audit_attempt(root=self.root, repo_id=REPO, target_revision=REVISION,
                                  stage="CANDIDATE_INGESTION")
        self.assertEqual(inspected.journal_status, "VERIFIED_AFTER_WRITE")
        self.assertEqual((directory / "CANDIDATE_INGESTION.claim.json").read_bytes(), claim_bytes)
        self.assertEqual(self.backend.write_calls.count("CANDIDATE_INGESTION"), 1)


if __name__ == "__main__":
    unittest.main()
