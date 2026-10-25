"""Offline tests for M5.5.5c.3f. No HF, DB, or real M4 child."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as O
import sys
import tempfile
import unittest

from scripts.orchestration.safe_stage_transition import (
    Services, SCOPED, REPO_ID, TransitionBlocked, scoped_review,
    _download_options, _archive_plan,
)
from scripts.orchestration.guarded_preparation_executor import (
    plan_preparation, execute_preparation, PreparationGateError,
)

REV = 'a'*40
BASE = 367
DIGEST = 'c'*64


class Fixture:
    def __init__(self, root: Path, stage: str = 'CACHE_DOWNLOAD'):
        self.root = root
        self.stage = stage
        self.prev = ('MANIFEST_PREPARATION',) if stage == 'CACHE_DOWNLOAD' else (
            'MANIFEST_PREPARATION', 'CACHE_DOWNLOAD')
        self.m4_stage = 'MANIFEST_PREPARATION'
        self.decision = 'RECOVERY_REVIEW_REQUIRED'
        self.recovery = 'PREPARATION_PARTIAL'
        self.records = (4, 8, 10)
        self.cache_entries = ['a.json', 'b.csv', 'c.json']
        self.resolution_state = 'ROUTER_ALIGNMENT_REQUIRED'
        self.statuses = {s: 'VERIFIED' for s in self.prev}
        self.statuses[stage] = 'INCOMPLETE'
        self.journals = {s: 'VERIFIED_AFTER_WRITE' for s in self.prev}
        self.journals[stage] = 'NO_LOCAL_ATTEMPT'
        self.planned = O(repo_id=REPO_ID, target_revision=REV, baseline_snapshot_id=BASE)
        self.out_of_order = False
        self.count_read = 0
        self.launched = 0
        self.mark_complete = False

    def m4_review(self, **kw):
        return O(plan=O(repo_id=REPO_ID,target_revision=REV,decision=self.decision,
                        recovery_state=self.recovery,next_review_stage=self.m4_stage,
                        write_authorized=False,blockers=()),
                 contract=O(write_authorized=False,review_stage=self.m4_stage,
                            disposition='REVIEW_READ_ONLY_PLAN',
                            context_sha256=DIGEST,blockers=()))

    def resolve(self, **kw):
        statuses = dict(self.statuses)
        journals = dict(self.journals)
        return O(repo_id=REPO_ID,target_revision=REV,
                 m4_next_stage=self.m4_stage,state=self.resolution_state,
                 evidence_next_stage=self.stage,verified_stages=self.prev,
                 evidence_statuses=statuses,journal_statuses=journals,
                 write_authorized=False,artifact_write_executed=False,
                 db_write_executed=False,retry_authorized=False,stage_advanced=False)

    def checkpoints(self, root, revision):
        self.count_read += 1
        return (self.planned, None, None)

    def download_plan(self, planned):
        return (sys.executable,'-m','scripts.tools.download_incremental_candidates',
                '--repo-id',REPO_ID,'--target-revision',REV,
                '--baseline-snapshot',str(BASE),'--expected-fingerprint','f'*64,
                '--expected-files','3','--expected-bytes','3000',
                '--allow-csv-cache-only','--plan')

    def cache_evidence(self, planned, root):
        return O(receipts=tuple(O(path=p,records=r) for p,r in zip(self.cache_entries,self.records)))

    def services(self):
        return Services(self.m4_review, self.resolve, self.checkpoints,
                        lambda planned: DIGEST, self.download_plan, self.cache_evidence)

    def reviewer(self, **kw):
        return scoped_review(**kw, services=self.services())

    def plan(self, **extra):
        kw = dict(root=self.root, repo_id=REPO_ID,target_revision=REV,
                  baseline_snapshot=BASE,stage=self.stage,reviewer=self.reviewer,
                  source_checker=lambda r,s: ('1'*64,'2'*64))
        kw.update(extra)
        return plan_preparation(**kw)

    def verifier(self, **kw):
        status = 'VERIFIED' if self.mark_complete else 'INCOMPLETE'
        return O(stage=kw['stage'], status=status,write_authorized=False)

    def launcher(self, argv, **kwargs):
        self.launched += 1
        self.mark_complete = True
        return O(returncode=0)

    def write(self, intent, **extra):
        kw = dict(root=self.root,repo_id=REPO_ID,target_revision=REV,
                  stage=self.stage,baseline_snapshot=BASE,
                  approved_stage=self.stage,expected_context_sha256=DIGEST,
                  expected_intent_sha256=intent.intent_sha256,
                  reviewer=self.reviewer,source_checker=lambda r,s: ('1'*64,'2'*64),
                  verifier=self.verifier,launcher=self.launcher)
        kw.update(extra)
        return execute_preparation(**kw)


class SafeTransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_cache_review_from_sticky_manifest(self):
        f = Fixture(self.root)
        review = f.reviewer(root=self.root,repo_id=REPO_ID,target_revision=REV,
                            stage=f.stage,baseline_snapshot=BASE)
        self.assertEqual(review.state,'REVIEW_PREPARATION')
        self.assertEqual(review.stage,'CACHE_DOWNLOAD')
        self.assertTrue(review.read_only_argv[-1] == '--plan')
        self.assertFalse(review.write_authorized)

    def test_archive_review_from_sticky_manifest(self):
        f = Fixture(self.root,'ARCHIVE_PROMOTION')
        review = f.reviewer(root=self.root,repo_id=REPO_ID,target_revision=REV,
                            stage=f.stage,baseline_snapshot=BASE)
        self.assertEqual(review.state,'REVIEW_PREPARATION')
        self.assertIn('--expected-records',review.read_only_argv)
        ix=review.read_only_argv.index('--expected-records')
        self.assertEqual(review.read_only_argv[ix+1],str(sum(f.records)))

    def test_cache_scoped_to_same_m4_audit_entrypoint(self):
        f=Fixture(self.root);f.m4_stage='CACHE_DOWNLOAD'
        with self.assertRaises(TransitionBlocked): f.reviewer(root=self.root,repo_id=REPO_ID,
            target_revision=REV,stage=f.stage,baseline_snapshot=BASE)

    def test_wrong_m4_decision(self):
        f=Fixture(self.root); f.decision='PLAN_NEW_REVISION'
        with self.assertRaises(TransitionBlocked): f.reviewer(root=self.root,repo_id=REPO_ID,
            target_revision=REV,stage=f.stage,baseline_snapshot=BASE)

    def test_wrong_recovery_state(self):
        f=Fixture(self.root); f.recovery='PREPARED_READY'
        with self.assertRaises(TransitionBlocked): f.reviewer(root=self.root,repo_id=REPO_ID,
            target_revision=REV,stage=f.stage,baseline_snapshot=BASE)

    def test_previous_manifest_only_incomplete_blocks(self):
        f=Fixture(self.root); f.statuses['MANIFEST_PREPARATION']='INCOMPLETE'
        with self.assertRaises(TransitionBlocked): f.plan()

    def test_previous_manifest_journal_not_verified_blocks(self):
        f=Fixture(self.root); f.journals['MANIFEST_PREPARATION']='UNVERIFIED_AFTER_ATTEMPT'
        with self.assertRaises(TransitionBlocked): f.plan()

    def test_stage_already_attempted_blocks(self):
        f=Fixture(self.root); f.journals['CACHE_DOWNLOAD']='RESERVED_OUTCOME_UNKNOWN'
        with self.assertRaises(TransitionBlocked): f.plan()

    def test_manual_m4_no_m5_journal_blocks(self):
        f=Fixture(self.root);f.resolution_state='MANUAL_RECONCILIATION_REQUIRED'
        with self.assertRaises(TransitionBlocked):f.plan()

    def test_wrong_context_digest(self):
        f=Fixture(self.root)
        with self.assertRaises(TransitionBlocked): f.plan(expected_context_sha256='0'*64)

    def test_unexpected_checkpoint_shape(self):
        f=Fixture(self.root)
        f.checkpoints=lambda root, rev: (None,None,None)
        with self.assertRaises(TransitionBlocked):f.plan()

    def test_baseline_drift(self):
        f=Fixture(self.root)
        with self.assertRaises(TransitionBlocked):f.plan(baseline_snapshot=366)

    def test_archive_missing_cache_receipts(self):
        f=Fixture(self.root,'ARCHIVE_PROMOTION');f.cache_evidence=lambda p,r: O(receipts=())
        with self.assertRaises(TransitionBlocked):f.plan()

    def test_archive_duplicate_receipt_blocks(self):
        f=Fixture(self.root,'ARCHIVE_PROMOTION');f.cache_entries=['a.json','a.json','c.json']
        with self.assertRaises(TransitionBlocked):f.plan()

    def test_archive_bad_record_count(self):
        f=Fixture(self.root,'ARCHIVE_PROMOTION');f.records=(0,8,10)
        with self.assertRaises(TransitionBlocked):f.plan()

    def test_forbidden_cache_argument_blocks(self):
        f=Fixture(self.root)
        f.download_plan=lambda p: (sys.executable,'-m','scripts.tools.download_incremental_candidates',
                                   '--repo-id',REPO_ID,'--target-revision',REV,
                                   '--baseline-snapshot',str(BASE),'--expected-fingerprint','f'*64,
                                   '--expected-files','3','--expected-bytes','3000',
                                   '--confirm-cache-only','--plan')
        with self.assertRaises(Exception): f.plan()

    def test_review_preview_does_not_write(self):
        f=Fixture(self.root)
        p=f.plan();self.assertEqual(p.state,'READY_FOR_EXPLICIT_APPROVAL')
        self.assertFalse(p.artifact_write_executed)
        self.assertFalse((self.root/'reports').exists())

    def test_real_m5_guarded_executor_cache_success(self):
        f=Fixture(self.root)
        p=f.plan()
        result=f.write(p)
        self.assertEqual(result['state'],'VERIFIED_AFTER_WRITE')
        self.assertEqual(f.launched,1)
        files=list((self.root/'reports').rglob('CACHE_DOWNLOAD.claim.json'))
        self.assertEqual(len(files),1)
        with self.assertRaises(PreparationGateError): f.write(p)
        self.assertEqual(f.launched,1)

    def test_real_m5_guarded_executor_archive_success(self):
        f=Fixture(self.root,'ARCHIVE_PROMOTION')
        p=f.plan();result=f.write(p)
        self.assertEqual(result['state'],'VERIFIED_AFTER_WRITE')
        self.assertEqual(f.launched,1)

    def test_wrong_intent_does_not_create_claim(self):
        f=Fixture(self.root)
        p=f.plan()
        with self.assertRaises(PreparationGateError): f.write(p,expected_intent_sha256='0'*64)
        self.assertEqual(f.launched,0)
        self.assertFalse((self.root/'reports').exists())

    def test_timeout_claim_remains_blocked(self):
        import subprocess
        f=Fixture(self.root)
        p=f.plan()
        def timeout(*args,**kwargs):
            raise subprocess.TimeoutExpired('synthetic',1)
        with self.assertRaises(PreparationGateError):f.write(p,launcher=timeout)
        self.assertTrue(list((self.root/'reports').rglob('CACHE_DOWNLOAD.claim.json')))
        with self.assertRaises(PreparationGateError):f.write(p)
        self.assertEqual(f.launched,0)

    def test_source_drift_blocks_before_claim(self):
        f=Fixture(self.root)
        with self.assertRaises(RuntimeError):
            f.plan(source_checker=lambda r,s: (_ for _ in ()).throw(RuntimeError('source drift')))
        self.assertFalse((self.root/'reports').exists())


if __name__=='__main__':
    unittest.main()
