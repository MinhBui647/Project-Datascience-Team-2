"""M5.5.5c.3a — Preparation stage read-only bridge to the M4 stage contract.

Purpose: normalize the three M4 preparation previews (manifest/cache/archive)
into a single trusted, fingerprinted *review* with prerequisite checkpoint checks.

Not an executor: there is NO child launcher, no --execute, no artifact mutation,
no checkpoint publication, no database write, and no implicit stage advancement.
Only a future separately audited runner can execute M4 preparation side effects.

Live service readers are deliberately imported lazily, to make tests entirely
independent of the user's local PostgreSQL and Hugging Face installations.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

REPO_ID = "Toibinguz/epl-premier-league-dataset"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PREPARATION = {
    "MANIFEST_PREPARATION": "prepare_incremental_manifest",
    "CACHE_DOWNLOAD": "download_incremental_candidates",
    "ARCHIVE_PROMOTION": "promote_incremental_archive",
}
# Plan-safe switches. Other flags need scrutiny; these deny side-effect switches.
FORBIDDEN_SWITCHES = frozenset({
    "--apply", "--download", "--promote", "--publish", "--register",
    "--link", "--commit", "--confirm-manifest-archive-only", "--confirm-cache-only",
    "--confirm-archive-only", "--allow-csv-manifest-publish",
    "--confirm-snapshot-only",
    "--allow-csv-registration-write", "--allow-csv-reuse-link",
    "--confirm-reuse-only", "--confirm-candidate-write",
    "--allow-csv-candidate-write", "--allow-csv-manifest-registration-write",
    "--allow-csv-finalization-write", "--confirm-manifest-only", "--confirm-complete",
    "--confirm-single-stage-write",
})


@dataclass(frozen=True)
class Services:
    review_stage: Callable[..., Any]
    validated_commands: Callable[[Any], Any]
    read_chain: Callable[[Path, str], tuple[Any, Any, Any]]


def production_services() -> Services:
    from scripts.incremental.incremental_guarded_stage_executor import (
        review_one_stage, validate_read_only_commands,
    )
    from scripts.incremental.incremental_recovery_audit import read_checkpoints
    return Services(review_one_stage, validate_read_only_commands, read_checkpoints)


@dataclass(frozen=True)
class PreparationReview:
    state: str
    stage: str
    repo_id: str
    target_revision: str
    context_sha256: str | None
    preview_sha256: str | None
    cli_module: str | None
    read_only_argv: tuple[str, ...]
    planned_checkpoint: str
    reason: str
    write_authorized: bool = False
    db_write_executed: bool = False
    artifact_write_executed: bool = False
    hf_body_downloaded: bool = False
    stage_advanced: bool = False

    def __post_init__(self):
        if any((self.write_authorized, self.db_write_executed,
                self.artifact_write_executed, self.hf_body_downloaded,
                self.stage_advanced)):
            raise ValueError("Preparation review cannot authorize execution")


def _validate_inputs(*, root: Path, repo_id: str, revision: str,
                     stage: str, baseline: int | None, expected_context: str | None):
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ValueError("Real non-symlink project root required")
    if repo_id != REPO_ID:
        raise ValueError("Unexpected repository ID")
    if not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise ValueError("Pinned lowercase SHA40 revision required")
    if stage not in PREPARATION:
        raise ValueError("Unsupported preparation stage")
    if baseline is not None and (type(baseline) is not int or baseline <= 0):
        raise ValueError("Invalid baseline snapshot")
    if expected_context is not None and (not isinstance(expected_context, str)
                                         or not SHA256.fullmatch(expected_context)):
        raise ValueError("Invalid pinned execution context SHA256")


def _check_argv(argv: Any, expected_module: str,
                repo_id: str, revision: str) -> tuple[str, ...]:
    if not isinstance(argv, (tuple, list)) or len(argv) < 6 or not all(
        isinstance(x, str) and len(x) <= 4096 and x and "\x00" not in x for x in argv
    ):
        raise RuntimeError("Malformed M4 validated preview argv")
    command = tuple(argv)
    # M4's stage preview is always a trusted python -m scripts.tools.* entrypoint.
    if command[1:3] != ("-m", expected_module):
        raise RuntimeError("Preparation module does not match requested stage")
    if command.count("--plan") != 1 or command[-1] != "--plan":
        raise RuntimeError("M4 preparation argv must end with --plan")
    if any(arg in FORBIDDEN_SWITCHES or arg.startswith("--confirm-")
           or arg.startswith(("--execute", "--force", "--delete", "--purge", "--reset"))
           or (arg.startswith("--allow-csv-") and not arg.endswith("-plan-only")
               and not (arg == "--allow-csv-cache-only" and
                        expected_module.endswith(".download_incremental_candidates")))
           for arg in command[3:]):
        raise RuntimeError("Unexpected side-effect authorization flag in read-only argv")
    # A positional/flag pairing check prevents accidentally accepting extra commands.
    items = command[3:-1]
    options: dict[str,str] = {}
    standalone = ({"--allow-csv-manifest-plan-only"} if expected_module.endswith(
        ".prepare_incremental_manifest") else
        {"--allow-csv-cache-only"} if expected_module.endswith(
        ".download_incremental_candidates") else set())
    i = 0
    while i < len(items):
        key = items[i]
        if not key.startswith("--") or key in options:
            raise RuntimeError("Invalid or duplicate M4 preview option")
        if key in standalone:
            options[key] = "true"
            i += 1
            continue
        if i + 1 >= len(items) or items[i+1].startswith("--"):
            raise RuntimeError("M4 preview options must be key/value pairs")
        options[key] = items[i+1]
        i += 2
    if options.get("--repo-id") != repo_id or options.get("--target-revision") != revision:
        raise RuntimeError("M4 preparation argv has mismatched repo/revision")
    return command


def preview_preparation(*, root: Path, repo_id: str, target_revision: str,
                        stage: str, baseline_snapshot: int | None = None,
                        expected_context_sha256: str | None = None,
                        services: Services | None = None) -> PreparationReview:
    """One M4 review + verified checkpoint-chain read; NEVER launch argv."""
    _validate_inputs(root=root, repo_id=repo_id, revision=target_revision,
                     stage=stage, baseline=baseline_snapshot,
                     expected_context=expected_context_sha256)
    services = services or production_services()
    review = services.review_stage(repo_id=repo_id, target_revision=target_revision,
                                   baseline_snapshot=baseline_snapshot,
                                   root=root, readers=None)
    plan = getattr(review, "plan", None)
    contract = getattr(review, "contract", None)
    if plan is None or contract is None:
        raise RuntimeError("M4 typed stage review missing plan/contract")
    if (getattr(plan,"repo_id",None) != repo_id
            or getattr(plan,"target_revision",None) != target_revision
            or getattr(plan,"write_authorized",None) is not False
            or getattr(contract,"write_authorized",None) is not False):
        raise RuntimeError("M4 preparation review identity/authorization mismatch")

    def result(state: str, reason: str, *, digest=None, argv=(), module=None,
               planned="NOT_CHECKED") -> PreparationReview:
        h = (hashlib.sha256(json.dumps({"stage":stage,"repo":repo_id,
            "revision":target_revision,"context":digest,"argv":argv},
            sort_keys=True,separators=(",", ":")).encode()).hexdigest() if argv else None)
        return PreparationReview(state,stage,repo_id,target_revision,digest,h,
                                 module,tuple(argv),planned,reason)

    decision = getattr(plan,"decision",None)
    disposition = getattr(contract,"disposition",None)
    blockers = tuple(getattr(plan,"blockers",()) or ()) + tuple(getattr(contract,"blockers",()) or ())
    if not isinstance(decision,str) or not isinstance(disposition,str):
        raise RuntimeError("Malformed M4 decision or disposition")
    if decision.startswith("BLOCKED_") or disposition == "BLOCKED" or blockers:
        return result("BLOCKED", "M4 stage review contains blocker")
    if decision in ("SKIP_ALREADY_CURRENT", "SKIP_ALREADY_PROCESSED", "SKIP_NO_RAW_CHANGES"):
        # For immutable completed snapshots, no historical checkpoint backfill.
        if getattr(plan,"next_review_stage",None) is not None:
            raise RuntimeError("Inconsistent M4 skip stage")
        return result("SKIPPED", "M4 says no preparation work is necessary")
    if (decision not in ("RECOVERY_REVIEW_REQUIRED", "PLAN_NEW_REVISION")
            or getattr(plan,"next_review_stage",None) != stage
            or getattr(contract,"review_stage",None) != stage
            or disposition != "REVIEW_READ_ONLY_PLAN"):
        return result("NOT_CURRENT_STAGE", "M4 did not designate this preparation stage as READY")
    digest = getattr(contract,"context_sha256",None)
    if not isinstance(digest,str) or not SHA256.fullmatch(digest):
        raise RuntimeError("M4 execution context SHA256 missing")
    if expected_context_sha256 is not None and digest != expected_context_sha256:
        raise RuntimeError("Explicit execution context SHA256 mismatches M4")
    chain = services.read_chain(root,target_revision)
    if not isinstance(chain,tuple) or len(chain) != 3:
        raise RuntimeError("Malformed checkpoint chain")
    planned, prepared, registered = chain
    if planned is None or prepared is not None or registered is not None:
        return result("BLOCKED", "Preparation requires PLANNED only, without later checkpoints",
                      digest=digest, planned="ABSENT" if planned is None else "PRESENT")
    if (getattr(planned,"repo_id",None) != repo_id or
            getattr(planned,"target_revision",None) != target_revision):
        raise RuntimeError("Planned checkpoint identity mismatch")
    cmds = services.validated_commands(review)
    if not isinstance(cmds,(tuple,list)) or len(cmds) != 1:
        raise RuntimeError("Exactly one M4 validated read-only CLI preview required")
    module = "scripts.tools." + PREPARATION[stage]
    argv = _check_argv(cmds[0],module,repo_id,target_revision)
    return result("REVIEW_PREPARATION", "Command validated for human review only; no execution rights",
                  digest=digest,argv=argv,module=module,planned="PRESENT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan",required=True,action="store_true",
                        help="Read-only; no execution or publish flags")
    parser.add_argument("--stage",required=True,choices=tuple(PREPARATION))
    parser.add_argument("--repo-id",default=REPO_ID)
    parser.add_argument("--target-revision",required=True)
    parser.add_argument("--baseline-snapshot",type=int)
    parser.add_argument("--expected-context-sha256")
    args = parser.parse_args(argv)
    try:
        from scripts.incremental.incremental_execution_context import ROOT
        result = preview_preparation(root=ROOT,repo_id=args.repo_id,
                    target_revision=args.target_revision,stage=args.stage,
                    baseline_snapshot=args.baseline_snapshot,
                    expected_context_sha256=args.expected_context_sha256)
        print(json.dumps(asdict(result),ensure_ascii=False,indent=2))
        return 0 if result.state in ("REVIEW_PREPARATION","SKIPPED") else 2
    except Exception as exc:
        print(json.dumps({"state":"BLOCKED", "stage":args.stage,
             "reason":f"{type(exc).__name__}: {exc}",
             "write_authorized":False,"db_write_executed":False,
             "artifact_write_executed":False,"hf_body_downloaded":False,
             "stage_advanced":False},ensure_ascii=False,indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
