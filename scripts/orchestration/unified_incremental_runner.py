"""A2 | Scheduled, SINGLE INSTANCE, READ-ONLY incremental revision monitor.

ONE safe scheduled entrypoint: pin HF revision, review Controller/Router,
check checkpoints and durable journals, write a local JSON run receipt.

Does NOT publish checkpoints, download HF bodies, promote archives, modify
PostgreSQL, invoke a writer, approve a stage, or retry ambiguous attempts.
Review states are notifications for supervised operation, NOT ingest success.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
GOOD = frozenset({"SKIPPED_CURRENT", "REVIEW_CHECKPOINT", "REVIEW_PREPARATION", "REVIEW_DB_WRITE"})
MAX_STDOUT = 512 * 1024


@dataclass(frozen=True)
class RunReceipt:
    state: str
    repo_id: str
    revision_ref: str
    target_revision: str | None
    next_review_stage: str | None
    next_action: str
    child_exit_code: int | None
    run_id: str
    timestamp_utc: str
    detail: str = ""
    write_authorized: bool = False
    write_executed: bool = False
    retry_authorized: bool = False
    stage_advanced: bool = False

    def __post_init__(self):
        if any((self.write_authorized, self.write_executed,
                self.retry_authorized, self.stage_advanced)):
            raise ValueError("Scheduled monitor cannot authorize/perform writes")


@contextmanager
def local_process_lock(lock_file: Path) -> Iterator[None]:
    """Exclusive OS-held lock, released on normal exit and process crash.

    Intentionally leaves the lock file itself in place; never unlinks it.
    This coordinates one local machine/filesystem, not distributed workers.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.is_symlink() or lock_file.parent.is_symlink():
        raise RuntimeError("Unsafe scheduler lock path")
    with open(lock_file, "a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
            os.fsync(f.fileno())
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another scheduler instance is active") from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _safe_reports(root: Path) -> None:
    """Fail closed if runtime output directories are symlinked."""
    for relative in ("reports", "reports/m5_scheduler", "reports/m5_scheduler/runs"):
        candidate = root / relative
        if candidate.is_symlink():
            raise RuntimeError("Scheduler output must not traverse a symlink: " + relative)


def _validate(root: Path, repo_id: str, revision_ref: str, baseline_snapshot: int | None):
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Project root must be a real directory")
    _safe_reports(root)
    if not (root / "scripts/orchestration/end_to_end_readonly.py").is_file():
        raise ValueError("Project root lacks E2E read-only module")
    if (not isinstance(repo_id, str) or not repo_id.strip()
            or len(repo_id) > 300 or any(c in repo_id for c in "\r\n\0")):
        raise ValueError("Invalid repository id")
    if (not isinstance(revision_ref, str) or not revision_ref.strip()
            or len(revision_ref) > 200 or revision_ref.startswith("-")
            or any(c in revision_ref for c in "\r\n\0")):
        raise ValueError("Invalid revision ref")
    if baseline_snapshot is not None and (type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise ValueError("Invalid baseline snapshot")


def _child_argv(repo_id: str, revision_ref: str, baseline_snapshot: int | None) -> list[str]:
    argv = [sys.executable, "-B", "-m", "scripts.orchestration.end_to_end_readonly", "--check",
            "--repo-id", repo_id, "--revision-ref", revision_ref]
    if baseline_snapshot is not None:
        argv += ["--baseline-snapshot", str(baseline_snapshot)]
    return argv


def _interpret(returncode: int, stdout: str, repo_id: str, revision_ref: str) -> tuple[str, str | None, str | None, str, str]:
    if not isinstance(stdout, str):
        raise ValueError("E2E stdout was not text")
    if len(stdout.encode("utf-8")) > MAX_STDOUT:
        raise ValueError("E2E output exceeded bounded size")
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("E2E output is not a JSON object")
    state = payload.get("state")
    if not isinstance(state, str):
        raise ValueError("E2E state missing")
    # A child must never report activity. Literal False required, not falsey values.
    for key in ("write_authorized", "write_executed", "retry_authorized", "stage_advanced"):
        if payload.get(key) is not False:
            raise ValueError("E2E child missing safety guarantee: " + key)
    if state in GOOD:
        if returncode != 0:
            raise ValueError("E2E safe state conflicts with subprocess exit code")
    else:
        if returncode != 2:
            raise ValueError("E2E unsafe/blocked state has unexpected exit code")
        # Do not accidentally classify an unknown successful state as safe.
        return "BLOCKED_REVIEW", None, None, "Manual investigation required", f"E2E state: {state}"
    if payload.get("repo_id") != repo_id or payload.get("requested_ref") != revision_ref:
        raise ValueError("E2E identity/ref mismatch")
    sha = payload.get("target_revision")
    if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
        raise ValueError("E2E target revision is not pinned SHA40")
    stage = payload.get("next_review_stage")
    if stage is not None and (not isinstance(stage, str) or not stage):
        raise ValueError("Invalid next review stage")
    if state == "SKIPPED_CURRENT" and stage is not None:
        raise ValueError("Completed revision reports next stage")
    if state != "SKIPPED_CURRENT" and stage is None:
        raise ValueError("Review required without next stage")
    action = payload.get("next_action")
    if not isinstance(action, str):
        raise ValueError("Invalid next action")
    return state, sha, stage, action, ""


def _receipt(root: Path, receipt: RunReceipt) -> Path:
    folder = root / "reports" / "m5_scheduler" / "runs"
    _safe_reports(root)
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise RuntimeError("Unsafe scheduler receipt path")
    path = folder / (receipt.run_id + ".json")
    data = (json.dumps(asdict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return path


def run_once(*, root: Path = ROOT, repo_id: str = DEFAULT_REPO,
             revision_ref: str = "main", baseline_snapshot: int | None = None,
             timeout_seconds: int = 600,
             launcher: Callable[..., Any] = subprocess.run) -> tuple[RunReceipt, int]:
    """Return (durably logged read-only receipt, process exit code)."""
    _validate(root, repo_id, revision_ref, baseline_snapshot)
    if type(timeout_seconds) is not int or not (1 <= timeout_seconds <= 3600):
        raise ValueError("Timeout outside safe bounds")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ_") + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    kwargs = dict(state="BLOCKED", repo_id=repo_id, revision_ref=revision_ref,
                  target_revision=None, next_review_stage=None,
                  next_action="No automatic execution", child_exit_code=None,
                  run_id=run_id, timestamp_utc=now)
    try:
        lock_path = root / "reports" / "m5_scheduler" / "single_instance.lock"
        with local_process_lock(lock_path):
            env = os.environ.copy()
            previous_pg = env.get("PGOPTIONS", "").strip()
            env["PGOPTIONS"] = (previous_pg + " -c default_transaction_read_only=on").strip()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = launcher(_child_argv(repo_id, revision_ref, baseline_snapshot),
                            cwd=str(root), env=env, shell=False, check=False,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout_seconds)
            kwargs["child_exit_code"] = proc.returncode
            state, sha, stage, action, detail = _interpret(proc.returncode, proc.stdout, repo_id, revision_ref)
            kwargs.update(state=state, target_revision=sha, next_review_stage=stage,
                          next_action=action, detail=detail)
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        kwargs.update(state="BLOCKED", detail=f"{type(exc).__name__}: {exc}")
    result = RunReceipt(**kwargs)
    _receipt(root, result)
    return result, (0 if result.state in GOOD else 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True,
                        help="Only HF metadata + PostgreSQL read-only planning; never ingest")
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--revision-ref", default="main")
    parser.add_argument("--baseline-snapshot", type=int, default=None,
                        help="Optional pinned baseline for testing; omit for recurring checks")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        result, code = run_once(repo_id=args.repo_id, revision_ref=args.revision_ref,
                                baseline_snapshot=args.baseline_snapshot,
                                timeout_seconds=args.timeout_seconds)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return code
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"state": "BLOCKED", "detail": f"{type(exc).__name__}: {exc}",
                          "write_authorized": False, "write_executed": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
