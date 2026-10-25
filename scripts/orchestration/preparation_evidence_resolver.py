"""M5.5.5c.3d | Read-only, evidence-based preparation stage resolver.

Checks existing M4 checkpoints, independently re-evaluates completed preparation
stages, and validates the immutable local M5 attempt journal. Reports the next
stage ONLY as an ADVISORY. Never overrides M4 routing, launches a writer, creates
checkpoints/journal entries, or authorizes a retry. In particular, if a sticky
M4 PREPARATION_PARTIAL route still designates MANIFEST_PREPARATION while the
independent evidence points at CACHE_DOWNLOAD, we return ROUTER_ALIGNMENT_REQUIRED
rather than running CACHE_DOWNLOAD.

A verified M4 stage without a matching M5 durable journal is marked for MANUAL
reconciliation rather than presumed to be an M5-executed stage.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import re
from typing import Any, Callable

from scripts.orchestration.durable_attempt_recovery_audit import (
    _directory, _read_json_file, _validate_claim, _validate_result,
)

REPO_ID = 'Toibinguz/epl-premier-league-dataset'
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
STAGES = ('MANIFEST_PREPARATION', 'CACHE_DOWNLOAD', 'ARCHIVE_PROMOTION')
SKIP = frozenset({'SKIP_ALREADY_CURRENT', 'SKIP_ALREADY_PROCESSED', 'SKIP_NO_RAW_CHANGES'})
ALLOWED_DECISIONS = frozenset({'PLAN_NEW_REVISION', 'RECOVERY_REVIEW_REQUIRED'})


@dataclass(frozen=True)
class Services:
    review: Callable[..., Any]
    checkpoints: Callable[[Path, str], tuple[Any, Any, Any]]
    verify: Callable[..., Any]
    journal: Callable[[Path, str, str, str], str]


@dataclass(frozen=True)
class StageResolution:
    state: str
    repo_id: str
    target_revision: str
    m4_next_stage: str | None
    evidence_next_stage: str | None
    verified_stages: tuple[str, ...]
    evidence_statuses: dict[str, str]
    journal_statuses: dict[str, str]
    detail: str
    write_authorized: bool = False
    artifact_write_executed: bool = False
    db_write_executed: bool = False
    retry_authorized: bool = False
    stage_advanced: bool = False

    def __post_init__(self):
        if any((self.write_authorized, self.artifact_write_executed,
                self.db_write_executed, self.retry_authorized,
                self.stage_advanced)):
            raise ValueError('Read-only evidence resolver must not authorize execution')


class ResolverBlocked(RuntimeError):
    pass


def read_preparation_journal(root: Path, repo_id: str, revision: str, stage: str) -> str:
    """Reuse M5's strict journal byte/claim-hash validators. Does not mutate."""
    if stage not in STAGES:
        raise ResolverBlocked('Unsupported preparation journal stage')
    base = _directory(root, repo_id, revision)
    claim, claim_sha = _read_json_file(base / (stage + '.claim.json'))
    result, _ = _read_json_file(base / (stage + '.result.json'))
    if claim is None and result is None:
        return 'NO_LOCAL_ATTEMPT'
    if claim is None:
        raise ResolverBlocked('Orphan result without durable claim: ' + stage)
    _validate_claim(claim, repo_id, revision, stage)
    if result is None:
        return 'RESERVED_OUTCOME_UNKNOWN'
    return _validate_result(result, claim_sha)


def production_services() -> Services:
    from scripts.incremental.incremental_guarded_stage_executor import (
        review_one_stage, verify_stage_evidence,
    )
    from scripts.incremental.incremental_recovery_audit import read_checkpoints
    return Services(review_one_stage, read_checkpoints,
                    verify_stage_evidence, read_preparation_journal)


