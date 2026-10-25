"""M5.5.5c.3f | Evidence-scoped preparation stage transition.

M4's PREPARATION_PARTIAL -> MANIFEST_PREPARATION is an AUDIT ENTRYPOINT, not
permission to republish the manifest. Only an independently VERIFIED preceding
stage with a terminal VERIFIED_AFTER_WRITE M5 journal can allow a scoped review
of CACHE_DOWNLOAD/ARCHIVE_PROMOTION, and all actual side effects still pass
through the existing M5 guarded_preparation_executor (explicit approval,
rehash pinned M4 sources, pre/post evidence, durable one-shot claim).

The `--plan` CLI is strictly read-only. There is deliberately NO automatic
stage loop, no bypass when earlier work happened manually, and no retry.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable

from scripts.orchestration.preparation_stage_coordinator import (
    PreparationReview, REPO_ID, _check_argv,
)
from scripts.orchestration.guarded_preparation_executor import (
    plan_preparation, execute_preparation, PreparationGateError,
)

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SCOPED = ("CACHE_DOWNLOAD", "ARCHIVE_PROMOTION")
PREVIOUS = {
    "CACHE_DOWNLOAD": ("MANIFEST_PREPARATION",),
    "ARCHIVE_PROMOTION": ("MANIFEST_PREPARATION", "CACHE_DOWNLOAD"),
}
MODULE = {
    "CACHE_DOWNLOAD": "scripts.tools.download_incremental_candidates",
    "ARCHIVE_PROMOTION": "scripts.tools.promote_incremental_archive",
}


class TransitionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class Services:
    m4_review: Callable[..., Any]
    resolve: Callable[..., Any]
    checkpoints: Callable[[Path, str], tuple[Any, Any, Any]]
    digest: Callable[[Any], str]
    download_plan: Callable[[Any], tuple[str, ...]]
    cache_evidence: Callable[[Any, Path], Any]


def production_services() -> Services:
    from scripts.incremental.incremental_guarded_stage_executor import review_one_stage
    from scripts.incremental.incremental_recovery_audit import read_checkpoints
    from scripts.incremental.incremental_execution_context import checkpoint_digest
    from scripts.incremental.incremental_guarded_stage_planner import plan_command
    from scripts.incremental.incremental_runner_contracts import audit_cache_evidence
    from scripts.orchestration.preparation_evidence_resolver import resolve_preparation
    return Services(
        m4_review=review_one_stage,
        resolve=resolve_preparation,
        checkpoints=read_checkpoints,
        digest=checkpoint_digest,
        download_plan=lambda planned: tuple(plan_command("download", planned)),
        cache_evidence=lambda planned, root: audit_cache_evidence(planned, root=root),
    )


def _download_options(command: tuple[str, ...], repo_id: str, revision: str,
                      baseline_snapshot: int) -> dict[str, str]:
    validated = _check_argv(command, MODULE["CACHE_DOWNLOAD"], repo_id, revision)
    if validated[0] != sys.executable:
        raise TransitionBlocked("M4 download preview interpreter mismatch")
    options: dict[str, str] = {}
    tokens = validated[3:-1]
    index = 0
    permitted = frozenset((
        "--repo-id", "--target-revision", "--baseline-snapshot",
        "--expected-fingerprint", "--expected-files", "--expected-bytes",
        "--expected-records", "--cache-dir", "--allow-csv-cache-only",
    ))
    while index < len(tokens):
        key = tokens[index]
        if key not in permitted or key in options:
            raise TransitionBlocked("Unexpected M4 cache option")
        if key == "--allow-csv-cache-only":
            options[key] = "true"
            index += 1
            continue
        if index + 1 >= len(tokens):
            raise TransitionBlocked("Missing M4 cache option value")
        options[key] = tokens[index + 1]
        index += 2
    for key in ("--repo-id", "--target-revision", "--baseline-snapshot",
                "--expected-fingerprint", "--expected-files", "--expected-bytes"):
        if key not in options:
            raise TransitionBlocked("Missing trusted M4 download parameter: " + key)
    if options["--baseline-snapshot"] != str(baseline_snapshot):
        raise TransitionBlocked("M4 preview baseline drift")
    if not SHA256.fullmatch(options["--expected-fingerprint"]):
        raise TransitionBlocked("M4 fingerprint invalid")
    for key in ("--expected-files", "--expected-bytes"):
        value = options[key]
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise TransitionBlocked("M4 numeric counts invalid: " + key)
    return options


def _archive_plan(root: Path, planned: Any, opts: dict[str, str],
                  services: Services) -> tuple[str, ...]:
    evidence = services.cache_evidence(planned, root)
    receipts = getattr(evidence, "receipts", None)
    if not isinstance(receipts, tuple) or not receipts:
        raise TransitionBlocked("Independently verified CacheEvidence receipts required")
    count = 0
    paths: set[str] = set()
    for receipt in receipts:
        records = getattr(receipt, "records", None)
        path = getattr(receipt, "path", None)
        # M4's CandidateReceipt.path is the pinned relative source path.
        if (type(records) is not int or records <= 0
                or not isinstance(path, str) or not path or path in paths):
            raise TransitionBlocked("Invalid or duplicate verified cache receipt")
        count += records
        paths.add(path)
    if len(receipts) != int(opts["--expected-files"]):
        raise TransitionBlocked("Verified cache file count changed")
    args = [sys.executable, "-m", MODULE["ARCHIVE_PROMOTION"],
            "--repo-id", opts["--repo-id"],
            "--target-revision", opts["--target-revision"],
            "--baseline-snapshot", opts["--baseline-snapshot"],
            "--expected-fingerprint", opts["--expected-fingerprint"],
            "--expected-files", opts["--expected-files"],
            "--expected-bytes", opts["--expected-bytes"],
            "--expected-records", str(count)]
    if "--cache-dir" in opts:
        args.extend(("--cache-dir", opts["--cache-dir"]))
    args.append("--plan")
    return _check_argv(tuple(args), MODULE["ARCHIVE_PROMOTION"],
                       opts["--repo-id"], opts["--target-revision"])


def scoped_review(*, root: Path, repo_id: str, target_revision: str,
                  stage: str, baseline_snapshot: int | None = None,
                  expected_context_sha256: str | None = None,
                  services: Services | None = None) -> PreparationReview:
    """Strict preparation review for a VERIFIED predecessor chain only.

    No write authority. Use this as the injected `reviewer` for the existing
    M5 Guarded Preparation Executor; no production M4 source is modified.
    """
    if (stage not in SCOPED or repo_id != REPO_ID
            or not isinstance(target_revision, str) or not SHA40.fullmatch(target_revision)
            or type(baseline_snapshot) is not int or baseline_snapshot <= 0
            or not isinstance(root, Path) or not root.is_dir() or root.is_symlink()):
        raise TransitionBlocked("Invalid scoped transition inputs")
    if (expected_context_sha256 is not None and
            (not isinstance(expected_context_sha256, str)
             or not SHA256.fullmatch(expected_context_sha256))):
        raise TransitionBlocked("Malformed expected context SHA256")
    svc = services or production_services()
    review = svc.m4_review(repo_id=repo_id, target_revision=target_revision,
                           baseline_snapshot=baseline_snapshot, root=root, readers=None)
    plan, contract = getattr(review, "plan", None), getattr(review, "contract", None)
    if plan is None or contract is None:
        raise TransitionBlocked("Missing independently typed M4 review")
    if (getattr(plan, "repo_id", None) != repo_id
            or getattr(plan, "target_revision", None) != target_revision
            or getattr(plan, "write_authorized", None) is not False
            or getattr(contract, "write_authorized", None) is not False
            or getattr(plan, "decision", None) != "RECOVERY_REVIEW_REQUIRED"
            or getattr(plan, "recovery_state", None) != "PREPARATION_PARTIAL"
            or getattr(plan, "next_review_stage", None) != "MANIFEST_PREPARATION"
            or getattr(contract, "review_stage", None) != "MANIFEST_PREPARATION"
            or getattr(contract, "disposition", None) != "REVIEW_READ_ONLY_PLAN"
            or getattr(plan, "blockers", ()) or getattr(contract, "blockers", ())):
        raise TransitionBlocked("M4 is not at the validated preparation audit entrypoint")
    # A scoped stage must arise from fresh, independently VERIFIED predecessor
    # evidence plus terminal durable attempt journal. Router's audit entrypoint
    # is *not* itself a write approval.
    res = svc.resolve(root=root, repo_id=repo_id, target_revision=target_revision,
                      baseline_snapshot=baseline_snapshot)
    if (getattr(res, "repo_id", None) != repo_id
            or getattr(res, "target_revision", None) != target_revision
            or getattr(res, "m4_next_stage", None) != "MANIFEST_PREPARATION"
            or getattr(res, "state", None) != "ROUTER_ALIGNMENT_REQUIRED"
            or getattr(res, "evidence_next_stage", None) != stage
            or any(getattr(res, k, None) is not False for k in (
                "write_authorized", "artifact_write_executed",
                "db_write_executed", "retry_authorized", "stage_advanced"))):
        raise TransitionBlocked("Independent evidence has not selected requested stage")
    if tuple(getattr(res, "verified_stages", ())) != PREVIOUS[stage]:
        raise TransitionBlocked("Unexpected verified predecessor sequence")
    statuses = getattr(res, "evidence_statuses", {})
    journals = getattr(res, "journal_statuses", {})
    if not isinstance(statuses, dict) or not isinstance(journals, dict):
        raise TransitionBlocked("Missing independent evidence/journal map")
    for previous in PREVIOUS[stage]:
        if statuses.get(previous) != "VERIFIED" or journals.get(previous) != "VERIFIED_AFTER_WRITE":
            raise TransitionBlocked("A predecessor is not independently verified")
    if statuses.get(stage) != "INCOMPLETE" or journals.get(stage) != "NO_LOCAL_ATTEMPT":
        raise TransitionBlocked("Requested stage has existing/blocked attempt")
    chain = svc.checkpoints(root, target_revision)
    if not isinstance(chain, tuple) or len(chain) != 3:
        raise TransitionBlocked("Malformed checkpoint chain")
    planned, prepared, registered = chain
    if planned is None or prepared is not None or registered is not None:
        raise TransitionBlocked("Scoped stage requires PLANNED only")
    if (getattr(planned, "repo_id", None) != repo_id
            or getattr(planned, "target_revision", None) != target_revision
            or getattr(planned, "baseline_snapshot_id", None) != baseline_snapshot):
        raise TransitionBlocked("Planned checkpoint identity/baseline mismatch")
    digest = svc.digest(planned)
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise TransitionBlocked("Invalid planned checkpoint digest")
    if getattr(contract, "context_sha256", None) != digest:
        raise TransitionBlocked("M4 review/checkpoint digest drift")
    if expected_context_sha256 is not None and digest != expected_context_sha256:
        raise TransitionBlocked("Explicit context SHA256 drift")
    cache_cmd = tuple(svc.download_plan(planned))
    opts = _download_options(cache_cmd, repo_id, target_revision, baseline_snapshot)
    argv = (cache_cmd if stage == "CACHE_DOWNLOAD" else
            _archive_plan(root, planned, opts, svc))
    module = MODULE[stage]
    preview_sha = hashlib.sha256(json.dumps({
        "stage": stage, "repo": repo_id, "revision": target_revision,
        "context": digest, "argv": argv,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return PreparationReview(
        "REVIEW_PREPARATION", stage, repo_id, target_revision,
        digest, preview_sha, module, argv, "PRESENT",
        "M4 preparation audit entrypoint + verified preceding stages; review only",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--execute-one-stage", action="store_true")
    p.add_argument("--stage", required=True, choices=SCOPED)
    p.add_argument("--repo-id", default=REPO_ID)
    p.add_argument("--target-revision", required=True)
    p.add_argument("--baseline-snapshot", type=int, required=True)
    p.add_argument("--expected-context-sha256")
    p.add_argument("--approve-stage", choices=SCOPED)
    p.add_argument("--expected-intent-sha256")
    p.add_argument("--confirm-artifact-write", action="store_true")
    a = p.parse_args(argv)
    if a.execute_one_stage:
        if (not a.confirm_artifact_write or a.approve_stage != a.stage
                or not a.expected_context_sha256 or not a.expected_intent_sha256):
            p.error("Explicit stage/context/intent + --confirm-artifact-write required")
    elif a.approve_stage or a.expected_intent_sha256 or a.confirm_artifact_write:
        p.error("--plan cannot include write authorization")
    from scripts.incremental.incremental_execution_context import ROOT
    shared = dict(root=ROOT, repo_id=a.repo_id, target_revision=a.target_revision,
                  stage=a.stage, baseline_snapshot=a.baseline_snapshot,
                  expected_context_sha256=a.expected_context_sha256,
                  reviewer=scoped_review)
    try:
        if a.plan:
            result = asdict(plan_preparation(**shared))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["state"] == "READY_FOR_EXPLICIT_APPROVAL" else 2
        result = execute_preparation(**shared, approved_stage=a.approve_stage,
                                     expected_intent_sha256=a.expected_intent_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"state": "BLOCKED", "stage": a.stage,
             "reason": f"{type(exc).__name__}: {exc}",
             "write_authorized": False, "artifact_write_executed": False,
             "db_write_executed": False, "stage_advanced": False},
             ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
