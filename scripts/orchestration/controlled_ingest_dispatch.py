"""Controlled DB recovery: terminal process evidence, fresh audit, pinned replay.

The permanent M5 fence remains intact. Separate append-only attempt receipts
permit a bounded replay only after a known transient, exited writer and a
fresh independent INCOMPLETE audit. An unknown process is never replayed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any, Callable

from scripts.orchestration.auto_recovery import RetryPolicy, retry_call
from scripts.orchestration.auto_recovery_journal import StageJournal, digest_json, RecoveryJournalError
from scripts.orchestration.auto_ingest_backend import ChildProcessFailure
from scripts.orchestration.durable_attempt_recovery_audit import audit_attempt
from scripts.orchestration.write_attempt_guard import AttemptClaim, reserve_write_attempt, record_attempt_result


class ManualIntervention(RuntimeError):
    def __init__(self, message: str, reason_code: str = "UNVERIFIED_DATABASE_ATTEMPT"):
        self.reason_code = reason_code
        super().__init__(message)


def _identity(context: Any, argv: tuple[str, ...]) -> tuple[str, str]:
    from scripts.incremental.incremental_execution_context import checkpoint_digest
    digest = checkpoint_digest(context)
    intent = hashlib.sha256(json.dumps({"context": digest, "argv": argv},
                                      sort_keys=True).encode()).hexdigest()
    return digest, intent


def _ledger(root, repo, revision, stage, context, argv, audited):
    digest, intent = _identity(context, argv)
    from scripts.orchestration.durable_attempt_recovery_audit import _directory, _read_json_file
    original, original_sha = _read_json_file(_directory(root, repo, revision) / (stage + ".claim.json"))
    if (original is None or original_sha != audited.claim_sha256
            or original["context_sha256"] != digest or original["intent_sha256"] != intent
            or original["argv_sha256"] != digest_json(argv)
            or original["target_snapshot_id"] != getattr(context, "target_snapshot_id", None)):
        raise RecoveryJournalError("Original attempt differs from pinned recovery context/command")
    return StageJournal(root=root, repo_id=repo, revision=revision, stage=stage,
                        original_claim_sha256=audited.claim_sha256,
                        context_sha256=digest, argv=argv)


def assert_no_unknown_attempts(*, backend, root, repo, revision, prepared, registered):
    from scripts.orchestration.auto_ingest import DB_STAGES
    for stage in DB_STAGES:
        audited = audit_attempt(root=root, repo_id=repo, target_revision=revision, stage=stage)
        if audited.state in ("NO_LOCAL_ATTEMPT", "RECORDED_VERIFIED_REQUIRES_EVIDENCE"):
            continue
        if not audited.claim_present or audited.state.startswith("BLOCKED_"):
            raise ManualIntervention(stage + ": " + audited.state, "INVALID_ATTEMPT_JOURNAL")
        context = prepared if stage == "SNAPSHOT_REGISTRATION" else registered
        if context is None:
            raise ManualIntervention(stage + ": missing REGISTERED checkpoint for existing attempt")
        ledger = _ledger(root, repo, revision, stage, context,
                         backend.writer_argv(stage, context), audited)
        history = ledger.history()
        if not history or history[-1].outcome in (None, "OUTCOME_UNKNOWN"):
            raise ManualIntervention(stage + ": MANUAL_RECONCILIATION_REQUIRED; unknown/crashed writer outcome; inspect durable attempt journal")


def dispatch(*, backend: Any, root: Path, repo: str, revision: str,
             stage: str, context: Any, log_dir: Path, heartbeat: Callable,
             after_write: Callable | None = None, on_attempt: Callable | None = None,
             verification_context: Any = None, policy: RetryPolicy | None = None,
             safe_call: Callable | None = None, on_recovery: Callable | None = None,
             sleeper: Callable = time.sleep) -> Any:
    policy = policy or RetryPolicy()
    safe = safe_call or (lambda name, operation: retry_call(operation, policy=policy, sleeper=sleeper))
    notify = on_recovery or (lambda **data: None)
    argv = backend.writer_argv(stage, context)
    digest, intent = _identity(context, argv)
    audited = audit_attempt(root=root, repo_id=repo, target_revision=revision, stage=stage)
    ledger = None
    claim = None
    candidate_context = verification_context or context

    def verified(*, reconcile=False):
        nonlocal candidate_context
        if stage == "SNAPSHOT_REGISTRATION" and candidate_context is context:
            target = safe(stage + "_TARGET_READ", lambda: backend.target(repo, revision))
            if target is not None:
                if not reconcile or after_write is None:
                    raise ManualIntervention("Target exists without REGISTERED checkpoint; reconcile registration evidence",
                                             "MISSING_REGISTRATION_CHECKPOINT")
                candidate_context = safe(stage + "_CHECKPOINT_RECOVERY", after_write)
        return safe(stage + "_VERIFY", lambda: backend.verify(stage, candidate_context))

    def record_primary(status):
        if claim is not None:
            current = audit_attempt(root=root, repo_id=repo, target_revision=revision, stage=stage)
            if not current.result_present:
                record_attempt_result(claim, status=status)

    def accept(*, recovered):
        if ledger is not None:
            ledger.mark_verified()
        record_primary("VERIFIED_AFTER_WRITE")
        if recovered:
            notify(stage=stage, action="RECONCILED_WITHOUT_REWRITE",
                   attempt=ledger.history()[-1].number if ledger else None)
        return candidate_context

    if audited.state == "RECORDED_VERIFIED_REQUIRES_EVIDENCE":
        if not verified(reconcile=verification_context is not None):
            raise ManualIntervention(stage + ": verified journal conflicts with current database evidence",
                                     "CONTRADICTORY_VERIFIED_EVIDENCE")
        return candidate_context
    if audited.state != "NO_LOCAL_ATTEMPT":
        if not audited.claim_present or audited.state.startswith("BLOCKED_"):
            raise ManualIntervention(stage + ": " + audited.state, "INVALID_ATTEMPT_JOURNAL")
        ledger = _ledger(root, repo, revision, stage, context, argv, audited)
        history = ledger.history()
        if not history or history[-1].outcome in (None, "OUTCOME_UNKNOWN"):
            raise ManualIntervention(stage + ": MANUAL_RECONCILIATION_REQUIRED; unknown/crashed writer outcome; inspect durable attempt journal")
        from scripts.orchestration.durable_attempt_recovery_audit import _directory
        claim = AttemptClaim(_directory(root, repo, revision) / (stage + ".claim.json"), audited.claim_sha256)
        latest = history[-1]
        if verified(reconcile=True):
            return accept(recovered=True)
        if latest.verified or latest.outcome == "CHILD_SUCCEEDED":
            raise ManualIntervention(stage + ": successful/verified writer conflicts with incomplete database evidence",
                                     "CONTRADICTORY_VERIFIED_EVIDENCE")
    else:
        if verified():
            return candidate_context

    while True:
        history = ledger.history() if ledger else ()
        if history and len(history) >= policy.max_attempts:
            raise ManualIntervention(stage + ": controlled writer retry budget exhausted after " + str(len(history)) + " attempts",
                                     "WRITER_RETRY_BUDGET_EXHAUSTED")
        number = len(history) + 1
        attempt_logs = log_dir if number == 1 else log_dir / f"{stage}_attempt_{number:04d}"
        preflight = getattr(backend, "preflight", None)
        if callable(preflight):
            # Separate folders keep every failed read-only preview immutable.
            preview_number = [0]

            def preview():
                preview_number[0] += 1
                folder = attempt_logs / f"preflight_{preview_number[0]:04d}"
                return preflight(stage, context, folder)

            safe(stage + "_PREFLIGHT", preview)
        heartbeat()  # Never continue a writer after losing the lock session.
        if ledger is None:
            claim = reserve_write_attempt(root=root, repo_id=repo, target_revision=revision,
                                          stage=stage, context_sha256=digest, intent_sha256=intent,
                                          target_snapshot_id=getattr(context, "target_snapshot_id", None), argv=argv)
            audited = audit_attempt(root=root, repo_id=repo, target_revision=revision, stage=stage)
            ledger = _ledger(root, repo, revision, stage, context, argv, audited)
        attempt = ledger.begin()
        try:
            if on_attempt is not None:
                on_attempt()
            if callable(preflight):
                backend.write(stage, context, attempt_logs, preflight=False)
            else:
                backend.write(stage, context, attempt_logs)
        except BaseException as exc:
            retryable = (isinstance(exc, ChildProcessFailure) and exc.process_exited is True
                         and exc.retryable is True and exc.outcome_unknown is False)
            category = None
            if retryable:
                category = "database" if "database" in exc.category else "http" if "http" in exc.category else "network"
            ledger.finish(attempt, outcome="TRANSIENT_FAILURE" if retryable else "OUTCOME_UNKNOWN",
                          category=category, exit_code=getattr(exc, "exit_code", None))
            record_primary("UNVERIFIED_AFTER_ATTEMPT")
            if not retryable:
                raise
            if verified(reconcile=True):
                return accept(recovered=True)
            if number >= policy.max_attempts:
                raise ManualIntervention(stage + ": controlled writer retry budget exhausted",
                                         "WRITER_RETRY_BUDGET_EXHAUSTED") from exc
            ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** (number - 1))
            delay = random.uniform(0, ceiling)
            notify(stage=stage, action="RETRY_VERIFIED_INCOMPLETE_STAGE", attempt=number,
                   next_attempt=number + 1, category=category, delay_seconds=delay)
            sleeper(delay)
            continue
        # Persist a known exited-success process *before* readbacks/checkpoints.
        # If a readback is temporarily unavailable, a later run verifies only.
        ledger.finish(attempt, outcome="CHILD_SUCCEEDED", exit_code=0)
        try:
            if after_write is not None:
                candidate_context = safe(stage + "_CHECKPOINT_PUBLICATION", after_write)
            if not verified(reconcile=True):
                raise ManualIntervention(stage + ": independent post-write verification is incomplete",
                                         "POST_WRITE_VERIFICATION_INCOMPLETE")
            return accept(recovered=number > 1)
        except BaseException:
            record_primary("UNVERIFIED_AFTER_ATTEMPT")
            raise
