"""Scheduled, revision-pinned incremental ingestion into PostgreSQL Raw Storage.

--check only inspects metadata/checkpoints. --run authorizes the complete
pipeline once; the scheduler supplies this flag after installation. Existing
M4 writers, immutable archives and independent evidence checks remain in use.
Artifact preparation is retryable; ambiguous database attempts are fenced.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable
import uuid

from scripts.orchestration.commit_watcher import validate_ref
from scripts.orchestration.durable_attempt_recovery_audit import audit_attempt
from scripts.orchestration.unified_incremental_runner import local_process_lock
from scripts.orchestration.write_attempt_guard import reserve_write_attempt, record_attempt_result
from scripts.orchestration.auto_recovery import RetryPolicy, RetryExhausted, classify_failure, retry_call

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
DB_STAGES = ("SNAPSHOT_REGISTRATION", "REUSE_LINKING", "CANDIDATE_INGESTION",
             "MANIFEST_REGISTRATION", "FINALIZATION")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_detail(exc: Exception, root: Path) -> str:
    detail = str(exc)
    secrets = [os.environ.get(key) for key in ("HF_TOKEN", "DB_PASSWORD", "DATABASE_URL")]
    try:
        from dotenv import dotenv_values
        for name in (".env", ".env.v2"):
            values = dotenv_values(root / name, encoding="utf-8-sig")
            secrets.extend(values.get(key) for key in ("HF_TOKEN", "DB_PASSWORD", "DATABASE_URL"))
    except Exception:
        pass
    for value in secrets:
        if value:
            detail = detail.replace(value, "[REDACTED]")
    detail = re.sub(r"(?i)(password|token)\s*=\s*\S+", r"\1=[REDACTED]", detail)
    return detail[:2000]


def _safe(root: Path, relative: str) -> Path:
    path = root
    for part in relative.split("/"):
        if part in ("", ".", ".."):
            raise RuntimeError("Unsafe automation path")
        path /= part
        if path.is_symlink():
            raise RuntimeError("Automation path traverses a symlink")
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Automation path escapes project root")
    return path


def _save(path: Path, payload: dict) -> None:
    """Replace only mutable run metadata, never an M4 checkpoint or raw file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".auto-", delete=False) as out:
            staged = Path(out.name)
            json.dump(payload, out, ensure_ascii=False, indent=2, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        # Windows readers (including --status) can temporarily deny deletion of
        # the previous receipt. Retry only this atomic metadata replacement,
        # keeping the same flushed bytes; never replay a pipeline stage.
        for attempt in range(6):
            try:
                os.replace(staged, path)
                break
            except OSError as exc:
                if (os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33)
                        or attempt == 5):
                    raise
                time.sleep(0.01 * 2 ** attempt)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _read_active(path: Path, repo: str) -> dict | None:
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 16384:
        raise RuntimeError("Invalid active ingestion receipt")
    active = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(active, dict) or active.get("schema") != "auto-ingest-active-v1"
            or active.get("repo_id") != repo
            or not isinstance(active.get("target_revision"), str)
            or not SHA40.fullmatch(active["target_revision"])
            or type(active.get("finished")) is not bool):
        raise RuntimeError("Active ingestion identity is invalid")
    return None if active["finished"] else active


