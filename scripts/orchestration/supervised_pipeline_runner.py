"""A4 | One-entrypoint, human-approved, single-stage EPL raw-storage runner.

`--plan` delegates to exactly one trusted M5 read-only planner.
`--execute-one-stage` first RE-PLANS, matches exact stage/context/intent,
then delegates to a trusted M5 guarded executor. No auto-loop or retries.

The registered Windows scheduled task must continue to invoke the *A2* read-only
unified_incremental_runner --check. Do not schedule this execution interface.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
ROOT = Path(__file__).resolve().parents[2]
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_OUTPUT = 256 * 1024

CHECKPOINTS = frozenset({"PLANNED", "PREPARED", "REGISTERED"})
PREPARATIONS = frozenset({"MANIFEST_PREPARATION", "CACHE_DOWNLOAD", "ARCHIVE_PROMOTION"})
DB_STAGES = frozenset({"SNAPSHOT_REGISTRATION", "REUSE_LINKING", "CANDIDATE_INGESTION",
                       "MANIFEST_REGISTRATION", "FINALIZATION"})
STAGES = CHECKPOINTS | PREPARATIONS | DB_STAGES
MODULES = {
    "checkpoint": "scripts.orchestration.checkpoint_lifecycle",
    "preparation": "scripts.orchestration.guarded_preparation_executor",
    "scoped": "scripts.orchestration.safe_stage_transition",
    "database": "scripts.orchestration.single_stage_write_executor",
}


class SupervisedBlocked(RuntimeError):
    def __init__(self, message: str, *, may_have_attempted: bool = False):
        super().__init__(message)
        self.may_have_attempted = may_have_attempted


@dataclass(frozen=True)
class Request:
    stage: str
    repo_id: str
    target_revision: str
    baseline_snapshot: int
    manifest_sha256: str | None = None
    context_sha256: str | None = None
    intent_sha256: str | None = None
    candidate_cache: str | None = None
    scoped_preparation: bool = False

    def __post_init__(self):
        if (self.stage not in STAGES or self.repo_id != DEFAULT_REPO
                or not SHA40.fullmatch(self.target_revision)
                or type(self.baseline_snapshot) is not int or self.baseline_snapshot <= 0):
            raise SupervisedBlocked("Invalid pinned repository, revision, stage or baseline")
        for name in ("manifest_sha256", "context_sha256", "intent_sha256"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not SHA256.fullmatch(value)):
                raise SupervisedBlocked("Invalid pinned SHA256 for " + name)
        if self.manifest_sha256 is not None and self.stage not in ("MANIFEST_PREPARATION", "PREPARED"):
            raise SupervisedBlocked("Manifest digest is only used for MANIFEST_PREPARATION/PREPARED")
        if self.stage in ("MANIFEST_PREPARATION", "PREPARED") and self.manifest_sha256 is None:
            # For completed revision skip MANIFEST, allow --plan without digest,
            # but no execution can ever succeed without it.
            pass
        if self.candidate_cache is not None:
            if (self.stage != "CANDIDATE_INGESTION" or not isinstance(self.candidate_cache, str)
                    or not self.candidate_cache.strip() or len(self.candidate_cache) > 1024
                    or "\0" in self.candidate_cache or "\n" in self.candidate_cache):
                raise SupervisedBlocked("Candidate cache only applies to candidate ingestion")
        if self.scoped_preparation and self.stage not in ("CACHE_DOWNLOAD", "ARCHIVE_PROMOTION"):
            raise SupervisedBlocked("Scoped preparation is only for Cache/Archive recovery")
        if self.stage in CHECKPOINTS and self.intent_sha256 is not None:
            raise SupervisedBlocked("Checkpoint publication requires context digest only")

    @property
    def kind(self) -> str:
        if self.stage in CHECKPOINTS:
            return "checkpoint"
        if self.stage in PREPARATIONS:
            return "scoped" if self.scoped_preparation else "preparation"
        return "database"


def _argv(request: Request, *, execution: bool) -> list[str]:
    argv = [sys.executable, "-B", "-m", MODULES[request.kind]]
    argv.extend(("--repo-id", request.repo_id, "--target-revision", request.target_revision,
                 "--baseline-snapshot", str(request.baseline_snapshot), "--stage", request.stage))
    if request.manifest_sha256:
        argv.extend(("--expected-manifest-sha256", request.manifest_sha256))
    if request.candidate_cache:
        argv.extend(("--expected-candidate-cache", request.candidate_cache))
    if not execution:
        argv.append("--plan")
        if request.kind != "checkpoint" and request.context_sha256:
            argv.extend(("--expected-context-sha256", request.context_sha256))
    elif request.kind == "checkpoint":
        argv.extend(("--publish", "--approve-stage", request.stage,
                     "--expected-context-sha256", request.context_sha256))
    elif request.kind in ("preparation", "scoped"):
        argv.extend(("--execute-one-stage", "--approve-stage", request.stage,
                     "--expected-context-sha256", request.context_sha256,
                     "--expected-intent-sha256", request.intent_sha256,
                     "--confirm-artifact-write"))
    else:
        argv.extend(("--execute-one-stage", "--approve-stage", request.stage,
                     "--expected-context-sha256", request.context_sha256,
                     "--expected-write-intent-sha256", request.intent_sha256,
                     "--confirm-single-stage-write"))
    return argv


def _parse_result(proc: Any, *, stage: str, execution: bool = False) -> dict[str, Any]:
    if type(getattr(proc, "returncode", None)) is not int or proc.returncode not in (0, 2):
        raise SupervisedBlocked("Trusted backend returned unsupported exit code", may_have_attempted=execution)
    raw = getattr(proc, "stdout", None)
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_OUTPUT:
        raise SupervisedBlocked("Trusted backend output absent or oversized", may_have_attempted=execution)
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SupervisedBlocked("Trusted backend did not emit one JSON object", may_have_attempted=execution) from exc
    if not isinstance(result, dict) or result.get("stage") != stage:
        raise SupervisedBlocked("Trusted backend stage identity mismatch", may_have_attempted=execution)
    if not isinstance(result.get("state"), str):
        raise SupervisedBlocked("Trusted backend state missing", may_have_attempted=execution)
    for flag in ("write_authorized", "db_write_executed", "write_executed", "artifact_write_executed",
                 "hf_body_downloaded", "retry_authorized", "stage_advanced", "checkpoint_published"):
        if flag in result and type(result[flag]) is not bool:
            raise SupervisedBlocked("Backend safety field is not a boolean: " + flag,
                                    may_have_attempted=execution)
        if not execution and result.get(flag) is True:
            raise SupervisedBlocked("Backend preview falsely reports a write: " + flag)
    return result


def _trusted_backend_result(result: dict[str, Any], proc: Any, request: Request,
                            *, executing: bool) -> bool:
    """Reject any accidental success on incomplete or mismatched output."""
    if (result.get("repo_id") != request.repo_id
            or result.get("target_revision") != request.target_revision):
        raise SupervisedBlocked("Backend repository/revision identity drift", may_have_attempted=executing)
    state = result["state"]
    if executing:
        if proc.returncode != 0:
            raise SupervisedBlocked("Writer/checkpoint backend blocked; inspect its durable evidence", may_have_attempted=True)
        if request.kind == "checkpoint":
            if state not in ("CREATED_NEW", "ALREADY_VERIFIED") or result.get("checkpoint_published") is not True:
                raise SupervisedBlocked("Checkpoint backend did not confirm immutable publication", may_have_attempted=True)
        elif request.kind in ("preparation", "scoped"):
            if (state != "VERIFIED_AFTER_WRITE" or result.get("artifact_write_verified") is not True
                    or result.get("db_write_executed") is not False
                    or result.get("stage_advanced") is not False):
                raise SupervisedBlocked("Artifact backend not independently verified", may_have_attempted=True)
        else:
            if (state != "VERIFIED_AFTER_WRITE" or result.get("write_verified") is not True
                    or result.get("next_stage_authorized") is not False):
                raise SupervisedBlocked("DB backend not independently verified", may_have_attempted=True)
        return True
    if request.kind == "checkpoint":
        return proc.returncode == 0 and state == "REVIEW_ONLY" and isinstance(result.get("context_sha256"), str)
    if request.kind in ("preparation", "scoped"):
        return (proc.returncode == 0 and state == "READY_FOR_EXPLICIT_APPROVAL"
                and isinstance(result.get("context_sha256"), str)
                and isinstance(result.get("intent_sha256"), str))
    return (proc.returncode == 0 and state == "READY_FOR_EXPLICIT_APPROVAL"
            and isinstance(result.get("context_sha256"), str)
            and isinstance(result.get("intent_sha256"), str))


def _run_child(argv: list[str], *, root: Path, timeout: int,
               launcher: Callable[..., Any], execution: bool):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if not execution:
        # A read-only review can never escalate even if its M4 DB connection
        # misbehaves. For a writer, normal transaction settings are required.
        env["PGOPTIONS"] = (env.get("PGOPTIONS", "") + " -c default_transaction_read_only=on").strip()
    try:
        return launcher(argv, cwd=str(root), shell=False, check=False,
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SupervisedBlocked("Trusted backend launch/timeout; manual evidence review required",
                                may_have_attempted=execution) from exc


def operate(*, request: Request, root: Path = ROOT, execute: bool = False,
            approve_stage: str | None = None, confirm: bool = False,
            launcher: Callable[..., Any] = subprocess.run,
            timeout_seconds: int = 900) -> tuple[dict[str, Any], int]:
    """One review, optionally one approved dispatch; NEVER auto-advance."""
    if (not isinstance(root, Path) or not root.is_dir() or root.is_symlink()
            or not (root / "scripts/orchestration/end_to_end_readonly.py").is_file()):
        raise SupervisedBlocked("Real Raw Storage V2 root required")
    if type(execute) is not bool or type(confirm) is not bool:
        raise SupervisedBlocked("Invalid execution/confirmation flags")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 900:
        raise SupervisedBlocked("Timeout outside safe limits")
    if not execute and (approve_stage is not None or confirm):
        raise SupervisedBlocked("--plan forbids approvals")
    if execute:
        if approve_stage != request.stage or not confirm or not request.context_sha256:
            raise SupervisedBlocked("Explicit matching stage, context SHA256 and confirmation required")
        if request.kind != "checkpoint" and not request.intent_sha256:
            raise SupervisedBlocked("Writer Intent SHA256 mandatory")
        if request.stage in ("MANIFEST_PREPARATION", "PREPARED") and request.manifest_sha256 is None:
            raise SupervisedBlocked("Pinned manifest SHA256 mandatory for this stage")
        if request.stage == "PLANNED" and request.baseline_snapshot <= 0:
            raise SupervisedBlocked("PLANNED requires pinned baseline")
    # No scheduler lock for read-only preview; the existing A2 task may read in
    # parallel. For execution, serialize with that monitor on this local machine.
    if execute:
        from scripts.orchestration.unified_incremental_runner import local_process_lock
        lock = root / "reports" / "m5_scheduler" / "single_instance.lock"
        context = local_process_lock(lock)
    else:
        from contextlib import nullcontext
        context = nullcontext()
    with context:
        plan_proc = _run_child(_argv(request, execution=False), root=root,
                               timeout=timeout_seconds, launcher=launcher, execution=False)
        plan = _parse_result(plan_proc, stage=request.stage)
        ready = _trusted_backend_result(plan, plan_proc, request, executing=False)
        if not execute:
            return {"state": "READY_FOR_MANUAL_APPROVAL" if ready else "REVIEW_ONLY_OR_BLOCKED",
                    "stage": request.stage, "kind": request.kind, "repo_id": request.repo_id,
                    "target_revision": request.target_revision, "backend": plan,
                    "write_authorized": False, "write_executed": False, "stage_advanced": False,
                    "next_stage_authorized": False}, (0 if ready or (plan_proc.returncode == 0 and plan["state"] == "SKIPPED") else 2)
        if not ready:
            raise SupervisedBlocked("Trusted backend did not issue a ready preview; no execution")
        if plan.get("context_sha256") != request.context_sha256:
            raise SupervisedBlocked("Approved context SHA256 differs from fresh preview")
        if request.kind != "checkpoint" and plan.get("intent_sha256") != request.intent_sha256:
            raise SupervisedBlocked("Approved intent SHA256 differs from fresh preview")
        proc = _run_child(_argv(request, execution=True), root=root,
                          timeout=timeout_seconds, launcher=launcher, execution=True)
        result = _parse_result(proc, stage=request.stage, execution=True)
        _trusted_backend_result(result, proc, request, executing=True)
        return {"state": "ONE_STAGE_VERIFIED", "stage": request.stage, "kind": request.kind,
                "repo_id": request.repo_id, "target_revision": request.target_revision,
                "backend": result, "execution_attempted": True,
                "next_stage_authorized": False, "automatic_retry_authorized": False,
                "stage_advanced": False}, 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--execute-one-stage", action="store_true")
    p.add_argument("--stage", choices=sorted(STAGES), required=True)
    p.add_argument("--repo-id", default=DEFAULT_REPO)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--expected-manifest-sha256")
    p.add_argument("--expected-context-sha256")
    p.add_argument("--expected-intent-sha256")
    p.add_argument("--expected-candidate-cache")
    p.add_argument("--scoped-preparation", action="store_true")
    p.add_argument("--approve-stage")
    p.add_argument("--confirm-one-stage", action="store_true")
    a = p.parse_args(argv)
    try:
        request = Request(stage=a.stage, repo_id=a.repo_id,
                          target_revision=a.target_revision, baseline_snapshot=a.baseline_snapshot,
                          manifest_sha256=a.expected_manifest_sha256,
                          context_sha256=a.expected_context_sha256,
                          intent_sha256=a.expected_intent_sha256,
                          candidate_cache=a.expected_candidate_cache,
                          scoped_preparation=a.scoped_preparation)
        report, code = operate(request=request, execute=a.execute_one_stage,
                               approve_stage=a.approve_stage, confirm=a.confirm_one_stage)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code
    except (SupervisedBlocked, ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"state": "BLOCKED_OR_UNVERIFIED", "reason": str(exc),
                          "execution_may_have_been_attempted": bool(getattr(exc, "may_have_attempted", False)),
                          "automatic_retry_authorized": False,
                          "next_stage_authorized": False}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
