"""Real child-process diagnostics and split preflight coverage for recovery."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.orchestration.auto_ingest_backend import (
    Backend, ChildProcessFailure, ChildProcessTimeout, _exit_cause,
)
from scripts.incremental.incremental_execution_context import RegisteredContext
from tests.test_auto_ingest import contexts


class BackendRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.backend = Backend(self.root, timeout_seconds=1)
        self.logs = self.backend._log_directory(self.root / "reports" / "recovery")

    def diagnostic(self, text, *, readonly=False):
        path = self.logs / "diagnostic.log"
        path.write_text(text, encoding="utf-8")
        return _exit_cause(path, readonly=readonly)

    def test_readonly_transport_diagnostics_retry_but_writer_outcomes_stay_unknown(self):
        for diagnostic in (
            "httpx.ConnectTimeout: timed out",
            "httpx2.ConnectError: connection refused",
            "httpx2._exceptions.ReadTimeout: timed out",
            "httpcore.ConnectError: connection reset",
            "requests.exceptions.ConnectionError: disconnected",
            "TimeoutError: timed out",
            "ConnectionResetError: connection reset",
        ):
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(self.diagnostic(diagnostic + "\n", readonly=True),
                                 (True, False, "network_transient"))
                self.assertEqual(self.diagnostic(diagnostic + "\n"),
                                 (False, True, "network_outcome_unknown"))

    def test_readonly_database_connection_failures_do_not_imply_unknown_writes(self):
        for message in ("connection timed out", "timeout expired",
                        "server closed the connection unexpectedly", "SQLSTATE=08006 lost connection"):
            with self.subTest(message=message):
                self.assertEqual(self.diagnostic("psycopg.OperationalError: " + message + "\n",
                                                 readonly=True),
                                 (True, False, "database_connection_transient"))
                retryable, unknown, _ = self.diagnostic("psycopg.OperationalError: " + message + "\n")
                self.assertFalse(retryable)
                self.assertTrue(unknown)

    def test_tls_authentication_and_dns_configuration_errors_stay_permanent(self):
        for diagnostic in (
            "httpx2.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
            "httpx.ConnectError: [Errno -2] Name or service not known",
            "httpx2.ConnectError: [Errno 11001] No such host is known",
            "psycopg.OperationalError: password authentication failed",
            "psycopg.OperationalError: certificate verify failed; connection refused",
            "httpx2.HTTPStatusError: 403 Client Error",
            "httpx2.ProxyError: 407 Proxy Authentication Required",
            "application.ConnectTimeout: arbitrary application exception",
        ):
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(self.diagnostic(diagnostic + "\n", readonly=True),
                                 (False, False, "child_failed_permanently"))

    def nested_preflight_argv(self, gate):
        """Use the actual nested M4 CLI, replacing only DB/source reads."""
        from scripts.incremental import register_incremental_snapshot as registration
        from scripts.incremental import ingest_incremental_candidates as candidates
        from tests.test_register_incremental_snapshot_m46 import valid_req
        from tests.test_ingest_incremental_candidates_m46 import req

        source_root = str(Path(__file__).resolve().parents[1])
        if gate == "registration":
            command = registration.preflight_command(valid_req())
            module = "scripts.incremental.incremental_snapshot_prepare"
            arguments = command[1:]
            outer_import = "from scripts.incremental import register_incremental_snapshot as caller\nfrom tests.test_register_incremental_snapshot_m46 import valid_req"
            command_function = "preflight_command"
            invoke = "caller.run_preflight(valid_req())"
        else:
            command = candidates.planner_command(req(), "reports/cache")
            module = "scripts.incremental.plan_incremental_candidates"
            arguments = command[1:]
            outer_import = "from scripts.incremental import ingest_incremental_candidates as caller\nfrom tests.test_ingest_incremental_candidates_m46 import req"
            command_function = "planner_command"
            invoke = "caller.run_existing_plan(req(), 'reports/cache')"
        inner = (
            "import sys\nfrom unittest.mock import patch\n"
            f"sys.path.insert(0, {source_root!r})\n"
            f"from {module} import main\n"
            f"sys.argv = {arguments!r}\n"
            "with patch('scripts.tools.check_raw_updates.load_baseline', return_value=('1'*40, {})), "
            "patch('huggingface_hub.HfApi.repo_info', side_effect=TimeoutError('temporary metadata timeout')):\n"
            "    main()\n"
        )
        outer = (
            "import sys\nfrom unittest.mock import patch\n"
            f"sys.path.insert(0, {source_root!r})\n{outer_import}\n"
            f"with patch.object(caller, {command_function!r}, return_value=(sys.executable, '-B', '-c', {inner!r})):\n"
            f"    {invoke}\n"
        )
        return (sys.executable, "-B", "-c", outer)

    def test_actual_nested_preparation_and_candidate_timeouts_preserve_retryable_cause(self):
        backend = Backend(self.root, timeout_seconds=20)
        for gate in ("registration", "candidate"):
            with self.subTest(gate=gate):
                with self.assertRaises(ChildProcessFailure) as caught:
                    # These wrappers run before SQL even in a writer invocation.
                    backend._run(self.nested_preflight_argv(gate),
                                 self.logs / (gate + "-nested.log"), readonly=False)
                failure = caught.exception
                self.assertTrue(failure.retryable)
                self.assertTrue(failure.process_exited)
                self.assertFalse(failure.outcome_unknown)
                self.assertEqual(failure.category, "network_transient")
                self.assertNotIn("temporary metadata timeout", str(failure))

    def test_missing_success_contract_never_uses_an_earlier_transient_diagnostic(self):
        # A zero exit with missing markers/totals is an integrity failure, not
        # a failed read-only child that permits unwrapping its prior diagnostic.
        for terminal in (
            "RuntimeError: Preparation gate failed or expected totals drifted; refusing registration",
            "RuntimeError: Read-only candidate planner failed; refusing candidate writes",
            "ReadOnlyPreflightFailure: unrelated wrapper",
            "ReadOnlyPreflightFailure: Preparation gate failed; read-only child exited with status 0",
        ):
            self.assertEqual(self.diagnostic("TimeoutError: earlier timeout\n" + terminal + "\n",
                                             readonly=True),
                             (False, False, "child_failed_permanently"))

    def test_known_postgresql_transaction_diagnostics_are_retryable(self):
        for name in ("SerializationFailure", "DeadlockDetected", "LockNotAvailable",
                     "TooManyConnections", "CannotConnectNow"):
            with self.subTest(name=name):
                self.assertEqual(
                    self.diagnostic("M4.6.3 ERROR | " + name + ": transaction aborted\n"),
                    (True, False, "database_transaction_transient"),
                )
        self.assertEqual(
            self.diagnostic("psycopg.OperationalError: SQLSTATE=40001 retry transaction\n"),
            (True, False, "database_transaction_transient"),
        )

    def test_refused_transport_diagnostic_is_retryable_but_commit_disconnect_is_unknown(self):
        self.assertEqual(
            self.diagnostic("psycopg.OperationalError: connection refused\n"),
            (True, False, "database_connection_transient"),
        )
        self.assertEqual(
            self.diagnostic("M4 ERROR | OperationalError: server closed the connection unexpectedly\n"),
            (False, True, "database_connection_outcome_unknown"),
        )

    def test_http_transient_is_distinguished_from_permanent_status(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(
                    self.diagnostic(f"huggingface_hub.errors.HfHubHTTPError: {status} Server Error\n"),
                    (True, False, "http_transient"),
                )
                self.assertEqual(
                    self.diagnostic(f"M4 ERROR | HfHubHTTPError: {status} Server Error\n"),
                    (True, False, "http_transient"),
                )
        for status in (400, 401, 403, 404, 422):
            self.assertEqual(
                self.diagnostic(f"M4 ERROR | HfHubHTTPError: {status} Client Error\n"),
                (False, False, "child_failed_permanently"),
            )

    def test_arbitrary_retry_words_or_earlier_exception_do_not_authorize_retry(self):
        for text in (
            "deadlock detected and connection refused\n",
            "M4 ERROR | RuntimeError: candidate contains Connection refused\n",
            "psycopg.errors.SerializationFailure: retry\nRuntimeError: hash mismatch\n",
            "M4 ERROR | OperationalError: password authentication failed\n",
            "M4 ERROR | OperationalError: SQLSTATE=23505 duplicate key\n",
            "M4 ERROR | OperationalError: SQLSTATE=23505 connection refused in payload\n",
            "M4 ERROR | OperationalError: could not translate host name bad.example: No such host is known\n",
            "M4 ERROR | HfHubHTTPError: 401 Client Error for URL https://example.test/500\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.diagnostic(text), (False, False, "child_failed_permanently"))

    def test_only_exact_m4_advisory_lock_messages_are_transient(self):
        for message in (
            "Another incremental writer/finalizer holds the repository lock",
            "Concurrent repository writer/registrar holds lock",
            "Another incremental registrar holds the repository lock",
            "Another incremental registrar/writer holds repository lock",
            "Another candidate writer holds path lock",
        ):
            self.assertEqual(self.diagnostic("M4 ERROR | RuntimeError: " + message + "\n"),
                             (True, False, "database_lock_transient"))
        # This exception is actually a missing/wrong target row, rather than
        # advisory lock contention; retrying it would hide broken lineage.
        self.assertEqual(self.diagnostic("RuntimeError: Target row lock failed\n"),
                         (False, False, "child_failed_permanently"))

    def test_scan_reads_bounded_terminal_log_and_never_returns_message(self):
        text = "psycopg.errors.DeadlockDetected: password=example-secret\n" + "X" * 70000
        result = self.diagnostic(text)
        self.assertEqual(result, (False, False, "child_failed_permanently"))
        self.assertNotIn("example-secret", str(result))

    def test_readonly_preflight_is_separate_from_one_writer_attempt(self):
        _, prepared, _ = contexts()
        with patch.object(self.backend, "_run") as run:
            self.backend.preflight("SNAPSHOT_REGISTRATION", prepared, self.logs / "preflight-1")
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.kwargs["readonly"])
            self.assertIn("--plan", run.call_args.args[0])
            self.assertIn("--allow-csv-registration-plan-only", run.call_args.args[0])
            run.reset_mock()
            self.backend.write("SNAPSHOT_REGISTRATION", prepared, self.logs / "writer-1", preflight=False)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(run.call_args.kwargs["readonly"])
            self.assertEqual(run.call_args.args[0], self.backend.writer_argv("SNAPSHOT_REGISTRATION", prepared))
            self.assertNotIn("--plan", run.call_args.args[0])

    def test_mixed_json_csv_preparation_retains_both_formats_for_recovery_dispatch(self):
        # Exercise existing real parsers/archive fixtures before splitting the
        # readonly/write calls; no network or PostgreSQL writes are performed.
        from tests.test_auto_ingest_backend import BackendTests
        fixture = BackendTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        prepared = fixture.prepare_fixture()
        self.assertEqual({candidate.path.rsplit(".", 1)[1]
                          for candidate in prepared.planned.candidates}, {"json", "csv"})
        registered = RegisteredContext(prepared, 2)
        with patch.object(fixture.backend, "_run") as run:
            fixture.backend.preflight("CANDIDATE_INGESTION", registered,
                                      fixture.root / "reports" / "recovery" / "plan")
            fixture.backend.write("CANDIDATE_INGESTION", registered,
                                  fixture.root / "reports" / "recovery" / "write", preflight=False)
        self.assertEqual(run.call_count, 2)
        self.assertIn("--allow-csv-candidate-plan-only", run.call_args_list[0].args[0])
        self.assertIn("--allow-csv-candidate-write", run.call_args_list[1].args[0])

    def test_real_exited_child_failure_has_sanitized_transient_diagnostics(self):
        secret = "do-not-copy-this-password"
        argv = (sys.executable, "-c",
                "import sys; print('M4 ERROR | DeadlockDetected: password=" + secret + "'); sys.exit(2)")
        with self.assertRaises(ChildProcessFailure) as caught:
            self.backend._run(argv, self.logs / "deadlock.log", readonly=False)
        failure = caught.exception
        self.assertTrue(failure.retryable)
        self.assertTrue(failure.process_exited)
        self.assertFalse(failure.outcome_unknown)
        self.assertEqual(failure.exit_code, 2)
        self.assertNotIn(secret, str(failure))

    def test_real_child_timeout_remains_fenced_and_backwards_compatible(self):
        with self.assertRaises(ChildProcessTimeout) as caught:
            self.backend._run((sys.executable, "-c", "import time; time.sleep(20)"),
                              self.logs / "timeout.log", readonly=False)
        failure = caught.exception
        self.assertIsInstance(failure, subprocess.TimeoutExpired)
        self.assertIsInstance(failure, ChildProcessFailure)
        self.assertFalse(failure.retryable)
        self.assertTrue(failure.outcome_unknown)
        self.assertTrue(failure.process_exited)

    def test_stopped_readonly_preflight_timeout_is_safe_to_retry(self):
        with self.assertRaises(ChildProcessTimeout) as caught:
            self.backend._run((sys.executable, "-c", "import time; time.sleep(20)"),
                              self.logs / "readonly-timeout.log", readonly=True)
        failure = caught.exception
        self.assertTrue(failure.readonly)
        self.assertTrue(failure.process_exited)
        self.assertTrue(failure.retryable)
        self.assertFalse(failure.outcome_unknown)

    def test_failed_launch_is_not_a_transient_database_diagnostic(self):
        with patch("subprocess.Popen", side_effect=PermissionError("secret-path")):
            with self.assertRaises(ChildProcessFailure) as caught:
                self.backend._run((sys.executable, "-m", "example"),
                                  self.logs / "launch.log", readonly=False)
        failure = caught.exception
        self.assertFalse(failure.retryable)
        self.assertFalse(failure.outcome_unknown)
        self.assertFalse(failure.process_exited)
        self.assertNotIn("secret-path", str(failure))

    def test_registration_evidence_reads_exact_existing_context_without_publishing(self):
        _, prepared, registered = contexts()
        with patch.object(self.backend, "registered", return_value=registered) as readback, \
             patch.object(self.backend, "publish") as publish:
            self.assertIs(self.backend.registration_evidence(prepared), registered)
        readback.assert_called_once_with(prepared)
        publish.assert_not_called()

    def test_verifier_progress_goes_to_stderr_and_not_json_stdout(self):
        _, _, registered = contexts()
        verdict = SimpleNamespace(status="VERIFIED")

        def verify(**kwargs):
            print("independent audit progress")
            return verdict

        stdout = io.StringIO()
        with patch.object(self.backend, "target", return_value=SimpleNamespace(status="in_progress")), \
             patch("scripts.incremental.incremental_post_execution_verifier.verify_read_only", side_effect=verify), \
             redirect_stdout(stdout):
            self.assertTrue(self.backend.verify("CANDIDATE_INGESTION", registered))
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