def resolve_preparation(*, root: Path, repo_id: str, target_revision: str,
                        baseline_snapshot: int | None = None,
                        services: Services | None = None) -> StageResolution:
    """Find evidence-derived next stage, but NEVER modify the M4 stage gate."""
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ResolverBlocked('Real project root required')
    if repo_id != REPO_ID or not isinstance(target_revision, str) or not SHA40.fullmatch(target_revision):
        raise ResolverBlocked('Pinned repository and lowercase SHA40 required')
    if baseline_snapshot is not None and (type(baseline_snapshot) is not int or baseline_snapshot <= 0):
        raise ResolverBlocked('Invalid baseline snapshot')
    svc = services or production_services()
    statuses: dict[str, str] = {}
    journals: dict[str, str] = {}
    verified: list[str] = []
    next_m4: str | None = None

    def output(state: str, next_stage: str | None, detail: str) -> StageResolution:
        return StageResolution(state, repo_id, target_revision, next_m4, next_stage,
                               tuple(verified), dict(statuses), dict(journals), detail)

    try:
        review = svc.review(repo_id=repo_id, target_revision=target_revision,
                            baseline_snapshot=baseline_snapshot, root=root, readers=None)
        plan, contract = getattr(review, 'plan', None), getattr(review, 'contract', None)
        if plan is None or contract is None:
            raise ResolverBlocked('M4 review missing typed plan/contract')
        if (getattr(plan, 'repo_id', None) != repo_id or
            getattr(plan, 'target_revision', None) != target_revision or
            getattr(plan, 'write_authorized', None) is not False or
            getattr(contract, 'write_authorized', None) is not False):
            raise ResolverBlocked('M4 review identity/authorization mismatch')
        decision = getattr(plan, 'decision', None)
        next_m4 = getattr(plan, 'next_review_stage', None)
        blockers = tuple(getattr(plan, 'blockers', ()) or ()) + tuple(getattr(contract, 'blockers', ()) or ())
        if blockers or getattr(contract, 'disposition', None) == 'BLOCKED':
            return output('BLOCKED', None, 'M4 router/contract blocker')
        if decision in SKIP:
            if next_m4 is not None:
                raise ResolverBlocked('M4 skip unexpectedly proposes a next stage')
            return output('SKIPPED_CURRENT', None,
                          'Completed/no-change revision: do not backfill checkpoints')
        if decision not in ALLOWED_DECISIONS:
            raise ResolverBlocked('Unknown or blocked M4 decision')
        if next_m4 not in (*STAGES, 'PLANNED', 'PREPARED'):
            raise ResolverBlocked('M4 next stage outside preparation lifecycle')
        chain = svc.checkpoints(root, target_revision)
        if not isinstance(chain, tuple) or len(chain) != 3:
            raise ResolverBlocked('Malformed checkpoint chain')
        planned, prepared, registered = chain
        if planned is None:
            if prepared is not None or registered is not None:
                raise ResolverBlocked('Downstream checkpoint without planned context')
            if next_m4 == 'PLANNED':
                return output('REVIEW_PLANNED', 'PLANNED',
                              'Planned checkpoint missing; review checkpoint lifecycle separately')
            return output('ROUTER_ALIGNMENT_REQUIRED', 'PLANNED',
                          'M4 route is not PLANNED but no planned checkpoint exists')
        if (getattr(planned, 'repo_id', None) != repo_id or
            getattr(planned, 'target_revision', None) != target_revision):
            raise ResolverBlocked('Planned checkpoint identity differs')
        if prepared is not None:
            if getattr(prepared, 'planned', None) != planned:
                raise ResolverBlocked('Prepared checkpoint predecessor changed')
            if registered is not None and getattr(registered, 'prepared', None) != prepared:
                raise ResolverBlocked('Registered checkpoint predecessor changed')
            return output('PREPARATION_ALREADY_CLOSED', None,
                          'Prepared checkpoint exists; this resolver cannot approve downstream DB work')
        if registered is not None:
            raise ResolverBlocked('Registered checkpoint exists without prepared predecessor')

        # Journal claims may exist even where an earlier stage is still incomplete.
        # Audit ALL three first to detect orphan/malformed/future-stage records.
        for stage in STAGES:
            value = svc.journal(root, repo_id, target_revision, stage)
            if value not in {'NO_LOCAL_ATTEMPT', 'RESERVED_OUTCOME_UNKNOWN',
                             'UNVERIFIED_AFTER_ATTEMPT', 'VERIFIED_AFTER_WRITE'}:
                raise ResolverBlocked('Unrecognized journal state: ' + stage)
            journals[stage] = value

        for index, stage in enumerate(STAGES):
            ev = svc.verify(repo_id=repo_id, target_revision=target_revision,
                            stage=stage, root=root)
            if (getattr(ev, 'stage', None) != stage or
                getattr(ev, 'write_authorized', None) is not False or
                getattr(ev, 'status', None) not in ('VERIFIED', 'INCOMPLETE', 'BLOCKED')):
                raise ResolverBlocked('Malformed independent M4 evidence for ' + stage)
            status = ev.status
            statuses[stage] = status
            journal = journals[stage]
            if status == 'BLOCKED':
                return output('BLOCKED', None, 'M4 evidence BLOCKED: ' + stage)
            if status == 'INCOMPLETE':
                if journal != 'NO_LOCAL_ATTEMPT':
                    return output('BLOCKED_ATTEMPT_REVIEW', stage,
                                  'Stage evidence incomplete with an existing attempt; no retry: ' + stage)
                if any(journals[later] != 'NO_LOCAL_ATTEMPT' for later in STAGES[index+1:]):
                    return output('BLOCKED_OUT_OF_ORDER_ATTEMPT', stage,
                                  'Later-stage attempt exists before this stage is verified')
                if next_m4 != stage:
                    return output('ROUTER_ALIGNMENT_REQUIRED', stage,
                                  'Evidence-derived stage differs from M4 router; no execution bypass')
                return output('REVIEW_STAGE_READ_ONLY', stage,
                              'Both evidence order and M4 route agree; explicit approval still required')
            # The independent stage is VERIFIED, but unverified journal / external
            # manual M4 work is NOT an automatic advancement certificate.
            if journal != 'VERIFIED_AFTER_WRITE':
                return output('MANUAL_RECONCILIATION_REQUIRED', stage,
                              'VERIFIED M4 evidence lacks matching verified M5 attempt: ' + stage)
            verified.append(stage)

        # PREPARED can only be proposed when all three independently verified
        # artifact writes have matching terminal durable journal evidence.
        if next_m4 != 'PREPARED':
            return output('ROUTER_ALIGNMENT_REQUIRED', 'PREPARED',
                          'All preparation evidence verified but M4 has not designated PREPARED')
        return output('REVIEW_PREPARED', 'PREPARED',
                      'Preparation evidence complete; separate checkpoint publisher still required')
    except Exception as exc:
        return output('BLOCKED', None, f'{type(exc).__name__}: {exc}')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit', action='store_true', required=True)
    p.add_argument('--repo-id', default=REPO_ID)
    p.add_argument('--target-revision', required=True)
    p.add_argument('--baseline-snapshot', type=int)
    args = p.parse_args(argv)
    from scripts.incremental.incremental_execution_context import ROOT
    result = resolve_preparation(root=ROOT, repo_id=args.repo_id,
                                 target_revision=args.target_revision,
                                 baseline_snapshot=args.baseline_snapshot)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.state in ('SKIPPED_CURRENT', 'REVIEW_PLANNED',
                                 'REVIEW_STAGE_READ_ONLY', 'REVIEW_PREPARED',
                                 'PREPARATION_ALREADY_CLOSED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
