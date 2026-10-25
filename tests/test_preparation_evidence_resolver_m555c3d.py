"""M5.5.5c.3d read-only evidence-based stage resolution regression.

All M4 router/checkpoint/evidence readers are injected synthetic fixtures,
except the real durable journal parser test, which writes into TemporaryDirectory.
No DB, Hugging Face, or M4 writer is ever called.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as O
import tempfile
import unittest

from scripts.orchestration.preparation_evidence_resolver import (
    REPO_ID, STAGES, Services, resolve_preparation, read_preparation_journal,
)
from scripts.orchestration.write_attempt_guard import (
    reserve_write_attempt, record_attempt_result,
)

REV = 'c' * 40
BASE = 367


class Fixture(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix='m555c3d_offline_')
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.decision = 'PLAN_NEW_REVISION'
        self.m4_stage = 'MANIFEST_PREPARATION'
        self.blockers = ()
        self.disposition = 'REVIEW_READ_ONLY_PLAN'
        self.planned = O(repo_id=REPO_ID, target_revision=REV)
        self.prepared = None
        self.registered = None
        self.evidence = dict.fromkeys(STAGES, 'INCOMPLETE')
        self.journals = dict.fromkeys(STAGES, 'NO_LOCAL_ATTEMPT')
        self.calls = []
        self.fail_at = None

    def review(self, **kwargs):
        self.calls.append('review')
        self.assertEqual(kwargs['root'], self.root)
        self.assertEqual(kwargs['repo_id'], REPO_ID)
        self.assertEqual(kwargs['target_revision'], REV)
        self.assertEqual(kwargs['baseline_snapshot'], BASE)
        if self.fail_at == 'review':
            raise RuntimeError('read-only route error')
        return O(plan=O(repo_id=REPO_ID, target_revision=REV,
                        decision=self.decision, next_review_stage=self.m4_stage,
                        write_authorized=False, blockers=self.blockers),
                 contract=O(write_authorized=False, disposition=self.disposition,
                            blockers=()))

    def checkpoints(self, root, revision):
        self.calls.append('checkpoints')
        self.assertEqual((root,revision), (self.root,REV))
        if self.fail_at == 'checkpoints':
            raise RuntimeError('read-only checkpoint error')
        return (self.planned, self.prepared, self.registered)

    def verify(self, **kwargs):
        stage = kwargs['stage']
        self.calls.append('verify:'+stage)
        self.assertEqual(kwargs['root'],self.root)
        if self.fail_at == stage:
            raise RuntimeError('read-only verifier error')
        return O(stage=stage, status=self.evidence[stage], write_authorized=False)

    def journal(self, root, repo_id, revision, stage):
        self.calls.append('journal:'+stage)
        if self.fail_at == 'journal:'+stage:
            raise RuntimeError('corrupt journal')
        self.assertEqual((root,repo_id,revision),(self.root,REPO_ID,REV))
        return self.journals[stage]

    def resolve(self):
        svc = Services(self.review,self.checkpoints,self.verify,self.journal)
        result=resolve_preparation(root=self.root,repo_id=REPO_ID,
                                   target_revision=REV,baseline_snapshot=BASE,
                                   services=svc)
        self.assertFalse(result.write_authorized)
        self.assertFalse(result.artifact_write_executed)
        self.assertFalse(result.db_write_executed)
        self.assertFalse(result.retry_authorized)
        self.assertFalse(result.stage_advanced)
        return result

    def verified(self, stage):
        self.evidence[stage] = 'VERIFIED'
        self.journals[stage] = 'VERIFIED_AFTER_WRITE'

    def test_completed_skips_without_checkpoint_access(self):
        self.decision='SKIP_ALREADY_CURRENT'; self.m4_stage=None
        self.planned=None
        r=self.resolve()
        self.assertEqual(r.state,'SKIPPED_CURRENT')
        self.assertEqual(self.calls,['review'])

    def test_missing_planned_requests_checkpoint(self):
        self.planned=None; self.m4_stage='PLANNED'
        r=self.resolve()
        self.assertEqual((r.state,r.evidence_next_stage),('REVIEW_PLANNED','PLANNED'))
        self.assertEqual(len([x for x in self.calls if x.startswith('verify:')]),0)

    def test_missing_planned_m4_misalignment(self):
        self.planned=None
        r=self.resolve()
        self.assertEqual(r.state,'ROUTER_ALIGNMENT_REQUIRED')
        self.assertEqual(r.evidence_next_stage,'PLANNED')

    def test_initial_manifest_review_readonly_only(self):
        r=self.resolve()
        self.assertEqual(r.state,'REVIEW_STAGE_READ_ONLY')
        self.assertEqual(r.evidence_next_stage,'MANIFEST_PREPARATION')
        self.assertEqual(r.evidence_statuses, {'MANIFEST_PREPARATION':'INCOMPLETE'})
        self.assertEqual(len(r.verified_stages),0)

    def test_sticky_manifest_detected_after_verified_manifest(self):
        self.verified('MANIFEST_PREPARATION')
        r=self.resolve()
        self.assertEqual(r.state,'ROUTER_ALIGNMENT_REQUIRED')
        self.assertEqual(r.m4_next_stage,'MANIFEST_PREPARATION')
        self.assertEqual(r.evidence_next_stage,'CACHE_DOWNLOAD')
        self.assertEqual(r.verified_stages,('MANIFEST_PREPARATION',))

    def test_manifest_then_cache_router_agree(self):
        self.verified('MANIFEST_PREPARATION')
        self.m4_stage='CACHE_DOWNLOAD'
        r=self.resolve()
        self.assertEqual(r.state,'REVIEW_STAGE_READ_ONLY')
        self.assertEqual(r.evidence_next_stage,'CACHE_DOWNLOAD')

    def test_archive_when_two_verified_and_routed(self):
        self.verified('MANIFEST_PREPARATION')
        self.verified('CACHE_DOWNLOAD')
        self.m4_stage='ARCHIVE_PROMOTION'
        r=self.resolve()
        self.assertEqual(r.state,'REVIEW_STAGE_READ_ONLY')
        self.assertEqual(r.evidence_next_stage,'ARCHIVE_PROMOTION')
        self.assertEqual(len(r.verified_stages),2)

    def test_prepared_requires_m4_alignment(self):
        for stage in STAGES: self.verified(stage)
        r=self.resolve()
        self.assertEqual(r.state,'ROUTER_ALIGNMENT_REQUIRED')
        self.assertEqual(r.evidence_next_stage,'PREPARED')
        self.m4_stage='PREPARED'
        r=self.resolve()
        self.assertEqual(r.state,'REVIEW_PREPARED')
        self.assertEqual(len(r.verified_stages),3)

    def test_prepared_checkpoint_closed_without_reexecution(self):
        self.prepared=O(planned=self.planned)
        r=self.resolve()
        self.assertEqual(r.state,'PREPARATION_ALREADY_CLOSED')
        self.assertFalse(any(x.startswith('verify:') for x in self.calls))

    def test_unknown_m4_decision_blocks(self):
        self.decision='UNEXPECTED'
        r=self.resolve()
        self.assertEqual(r.state,'BLOCKED')

    def test_m4_blocker_preempts_artifact_probes(self):
        self.blockers=('incomplete',)
        r=self.resolve()
        self.assertEqual(r.state,'BLOCKED')
        self.assertNotIn('checkpoints',self.calls)

    def test_verified_evidence_without_m5_journal_requires_manual_review(self):
        self.evidence['MANIFEST_PREPARATION']='VERIFIED'
        r=self.resolve()
        self.assertEqual(r.state,'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(r.evidence_next_stage,'MANIFEST_PREPARATION')

    def test_unknown_crashed_attempt_blocks(self):
        self.journals['MANIFEST_PREPARATION']='RESERVED_OUTCOME_UNKNOWN'
        r=self.resolve()
        self.assertEqual(r.state,'BLOCKED_ATTEMPT_REVIEW')

    def test_unverified_attempt_blocks_even_with_verified_evidence(self):
        self.evidence['MANIFEST_PREPARATION']='VERIFIED'
        self.journals['MANIFEST_PREPARATION']='UNVERIFIED_AFTER_ATTEMPT'
        r=self.resolve()
        self.assertEqual(r.state,'MANUAL_RECONCILIATION_REQUIRED')

    def test_later_attempt_while_manifest_incomplete_blocks(self):
        self.journals['CACHE_DOWNLOAD']='VERIFIED_AFTER_WRITE'
        r=self.resolve()
        self.assertEqual(r.state,'BLOCKED_OUT_OF_ORDER_ATTEMPT')
        self.assertEqual(self.calls.count('verify:CACHE_DOWNLOAD'),0)

    def test_evidence_blocked_fails_closed(self):
        self.evidence['MANIFEST_PREPARATION']='BLOCKED'
        r=self.resolve()
        self.assertEqual(r.state,'BLOCKED')

    def test_evidence_reader_exception_fails_closed(self):
        self.fail_at='MANIFEST_PREPARATION'
        r=self.resolve()
        self.assertEqual(r.state,'BLOCKED')
        self.assertEqual(r.evidence_next_stage,None)

    def test_checkpoint_reader_exception_fails_closed(self):
        self.fail_at='checkpoints'
        self.assertEqual(self.resolve().state,'BLOCKED')

    def test_invalid_registered_without_prepared_blocks(self):
        self.registered=O(prepared=None)
        self.assertEqual(self.resolve().state,'BLOCKED')

    def test_malformed_journal_blocks(self):
        self.fail_at='journal:CACHE_DOWNLOAD'
        self.assertEqual(self.resolve().state,'BLOCKED')

    def test_no_write_methods_or_approval_args(self):
        from scripts.orchestration import preparation_evidence_resolver as m
        import inspect
        sig=inspect.signature(m.resolve_preparation)
        self.assertNotIn('launcher',sig.parameters)
        self.assertNotIn('approve_stage',sig.parameters)
        self.assertNotIn('execute',sig.parameters)
        self.assertNotIn('publisher',sig.parameters)

    def test_live_journal_parser_checks_actual_immutable_claim(self):
        self.assertEqual(read_preparation_journal(self.root, REPO_ID, REV, STAGES[0]),
                         'NO_LOCAL_ATTEMPT')
        claim = reserve_write_attempt(root=self.root,repo_id=REPO_ID,
            target_revision=REV,stage=STAGES[0],context_sha256='a'*64,
            intent_sha256='b'*64,target_snapshot_id=None,
            argv=('python','-m','test.module'))
        self.assertEqual(read_preparation_journal(self.root,REPO_ID,REV,STAGES[0]),
                         'RESERVED_OUTCOME_UNKNOWN')
        record_attempt_result(claim,status='VERIFIED_AFTER_WRITE')
        self.assertEqual(read_preparation_journal(self.root,REPO_ID,REV,STAGES[0]),
                         'VERIFIED_AFTER_WRITE')

    def test_corrupt_real_journal_claim_is_not_accepted(self):
        claim = reserve_write_attempt(root=self.root,repo_id=REPO_ID,
            target_revision=REV,stage=STAGES[1],context_sha256='a'*64,
            intent_sha256='b'*64,target_snapshot_id=None,
            argv=('python','-m','test.module'))
        claim.claim_path.write_text('{bad json}',encoding='utf-8')
        with self.assertRaises(Exception):
            read_preparation_journal(self.root,REPO_ID,REV,STAGES[1])


if __name__ == '__main__':
    unittest.main()
