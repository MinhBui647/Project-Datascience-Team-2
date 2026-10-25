"""Bounded retry policy for verified idempotent ingestion operations.

This module does not authorize replaying a database writer. Callers must prove
an operation is safe to repeat, or reconcile its durable write evidence first.
Unknown, authentication, parsing, integrity and filesystem failures are never
retried merely because they happened during a network/database operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import math
import random
import re
import socket
import ssl
import time
from typing import Any, Callable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be an integer in 1..5")
        for name, maximum in (("base_delay_seconds", 30), ("max_delay_seconds", 60)):
            value = getattr(self, name)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= maximum):
                raise ValueError(f"{name} must be finite and in 0..{maximum}")


@dataclass(frozen=True)
class FailureClassification:
    retryable: bool
    category: str
    intervention_required: bool
    reason: str


class RetryExhausted(RuntimeError):
    """A transient failure exhausted its budget; original error remains intact."""

    def __init__(self, last_error: Exception, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        classification = classify_failure(last_error)
        super().__init__(f"Transient operation exhausted {attempts} attempts "
                         f"({type(last_error).__name__}/{classification.category})")


_HTTP_RETRY = frozenset({408, 429, 500, 502, 503, 504})
_SQL_RETRY = frozenset({"40001", "40P01", "55P03", "53300", "57P03"})
_NETWORK_ERRNOS = frozenset({
    errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ECONNABORTED,
    errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EPIPE,
    # Winsock values are not consistently exposed as errno constants.
    10050, 10051, 10052, 10053, 10054, 10060, 10061, 10064, 10065, 11002,
})
_TRANSPORT_PACKAGES = frozenset({"httpx", "httpx2", "httpcore", "httpcore2", "requests", "urllib3"})
_TRANSPORT_NAMES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ReadError", "WriteError", "RemoteProtocolError", "ProxyError", "Timeout",
    "ConnectionError", "NewConnectionError", "ProtocolError",
})
_PSYCOPG_TRANSPORT_MESSAGES = (
    "connection refused", "connection timed out", "timeout expired",
    "temporary failure in name resolution", "server closed the connection unexpectedly",
    "connection reset by peer", "connection was forcibly closed",
    "connection to the server was lost", "could not receive data from server",
    "could not send data to server", "network is unreachable", "no route to host",
    "the database system is starting up", "the database system is in recovery mode",
)


def _classified(retryable: bool, category: str, reason: str) -> FailureClassification:
    return FailureClassification(retryable, category, not retryable, reason)


def classify_failure(exc: BaseException) -> FailureClassification:
    """Classify the actual exception, never arbitrary application error text.

    HTTP clients are identified without importing optional transport packages;
    response status takes precedence over transport inheritance. PostgreSQL
    SQLSTATEs have a small explicit retry allowlist. A missing SQLSTATE permits
    only recognized psycopg transport diagnostics, not every OperationalError.
    """
    while isinstance(exc, RetryExhausted):
        exc = exc.last_error

    if not isinstance(exc, Exception):
        return _classified(False, "interrupted", "process_interruption")
    # Child failures carry evidence collected by the trusted backend. In
    # particular an unknown writer outcome must override TimeoutError-style
    # inheritance and must never be retried as a generic transport failure.
    try:
        from scripts.orchestration.auto_ingest_backend import ChildProcessFailure
    except ImportError:
        ChildProcessFailure = ()
    if isinstance(exc, ChildProcessFailure):
        category = getattr(exc, "category", "unknown")
        if not isinstance(category, str) or not category.replace("_", "").isalnum() or len(category) > 64:
            category = "unknown"
        if getattr(exc, "outcome_unknown", True):
            return _classified(False, category, "child_outcome_unknown")
        retryable = getattr(exc, "retryable", False) is True
        return _classified(retryable, category,
                           "temporary_child_failure" if retryable else "non_retryable_child_failure")
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if type(status) is int:
        if status in _HTTP_RETRY:
            return _classified(True, "http", "temporary_http_status")
        if status in (401, 403):
            return _classified(False, "authentication", "http_access_denied")
        return _classified(False, "http", "non_retryable_http_status")

    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate is None:
        sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
    if isinstance(sqlstate, str) and len(sqlstate) == 5:
        if sqlstate.startswith("08") or sqlstate in _SQL_RETRY:
            return _classified(True, "database", "temporary_sqlstate")
        if sqlstate.startswith("28") or sqlstate == "42501":
            return _classified(False, "authentication", "database_access_denied")
        if sqlstate[:2] in ("22", "23"):
            return _classified(False, "data_integrity", "database_data_or_constraint_error")
        return _classified(False, "database", "non_retryable_sqlstate")

    # TLS validation failures require configuration repair, even when wrapped
    # by ConnectError or a psycopg OperationalError with no SQLSTATE.
    message = str(exc).lower()
    if (isinstance(exc, ssl.SSLCertVerificationError)
            or "certificate_verify_failed" in message
            or "certificate verify failed" in message):
        return _classified(False, "configuration", "tls_certificate_verification_failed")
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return _classified(True, "network", "temporary_connection_or_timeout")
    if isinstance(exc, socket.gaierror):
        return _classified(exc.errno == socket.EAI_AGAIN, "network",
                           "temporary_dns_failure" if exc.errno == socket.EAI_AGAIN else "dns_configuration_error")
    if isinstance(exc, OSError):
        error_number = exc.errno
        windows_number = getattr(exc, "winerror", None)
        if error_number in _NETWORK_ERRNOS or windows_number in _NETWORK_ERRNOS:
            return _classified(True, "network", "temporary_socket_error")
        return _classified(False, "filesystem", "non_retryable_os_error")

    hierarchy = tuple(type(exc).__mro__)
    if any(base.__module__.split(".", 1)[0] in _TRANSPORT_PACKAGES
           and base.__name__ in _TRANSPORT_NAMES for base in hierarchy):
        if (any(base.__name__ == "ProxyError" for base in hierarchy)
                and re.match(r"\s*407\b", message)):
            return _classified(False, "authentication", "proxy_authentication_required")
        return _classified(True, "network", "temporary_http_transport_error")
    if any(base.__module__.split(".", 1)[0] in ("psycopg", "psycopg2")
           and base.__name__ == "OperationalError" for base in hierarchy):
        if any(phrase in message for phrase in _PSYCOPG_TRANSPORT_MESSAGES):
            return _classified(True, "database", "temporary_database_transport_error")
        return _classified(False, "database", "unclassified_database_operational_error")
    if isinstance(exc, (ValueError, TypeError, UnicodeError)):
        return _classified(False, "data_integrity", "invalid_data_or_configuration")
    return _classified(False, "unknown", "unclassified_failure")


def retry_call(operation: Callable[[], T], *, policy: RetryPolicy,
               on_retry: Callable[[dict[str, Any]], None] | None = None,
               sleeper: Callable[[float], None] = time.sleep) -> T:
    """Retry a safe operation with bounded exponential full jitter.

    max_attempts includes the first call. Callback events intentionally omit
    exception messages, URLs, connection strings and other secret-bearing data.
    Permanent errors and BaseException (including KeyboardInterrupt) propagate
    immediately. An exhausted transient failure raises RetryExhausted from the
    original exception, allowing reports to retain its category and cause.
    """
    if not isinstance(policy, RetryPolicy):
        raise TypeError("A validated RetryPolicy is required")
    if not callable(operation) or not callable(sleeper) or (on_retry is not None and not callable(on_retry)):
        raise TypeError("Operation, sleeper and optional retry callback must be callable")
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            classified = classify_failure(exc)
            if not classified.retryable:
                raise
            if attempt == policy.max_attempts:
                raise RetryExhausted(exc, attempt) from exc
            ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** (attempt - 1))
            delay = random.uniform(0.0, ceiling)
            if on_retry is not None:
                on_retry({"attempt": attempt, "next_attempt": attempt + 1,
                          "delay_seconds": delay, "error_type": type(exc).__name__,
                          "category": classified.category})
            sleeper(delay)
    raise AssertionError("Validated retry policy did not execute an attempt")
