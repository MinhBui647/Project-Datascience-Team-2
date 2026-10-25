#!/usr/bin/env python3
"""M4.6.4d.6c.2b.2a | Pure/offline single-stage guarded executor tests."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.tools.incremental_execution_context import checkpoint_digest
from scripts.tools.incremental_execution_contract_m46 import build_execution_contract
from scripts.tools.incremental_guarded_stage_planner import build_stage_plan_from_route
from tests.test_incremental_execution_context_m46 import planned, prepared, registered
from tests.test_incremental_guarded_stage_planner_m46 import (
    REPO, TARGET, make_plan, routed_new, routed_skip, routed_block,
    routed_recovery,
)
from scripts.tools.incremental_guarded_stage_executor import (
    MAX_PLAN_TIMEOUT, StageReview, StageResult, WRITE_TOKENS,
    review_one_stage, validate_read_only_commands, _check_approval,
    execute_read_only_plan, describe_review, parse_args, format_result, main,
)


def review_from_route(route, *, checkpoint_reader=None):
    return review_one_stage(repo_id=REPO, target_revision=TARGET,
                            root=Path(tempfile.gettempdir()),
                            router=lambda **kw: route,
                            checkpoint_reader=checkpoint_reader or (lambda *a: (None,None,None)))


def recovery_review(state='MANIFEST_REGISTERED', *, got_reuse=1, got_candidates=2):
    route=routed_recovery(state,got_reuse=got_reuse,got_candidates=got_candidates)
    return review_from_route(route,checkpoint_reader=lambda *args:(planned(),prepared(),registered()))


def fake_launcher(cmd, **kw):
    return SimpleNamespace(returncode=0,stdout='PASS',stderr='')


class OfflineReviewTests(unittest.TestCase):
    def test_new_revision_context_in_memory(self):
        v=review_from_route(routed_new())
        self.assertEqual(v.plan.next_review_stage,'PLANNED')
        self.assertEqual(v.contract.context_sha256,checkpoint_digest(planned()))
        self.assertEqual(v.contract.read_only_argv,())
    def test_skip_is_no_action(self):
        v=review_from_route(routed_skip())
        self.assertEqual(v.contract.disposition,'NO_ACTION')
    def test_block_is_blocked(self):
        v=review_from_route(routed_block())
        self.assertEqual(v.contract.disposition,'BLOCKED')
    def test_recovery_manifest_stage(self):
        v=recovery_review()
        self.assertEqual(v.plan.next_review_stage,'FINALIZATION')
        self.assertEqual(v.contract.disposition,'REVIEW_READ_ONLY_PLAN')
        self.assertEqual(len(v.contract.read_only_argv),1)
    def test_recovery_candidate_two_preview_modules(self):
        v=recovery_review('REUSE_COMPLETE',got_candidates=0)
        self.assertEqual(v.plan.next_review_stage,'CANDIDATE_INGESTION')
        self.assertEqual(len(v.contract.read_only_argv),2)
    def test_unregistered_preparation_does_not_need_registered(self):
        v=review_from_route(routed_recovery('PREPARATION_PARTIAL'),checkpoint_reader=lambda *a:(planned(),None,None))
        self.assertEqual(v.plan.next_review_stage,'MANIFEST_PREPARATION')
        self.assertEqual(len(v.contract.read_only_argv),1)
    def test_unregistered_prepared_stage(self):
        v=review_from_route(routed_recovery('PREPARED_READY'),checkpoint_reader=lambda *a:(planned(),prepared(),None))
        self.assertEqual(v.plan.next_review_stage,'SNAPSHOT_REGISTRATION')
        self.assertEqual(len(v.contract.read_only_argv),2)
    def test_in_progress_missing_registered_blocks(self):
        with self.assertRaises(RuntimeError):
            review_from_route(routed_recovery('REUSE_COMPLETE'),checkpoint_reader=lambda *a:(planned(),prepared(),None))
    def test_registered_snapshot_id_mismatch_blocks(self):
        with self.assertRaises(RuntimeError):
            review_from_route(routed_recovery('REUSE_COMPLETE'),checkpoint_reader=lambda *a:(planned(),prepared(),replace(registered(),target_snapshot_id=999)))
    def test_unregistered_has_unexpected_registered_blocks(self):
        with self.assertRaises(RuntimeError):
            review_from_route(routed_recovery('PREPARED_READY'),checkpoint_reader=lambda *a:(planned(),prepared(),registered()))
    def test_recovery_without_planned_blocks(self):
        with self.assertRaises(RuntimeError):
            review_from_route(routed_recovery('PREPARATION_PARTIAL'),checkpoint_reader=lambda *a:(None,None,None))
    def test_skip_does_not_read_checkpoints(self):
        self.assertEqual(review_one_stage(repo_id=REPO,target_revision=TARGET,
            root=Path(tempfile.gettempdir()),router=lambda **k:routed_skip(),
            checkpoint_reader=lambda *args: self.fail('Must not read checkpoints')).contract.disposition,'NO_ACTION')
    def test_new_does_not_read_checkpoints(self):
        self.assertEqual(review_one_stage(repo_id=REPO,target_revision=TARGET,
            root=Path(tempfile.gettempdir()),router=lambda **k:routed_new(),
            checkpoint_reader=lambda *args:self.fail('Must not read checkpoints')).plan.next_review_stage,'PLANNED')
    def test_invalid_repo(self):
        with self.assertRaises(ValueError):review_one_stage(repo_id='',target_revision=TARGET)
    def test_invalid_revision(self):
        with self.assertRaises(ValueError):review_one_stage(repo_id=REPO,target_revision='main')
    def test_invalid_baseline(self):
        with self.assertRaises(ValueError):review_one_stage(repo_id=REPO,target_revision=TARGET,baseline_snapshot=0)
    def test_plan_is_read_only(self):
        v=review_from_route(routed_new())
        with self.assertRaises(ValueError):StageReview(replace(v.plan,write_authorized=True),v.contract)
    def test_review_rejects_wrong_identity(self):
        v=review_from_route(routed_new())
        with self.assertRaises(ValueError):StageReview(v.plan,replace(v.contract,repo_id='wrong/repo'))
    def test_review_rejects_mismatched_stage(self):
        v=recovery_review()
        with self.assertRaises(ValueError):StageReview(v.plan,replace(v.contract,review_stage='REUSE_LINKING'))
    def test_review_rejects_wrong_types(self):
        with self.assertRaises(TypeError):StageReview(None,None)
    def test_no_new_context_forging_when_recovery(self):
        v=recovery_review()
        self.assertIsNone(v.plan.planned_context_sha256)
    def test_recovery_malformed_checkpoint_bubbles(self):
        with self.assertRaisesRegex(RuntimeError,'checkpoint'):
            review_from_route(routed_recovery('PREPARATION_PARTIAL'),checkpoint_reader=lambda *a: (_ for _ in ()).throw(RuntimeError('bad checkpoint')))


class PreviewGuardTests(unittest.TestCase):
    def setUp(self):self.v=recovery_review()
    def test_current_preview_valid(self):
        self.assertEqual(validate_read_only_commands(self.v),self.v.contract.read_only_argv)
    def test_candidate_two_commands_valid(self):
        v=recovery_review('REUSE_COMPLETE',got_candidates=0)
        self.assertEqual(len(validate_read_only_commands(v)),2)
    def test_new_planned_has_no_children(self):
        self.assertEqual(validate_read_only_commands(review_from_route(routed_new())),())
    def test_blocked_has_no_children(self):
        with self.assertRaises(RuntimeError):validate_read_only_commands(review_from_route(routed_block()))
    def test_skip_has_no_children(self):
        with self.assertRaises(RuntimeError):validate_read_only_commands(review_from_route(routed_skip()))
    def test_wrong_type(self):
        with self.assertRaises(TypeError):validate_read_only_commands('fake')
    def test_unapproved_flag_block(self):
        with self.assertRaises(ValueError):
            replace(self.v.contract,read_only_argv=(self.v.contract.read_only_argv[0][:-1]+('--commit',),))
    def test_shell_python_m_flag_block(self):
        with self.assertRaises(ValueError):
            replace(self.v.contract,read_only_argv=(('sh','-c','echo','--plan'),))
    def test_unknown_module_block(self):
        argv=list(self.v.contract.read_only_argv[0]);argv[2]='scripts.tools.malicious'
        with self.assertRaises(RuntimeError):validate_read_only_commands(StageReview(self.v.plan,replace(self.v.contract,read_only_argv=(tuple(argv),))))
    def test_duplicate_flags_block(self):
        argv=list(self.v.contract.read_only_argv[0]);argv[3:3]=['--repo-id',REPO]
        with self.assertRaises(RuntimeError):validate_read_only_commands(StageReview(self.v.plan,replace(self.v.contract,read_only_argv=(tuple(argv),))))
    def test_foreign_target_block(self):
        argv=list(self.v.contract.read_only_argv[0]);argv[argv.index('--target-revision')+1]='a'*40
        with self.assertRaises(RuntimeError):validate_read_only_commands(StageReview(self.v.plan,replace(self.v.contract,read_only_argv=(tuple(argv),))))
    def test_unexpected_extra_flag_block(self):
        argv=list(self.v.contract.read_only_argv[0]);argv[-1:-1]=['--arbitrary', '1']
        with self.assertRaises(RuntimeError):validate_read_only_commands(StageReview(self.v.plan,replace(self.v.contract,read_only_argv=(tuple(argv),))))
    def test_python_extra_flags_block(self):
        argv=list(self.v.contract.read_only_argv[0]);argv[1]='-c'
        with self.assertRaises(ValueError):
            replace(self.v.contract,read_only_argv=(tuple(argv),))
    def test_extra_child_block(self):
        c=self.v.contract.read_only_argv
        with self.assertRaises(RuntimeError):validate_read_only_commands(StageReview(self.v.plan,replace(self.v.contract,read_only_argv=c+c)))
    def test_cannot_approve_any_write_tokens(self):
        self.assertIn('--commit',WRITE_TOKENS)
        self.assertIn('--apply',WRITE_TOKENS)
        self.assertIn('--download',WRITE_TOKENS)


class AdvisoryStageTests(unittest.TestCase):
    def test_new_revision_planned_review(self):
        o=describe_review(review_from_route(routed_new()))
        self.assertEqual(o.decision,'REVIEW_ONLY')
        self.assertEqual(o.stage,'PLANNED')
        self.assertEqual(o.executed_read_only_commands,0)
    def test_skip(self):
        o=describe_review(review_from_route(routed_skip()))
        self.assertEqual(o.decision,'SKIPPED')
    def test_block(self):
        o=describe_review(review_from_route(routed_block()))
        self.assertEqual(o.decision,'BLOCKED')
    def test_wrong_stage(self):
        o=describe_review(recovery_review(),stage='CANDIDATE_INGESTION')
        self.assertEqual(o.decision,'BLOCKED')
    def test_awaiting_evidence(self):
        v=recovery_review()
        c=replace(v.contract,disposition='AWAITING_EVIDENCE',read_only_argv=(),blockers=('missing',))
        o=describe_review(StageReview(v.plan,c))
        self.assertEqual(o.decision,'AWAITING_EVIDENCE')
    def test_report_no_write_approval(self):
        txt=format_result(describe_review(recovery_review()))
        self.assertIn('Write authorization: NO',txt)
        self.assertIn('Checkpoint writes: NONE',txt)
    def test_bad_stage(self):
        with self.assertRaises(ValueError):describe_review(recovery_review(),stage='COMMIT_ALL')
    def test_bad_result(self):
        with self.assertRaises(ValueError):StageResult('ANY','COMMIT_ALL','bad')
    def test_bad_sha(self):
        with self.assertRaises(ValueError):StageResult('ANY','PLANNED','',context_sha256='main')
    def test_invalid_count(self):
        with self.assertRaises(ValueError):StageResult('ANY','PLANNED','',executed_read_only_commands=-1)


class SingleStageExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.v=recovery_review()
        self.kw=dict(repo_id=REPO,target_revision=TARGET,stage='FINALIZATION',
            approve_stage='FINALIZATION',expected_context_sha256=self.v.contract.context_sha256,
            root=self.root,reviewer=lambda **kwargs:self.v)
    def tearDown(self):self.tmp.cleanup()
    def test_runs_one_read_only_command(self):
        seen=[]
        def launcher(argv,**kwargs):
            seen.append((argv,kwargs));return fake_launcher(argv,**kwargs)
        result=execute_read_only_plan(**self.kw,launcher=launcher)
        self.assertEqual(result.decision,'READ_ONLY_PLANS_RAN')
        self.assertEqual(result.executed_read_only_commands,1)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0][0][-1],'--plan')
        self.assertFalse(seen[0][1]['shell'])
        self.assertEqual(seen[0][1]['cwd'],str(self.root))
    def test_two_children_same_stage(self):
        v=recovery_review('REUSE_COMPLETE',got_candidates=0)
        kw={**self.kw,'stage':'CANDIDATE_INGESTION','approve_stage':'CANDIDATE_INGESTION',
            'reviewer':lambda **kwargs:v,'expected_context_sha256':v.contract.context_sha256}
        called=[]
        def launch(argv,**kwargs):called.append(argv);return fake_launcher(argv,**kwargs)
        result=execute_read_only_plan(**kw,launcher=launch)
        self.assertEqual(result.executed_read_only_commands,2)
        self.assertEqual(len(called),2)
    def test_fresh_review_twice(self):
        seen=[]
        def reviewer(**k):seen.append(True);return self.v
        execute_read_only_plan(**{**self.kw,'reviewer':reviewer},launcher=fake_launcher)
        self.assertEqual(len(seen),2)
    def test_rechecks_before_any_child(self):
        sequence=[self.v,review_from_route(routed_skip())]
        calls=[]
        with self.assertRaises(RuntimeError):
            execute_read_only_plan(**{**self.kw,'reviewer':lambda **k:sequence.pop(0)},
                launcher=lambda *a,**kw:calls.append(1))
        self.assertEqual(calls,[])
    def test_plan_changed_after_review_blocks(self):
        modified=replace(self.v.contract,context_sha256='b'*64)
        seq=[self.v,StageReview(self.v.plan,modified)]
        with self.assertRaises(RuntimeError):execute_read_only_plan(**{**self.kw,'reviewer':lambda **k:seq.pop(0)},launcher=fake_launcher)
    def test_wrong_stage_no_launch(self):
        with self.assertRaises(RuntimeError):execute_read_only_plan(**{**self.kw,'stage':'REUSE_LINKING',
            'approve_stage':'REUSE_LINKING'}, launcher=lambda *a,**k:self.fail('launched'))
    def test_wrong_approval_no_launch(self):
        with self.assertRaises(RuntimeError):execute_read_only_plan(**{**self.kw,'approve_stage':'REUSE_LINKING'},launcher=lambda *a,**k:self.fail('launched'))
    def test_missing_hash_no_launch(self):
        with self.assertRaises(RuntimeError):execute_read_only_plan(**{**self.kw,'expected_context_sha256':None},launcher=lambda *a,**k:self.fail('launched'))
    def test_mismatched_hash_no_launch(self):
        with self.assertRaises(RuntimeError):execute_read_only_plan(**{**self.kw,'expected_context_sha256':'a'*64},launcher=lambda *a,**k:self.fail('launched'))
    def test_no_children_checkpoint_stage_no_launch(self):
        v=review_from_route(routed_new())
        kw={**self.kw,'stage':'PLANNED','approve_stage':'PLANNED',
            'reviewer':lambda **k:v,'expected_context_sha256':v.contract.context_sha256}
        with self.assertRaisesRegex(RuntimeError,'No independent'):execute_read_only_plan(**kw,launcher=lambda *a,**k:self.fail('launched'))
    def test_skipped_no_launch(self):
        with self.assertRaises(RuntimeError):execute_read_only_plan(**{**self.kw,'reviewer':lambda **k:review_from_route(routed_skip())},launcher=lambda *a,**k:self.fail('launched'))
    def test_child_return_code_failure_not_stage_success(self):
        with self.assertRaisesRegex(RuntimeError,'failed'):
            execute_read_only_plan(**self.kw,launcher=lambda *a,**k:SimpleNamespace(returncode=1))
    def test_child_timeout_blocks(self):
        with self.assertRaisesRegex(RuntimeError,'timeout'):
            execute_read_only_plan(**self.kw,launcher=lambda *a,**k:(_ for _ in ()).throw(subprocess.TimeoutExpired('plan',10)))
    def test_child_invalid_exit_code(self):
        with self.assertRaises(RuntimeError):execute_read_only_plan(**self.kw,launcher=lambda *a,**k:SimpleNamespace(returncode=True))
    def test_bad_timeout_zero(self):
        with self.assertRaises(ValueError):execute_read_only_plan(**self.kw,timeout_seconds=0)
    def test_bad_timeout_high(self):
        with self.assertRaises(ValueError):execute_read_only_plan(**self.kw,timeout_seconds=MAX_PLAN_TIMEOUT+1)
    def test_bad_root(self):
        with self.assertRaises(ValueError):execute_read_only_plan(**{**self.kw,'root':self.root/'nope'})
    def test_no_real_subprocess_in_unit_suite(self):
        with mock.patch('subprocess.run',side_effect=AssertionError('real subprocess forbidden')):
            outcome=describe_review(self.v)
        self.assertEqual(outcome.decision,'REVIEW_ONLY')


class CLITests(unittest.TestCase):
    def test_plan_parse(self):
        a=parse_args(['--target-revision',TARGET,'--plan'])
        self.assertTrue(a.plan)
    def test_execute_parse(self):
        a=parse_args(['--target-revision',TARGET,'--execute-read-only-plan',
            '--stage','REUSE_LINKING','--approve-stage','REUSE_LINKING',
            '--expected-context-sha256','a'*64])
        self.assertTrue(a.execute_read_only_plan)
    def test_unknown_write_flags_block(self):
        for argv in (['--commit'],['--apply'],['--download'],['--run-all'],['--register'],['--publish']):
            with self.subTest(argv=argv),self.assertRaises(SystemExit),mock.patch('sys.stderr'):
                parse_args(['--plan','--target-revision',TARGET]+argv)
    def test_plan_cannot_supply_approval(self):
        with self.assertRaises(SystemExit),mock.patch('sys.stderr'):
            parse_args(['--plan','--target-revision',TARGET,'--approve-stage','REUSE_LINKING'])
    def test_execute_needs_all_factors(self):
        with self.assertRaises(SystemExit),mock.patch('sys.stderr'):
            parse_args(['--execute-read-only-plan','--target-revision',TARGET,'--stage','REUSE_LINKING'])
    def test_execute_rejects_different_stage(self):
        with self.assertRaises(SystemExit),mock.patch('sys.stderr'):
            parse_args(['--execute-read-only-plan','--target-revision',TARGET,
                '--stage','FINALIZATION','--approve-stage','REUSE_LINKING',
                '--expected-context-sha256','a'*64])
    def test_invalid_target(self):
        with self.assertRaises(SystemExit),mock.patch('sys.stderr'):
            parse_args(['--plan','--target-revision','main'])
    def test_main_skip(self):
        messages=[]
        ret=main(['--plan','--target-revision',TARGET],reviewer=lambda **k:review_from_route(routed_skip()),output=messages.append)
        self.assertEqual(ret,0);self.assertIn('SKIPPED',messages[0])
    def test_main_block(self):
        messages=[]
        ret=main(['--plan','--target-revision',TARGET],reviewer=lambda **k:review_from_route(routed_block()),output=messages.append)
        self.assertEqual(ret,2);self.assertIn('BLOCKED',messages[0])
    def test_main_stage_wrong(self):
        messages=[]
        ret=main(['--plan','--target-revision',TARGET,'--stage','FINALIZATION'],
                 reviewer=lambda **k:review_from_route(routed_new()),output=messages.append)
        self.assertEqual(ret,2)
    def test_main_execute_with_fake_launcher(self):
        messages=[]
        v=recovery_review()
        ret=main(['--target-revision',TARGET,'--execute-read-only-plan',
            '--stage','FINALIZATION','--approve-stage','FINALIZATION',
            '--expected-context-sha256',v.contract.context_sha256],
            reviewer=lambda **k:v,launcher=fake_launcher,output=messages.append)
        self.assertEqual(ret,0)
        self.assertIn('READ_ONLY_PLANS_RAN',messages[0])
    def test_no_write_calls_by_default(self):
        messages=[]
        v=review_from_route(routed_new())
        with mock.patch('subprocess.run',side_effect=AssertionError('not allowed')):
            ret=main(['--target-revision',TARGET,'--plan'],reviewer=lambda **k:v,output=messages.append)
        self.assertEqual(ret,0)
        self.assertIn('Read-only child commands run: 0',messages[0])


if __name__ == '__main__':unittest.main()
