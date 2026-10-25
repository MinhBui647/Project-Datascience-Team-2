"""Production adapters for unattended ingestion using the existing M4 writers.

The caller owns scheduling, repository serialization, durable write-attempt
reservations and checkpoint publication. This module never retries a writer.
New or changed candidates are limited to the JSON/CSV formats accepted by the
existing bounded transactional writer; other formats fail during planning.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any


CACHE_DIR = "reports/m4_6_content_cache"
_WRITERS = {
    "SNAPSHOT_REGISTRATION": (
        "snapshot_registration", "register_incremental_snapshot",
        "--allow-csv-registration-plan-only",
        ("--register", "--confirm-snapshot-only", "--allow-csv-registration-write"),
    ),
    "REUSE_LINKING": (
        "reuse", "populate_incremental_reuse", "--allow-csv-reuse-plan-only",
        ("--link", "--confirm-reuse-only", "--allow-csv-reuse-link"),
    ),
    "CANDIDATE_INGESTION": (
        "candidate_ingestion", "ingest_incremental_candidates",
        "--allow-csv-candidate-plan-only",
        ("--apply", "--confirm-candidate-write", "--allow-csv-candidate-write"),
    ),
    "MANIFEST_REGISTRATION": (
        "manifest_registration", "register_incremental_manifest",
        "--allow-csv-manifest-registration-plan-only",
        ("--commit", "--confirm-manifest-only", "--allow-csv-manifest-registration-write"),
    ),
    "FINALIZATION": (
        "finalization", "finalize_incremental_snapshot",
        "--allow-csv-finalization-plan-only",
        ("--commit", "--confirm-complete", "--allow-csv-finalization-write"),
    ),
}


class ChildProcessFailure(RuntimeError):
    """Bounded child diagnostic; never embeds raw logs or connection strings.

    ``retryable`` classifies a transient cause, not permission to run a writer
    again. The coordinator must first reconcile independently verified effects
    and reserve a separate recovery attempt. ``outcome_unknown`` also fences
    processes whose termination or connection/commit outcome is ambiguous.
    """

    def __init__(self, *, exit_code: int | None, log_relative: str,
                 readonly: bool, process_exited: bool, retryable: bool = False,
                 outcome_unknown: bool = False, category: str = "child_failure"):
        self.exit_code = exit_code
        self.log_relative = log_relative
        self.readonly = readonly
        self.process_exited = process_exited
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.category = category
        super().__init__(f"M4 child {category}; exit={exit_code}; inspect {log_relative}")


class ChildProcessTimeout(ChildProcessFailure, subprocess.TimeoutExpired):
    """A stopped child still needs reconciliation before any writer retry."""

    def __init__(self, *, argv: tuple[str, ...], timeout: int, log_relative: str,
                 readonly: bool, process_exited: bool):
        # Preserve TimeoutExpired's public attributes and backwards-compatible
        # exception type while exposing the coordinator's explicit fence.
        self.cmd = argv
        self.timeout = timeout
        self.output = None
        self.stderr = None
        safe_readonly_timeout = readonly and process_exited
        ChildProcessFailure.__init__(
            self, exit_code=None, log_relative=log_relative, readonly=readonly,
            process_exited=process_exited, retryable=safe_readonly_timeout,
            outcome_unknown=not safe_readonly_timeout,
            category="readonly_timeout" if safe_readonly_timeout else "child_timeout",
        )

    def __str__(self) -> str:
        return f"M4 child timed out; outcome requires verification; inspect {self.log_relative}"


_TRANSIENT_DB_CLASSES = frozenset({
    "SerializationFailure", "DeadlockDetected", "LockNotAvailable",
    "TooManyConnections", "CannotConnectNow",
})
_TRANSIENT_DB_STATES = frozenset({"40001", "40P01", "55P03", "53300", "57P03"})
_TRANSPORT_CLASSES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ReadError", "WriteError", "RemoteProtocolError", "ProxyError", "Timeout",
    "ConnectionError", "NewConnectionError", "ProtocolError", "TimeoutError",
    "ConnectionRefusedError", "ConnectionResetError", "ConnectionAbortedError",
    "BrokenPipeError",
})
_TRANSIENT_LOCK_MESSAGES = frozenset({
    "Another incremental writer/finalizer holds the repository lock",
    "Concurrent repository writer/registrar holds lock",
    "Another incremental registrar holds the repository lock",
    "Another incremental registrar/writer holds repository lock",
    "Another candidate writer holds path lock",
})
_DIAGNOSTIC = re.compile(
    r"(?:^|\bERROR\s*\|\s*)(?:(?:psycopg(?:\.errors)?|huggingface_hub\.errors|"
    r"httpx2?(?:\._exceptions)?|httpcore2?(?:\._exceptions)?|requests\.exceptions|"
    r"urllib3\.exceptions|scripts\.incremental\.register_incremental_snapshot)\.)?"
    r"(?P<kind>[A-Za-z][A-Za-z0-9_]*):\s*(?P<message>[^\r\n]*)\r?$", re.MULTILINE,
)
_PREFLIGHT_FAILURE = re.compile(
    r"(?:Preparation gate|Read-only candidate planner) failed; "
    r"read-only child exited with status -?[1-9][0-9]*\Z"
)


def _exit_cause(log_path: Path, *, readonly: bool = False) -> tuple[bool, bool, str]:
    """Classify terminal, recognized exception diagnostics from at most 64 KiB.

    Arbitrary error text, data validation failures, auth errors and corrupt
    archive messages are permanent. Never return the inspected text to callers.
    """
    try:
        with log_path.open("rb") as handle:
            handle.seek(max(0, log_path.stat().st_size - 65536))
            tail = handle.read(65536).decode("utf-8", errors="replace")
    except OSError:
        return False, True, "child_diagnostic_unavailable"
    diagnostics = tuple(_DIAGNOSTIC.finditer(tail))
    if not diagnostics:
        return False, False, "child_failed_permanently"
    terminal = diagnostics[-1]
    kind, message = terminal.group("kind"), terminal.group("message")
    if kind == "ReadOnlyPreflightFailure" and _PREFLIGHT_FAILURE.fullmatch(message):
        # Only these typed wrappers prove the nested child ran before any
        # database write. A missing success marker/totals mismatch remains a
        # normal RuntimeError and must never expose an earlier retryable cause.
        if len(diagnostics) < 2:
            return False, False, "child_failed_permanently"
        terminal = diagnostics[-2]
        kind, message = terminal.group("kind"), terminal.group("message")
        readonly = True
    lowered = message.lower()
    if ("certificate_verify_failed" in lowered or "certificate verify failed" in lowered
            or "no such host is known" in lowered or "name or service not known" in lowered
            or "nodename nor servname provided" in lowered):
        return False, False, "child_failed_permanently"
    if kind in _TRANSIENT_DB_CLASSES:
        return True, False, "database_transaction_transient"
    if kind == "RuntimeError" and message in _TRANSIENT_LOCK_MESSAGES:
        return True, False, "database_lock_transient"
    if kind in ("OperationalError", "DatabaseError", "Error"):
        state = re.search(r"\b(?:SQLSTATE|sqlstate)\s*[=:]?\s*([0-9A-Z]{5})\b", message)
        if state:
            if state.group(1) in _TRANSIENT_DB_STATES:
                return True, False, "database_transaction_transient"
            if state.group(1).startswith("08") and readonly:
                return True, False, "database_connection_transient"
            return False, state.group(1).startswith("08"), "child_failed_permanently"
        if any(text in lowered for text in (
            "connection refused", "actively refused",
            "temporary failure in name resolution", "too many connections",
            "too many clients already", "database system is starting up",
            "database system is in recovery mode",
        )):
            return True, False, "database_connection_transient"
        if any(text in lowered for text in (
            "server closed the connection", "connection unexpectedly closed",
            "connection reset", "connection is lost", "ssl connection has been closed",
        )):
            if readonly:
                return True, False, "database_connection_transient"
            return False, True, "database_connection_outcome_unknown"
        if any(text in lowered for text in ("connection timed out", "timeout expired")):
            if readonly:
                return True, False, "database_connection_transient"
            return False, True, "database_connection_outcome_unknown"
    if kind in ("HfHubHTTPError", "HTTPStatusError", "HTTPError"):
        status = re.match(r"\s*(?:(?:Client|Server) error\s+)?['\"]?([1-5][0-9]{2})\b", message, re.IGNORECASE)
        if status and int(status.group(1)) in (408, 429, 500, 502, 503, 504):
            return True, False, "http_transient"
    if kind in _TRANSPORT_CLASSES:
        if kind == "ProxyError" and re.match(r"\s*407\b", message):
            return False, False, "child_failed_permanently"
        if readonly:
            return True, False, "network_transient"
        return False, True, "network_outcome_unknown"
    return False, False, "child_failed_permanently"


class Backend:
    """Thin, revision-pinned interface to the tested incremental M4 APIs."""

    def __init__(self, root: Path, timeout_seconds: int = 3600):
        root = Path(root)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("A real project directory is required")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
            raise ValueError("Invalid ingestion subprocess timeout")
        self.root = root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds

    def resolve(self, repo: str, ref: str) -> str:
        from scripts.incremental.check_raw_updates import resolve_target_revision
        return resolve_target_revision(repo, ref)

    def target(self, repo: str, sha: str) -> Any:
        from scripts.incremental.check_raw_updates import read_target_snapshot_state
        return read_target_snapshot_state(repo, sha)

    def plan(self, repo: str, sha: str) -> Any:
        import psycopg
        from scripts.incremental.register_incremental_snapshot import db_parameters
        from scripts.incremental.plan_incremental_orchestrator import plan_read_only
        parameters = db_parameters()
        parameters["options"] = ("-c default_transaction_read_only=on "
                                 "-c statement_timeout=60000 -c lock_timeout=5000")
        parameters["application_name"] = "auto_ingest_active_snapshot_check"
        with psycopg.connect(**parameters) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_setting('transaction_read_only')")
                if cursor.fetchone() != ("epl_raw_storage_v2", "on"):
                    raise RuntimeError("Unexpected database or writable planning connection")
                cursor.execute(
                    "SELECT snapshot_id,hf_revision FROM raw.snapshot_runs "
                    "WHERE repo_id=%s AND status='in_progress'", (repo,),
                )
                if any(revision != sha for _, revision in cursor.fetchall()):
                    raise RuntimeError("Another revision has an in_progress snapshot; reconcile it before a new plan")
        return plan_read_only(repo_id=repo, target_revision=sha, allow_csv_planning=True)

    def read_chain(self, revision: str) -> tuple[Any, Any, Any]:
        from scripts.incremental.incremental_recovery_audit import read_checkpoints
        return read_checkpoints(self.root, revision)

    def publish(self, context: Any) -> tuple[str, str]:
        from scripts.incremental.incremental_execution_context import publish_checkpoint
        return publish_checkpoint(self.root, context)

    def _context(self, planned: Any) -> Any:
        """Rebuild metadata and DB evidence, rejecting drift from the checkpoint."""
        from scripts.incremental.download_incremental_candidates import load_context
        from scripts.incremental.incremental_execution_context import build_planned
        context = load_context(argparse.Namespace(
            repo_id=planned.repo_id,
            baseline_snapshot=planned.baseline_snapshot_id,
            target_revision=planned.target_revision,
            expected_fingerprint=planned.plan_fingerprint,
            expected_files=planned.expected_files,
            expected_bytes=planned.expected_bytes,
            allow_csv_cache_only=True,
        ))
        fresh = build_planned(
            context, expected_baseline_files=planned.expected_baseline_files,
            expected_full_files=planned.expected_full_files, allow_csv_context=True,
        )
        if fresh != planned:
            raise RuntimeError("Pinned ingestion context changed during preparation")
        return context

    def manifest(self, planned: Any) -> str:
        """Build and atomically publish deterministic full-revision metadata."""
        from scripts.incremental.prepare_incremental_manifest import (
            build_manifest, canonical_bytes, fetch_full_tree, publish_manifest,
        )
        context = self._context(planned)
        manifest = build_manifest(
            context, fetch_full_tree(context),
            expected_full_files=planned.expected_full_files,
            expected_raw_files=planned.expected_raw_files,
        )
        payload = canonical_bytes(manifest)
        digest = hashlib.sha256(payload).hexdigest()
        publish_manifest(self.root, planned.target_revision, digest, payload)
        return digest

    def prepare(self, planned: Any, manifest_sha: str) -> Any:
        """Verify cached bodies, publish archives and prove writer compatibility.

        No PostgreSQL writes occur here. Archive publication is create-only and
        independently rehashed before building the prepared checkpoint value.
        """
        from scripts.incremental.download_incremental_candidates import (
            download_candidates, validated_cache_path,
        )
        from scripts.incremental.promote_incremental_archive import resolve_cached_candidates
        from scripts.incremental.incremental_archive_promotion import (
            audit_candidates, publish_immutable,
        )
        from scripts.incremental.incremental_runner_contracts import (
            build_prepared_from_archives, receipts_from_archive_audit,
        )
        from scripts.incremental.incremental_snapshot_prepare import verify_writer_v1_compatibility

        context = self._context(planned)
        # A completed baseline in PostgreSQL does not prove that its immutable
        # objects exist on this machine (for example after copying the source).
        # Check every reused object before downloading or registering anything.
        self.verify_reuse_archives(planned, context)
        cache = validated_cache_path(CACHE_DIR, root=self.root, create=True)
        # The underlying API prints progress. Keep the runner's stdout available
        # for its single structured receipt, with progress on stderr instead.
        with redirect_stdout(sys.stderr):
            checked, projected = download_candidates(context, cache)
        records = sum(item.records for item in checked)
        if records <= 0 or projected != planned.expected_reused_records + records:
            raise RuntimeError("Downloaded candidate record accounting mismatch")
        local = resolve_cached_candidates(context, cache)
        before = tuple(audit_candidates(
            context.plan, context.target, local, self.root,
            expected_records=records, allow_csv=True,
        ))
        for item in before:
            result = publish_immutable(
                self.root, local[item.relative_path], context.target[item.relative_path],
                item, allow_csv=True,
            )
            if result not in ("ARCHIVED_NEW", "ALREADY_ARCHIVED"):
                raise RuntimeError("Unexpected immutable archive publication result")
        archived = tuple(audit_candidates(
            context.plan, context.target, local, self.root,
            expected_records=records, allow_csv=True,
        ))
        if (len(archived) != len(before)
                or any(item.action != "ALREADY_ARCHIVED" for item in archived)
                or tuple((x.relative_path, x.sha256, x.byte_count, x.record_count)
                         for x in before)
                != tuple((x.relative_path, x.sha256, x.byte_count, x.record_count)
                         for x in archived)):
            raise RuntimeError("Post-promotion immutable archive audit failed")
        parsed = verify_writer_v1_compatibility(
            planned.repo_id, planned.target_revision, context.target,
            context.actions, archived, root=self.root, allow_csv=True,
        )
        if parsed != records:
            raise RuntimeError("Writer preflight record count differs from verified archive")
        evidence = receipts_from_archive_audit(planned, archived)
        return build_prepared_from_archives(
            planned, evidence, root=self.root, expected_manifest_sha256=manifest_sha,
        )

    def _reuse_rows(self, planned: Any, paths: tuple[str, ...]) -> tuple[Any, ...]:
        import psycopg
        from scripts.incremental.register_incremental_snapshot import db_parameters
        parameters = db_parameters()
        parameters["options"] = ("-c default_transaction_read_only=on "
                                 "-c statement_timeout=60000 -c lock_timeout=5000")
        parameters["application_name"] = "auto_ingest_reuse_archive_preflight"
        with psycopg.connect(**parameters) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_setting('transaction_read_only')")
                if cursor.fetchone() != ("epl_raw_storage_v2", "on"):
                    raise RuntimeError("Unexpected database or writable REUSE preflight connection")
                cursor.execute(
                    "SELECT repo_id,hf_revision,status,raw_scope_file_count "
                    "FROM raw.snapshot_runs WHERE snapshot_id=%s",
                    (planned.baseline_snapshot_id,),
                )
                if cursor.fetchone() != (planned.repo_id, planned.baseline_revision,
                                         "completed", planned.expected_baseline_files):
                    raise RuntimeError("Completed baseline changed before REUSE archive preflight")
                cursor.execute(
                    "SELECT o.relative_path,o.repo_id,o.presence_status,o.manifest_size_bytes,"
                    "o.git_blob_id,o.lfs_sha256,f.file_id,f.repo_id,f.relative_path,"
                    "f.file_size_bytes,f.content_sha256,f.archive_path,f.archive_state,"
                    "f.parse_state,f.record_count "
                    "FROM raw.file_observations o "
                    "LEFT JOIN raw.raw_files f ON f.file_id=o.file_id "
                    "WHERE o.snapshot_id=%s AND o.repo_id=%s "
                    "AND o.relative_path=ANY(%s::text[])",
                    (planned.baseline_snapshot_id, planned.repo_id, list(paths)),
                )
                return tuple(cursor.fetchall())

    def verify_reuse_archives(self, planned: Any, context: Any) -> dict[str, Any]:
        """Reconcile REUSE metadata and rehash all baseline objects, read-only.

        Missing/corrupt objects stop preparation before snapshot registration;
        this method neither repairs archives nor downloads replacement bytes.
        """
        from scripts.incremental.finalize_incremental_snapshot import scan_archives
        from scripts.incremental.incremental_execution_context import SHA256
        candidate_paths = {spec.path for spec in planned.candidates}
        paths = tuple(sorted(set(context.target) - candidate_paths))
        if len(paths) != planned.expected_reuse:
            raise RuntimeError("REUSE archive path coverage differs from pinned plan")
        if not paths:
            if planned.expected_reused_records != 0:
                raise RuntimeError("REUSE record counts exist without reused files")
            return {"verified": True, "objects": 0, "bytes": 0}
        rows = self._reuse_rows(planned, paths)
        if len(rows) != planned.expected_reuse:
            raise RuntimeError("Completed baseline REUSE observations are missing or duplicated")
        norm = lambda value: value.strip().lower() if isinstance(value, str) else ""
        seen_paths, seen_ids, objects = set(), set(), []
        records = 0
        for row in rows:
            (path, repo, presence, size, git, lfs, fid, file_repo, file_path,
             file_size, sha, archive, archive_state, parse_state, count) = row
            if path not in paths or path in seen_paths:
                raise RuntimeError("Unexpected or duplicate REUSE observation path")
            identity = context.target[path]
            digest = norm(sha)
            if (repo != planned.repo_id or file_repo != planned.repo_id or file_path != path
                    or presence != "linked" or size != identity.size or file_size != identity.size
                    or norm(git) != norm(identity.git_blob_id) or norm(lfs) != norm(identity.lfs_sha256)
                    or type(fid) is not int or fid <= 0 or fid in seen_ids
                    or archive_state != "verified" or parse_state != "success"
                    or type(count) is not int or count < 0 or not SHA256.fullmatch(digest)
                    or archive != f"raw-archive-v2/objects/{digest[:2]}/{digest}"):
                raise RuntimeError("Invalid baseline REUSE file metadata: " + path)
            seen_paths.add(path)
            seen_ids.add(fid)
            records += count
            objects.append({
                "relative_path": path, "archive_path": archive, "sha256": digest,
                "size_bytes": identity.size, "git_blob_id": norm(identity.git_blob_id),
                "lfs_sha256": norm(identity.lfs_sha256),
            })
        if seen_paths != set(paths) or records != planned.expected_reused_records:
            raise RuntimeError("REUSE archive record/path accounting differs from pinned plan")
        ordered = tuple(sorted(objects, key=lambda item: item["relative_path"]))
        try:
            with redirect_stdout(sys.stderr):
                receipt = scan_archives(ordered, root=self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError("Required REUSE archives are missing or corrupt before snapshot registration: "
                               + str(exc)) from exc
        if receipt.objects != ordered or receipt.total_bytes != sum(item["size_bytes"] for item in ordered):
            raise RuntimeError("REUSE archive scan returned inconsistent evidence")
        return {"verified": True, "objects": len(ordered), "bytes": receipt.total_bytes}

    def registered(self, prepared: Any) -> Any:
        from scripts.incremental.incremental_runner_contracts import build_registered_from_db
        return build_registered_from_db(prepared)

    def registration_evidence(self, prepared: Any) -> Any:
        """Exact independent readback for a committed registration checkpoint.

        This does not publish a checkpoint or authorize another registration.
        The existing M4 reader verifies pinned identity, counts and baseline.
        """
        return self.registered(prepared)

    def _plan_argv(self, stage: str, context: Any) -> tuple[str, ...]:
        from scripts.incremental.incremental_execution_context import (
            PreparedContext, RegisteredContext,
        )
        from scripts.incremental.incremental_runner_contracts import plan_command
        if stage not in _WRITERS:
            raise ValueError("Unsupported automatic database stage")
        required = PreparedContext if stage == "SNAPSHOT_REGISTRATION" else RegisteredContext
        if not isinstance(context, required):
            raise TypeError("Stage requires its verified pinned execution context")
        step, module, _, _ = _WRITERS[stage]
        argv = plan_command(step, context, cache_dir=CACHE_DIR)
        if (argv[:3] != (sys.executable, "-m", "scripts.tools." + module)
                or argv[-1] != "--plan" or argv.count("--plan") != 1):
            raise RuntimeError("Unexpected M4 writer command shape")
        if stage == "SNAPSHOT_REGISTRATION":
            argv = argv[:-1] + ("--cache-dir", CACHE_DIR, "--plan")
        return argv

    def writer_argv(self, stage: str, context: Any) -> tuple[str, ...]:
        """Exact allowlisted writer argv, suitable for durable intent hashing."""
        argv = self._plan_argv(stage, context)
        return argv[:-1] + _WRITERS[stage][3]

    def preflight(self, stage: str, context: Any, log_dir: Path) -> None:
        """Run a read-only preflight before reserving a DB write attempt."""
        plan = self._plan_argv(stage, context)
        directory = self._log_directory(log_dir)
        self._run(plan + (_WRITERS[stage][2],), directory / (stage + ".plan.log"),
                  readonly=True)

    def write(self, stage: str, context: Any, log_dir: Path, *, preflight: bool = True) -> None:
        """Run one writer, optionally preceded by the legacy fresh preflight.

        A coordinator using recovery calls preflight before reserving its claim,
        then dispatches once with ``preflight=False``. This method never retries.
        """
        if type(preflight) is not bool:
            raise ValueError("Invalid writer preflight switch")
        if preflight:
            self.preflight(stage, context, log_dir)
        writer = self.writer_argv(stage, context)
        directory = self._log_directory(log_dir)
        self._run(writer, directory / (stage + ".write.log"), readonly=False)

    def _log_directory(self, log_dir: Path) -> Path:
        directory = Path(log_dir)
        if not directory.is_absolute():
            directory = self.root / directory
        try:
            parts = directory.relative_to(self.root / "reports").parts
        except ValueError as exc:
            raise ValueError("Writer logs must be inside project reports/") from exc
        current = self.root / "reports"
        for part in ("",) + parts:
            if part in (".", ".."):
                raise ValueError("Unsafe writer log path")
            if part:
                current = current / part
            if (current.is_symlink() or current.resolve(strict=False) != current
                    or (current.exists() and not current.is_dir())):
                raise ValueError("Unsafe writer log directory")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _run(self, argv: tuple[str, ...], log_path: Path, *, readonly: bool) -> None:
        if log_path.is_symlink():
            raise ValueError("Writer log cannot be a symlink")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        if readonly:
            env["PGOPTIONS"] = (env.get("PGOPTIONS", "")
                                + " -c default_transaction_read_only=on").strip()
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                        | subprocess.CREATE_NO_WINDOW)
        else:
            kwargs["start_new_session"] = True
        # Each run/stage log is immutable evidence. A collision never overwrites
        # an earlier writer's output or silently starts an additional attempt.
        with log_path.open("xb") as output:
            try:
                child = subprocess.Popen(
                    argv, cwd=str(self.root), env=env, shell=False,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    **kwargs,
                )
            except OSError as exc:
                raise ChildProcessFailure(
                    exit_code=None, log_relative=log_path.relative_to(self.root).as_posix(),
                    readonly=readonly, process_exited=False, retryable=False,
                    outcome_unknown=False, category="child_launch_failed",
                ) from exc
            try:
                code = child.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                try:
                    self._stop_tree(child)
                    exited = child.poll() is not None
                except Exception:
                    exited = False
                raise ChildProcessTimeout(
                    argv=argv, timeout=self.timeout_seconds,
                    log_relative=log_path.relative_to(self.root).as_posix(),
                    readonly=readonly, process_exited=exited,
                ) from exc
            except BaseException:
                self._stop_tree(child)
                raise
        if code != 0:
            retryable, unknown, category = _exit_cause(log_path, readonly=readonly)
            raise ChildProcessFailure(
                exit_code=code, log_relative=log_path.relative_to(self.root).as_posix(),
                readonly=readonly, process_exited=True, retryable=retryable,
                outcome_unknown=unknown, category=category,
            )

    @staticmethod
    def _stop_tree(child: subprocess.Popen) -> None:
        """Stop nested M4 preflight/writer processes before releasing the lock."""
        if os.name == "nt":
            executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                             / "System32" / "taskkill.exe")
            try:
                result = subprocess.run(
                    [executable, "/PID", str(child.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode != 0 and child.poll() is None:
                    child.kill()
            except (OSError, subprocess.TimeoutExpired):
                if child.poll() is None:
                    child.kill()
        else:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=30)

    def verify(self, stage: str, context: Any) -> bool:
        """Return False only for incomplete evidence; blockers are fatal."""
        from scripts.incremental.incremental_execution_context import (
            PreparedContext, RegisteredContext,
        )
        from scripts.incremental.incremental_post_execution_verifier import verify_read_only
        if stage == "SNAPSHOT_REGISTRATION" and isinstance(context, PreparedContext):
            plan = context.planned
            if self.target(plan.repo_id, plan.target_revision) is None:
                return False
            raise RuntimeError("Target already registered without registered checkpoint; reconcile before writing")
        if stage not in _WRITERS or not isinstance(context, RegisteredContext):
            raise ValueError("Database stage verification needs a RegisteredContext")
        plan = context.prepared.planned
        target = self.target(plan.repo_id, plan.target_revision)
        if target is not None and target.status == "completed":
            self.completed(context)
            return True
        with redirect_stdout(sys.stderr):
            result = verify_read_only(
                stage=stage, repo_id=plan.repo_id,
                target_revision=plan.target_revision, root=self.root,
            )
        if result.status == "VERIFIED":
            return True
        if result.status == "INCOMPLETE":
            return False
        raise RuntimeError("Independent stage verification blocked: "
                           + "; ".join(result.blockers))

    def completed(self, registered: Any) -> dict[str, Any]:
        from scripts.incremental.incremental_completed_snapshot_audit import audit_completed_snapshot
        with redirect_stdout(sys.stderr):
            evidence = audit_completed_snapshot(registered, root=self.root)
        prefix = f"Completed snapshot #{registered.target_snapshot_id}; "
        if not isinstance(evidence, str) or not evidence.startswith(prefix):
            raise RuntimeError("Independent completion audit returned invalid evidence")
        return {
            "verified": True, "snapshot_id": registered.target_snapshot_id,
            "target_revision": registered.prepared.planned.target_revision,
            "evidence": evidence,
        }
