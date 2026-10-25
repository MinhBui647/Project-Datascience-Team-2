"""M5.5.4d offline fail-closed stage execution contract tests."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
import subprocess
import tempfile
from scripts.orchestration.csv_write_intent import STAGE_WRITERS
from scripts.orchestration.single_stage_write_executor import (
    make_write_plan, execute_one_stage, WriteGateError, main, SHA256,
)

REPO="Toibinguz/epl-premier-league-dataset"
REV="a"*40
CTX="b"*64
CACHE="reports/m4_6_content_cache"
ROOT=Path.cwd()
COMMON={
    '--repo-id':REPO, '--target-revision':REV,
    '--baseline-snapshot':'366','--expected-fingerprint':'c'*64,
    '--expected-baseline-files':'296','--expected-full-files':'314',
    '--expected-raw-files':'296','--expected-reuse':'293',
    '--expected-files':'3','--expected-bytes':'2916818',
    '--expected-records':'72','--target-snapshot-id':'367',
    '--expected-baseline-records':'8502604',
    '--expected-reused-records':'8502542',
    '--cache-dir':CACHE,
    '--manifest-report':'reports/m4_6_manifests/'+REV+'/hf_manifest_v2.json',
    '--manifest-sha256':'d'*64,
}

def command(stage='CANDIDATE_INGESTION', options=None):
    fields={**COMMON,**(options or {})}
    return (sys.executable,'-m','scripts.tools.'+STAGE_WRITERS[stage][0],
            *(v for pair in fields.items() for v in pair),'--plan')

class FakeStageReview:
    """Isolated M5 writer-test fixture, NOT the production M4 StageReview."""
    def __init__(self, plan, contract, commands):
        self.plan = plan
        self.contract = contract
        self.commands = commands


def mocked_m4_preview_validation(review):
    """The real M4 validator has its own regression suite."""
    return review.commands


def review(stage='CANDIDATE_INGESTION', *, decision='RECOVERY_REVIEW_REQUIRED',
           disposition='REVIEW_READ_ONLY_PLAN', options=None):
    p=SimpleNamespace(repo_id=REPO,target_revision=REV,decision=decision,
                      next_review_stage=stage,target_snapshot_id=367,
                      write_authorized=False,blockers=())
    c=SimpleNamespace(review_stage=stage,disposition=disposition,blockers=(),
                      context_sha256=CTX,write_authorized=False)
    return FakeStageReview(p,c,(command(stage,options),))

def evidence(stage='CANDIDATE_INGESTION',status='INCOMPLETE'):
    return SimpleNamespace(stage=stage,status=status,write_authorized=False)

def reviewers(*reviews):
    calls=[]
    def do(**kwargs):
        calls.append(kwargs)
        if len(calls)>len(reviews):return reviews[-1]
        return reviews[len(calls)-1]
    do.calls=calls
    return do

def verifier_states(*states):
    index=[]
    def do(**kw):
        index.append(kw)
        return evidence(status=states[min(len(index)-1,len(states)-1)])
    do.calls=index
    return do

class SingleStageWriteTests(unittest.TestCase):
    def setUp(self):
        # Patch ONLY the M4 type/validated argv boundary. Run the REAL
        # coordinator, writer adapter and single-stage execution logic.
        for target, replacement in (
            ('scripts.orchestration.guarded_write_coordinator.StageReview', FakeStageReview),
            ('scripts.orchestration.guarded_write_coordinator.validate_read_only_commands', mocked_m4_preview_validation),
            ('scripts.orchestration.single_stage_write_executor.validate_read_only_commands', mocked_m4_preview_validation),
        ):
            p = patch(target, replacement)
            p.start()
            self.addCleanup(p.stop)
        self.one=review()
        self.read=reviewers(self.one)
        self.sandbox = tempfile.TemporaryDirectory(prefix="m554e3-executor-test-")
        self.addCleanup(self.sandbox.cleanup)
        self.sandbox_root = Path(self.sandbox.name)
        self.base=dict(repo_id=REPO,target_revision=REV,stage='CANDIDATE_INGESTION',
                       baseline_snapshot=366,context_sha256=CTX,
                       candidate_cache=CACHE,reviewer=self.read,root=self.sandbox_root)
        self.plan=make_write_plan(**self.base)
        self.approved=dict(self.base,approved_stage='CANDIDATE_INGESTION',
                           expected_intent_sha256=self.plan.intent_sha256)
    def test_plan_is_never_write_authorized(self):
        self.assertEqual(self.plan.state,'READY_FOR_EXPLICIT_APPROVAL')
        self.assertFalse(self.plan.write_authorized)
        self.assertFalse(self.plan.write_executed)
        self.assertTrue(SHA256.fullmatch(self.plan.intent_sha256))
    def test_plan_intent_stable(self):
        self.assertEqual(make_write_plan(**self.base).intent_sha256,self.plan.intent_sha256)
    def test_plan_no_change_skips(self):
        skip=review(decision='SKIP_ALREADY_CURRENT')
        obj=make_write_plan(**{**self.base,'reviewer':reviewers(skip), 'context_sha256':None})
        self.assertEqual(obj.state,'SKIPPED')
        self.assertIsNone(obj.intent_sha256)
    def test_plan_blocked(self):
        blocked=review(decision='BLOCKED_RECOVERY_REVIEW',disposition='BLOCKED')
        obj=make_write_plan(**{**self.base,'reviewer':reviewers(blocked)})
        self.assertEqual(obj.state,'BLOCKED')
    def test_mismatched_intent_never_launches(self):
        launch=Mock()
        with self.assertRaisesRegex(WriteGateError,'intent SHA256'):
            execute_one_stage(**{**self.approved,'expected_intent_sha256':'f'*64},
                              verifier=verifier_states('INCOMPLETE'),launcher=launch)
        launch.assert_not_called()
    def test_mismatched_stage_never_launches(self):
        launch=Mock()
        with self.assertRaisesRegex(WriteGateError,'matching stage'):
            execute_one_stage(**{**self.approved,'approved_stage':'REUSE_LINKING'},
                              verifier=verifier_states('INCOMPLETE'),launcher=launch)
        launch.assert_not_called()
    def test_completed_stage_refuses_write(self):
        launch=Mock()
        with self.assertRaisesRegex(WriteGateError,'not ready'):
            execute_one_stage(**{**self.approved,'reviewer':reviewers(review(decision='SKIP_ALREADY_CURRENT'))},
                              verifier=verifier_states('INCOMPLETE'),launcher=launch)
        launch.assert_not_called()
    def test_wrong_cache_never_launches(self):
        launch=Mock()
        with self.assertRaisesRegex(RuntimeError,'Candidate cache'):
            execute_one_stage(**{**self.approved,'candidate_cache':'reports/other_cache'},
                              verifier=verifier_states('INCOMPLETE'),launcher=launch)
        launch.assert_not_called()
    def test_fresh_review_drift_never_launches(self):
        launch=Mock()
        two=review(options={'--expected-records':'73'})
        with self.assertRaisesRegex(WriteGateError,'Fresh M4'):
            execute_one_stage(**{**self.approved,'reviewer':reviewers(self.one,two)},
                              verifier=verifier_states('INCOMPLETE'),launcher=launch)
        launch.assert_not_called()
    def test_pre_verified_skips(self):
        launch=Mock()
        with self.assertRaisesRegex(WriteGateError,'not INCOMPLETE'):
            execute_one_stage(**self.approved,verifier=verifier_states('VERIFIED'),launcher=launch)
        launch.assert_not_called()
    def test_pre_blocked_skips(self):
        launch=Mock()
        with self.assertRaisesRegex(WriteGateError,'not INCOMPLETE'):
            execute_one_stage(**self.approved,verifier=verifier_states('BLOCKED'),launcher=launch)
        launch.assert_not_called()
    def test_writer_failed_marks_attempted(self):
        proc=Mock(returncode=1)
        with self.assertRaises(WriteGateError) as cm:
            execute_one_stage(**self.approved,verifier=verifier_states('INCOMPLETE'),launcher=Mock(return_value=proc))
        self.assertTrue(cm.exception.attempted)
    def test_writer_timeout_marks_attempted(self):
        launch=Mock(side_effect=subprocess.TimeoutExpired('argv',1))
        with self.assertRaises(WriteGateError) as cm:
            execute_one_stage(**self.approved,verifier=verifier_states('INCOMPLETE'),launcher=launch)
        self.assertTrue(cm.exception.attempted)
    def test_post_incomplete_blocks(self):
        with self.assertRaises(WriteGateError) as cm:
            execute_one_stage(**self.approved,verifier=verifier_states('INCOMPLETE','INCOMPLETE'),
                              launcher=Mock(return_value=Mock(returncode=0)))
        self.assertTrue(cm.exception.attempted)
    def test_post_verified_success(self):
        launch=Mock(return_value=Mock(returncode=0))
        v=verifier_states('INCOMPLETE','VERIFIED')
        result=execute_one_stage(**self.approved,verifier=v,launcher=launch)
        self.assertEqual(result['state'],'VERIFIED_AFTER_WRITE')
        self.assertTrue(result['write_verified'])
        self.assertFalse(result['next_stage_authorized'])
        self.assertEqual(len(v.calls),2)
        self.assertEqual(launch.call_count,1)
        args=launch.call_args
        self.assertEqual(args.args[0][-3:],('--apply','--confirm-candidate-write','--allow-csv-candidate-write'))
        self.assertFalse(args.kwargs['shell'])
    def test_durable_claim_exists_before_writer_launch(self):
        seen = []
        def launcher(*args, **kwargs):
            claims = list(self.sandbox_root.rglob("CANDIDATE_INGESTION.claim.json"))
            self.assertEqual(len(claims), 1)
            seen.append(claims[0])
            return Mock(returncode=0)
        result = execute_one_stage(
            **self.approved,
            verifier=verifier_states('INCOMPLETE', 'VERIFIED'),
            launcher=launcher,
        )
        self.assertEqual(result['state'], 'VERIFIED_AFTER_WRITE')
        self.assertEqual(len(seen), 1)
        terminal = seen[0].with_name('CANDIDATE_INGESTION.result.json')
        self.assertTrue(terminal.is_file())
        self.assertIn('VERIFIED_AFTER_WRITE', terminal.read_text(encoding='utf-8'))

    def test_failed_writer_claim_stays_reserved(self):
        launcher = Mock(return_value=Mock(returncode=9))
        with self.assertRaises(WriteGateError) as first:
            execute_one_stage(**self.approved,
                              verifier=verifier_states('INCOMPLETE'),
                              launcher=launcher)
        self.assertTrue(first.exception.attempted)
        claims = list(self.sandbox_root.rglob('CANDIDATE_INGESTION.claim.json'))
        self.assertEqual(len(claims), 1)
        results = list(self.sandbox_root.rglob('CANDIDATE_INGESTION.result.json'))
        self.assertEqual(len(results), 1)
        self.assertIn('UNVERIFIED_AFTER_ATTEMPT', results[0].read_text(encoding='utf-8'))
        with self.assertRaisesRegex(WriteGateError, 'Durable write-attempt guard') as second:
            execute_one_stage(**self.approved,
                              verifier=verifier_states('INCOMPLETE'),
                              launcher=launcher)
        self.assertFalse(second.exception.attempted)
        self.assertEqual(launcher.call_count, 1)

    def test_pre_review_drift_refuses(self):
        self.one.contract.context_sha256='f'*64
        with self.assertRaisesRegex(RuntimeError,'context SHA256'):
            execute_one_stage(**self.approved,verifier=verifier_states('INCOMPLETE'),launcher=Mock())
    def test_bad_repo_refused(self):
        with self.assertRaisesRegex(WriteGateError,'Pinned repo'):
            make_write_plan(**{**self.base,'repo_id':'fake/rogue'})
    def test_cli_requires_explicit_confirmation(self):
        with self.assertRaises(SystemExit):
            main(['--execute-one-stage','--target-revision',REV,'--stage','CANDIDATE_INGESTION'])

if __name__=='__main__':unittest.main()
