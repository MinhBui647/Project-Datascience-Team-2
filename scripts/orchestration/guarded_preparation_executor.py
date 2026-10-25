"""M5.5.5c.3b.2 | Strict single-stage artifact executor (manifest/cache/archive).

`--plan` NEVER mutates or invokes subprocesses. `--execute-one-stage` requires
explicit stage, context and intent approval; re-reviews M4, uses the independent
M4 evidence verifier before and after ONE trusted M4 child, and reserves a
DURABLE write-attempt claim before launching. Any ambiguous failure permanently
blocks automated repeat. No auto-retry, DB writes, or stage advancement.

This is a single-host safety gate, NOT an unattended end-to-end scheduler.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

from scripts.orchestration.preparation_stage_coordinator import (
    PREPARATION, REPO_ID, PreparationReview, preview_preparation,
)
from scripts.orchestration.write_attempt_guard import (
    reserve_write_attempt, record_attempt_result, WriteAttemptBlocked,
)

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_TIMEOUT_SECONDS = 900

# Pinned independently from M5.5.5c.3b.1 AST contract audit, NOT from the
# M5 review's argv. Any M4 source update requires manual re-audit of this list.
M4_PINNED_SHA256 = {
    "MANIFEST_PREPARATION": (
        ("scripts/tools/prepare_incremental_manifest.py", "152ab4350f979d21ba3219b8b74c89be269f321f7b80812717cea24c12c34324"),
        ("scripts/incremental/prepare_incremental_manifest.py", "bd74f8cb5f8d79c3e87cf234aa565b4226df68c50b0c9e753ed6dcaa70a2035e"),
    ),
    "CACHE_DOWNLOAD": (
        ("scripts/tools/download_incremental_candidates.py", "e1eae9365303166d2c5a9c1a47a0ae421124ab381b5cf64f8450c3f2c9806ad6"),
        ("scripts/incremental/download_incremental_candidates.py", "a49cecedc2041d0a09e37aa7a49c741bc64325bd5983dfaba538b0334e49332c"),
    ),
    "ARCHIVE_PROMOTION": (
        ("scripts/tools/promote_incremental_archive.py", "6e15fc1fa066c87d2148d522484583102a35b1cd36ac2395984f8441eee7d635"),
        ("scripts/incremental/promote_incremental_archive.py", "bcebcb1f0ee95013d21a301044f1f8feb00e686dbf34cdfb3b53dcb74edf5fe7"),
    ),
}

# Exactly the arguments seen in the real M4 implementation contract.
PARAMETERS = {
    "MANIFEST_PREPARATION": frozenset((
        "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
        "--expected-full-files", "--expected-raw-files", "--expected-files",
        "--expected-bytes", "--repo-id")),
    "CACHE_DOWNLOAD": frozenset((
        "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
        "--expected-files", "--expected-bytes", "--expected-records",
        "--repo-id", "--cache-dir")),
    "ARCHIVE_PROMOTION": frozenset((
        "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
        "--expected-files", "--expected-bytes", "--expected-records",
        "--repo-id", "--cache-dir")),
}
REQUIRED_PARAMETERS = {
    "MANIFEST_PREPARATION": frozenset((
        "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
        "--expected-full-files", "--expected-raw-files", "--expected-files",
        "--expected-bytes", "--repo-id")),
    "CACHE_DOWNLOAD": frozenset((
        "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
        "--expected-files", "--expected-bytes", "--repo-id")),
    "ARCHIVE_PROMOTION": frozenset((
        "--baseline-snapshot", "--target-revision", "--expected-fingerprint",
        "--expected-files", "--expected-bytes", "--expected-records", "--repo-id")),
}
MODES = {
    "MANIFEST_PREPARATION": ("--publish", "--confirm-manifest-archive-only"),
    "CACHE_DOWNLOAD": ("--download", "--confirm-cache-only"),
    "ARCHIVE_PROMOTION": ("--promote", "--confirm-archive-only"),
}
FLAGS = {
    "MANIFEST_PREPARATION": "--allow-csv-manifest-plan-only",
    "CACHE_DOWNLOAD": "--allow-csv-cache-only",
    "ARCHIVE_PROMOTION": None,
}


class PreparationGateError(RuntimeError):
    def __init__(self, message: str, *, attempted: bool = False):
        super().__init__(message)
        self.attempted = attempted


@dataclass(frozen=True)
class PreparationIntent:
    stage: str
    repo_id: str
    target_revision: str
    context_sha256: str
    preview_sha256: str
    intent_sha256: str
    argv: tuple[str, ...]
    source_sha256: tuple[str, ...]


@dataclass(frozen=True)
class GatePreview:
    state: str
    stage: str
    repo_id: str
    target_revision: str
    context_sha256: str | None
    intent_sha256: str | None
    writer_argv: tuple[str, ...]
    reason: str
    write_authorized: bool = False
    db_write_executed: bool = False
    artifact_write_executed: bool = False
    hf_body_downloaded: bool = False
    stage_advanced: bool = False


def _pinned_sources(root: Path, stage: str) -> tuple[str, ...]:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise PreparationGateError("Trusted project root required")
    digests = []
    for relative, expected in M4_PINNED_SHA256[stage]:
        source = root.joinpath(*relative.split("/"))
        ancestor = root
        for component in relative.split("/"):
            ancestor = ancestor / component
            if ancestor.is_symlink():
                raise PreparationGateError("M4 source contains symlink: " + relative)
        if not source.is_file():
            raise PreparationGateError("Required M4 source missing: " + relative)
        if source.stat().st_size > 4 * 1024 * 1024:
            raise PreparationGateError("M4 source exceeds audit budget")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != expected:
            raise PreparationGateError("M4 source drift; re-audit before use: " + relative)
        digests.append(actual)
    return tuple(digests)


def _existing_attempt(root: Path, repo_id: str, revision: str, stage: str) -> bool:
    """Read-only journal presence fence. Missing journal is NOT write approval."""
    repo_hash = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()
    current = root
    for part in ("reports", "m5_write_attempts", "v1", repo_hash, revision):
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise PreparationGateError("Unsafe artifact attempt journal directory")
    claim = current / (stage + ".claim.json")
    result = current / (stage + ".result.json")
    for path in (claim, result):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PreparationGateError("Unsafe artifact attempt journal entry")
    return claim.exists() or result.exists()


def _parse_preview(review: PreparationReview, baseline: int | None) -> tuple[dict[str, str], bool]:
    stage = review.stage
    argv = review.read_only_argv
    if (not isinstance(argv, tuple) or len(argv) < 8 or argv[0] != sys.executable
            or argv[1:3] != ("-m", "scripts.tools." + PREPARATION[stage])
            or argv[-1] != "--plan" or argv.count("--plan") != 1):
        raise PreparationGateError("Untrusted M4 read-only command prefix/mode")
    options = {}
    csv_flag = FLAGS[stage]
    has_csv = False
    args = argv[3:-1]
    i = 0
    while i < len(args):
        key = args[i]
        if csv_flag is not None and key == csv_flag:
            if has_csv:
                raise PreparationGateError("Duplicate CSV plan flag")
            has_csv = True
            i += 1
            continue
        if key not in PARAMETERS[stage] or key in options or i + 1 >= len(args):
            raise PreparationGateError("Unknown, duplicate or incomplete M4 option")
        val = args[i+1]
        if not isinstance(val, str) or not val or val.startswith("--") or "\x00" in val:
            raise PreparationGateError("Malformed M4 option value")
        options[key] = val
        i += 2
    if not REQUIRED_PARAMETERS[stage].issubset(options):
        raise PreparationGateError("Missing mandatory M4 source options")
    if options["--repo-id"] != review.repo_id or options["--target-revision"] != review.target_revision:
        raise PreparationGateError("M4 preview identity mismatch")
    if baseline is not None and options["--baseline-snapshot"] != str(baseline):
        raise PreparationGateError("M4 baseline changed")
    try:
        ints = ["--baseline-snapshot", "--expected-files", "--expected-bytes",
                "--expected-records", "--expected-full-files", "--expected-raw-files"]
        for key in ints:
            if key in options and (not options[key].isascii() or not options[key].isdecimal()
                                   or int(options[key]) < 0):
                raise PreparationGateError("Invalid M4 numeric argument " + key)
        if int(options["--baseline-snapshot"]) <= 0:
            raise PreparationGateError("Invalid baseline")
    except (ValueError, OverflowError) as exc:
        raise PreparationGateError("Invalid M4 numeric value") from exc
    if not SHA256.fullmatch(options["--expected-fingerprint"]):
        raise PreparationGateError("Invalid plan fingerprint")
    return options, has_csv


def _intent(*, review: PreparationReview, root: Path,
            expected_manifest_sha256: str | None, baseline_snapshot: int | None,
            source_checker: Callable[[Path, str], tuple[str, ...]]) -> PreparationIntent:
    if review.state != "REVIEW_PREPARATION" or review.stage not in PREPARATION:
        raise PreparationGateError("Stage not ready for preparation write")
    if (review.write_authorized or review.db_write_executed or review.artifact_write_executed
            or review.hf_body_downloaded or review.stage_advanced
            or not isinstance(review.context_sha256, str)
            or not SHA256.fullmatch(review.context_sha256)
            or not isinstance(review.preview_sha256, str)
            or not SHA256.fullmatch(review.preview_sha256)):
        raise PreparationGateError("Invalid M4 preparation review / SHA256")
    options, csv_present = _parse_preview(review, baseline_snapshot)
    stage = review.stage
    if stage == "MANIFEST_PREPARATION":
        if not isinstance(expected_manifest_sha256, str) or not SHA256.fullmatch(expected_manifest_sha256):
            raise PreparationGateError("Manifest publication needs independently pinned expected SHA256")
    elif expected_manifest_sha256 is not None:
        raise PreparationGateError("Manifest SHA256 input only valid for Manifest publication")
    # Preserve every trusted original option, removing exactly the M4 --plan
    # switch and the read-only CSV flag. Never add arbitrary argv from caller.
    args = list(review.read_only_argv[:-1])
    if csv_present:
        args.remove(FLAGS[stage])
    args.extend(MODES[stage])
    if stage == "MANIFEST_PREPARATION":
        args.extend(("--expected-manifest-sha256", expected_manifest_sha256))
        if csv_present:
            args.append("--allow-csv-manifest-publish")
    elif stage == "CACHE_DOWNLOAD" and csv_present:
        args.append("--allow-csv-cache-only")
    sources = source_checker(root, stage)
    if (not isinstance(sources, tuple) or len(sources) != 2
            or any(not isinstance(s, str) or not SHA256.fullmatch(s) for s in sources)):
        raise PreparationGateError("Unverified M4 source digests")
    argv = tuple(args)
    blob = {"schema": "m555c3b2-prep-intent-v1", "stage": stage,
            "repo_id": review.repo_id, "revision": review.target_revision,
            "context_sha256": review.context_sha256, "preview_sha256": review.preview_sha256,
            "expected_manifest_sha256": expected_manifest_sha256,
            "argv": argv, "m4_sources": sources}
    digest = hashlib.sha256(json.dumps(blob, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return PreparationIntent(stage,review.repo_id,review.target_revision,
                             review.context_sha256,review.preview_sha256,digest,argv,sources)


def _validate_inputs(*, root: Path, repo_id: str, revision: str, stage: str,
                     baseline_snapshot: int | None, timeout_seconds: int):
    if (repo_id != REPO_ID or not isinstance(revision, str) or not SHA40.fullmatch(revision)
            or stage not in PREPARATION):
        raise PreparationGateError("Pinned repo/revision/stage invalid")
    if (baseline_snapshot is None or type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise PreparationGateError("Positive pinned baseline required")
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise PreparationGateError("Real project root required")
    if type(timeout_seconds) is not int or not (1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS):
        raise PreparationGateError("Unbounded/invalid timeout")


def _preview(*, root: Path, repo_id: str, target_revision: str, stage: str,
             baseline_snapshot: int, expected_context_sha256: str | None,
             expected_manifest_sha256: str | None,
             reviewer: Callable[..., PreparationReview],
             source_checker: Callable[[Path, str], tuple[str, ...]]) -> tuple[GatePreview, PreparationIntent | None]:
    review = reviewer(root=root,repo_id=repo_id,target_revision=target_revision,
                      stage=stage,baseline_snapshot=baseline_snapshot,
                      expected_context_sha256=expected_context_sha256)
    if not isinstance(review, PreparationReview):
        raise PreparationGateError("M5 preparation reviewer returned malformed type")
    if (review.stage != stage or review.repo_id != repo_id
            or review.target_revision != target_revision):
        raise PreparationGateError("Preparation review identity changed")
    if review.state != "REVIEW_PREPARATION":
        if review.state not in {"SKIPPED", "NOT_CURRENT_STAGE", "BLOCKED"}:
            raise PreparationGateError("Unknown preparation review state")
        return GatePreview(review.state,stage,repo_id,target_revision,
                           review.context_sha256,None,(),review.reason),None
    if expected_context_sha256 is not None and review.context_sha256 != expected_context_sha256:
        raise PreparationGateError("Approved context SHA256 drift")
    intent = _intent(review=review,root=root,
                     expected_manifest_sha256=expected_manifest_sha256,
                     baseline_snapshot=baseline_snapshot,source_checker=source_checker)
    if _existing_attempt(root,repo_id,target_revision,stage):
        return GatePreview("BLOCKED_ATTEMPT_REVIEW",stage,repo_id,target_revision,
                           intent.context_sha256,None,(),
                           "Durable preparation attempt exists; manual reconciliation required"),None
    return GatePreview("READY_FOR_EXPLICIT_APPROVAL",stage,repo_id,target_revision,
                       intent.context_sha256,intent.intent_sha256,intent.argv,
                       "Read-only approval preview; does not authorize or launch a writer"),intent


def plan_preparation(*,root: Path,repo_id: str,target_revision: str,stage: str,
                     baseline_snapshot: int,expected_manifest_sha256: str | None = None,
                     expected_context_sha256: str | None = None,
                     reviewer: Callable[...,PreparationReview] = preview_preparation,
                     source_checker: Callable[[Path,str],tuple[str,...]] = _pinned_sources,
                     timeout_seconds: int = MAX_TIMEOUT_SECONDS) -> GatePreview:
    _validate_inputs(root=root,repo_id=repo_id,revision=target_revision,stage=stage,
                     baseline_snapshot=baseline_snapshot,timeout_seconds=timeout_seconds)
    outcome,_ = _preview(root=root,repo_id=repo_id,target_revision=target_revision,
                         stage=stage,baseline_snapshot=baseline_snapshot,
                         expected_context_sha256=expected_context_sha256,
                         expected_manifest_sha256=expected_manifest_sha256,
                         reviewer=reviewer,source_checker=source_checker)
    return outcome


def _evidence(evidence: Any, stage: str) -> str:
    if (getattr(evidence,"stage",None) != stage
            or getattr(evidence,"write_authorized",None) is not False):
        raise PreparationGateError("Malformed independent M4 stage evidence")
    status = getattr(evidence,"status",None)
    if status not in {"INCOMPLETE", "VERIFIED", "BLOCKED"}:
        raise PreparationGateError("Unknown M4 evidence status")
    return status


def execute_preparation(*,root: Path,repo_id: str,target_revision: str,stage: str,
                        baseline_snapshot: int,approved_stage: str,
                        expected_context_sha256: str,expected_intent_sha256: str,
                        expected_manifest_sha256: str | None = None,
                        reviewer: Callable[...,PreparationReview] = preview_preparation,
                        source_checker: Callable[[Path,str],tuple[str,...]] = _pinned_sources,
                        verifier: Callable[..., Any] | None = None,
                        launcher: Callable[..., Any] = subprocess.run,
                        timeout_seconds: int = MAX_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Execute exactly one explicitly approved M4 artifact writer, no retry."""
    _validate_inputs(root=root,repo_id=repo_id,revision=target_revision,stage=stage,
                     baseline_snapshot=baseline_snapshot,timeout_seconds=timeout_seconds)
    if (approved_stage != stage or not isinstance(expected_context_sha256,str)
            or not SHA256.fullmatch(expected_context_sha256)
            or not isinstance(expected_intent_sha256,str)
            or not SHA256.fullmatch(expected_intent_sha256)):
        raise PreparationGateError("Exact stage/context/intent approval required")
    if verifier is None:
        from scripts.incremental.incremental_guarded_stage_executor import verify_stage_evidence
        verifier = verify_stage_evidence
    args = dict(root=root,repo_id=repo_id,target_revision=target_revision,
                stage=stage,baseline_snapshot=baseline_snapshot,
                expected_context_sha256=expected_context_sha256,
                expected_manifest_sha256=expected_manifest_sha256,
                reviewer=reviewer,source_checker=source_checker)
    first, intent = _preview(**args)
    if first.state != "READY_FOR_EXPLICIT_APPROVAL" or intent is None:
        raise PreparationGateError("Stage not approved by current M4 review: " + first.state)
    if intent.intent_sha256 != expected_intent_sha256:
        raise PreparationGateError("Writer Intent SHA256 mismatches approval")
    second, second_intent = _preview(**args)
    if first != second or second_intent != intent:
        raise PreparationGateError("Fresh M4 review/source/CLI drift before spawn")
    verify_args = dict(repo_id=repo_id,target_revision=target_revision,stage=stage,root=root)
    pre = _evidence(verifier(**verify_args),stage)
    if pre != "INCOMPLETE":
        raise PreparationGateError("Pre-write evidence not INCOMPLETE: " + pre)
    try:
        claim = reserve_write_attempt(root=root,repo_id=repo_id,
            target_revision=target_revision,stage=stage,
            context_sha256=expected_context_sha256,
            intent_sha256=expected_intent_sha256,target_snapshot_id=None,argv=intent.argv)
    except WriteAttemptBlocked as exc:
        raise PreparationGateError("Durable reservation blocked execution: " + str(exc)) from exc
    try:
        try:
            proc = launcher(intent.argv,cwd=str(root),shell=False,
                            capture_output=True,text=True,
                            timeout=timeout_seconds,check=False)
        except subprocess.TimeoutExpired as exc:
            raise PreparationGateError("Artifact writer timeout; manual reconciliation required",attempted=True) from exc
        except Exception as exc:
            raise PreparationGateError("Artifact writer launch/completion ambiguous; manual reconciliation required",attempted=True) from exc
        if type(getattr(proc,"returncode",None)) is not int or proc.returncode != 0:
            raise PreparationGateError("Artifact writer failed; manual reconciliation required",attempted=True)
        try:
            post = _evidence(verifier(**verify_args),stage)
        except Exception as exc:
            raise PreparationGateError("Post-artifact evidence unreadable; manual reconciliation required",attempted=True) from exc
        if post != "VERIFIED":
            raise PreparationGateError("Post-artifact evidence " + post + "; manual reconciliation required",attempted=True)
    except BaseException:
        try:
            record_attempt_result(claim,status="UNVERIFIED_AFTER_ATTEMPT")
        except Exception:
            pass
        raise
    try:
        record_attempt_result(claim,status="VERIFIED_AFTER_WRITE")
    except Exception as exc:
        raise PreparationGateError("Verified evidence but durable result write failed; manual audit required",attempted=True) from exc
    return {"state":"VERIFIED_AFTER_WRITE","stage":stage,
            "repo_id":repo_id,"target_revision":target_revision,
            "intent_sha256":expected_intent_sha256,"child_exit_code":0,
            "post_evidence_status":"VERIFIED","artifact_write_attempted":True,
            "artifact_write_verified":True,"db_write_executed":False,
            "stage_advanced":False,"next_stage_authorized":False}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan",action="store_true")
    modes.add_argument("--execute-one-stage",action="store_true")
    p.add_argument("--stage",required=True,choices=tuple(PREPARATION))
    p.add_argument("--repo-id",default=REPO_ID)
    p.add_argument("--target-revision",required=True)
    p.add_argument("--baseline-snapshot",type=int,required=True)
    p.add_argument("--expected-manifest-sha256")
    p.add_argument("--expected-context-sha256")
    p.add_argument("--approve-stage",choices=tuple(PREPARATION))
    p.add_argument("--expected-intent-sha256")
    p.add_argument("--confirm-artifact-write",action="store_true")
    a = p.parse_args(argv)
    if a.execute_one_stage:
        if (not a.confirm_artifact_write or a.approve_stage != a.stage
                or not a.expected_context_sha256 or not a.expected_intent_sha256):
            p.error("Artifact execution needs exact stage/context/intent + confirmation")
    elif a.confirm_artifact_write or a.approve_stage or a.expected_intent_sha256:
        p.error("Read-only --plan forbids approval and execution confirmation")
    try:
        from scripts.incremental.incremental_execution_context import ROOT
        shared = dict(root=ROOT,repo_id=a.repo_id,target_revision=a.target_revision,
                      stage=a.stage,baseline_snapshot=a.baseline_snapshot,
                      expected_context_sha256=a.expected_context_sha256,
                      expected_manifest_sha256=a.expected_manifest_sha256)
        if a.plan:
            outcome = asdict(plan_preparation(**shared))
            print(json.dumps(outcome,ensure_ascii=False,indent=2))
            return 0 if outcome["state"] in {"SKIPPED","READY_FOR_EXPLICIT_APPROVAL"} else 2
        outcome = execute_preparation(**shared,approved_stage=a.approve_stage,
                                      expected_intent_sha256=a.expected_intent_sha256)
        print(json.dumps(outcome,ensure_ascii=False,indent=2))
        return 0
    except (RuntimeError,ValueError,TypeError,OSError) as exc:
        print(json.dumps({"state":"BLOCKED_OR_FAILED","stage":a.stage,
            "reason":f"{type(exc).__name__}: {exc}",
            "artifact_write_attempted":bool(getattr(exc,"attempted",False)),
            "db_write_executed":False,"stage_advanced":False,
            "next_stage_authorized":False},ensure_ascii=False,indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
