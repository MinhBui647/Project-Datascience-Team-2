"""Controlled retry classification and bounded, sanitized retry behavior."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import errno
import socket
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.orchestration.auto_recovery import (
    RetryExhausted, RetryPolicy, classify_failure, retry_call,
)


def http_error(status):
    error = RuntimeError("secret-token must not appear in retry events")
    error.response = SimpleNamespace(status_code=status)
    return error


def sql_error(state):
    error = RuntimeError("database failure")
    error.sqlstate = state
    return error


class FailureClassificationTests(unittest.TestCase):
    def test_only_allowlisted_http_statuses_retry(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                result = classify_failure(http_error(status))
                self.assertTrue(result.retryable)
                self.assertEqual(result.category, "http")
                self.assertFalse(result.intervention_required)
        for status in (200, 301, 400, 401, 403, 404, 409, 410, 422, 501, 505):
            with self.subTest(status=status):
                result = classify_failure(http_error(status))
                self.assertFalse(result.retryable)
                self.assertTrue(result.intervention_required)

    def test_http_authentication_takes_precedence_over_transport_inheritance(self):
        error = ConnectionError("access denied")
        error.response = SimpleNamespace(status_code=403)
        self.assertEqual(classify_failure(error).category, "authentication")
        self.assertFalse(classify_failure(error).retryable)

    def test_postgres_sqlstate_allowlist_and_diagnostics(self):
        for state in ("08000", "08006", "08P01", "40001", "40P01", "55P03", "53300", "57P03"):
            with self.subTest(state=state):
                result = classify_failure(sql_error(state))
                self.assertTrue(result.retryable)
                self.assertEqual(result.category, "database")
        error = RuntimeError("diagnostics")
        error.diag = SimpleNamespace(sqlstate="40001")
        self.assertTrue(classify_failure(error).retryable)

    def test_postgres_data_permission_and_unknown_states_are_permanent(self):
        for state in ("22000", "22P02", "23505", "23503", "28P01", "42501", "42P01", "57014", "XX000"):
            with self.subTest(state=state):
                self.assertFalse(classify_failure(sql_error(state)).retryable)
        self.assertEqual(classify_failure(sql_error("23505")).category, "data_integrity")
        self.assertEqual(classify_failure(sql_error("28P01")).category, "authentication")

    def test_builtin_transport_errors_and_socket_codes_retry(self):
        for error in (ConnectionError("disconnected"), TimeoutError("timed out"),
                      OSError(errno.ENETUNREACH, "unreachable"), OSError(10060, "timeout"),
                      OSError(10061, "connection refused"), socket.gaierror(socket.EAI_AGAIN, "temporary")):
            with self.subTest(error=error):
                self.assertTrue(classify_failure(error).retryable)
        error = OSError("winsock")
        error.winerror = 10054
        self.assertTrue(classify_failure(error).retryable)

    def test_permanent_dns_disk_permission_parse_integrity_and_runtime_errors_do_not_retry(self):
        errors = (socket.gaierror(socket.EAI_NONAME, "unknown host"),
                  OSError(errno.ENOSPC, "disk full"), PermissionError("denied"),
                  FileNotFoundError("archive missing"), ValueError("hash mismatch"),
                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
                  RuntimeError("connection refused"), RuntimeError("HTTP 503"))
        for error in errors:
            with self.subTest(error=error):
                self.assertFalse(classify_failure(error).retryable)

    def test_httpx2_and_related_transport_errors_without_optional_imports(self):
        for module, name in (("httpx2", "ConnectError"), ("httpx", "ReadTimeout"),
                             ("httpcore2", "ReadError"), ("requests.exceptions", "ConnectionError")):
            with self.subTest(module=module):
                error_type = type(name, (Exception,), {"__module__": module})
                self.assertTrue(classify_failure(error_type("transport unavailable")).retryable)
        impostor = type("ConnectError", (Exception,), {"__module__": "application"})
        self.assertFalse(classify_failure(impostor("temporary")).retryable)

    def test_tls_certificate_failures_do_not_retry_wrapped_connect_errors(self):
        error_type = type("ConnectError", (Exception,), {"__module__": "httpx2"})
        for error in (ssl.SSLCertVerificationError("invalid certificate"),
                      error_type("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")):
            self.assertFalse(classify_failure(error).retryable)
            self.assertEqual(classify_failure(error).category, "configuration")

    def test_proxy_authentication_status_is_permanent_but_transport_errors_retry(self):
        proxy_error = type("ProxyError", (Exception,), {"__module__": "httpx2"})
        result = classify_failure(proxy_error("407 Proxy Authentication Required"))
        self.assertFalse(result.retryable)
        self.assertEqual(result.category, "authentication")
        self.assertTrue(classify_failure(proxy_error("502 Bad Gateway")).retryable)

    def test_psycopg_no_sqlstate_needs_recognized_transport_diagnostic(self):
        operational_error = type("OperationalError", (Exception,), {"__module__": "psycopg"})
        for message in ("connection failed: Connection refused", "connection timed out",
                        "could not translate host: Temporary failure in name resolution",
                        "server closed the connection unexpectedly"):
            with self.subTest(message=message):
                self.assertTrue(classify_failure(operational_error(message)).retryable)
        for message in ("password authentication failed", "database does not exist", "unknown failure",
                        "could not translate host: No such host is known"):
            with self.subTest(message=message):
                self.assertFalse(classify_failure(operational_error(message)).retryable)

    def test_interruption_and_exhaustion_classification(self):
        self.assertFalse(classify_failure(KeyboardInterrupt()).retryable)
        exhausted = RetryExhausted(TimeoutError("password=secret"), 3)
        self.assertTrue(classify_failure(exhausted).retryable)
        self.assertEqual(classify_failure(exhausted).category, "network")
        self.assertNotIn("secret", str(exhausted))

    def test_trusted_transient_child_diagnostic_requires_known_outcome(self):
        from scripts.orchestration.auto_ingest_backend import ChildProcessFailure
        error = ChildProcessFailure(exit_code=2, log_relative="reports/plan.log",
                                    readonly=True, process_exited=True,
                                    retryable=True, outcome_unknown=False, category="database")
        result = classify_failure(error)
        self.assertTrue(result.retryable)
        self.assertEqual(result.category, "database")
        self.assertEqual(result.reason, "temporary_child_failure")
        unknown = ChildProcessFailure(exit_code=2, log_relative="reports/write.log",
                                      readonly=False, process_exited=True,
                                      retryable=True, outcome_unknown=True, category="database")
        self.assertFalse(classify_failure(unknown).retryable)
        self.assertTrue(classify_failure(unknown).intervention_required)

    def test_child_timeout_stays_fenced_despite_timeout_inheritance(self):
        from scripts.orchestration.auto_ingest_backend import ChildProcessTimeout
        error = ChildProcessTimeout(argv=("python", "-m", "writer"), timeout=60,
                                    log_relative="reports/write.log", readonly=False,
                                    process_exited=True)
        result = classify_failure(error)
        self.assertFalse(result.retryable)
        self.assertEqual(result.reason, "child_outcome_unknown")
        operation, sleeper = Mock(side_effect=error), Mock()
        with self.assertRaises(ChildProcessTimeout):
            retry_call(operation, policy=RetryPolicy(), sleeper=sleeper)
        operation.assert_called_once_with()
        sleeper.assert_not_called()


class RetryBehaviorTests(unittest.TestCase):
    def test_policy_bounds_and_immutability(self):
        for field, values in {
            "max_attempts": (0, 6, True, 2.5),
            "base_delay_seconds": (-1, 31, True, float("nan"), float("inf")),
            "max_delay_seconds": (-1, 61, False, float("nan"), float("inf")),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        RetryPolicy(**{field: value})
        policy = RetryPolicy(max_attempts=5, base_delay_seconds=30, max_delay_seconds=60)
        with self.assertRaises(FrozenInstanceError):
            policy.max_attempts = 2
        self.assertEqual(RetryPolicy(max_attempts=1, base_delay_seconds=0, max_delay_seconds=0).max_attempts, 1)

    def test_success_preserves_value_and_does_not_sleep(self):
        value = object()
        operation, sleeper, callback = Mock(return_value=value), Mock(), Mock()
        self.assertIs(retry_call(operation, policy=RetryPolicy(), sleeper=sleeper, on_retry=callback), value)
        operation.assert_called_once_with()
        sleeper.assert_not_called()
        callback.assert_not_called()

    def test_transient_failures_retry_with_capped_exponential_jitter_and_sanitized_events(self):
        operation = Mock(side_effect=[http_error(429), TimeoutError("token=secret"), "ok"])
        sleeper, callback = Mock(), Mock()
        with patch("scripts.orchestration.auto_recovery.random.uniform", side_effect=[1.5, 3.0]) as jitter:
            result = retry_call(operation, policy=RetryPolicy(base_delay_seconds=2, max_delay_seconds=3),
                                sleeper=sleeper, on_retry=callback)
        self.assertEqual(result, "ok")
        self.assertEqual([call.args for call in jitter.call_args_list], [(0.0, 2), (0.0, 3)])
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [1.5, 3.0])
        events = [call.args[0] for call in callback.call_args_list]
        self.assertEqual([event["attempt"] for event in events], [1, 2])
        self.assertEqual([event["next_attempt"] for event in events], [2, 3])
        self.assertEqual([event["category"] for event in events], ["http", "network"])
        self.assertEqual(set(events[0]), {"attempt", "next_attempt", "delay_seconds", "error_type", "category"})
        self.assertNotIn("secret", repr(events))

    def test_retry_exhaustion_preserves_last_cause_and_emits_no_extra_sleep(self):
        errors = [TimeoutError("one"), TimeoutError("two"), TimeoutError("three")]
        operation, sleeper, callback = Mock(side_effect=errors), Mock(), Mock()
        with self.assertRaises(RetryExhausted) as caught:
            retry_call(operation, policy=RetryPolicy(), sleeper=sleeper, on_retry=callback)
        self.assertIs(caught.exception.last_error, errors[-1])
        self.assertIs(caught.exception.__cause__, errors[-1])
        self.assertEqual(caught.exception.attempts, 3)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleeper.call_count, 2)
        self.assertEqual(callback.call_count, 2)

    def test_permanent_failure_is_not_retried_or_wrapped(self):
        for error in (ValueError("invalid CSV"), RuntimeError("hash mismatch"), http_error(403)):
            operation, sleeper = Mock(side_effect=error), Mock()
            with self.subTest(error=error):
                with self.assertRaises(type(error)) as caught:
                    retry_call(operation, policy=RetryPolicy(), sleeper=sleeper)
                self.assertIs(caught.exception, error)
                operation.assert_called_once_with()
                sleeper.assert_not_called()

    def test_baseexception_does_not_retry_or_sleep(self):
        for error in (KeyboardInterrupt(), SystemExit(9), GeneratorExit()):
            operation, sleeper = Mock(side_effect=error), Mock()
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    retry_call(operation, policy=RetryPolicy(), sleeper=sleeper)
                operation.assert_called_once_with()
                sleeper.assert_not_called()

    def test_single_attempt_never_sleeps_even_for_transient_failure(self):
        sleeper = Mock()
        with self.assertRaises(RetryExhausted) as caught:
            retry_call(Mock(side_effect=TimeoutError("timeout")), policy=RetryPolicy(max_attempts=1), sleeper=sleeper)
        self.assertEqual(caught.exception.attempts, 1)
        sleeper.assert_not_called()

    def test_zero_delay_policy_supported(self):
        sleeper = Mock()
        self.assertEqual(retry_call(Mock(side_effect=[TimeoutError(), 7]),
                                    policy=RetryPolicy(base_delay_seconds=0, max_delay_seconds=0),
                                    sleeper=sleeper), 7)
        sleeper.assert_called_once_with(0.0)

    def test_callback_failure_stops_instead_of_silently_retrying(self):
        operation, sleeper = Mock(side_effect=TimeoutError()), Mock()
        with self.assertRaisesRegex(RuntimeError, "receipt failed"):
            retry_call(operation, policy=RetryPolicy(), sleeper=sleeper,
                       on_retry=Mock(side_effect=RuntimeError("receipt failed")))
        operation.assert_called_once_with()
        sleeper.assert_not_called()

    def test_invalid_dependencies_rejected_before_operation(self):
        operation = Mock()
        for kwargs in ({"policy": None}, {"policy": RetryPolicy(), "sleeper": None},
                       {"policy": RetryPolicy(), "on_retry": "not callable"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(TypeError):
                    retry_call(operation, **kwargs)
        operation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