@contextmanager
def database_lock():
    """Session lock also serializes automation from another checkout/machine."""
    import psycopg
    from scripts.tools.ingestion_helper import connection_parameters
    params = connection_parameters()
    params.update(keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    with psycopg.connect(**params) as conn:
        # A constant namespace serializes the only supported repository/database.
        if not conn.execute("SELECT pg_try_advisory_lock(1768974161, 1701866545)").fetchone()[0]:
            raise RuntimeError("Another database ingestion worker is active")
        try:
            yield lambda: conn.execute("SELECT 1").fetchone()
        finally:
            if not conn.closed:
                try:
                    conn.execute("SELECT pg_advisory_unlock(1768974161, 1701866545)")
                except psycopg.Error:
                    # A lost session releases its lock on disconnect. Cleanup
                    # must not mask an earlier unknown-write/manual fence.
                    pass


def _journal(root: Path, repo: str, revision: str, stage: str) -> str:
    audited = audit_attempt(root=root, repo_id=repo, target_revision=revision, stage=stage)
    if audited.state == "NO_LOCAL_ATTEMPT":
        return "ABSENT"
    if audited.state == "RECORDED_VERIFIED_REQUIRES_EVIDENCE":
        return "VERIFIED"
    raise RuntimeError(f"{stage}: {audited.state}; inspect durable attempt journal before recovery")


def _dispatch(*, backend: Any, root: Path, repo: str, revision: str,
              stage: str, context: Any, log_dir: Path, heartbeat: Callable,
              after_write: Callable | None = None,
              on_attempt: Callable | None = None, **recovery_options) -> Any:
    from scripts.orchestration.controlled_ingest_dispatch import dispatch
    return dispatch(backend=backend, root=root, repo=repo, revision=revision,
                    stage=stage, context=context, log_dir=log_dir, heartbeat=heartbeat,
                    after_write=after_write, on_attempt=on_attempt, **recovery_options)


def run_once(*, root: Path = ROOT, repo_id: str = DEFAULT_REPO,
             revision_ref: str = "main", execute: bool = False,
             timeout_seconds: int = 21600, backend: Any = None,
             lock_factory: Callable | None = None, retry_policy: RetryPolicy | None = None,
             sleeper: Callable = time.sleep) -> tuple[dict, int]:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Real project root required")
    if repo_id != DEFAULT_REPO:
        raise ValueError("Only the configured EPL dataset is supported")
    validate_ref(revision_ref)
    if type(execute) is not bool or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 21600:
        raise ValueError("Invalid run mode or stage timeout (1..21600 seconds)")
    policy = retry_policy or RetryPolicy()
    if not isinstance(policy, RetryPolicy):
        raise TypeError("Validated retry policy required")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ_") + uuid.uuid4().hex[:12]
    receipt = dict(schema="auto-ingest-run-v1", run_id=run_id, started_at_utc=_now(),
                   repo_id=repo_id, requested_ref=revision_ref, target_revision=None,
                   state="RUNNING", mode="run" if execute else "check", steps=[],
                   snapshot_id=None, write_authorized=execute, write_executed=False,
                   retry_policy=asdict(policy), retry_events=[], recoveries=[],
                   intervention_required=False, recovery_status="NOT_NEEDED",
                   supported_candidate_formats=["json", "csv"])
    receipt_path = _safe(root, f"reports/auto_ingest/runs/{run_id}/receipt.json")
    active_path = _safe(root, "reports/auto_ingest/active.json")
    stage = "INITIALIZE"

    def event(name: str, **data):
        receipt["steps"].append(dict(stage=name, verified_at_utc=_now(), **data))
        _save(receipt_path, receipt)

    def attempted():
        receipt["write_executed"] = True
        _save(receipt_path, receipt)

    def retry_event(operation, data):
        receipt["retry_events"].append(dict(operation=operation, timestamp_utc=_now(), **data))
        _save(receipt_path, receipt)

    def safe(operation, function):
        return retry_call(function, policy=policy, sleeper=sleeper,
                          on_retry=lambda data: retry_event(operation, data))

    def recovered(**data):
        receipt["recoveries"].append(dict(timestamp_utc=_now(), **data))
        _save(receipt_path, receipt)

    recovery_options = dict(policy=policy, safe_call=safe, on_recovery=recovered, sleeper=sleeper)

    try:
        with local_process_lock(_safe(root, "reports/m5_scheduler/single_instance.lock")):
            if backend is None:
                from scripts.orchestration.auto_ingest_backend import Backend
                backend = Backend(root, timeout_seconds)
            with ExitStack() as stack:
                heartbeat = safe("DATABASE_LOCK", lambda: stack.enter_context(
                    (lock_factory or database_lock)())) if execute else (lambda: None)
                heartbeat = heartbeat or (lambda: None)
                active = _read_active(active_path, repo_id)
                stage = "RESOLVE"
                revision = active["target_revision"] if active else safe(
                    "RESOLVE_REVISION", lambda: backend.resolve(repo_id, revision_ref))
                if not isinstance(revision, str) or not SHA40.fullmatch(revision):
                    raise RuntimeError("Source did not return a pinned lowercase commit SHA")
                receipt.update(target_revision=revision, resumed=active is not None)
                planned, prepared, registered = safe("READ_CHECKPOINTS", lambda: backend.read_chain(revision))
                state = safe("READ_TARGET", lambda: backend.target(repo_id, revision))
                if state is not None and state.status == "completed" and active is None:
                    receipt.update(state="SKIPPED_CURRENT", snapshot_id=state.snapshot_id)
                else:
                    if planned is None:
                        stage = "PLAN"
                        outcome = safe("PLAN", lambda: backend.plan(repo_id, revision))
                        if outcome.decision in ("SKIP_ALREADY_CURRENT", "SKIP_ALREADY_PROCESSED", "SKIP_NO_RAW_CHANGES"):
                            receipt.update(state=outcome.decision.replace("SKIP_", "SKIPPED_"),
                                           snapshot_id=outcome.target_snapshot_id)
                            if active and execute:
                                _save(active_path, {**active, "finished": True})
                            return _finish(receipt_path, receipt, 0)
                        if outcome.decision != "PLAN_NEW_REVISION" or outcome.planned is None:
                            raise RuntimeError("Planning blocked: " + outcome.decision)
                        planned = outcome.planned
                    if planned.repo_id != repo_id or planned.target_revision != revision:
                        raise RuntimeError("Checkpoint repository/revision drift")
                    receipt.update(baseline_snapshot_id=planned.baseline_snapshot_id,
                                   candidate_files=planned.expected_files,
                                   candidate_bytes=planned.expected_bytes,
                                   reuse_files=planned.expected_reuse,
                                   candidate_formats={fmt: sum(c.path.endswith("." + fmt) for c in planned.candidates)
                                                      for fmt in ("json", "csv")},
                                   candidate_paths=[c.path for c in planned.candidates])
                    if not execute:
                        if prepared is not None:
                            from scripts.orchestration.controlled_ingest_dispatch import assert_no_unknown_attempts
                            stage = "RECOVERY_AUDIT"
                            assert_no_unknown_attempts(backend=backend, root=root, repo=repo_id, revision=revision,
                                                       prepared=prepared, registered=registered)
                        receipt.update(state="READY_TO_INGEST", pending_checkpoint=type(
                            registered or prepared or planned).__name__)
                        if active is not None:
                            receipt.update(recovery_status="RESUME_AVAILABLE", next_action="Run --run to resume this pinned revision")
                    else:
                        if active is None:
                            active = dict(schema="auto-ingest-active-v1", repo_id=repo_id,
                                          target_revision=revision, started_at_utc=_now(), finished=False)
                            _save(active_path, active)
                        heartbeat()
                        stage = "PLANNED"
                        safe("PUBLISH_PLANNED", lambda: backend.publish(planned))
                        event(stage)
                        if prepared is None:
                            stage = "MANIFEST_PREPARATION"
                            manifest_sha = safe(stage, lambda: backend.manifest(planned))
                            event(stage, manifest_sha256=manifest_sha)
                            stage = "CACHE_AND_ARCHIVE_PREPARATION"
                            prepared = safe(stage, lambda: backend.prepare(planned, manifest_sha))
                            heartbeat()
                            safe("PUBLISH_PREPARED", lambda: backend.publish(prepared))
                            event("PREPARED", candidate_records=prepared.expected_records)
                        from scripts.orchestration.controlled_ingest_dispatch import assert_no_unknown_attempts
                        stage = "RECOVERY_AUDIT"
                        assert_no_unknown_attempts(backend=backend, root=root, repo=repo_id, revision=revision,
                                                   prepared=prepared, registered=registered)
                        stage = "SNAPSHOT_REGISTRATION"

                        def registration():
                            value = registered or backend.registered(prepared)
                            backend.publish(value)
                            return value

                        registered = _dispatch(backend=backend, root=root, repo=repo_id,
                                               revision=revision, stage=stage, context=prepared,
                                               log_dir=receipt_path.parent, heartbeat=heartbeat,
                                               verification_context=registered,
                                               after_write=registration, on_attempt=attempted,
                                               **recovery_options)
                        event(stage, snapshot_id=registered.target_snapshot_id)
                        receipt["snapshot_id"] = registered.target_snapshot_id
                        for stage in DB_STAGES[1:]:
                            _dispatch(backend=backend, root=root, repo=repo_id, revision=revision,
                                      stage=stage, context=registered, log_dir=receipt_path.parent,
                                      heartbeat=heartbeat, on_attempt=attempted, **recovery_options)
                            event(stage, snapshot_id=registered.target_snapshot_id)
                        stage = "COMPLETED_AUDIT"
                        audit = safe(stage, lambda: backend.completed(registered))
                        if not isinstance(audit, dict) or audit.get("verified") is not True:
                            raise RuntimeError("Independent completed snapshot audit did not pass")
                        event(stage, evidence=audit)
                        _save(active_path, {**active, "finished": True, "snapshot_id": registered.target_snapshot_id,
                                            "finished_at_utc": _now()})
                        receipt.update(state="COMPLETED", audit=audit)
    except Exception as exc:
        from scripts.orchestration.controlled_ingest_dispatch import ManualIntervention
        from scripts.orchestration.auto_recovery_journal import RecoveryJournalError
        classified = classify_failure(exc)
        manual = isinstance(exc, (ManualIntervention, RecoveryJournalError)) or classified.intervention_required
        busy = type(exc) is RuntimeError and str(exc) in (
            "Another scheduler instance is active", "Another database ingestion worker is active")
        if busy:
            manual = False
        # A transient readback failure can resume from its known-success process
        # receipt next run; unknown write processes remain a manual fence.
        recovery_state = "DEFERRED_BUSY" if busy else "MANUAL_INTERVENTION_REQUIRED" if manual else "RETRY_EXHAUSTED"
        receipt.update(state="BLOCKED", failed_stage=stage,
                       error_type=type(exc).__name__, detail=_error_detail(exc, root),
                       error_category="concurrency" if busy else classified.category,
                       reason_code="WORKER_ALREADY_ACTIVE" if busy else getattr(exc, "reason_code", classified.reason),
                       intervention_required=manual, recovery_status=recovery_state,
                       next_action=("Another worker owns the ingestion lock; wait for that run or the next scheduled run" if busy else
                                    "Inspect receipt, checkpoints and durable journals; no automatic replay of unknown writes"
                                    if manual else "Temporary failure exhausted this run; next scheduled run resumes the pinned verified state"))
        return _finish(receipt_path, receipt, 2)
    if receipt["retry_events"] or receipt["recoveries"]:
        receipt["recovery_status"] = "RECOVERED"
    return _finish(receipt_path, receipt, 0)


def _finish(path: Path, receipt: dict, code: int) -> tuple[dict, int]:
    if code == 0 and (receipt.get("retry_events") or receipt.get("recoveries")):
        receipt["recovery_status"] = "RECOVERED"
    receipt["finished_at_utc"] = _now()
    receipt["receipt_path"] = path.relative_to(path.parents[4]).as_posix()
    _save(path, receipt)
    _save(_safe(path.parents[4], "reports/auto_ingest/latest.json"), receipt)
    return receipt, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Only source metadata and database reads")
    mode.add_argument("--run", action="store_true", help="Run all verified ingestion stages automatically")
    mode.add_argument("--status", action="store_true", help="Read the latest local result without Hugging Face or database access")
    parser.add_argument("--revision-ref", default="main")
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=float, default=2.0)
    parser.add_argument("--retry-max-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        if args.status:
            path = _safe(ROOT, "reports/auto_ingest/latest.json")
            if not path.is_file():
                print(json.dumps(dict(state="NO_RUN_RECEIPT", next_action="Run --check or --run once")))
                return 0
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Latest ingestion receipt exceeds size limit")
            result = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or result.get("schema") != "auto-ingest-run-v1":
                raise ValueError("Latest ingestion receipt is invalid")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        result, code = run_once(repo_id=args.repo_id, revision_ref=args.revision_ref,
                                execute=args.run, timeout_seconds=args.timeout_seconds,
                                retry_policy=RetryPolicy(args.max_attempts, args.retry_base_seconds, args.retry_max_seconds))
    except Exception as exc:
        result, code = dict(state="BLOCKED", error_type=type(exc).__name__, detail=str(exc)), 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
