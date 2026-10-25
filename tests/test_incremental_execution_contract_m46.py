#!/usr/bin/env python3
"""M4.6.4d.6c.2b.1 | Execution Contract offline fail-closed tests."""
from __future__ import annotations

from dataclasses import replace
from io import StringIO
import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.tools.incremental_execution_context import checkpoint_digest
from tests.test_incremental_execution_context_m46 import planned, prepared, registered
from tests.test_incremental_runner_contracts_m46 import evidence
from tests.test_incremental_guarded_stage_planner_m46 import (
    REPO, TARGET, make_plan, routed_new, routed_skip, routed_block, routed_recovery,
)
from scripts.tools.incremental_guarded_stage_planner import (
    STAGE_ORDER, GuardedStagePlan, StageEntry,
)
from scripts.tools.incremental_execution_contract_m46 import (
    STAGE_RULES, KINDS, CONTEXT_KINDS, NO_WRITE, DISPOSITIONS,
    ExecutionContract, StageRule, build_execution_contract,
    describe_stage, format_contract, parse_args, main,
)


def recovery_plan(state, *, got_reuse=0, got_candidates=0):
    return make_plan(routed_recovery(state, got_reuse=got_reuse, got_candidates=got_candidates))


def from_context(p, stage=None):
    """Generate only context for the actual eligible review stage."""
    stage = stage or p.next_review_stage
    if stage in ('PLANNED', 'MANIFEST_PREPARATION', 'CACHE_DOWNLOAD'):
        return dict(planned=planned())
    if stage == 'ARCHIVE_PROMOTION':
        return dict(planned=planned(), cache_evidence=evidence())
    if stage in ('PREPARED', 'SNAPSHOT_REGISTRATION'):
        return dict(prepared=prepared())
    if stage in ('REUSE_LINKING', 'CANDIDATE_INGESTION', 'MANIFEST_REGISTRATION', 'FINALIZATION'):
        return dict(registered=registered())
    return {}


class CatalogTests(unittest.TestCase):
    def test_exact_11_stages(self):
        self.assertEqual(tuple(STAGE_RULES), STAGE_ORDER)
        self.assertEqual(len(STAGE_RULES), 11)
    def test_metadata_is_inert(self):
        self.assertTrue(all(isinstance(s, StageRule) for s in STAGE_RULES.values()))
        self.assertTrue(all(s.required_evidence and s.post_write_verification for s in STAGE_RULES.values()))
    def test_one_context_per_stage(self):
        self.assertEqual(STAGE_RULES['ROUTE'].context_kind, 'none')
        self.assertEqual(STAGE_RULES['ARCHIVE_PROMOTION'].context_kind, 'planned')
        self.assertEqual(STAGE_RULES['SNAPSHOT_REGISTRATION'].context_kind, 'prepared')
        self.assertEqual(STAGE_RULES['FINALIZATION'].context_kind, 'registered')
    def test_no_run_all_or_shell_in_preview_steps(self):
        for s in STAGE_RULES.values():
            with self.subTest(stage=s.stage):
                for step in s.preview_steps:
                    self.assertNotIn('--', step)
    def test_safety_defaults(self):
        self.assertEqual(NO_WRITE, 'NO_WRITE_APPROVAL')
        self.assertEqual(DISPOSITIONS, {'NO_ACTION','BLOCKED','AWAITING_EVIDENCE','REVIEW_READ_ONLY_PLAN'})
    def test_stage_spec_invalid(self):
        s = STAGE_RULES['MANIFEST_PREPARATION']
        changes = (
            dict(stage='WRONG'), dict(kind='WRONG'),dict(context_kind='WRONG'),
            dict(write_mode=None), dict(confirm_flag=None),
            dict(required_evidence=()), dict(post_write_verification=()),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(s, **change)
    def test_route_never_writer(self):
        with self.assertRaises(ValueError):
            replace(STAGE_RULES['ROUTE'], write_mode='--commit',confirm_flag='--confirm')
    def test_recovery_stages_have_no_auto_authorization(self):
        for stage in STAGE_ORDER:
            self.assertNotEqual(STAGE_RULES[stage].kind, 'EXECUTE_AUTOMATICALLY')
    def test_archive_promotion_needs_expected_records_from_cache(self):
        self.assertEqual(STAGE_RULES['ARCHIVE_PROMOTION'].preview_steps, ('archive_promotion',))
    def test_prepared_checkpoint_after_archive(self):
        self.assertLess(STAGE_ORDER.index('ARCHIVE_PROMOTION'), STAGE_ORDER.index('PREPARED'))
    def test_registration_gap_doc(self):
        self.assertIn('crash gap BLOCKS', STAGE_RULES['SNAPSHOT_REGISTRATION'].post_write_verification[0])
    def test_candidate_retry_doc(self):
        self.assertIn('never replay verified INSERTs', STAGE_RULES['CANDIDATE_INGESTION'].post_write_verification[0])
    def test_cli_static_mode(self):
        for argv in (['--list-stages'], ['--describe-stage', 'FINALIZATION']):
            self.assertIsNotNone(parse_args(argv))
    def test_cli_rejects_execution_options(self):
        for argv in (['--commit'],['--run-all'],['--apply'],['--stage','REUSE_LINKING'],
                     ['--describe-stage','UNKNOWN'],[]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), mock.patch('sys.stderr'):
                parse_args(argv)
    def test_describe_catalog_read_only(self):
        for stage in STAGE_ORDER:
            txt=describe_stage(stage)
            self.assertIn(stage,txt)
            self.assertIn('NOT runnable', txt) if STAGE_RULES[stage].write_mode else self.assertIn('Never approved',txt)
    def test_describe_unknown(self):
        with self.assertRaises(ValueError): describe_stage('DELETE_ALL')
    def test_main_only_static_print(self):
        messages=[]
        self.assertEqual(main(['--list-stages'],output=messages.append),0)
        self.assertIn('FINALIZATION',messages[0])
        messages=[]
        self.assertEqual(main(['--describe-stage','ARCHIVE_PROMOTION'],output=messages.append),0)
        self.assertIn('expected_records',messages[0]) if 'expected_records' in messages[0] else self.assertIn('Verified cache receipts',messages[0])


class ContractNoActionTests(unittest.TestCase):
    def test_completed_skip(self):
        c=build_execution_contract(make_plan(routed_skip()))
        self.assertEqual(c.disposition,'NO_ACTION')
        self.assertIsNone(c.review_stage)
        self.assertEqual(c.read_only_argv,())
        self.assertFalse(c.write_authorized)
    def test_absent_no_raw_skip(self):
        c=build_execution_contract(make_plan(routed_skip('SKIP_NO_RAW_CHANGES','NO_REGISTERED_SNAPSHOT',None)))
        self.assertEqual(c.disposition,'NO_ACTION')
    def test_blocked(self):
        c=build_execution_contract(make_plan(routed_block()))
        self.assertEqual(c.disposition,'BLOCKED')
        self.assertTrue(c.blockers)
        self.assertEqual(c.read_only_argv,())
    def test_blocked_even_with_supplied_context(self):
        c=build_execution_contract(make_plan(routed_block()),planned=planned())
        self.assertEqual(c.disposition,'BLOCKED')
    def test_skip_even_with_supplied_context(self):
        c=build_execution_contract(make_plan(routed_skip()),planned=planned())
        self.assertEqual(c.disposition,'NO_ACTION')
    def test_skip_report_has_no_write_approval(self):
        txt=format_contract(build_execution_contract(make_plan(routed_skip())))
        self.assertIn('Write authorized: NO',txt)
        self.assertIn('Generated write argv: NONE',txt)
    def test_blocked_report_contains_blocker(self):
        txt=format_contract(build_execution_contract(make_plan(routed_block())))
        self.assertIn('BLOCKER:',txt)
    def test_no_action_never_exposes_preview(self):
        c=build_execution_contract(make_plan(routed_skip()))
        with self.assertRaises(ValueError):
            replace(c,read_only_argv=(('python','-m','scripts.tools.any','--plan'),))


class NewRevisionContractTests(unittest.TestCase):
    def test_missing_planned_context(self):
        c=build_execution_contract(make_plan(routed_new()))
        self.assertEqual(c.disposition,'AWAITING_EVIDENCE')
        self.assertEqual(c.context_kind,'planned')
        self.assertEqual(c.read_only_argv,())
    def test_pinned_planned_context(self):
        p=make_plan(routed_new())
        c=build_execution_contract(p,planned=planned())
        self.assertEqual(c.disposition,'REVIEW_READ_ONLY_PLAN')
        self.assertEqual(c.review_stage,'PLANNED')
        self.assertEqual(c.context_sha256,checkpoint_digest(planned()))
        self.assertEqual(c.read_only_argv,())
    def test_planned_is_not_auto_checkpoint_publication(self):
        with mock.patch('scripts.tools.incremental_execution_context.publish_checkpoint',side_effect=AssertionError('NO')):
            c=build_execution_contract(make_plan(routed_new()), planned=planned())
        self.assertEqual(c.disposition,'REVIEW_READ_ONLY_PLAN')
    def test_changed_planned_pinned_hash_block(self):
        with self.assertRaises(RuntimeError):
            build_execution_contract(make_plan(routed_new()),
                planned=replace(planned(),expected_baseline_records=101))
    def test_invalid_plan_type(self):
        with self.assertRaises(TypeError): build_execution_contract(None)
    def test_invalid_context_type(self):
        with self.assertRaises(TypeError): build_execution_contract(make_plan(routed_new()),planned='bogus')
    def test_invalid_other_context_type(self):
        with self.assertRaises(TypeError): build_execution_contract(make_plan(routed_new()),prepared='bogus')
    def test_identity_mismatch(self):
        with self.assertRaises(RuntimeError):
            build_execution_contract(make_plan(routed_new()),planned=replace(planned(),repo_id='other/repo'))
    def test_no_stage_skip_around_planned(self):
        p=make_plan(routed_new())
        with self.assertRaises(RuntimeError):
            build_execution_contract(replace(p,next_review_stage='MANIFEST_PREPARATION',
                stages=tuple(replace(e,status='READY') if e.name=='MANIFEST_PREPARATION' else
                replace(e,status='WAITING') if e.name=='PLANNED' else e for e in p.stages)))
    def test_ready_must_be_unique(self):
        p=make_plan(routed_new())
        entries=tuple(replace(e,status='READY') if e.name=='CACHE_DOWNLOAD' else e for e in p.stages)
        with self.assertRaises(RuntimeError):
            build_execution_contract(replace(p,stages=entries))
    def test_contradictory_decision(self):
        p=make_plan(routed_new())
        with self.assertRaises(RuntimeError):
            build_execution_contract(replace(p,decision='UNKNOWN',read_only_previews=()))
    def test_stage_context_cannot_authorize_write(self):
        c=build_execution_contract(make_plan(routed_new()),planned=planned())
        with self.assertRaises(ValueError): replace(c,write_authorized=True)


class RecoveryStageContracts(unittest.TestCase):
    SAMPLES = (
        ('PREPARATION_PARTIAL','MANIFEST_PREPARATION',0,0,1),
        ('PREPARED_READY','SNAPSHOT_REGISTRATION',0,0,2),
        ('REGISTERED_PENDING_REUSE','REUSE_LINKING',0,0,1),
        ('REUSE_COMPLETE','CANDIDATE_INGESTION',1,0,2),
        ('CANDIDATES_PARTIAL','CANDIDATE_INGESTION',1,1,2),
        ('CANDIDATES_COMPLETE','MANIFEST_REGISTRATION',1,2,1),
        ('MANIFEST_REGISTERED','FINALIZATION',1,2,1),
    )
    def test_missing_verified_context_is_never_approval(self):
        for state,stage,reuse,candidates,_ in self.SAMPLES:
            with self.subTest(state=state):
                p=recovery_plan(state,got_reuse=reuse,got_candidates=candidates)
                c=build_execution_contract(p)
                self.assertEqual(c.disposition,'AWAITING_EVIDENCE')
                self.assertEqual(c.review_stage,stage)
                self.assertEqual(c.read_only_argv,())
                self.assertTrue(c.blockers)
    def test_preview_from_context_for_each_recovery_state(self):
        for state,stage,reuse,candidates,expected_count in self.SAMPLES:
            with self.subTest(state=state):
                p=recovery_plan(state,got_reuse=reuse,got_candidates=candidates)
                c=build_execution_contract(p,**from_context(p))
                self.assertEqual(c.disposition,'REVIEW_READ_ONLY_PLAN')
                self.assertEqual(c.review_stage,stage)
                self.assertEqual(len(c.read_only_argv),expected_count)
                for argv in c.read_only_argv:
                    self.assertEqual(argv[-1],'--plan')
                    self.assertEqual(argv[1],'-m')
                self.assertFalse(c.write_authorized)
    def test_recovery_does_not_skip_checkpoint_verification(self):
        p=recovery_plan('CANDIDATES_PARTIAL',got_reuse=1,got_candidates=1)
        c=build_execution_contract(p,registered=registered())
        self.assertIn('Read back candidate observations',c.post_write_verification[0])
    def test_manifest_recovery_plan_is_only_readonly(self):
        p=recovery_plan('CANDIDATES_COMPLETE',got_reuse=1,got_candidates=2)
        c=build_execution_contract(p,registered=registered())
        self.assertIn('scripts.tools.register_incremental_manifest',c.read_only_argv[0])
        self.assertNotIn('--commit',c.read_only_argv[0])
    def test_finalize_recovery_plan_is_only_readonly(self):
        p=recovery_plan('MANIFEST_REGISTERED',got_reuse=1,got_candidates=2)
        c=build_execution_contract(p,registered=registered())
        self.assertIn('scripts.tools.finalize_incremental_snapshot',c.read_only_argv[0])
    def test_snapshot_id_mismatch(self):
        p=recovery_plan('REUSE_COMPLETE',got_reuse=1)
        with self.assertRaises(RuntimeError):
            build_execution_contract(p,registered=replace(registered(),target_snapshot_id=367))
    def test_lineage_mismatch(self):
        p=recovery_plan('REUSE_COMPLETE',got_reuse=1)
        with self.assertRaises(RuntimeError):
            build_execution_contract(p,planned=replace(planned(),expected_baseline_records=101),
                                     registered=registered())
    def test_context_mismatched_repo(self):
        p=recovery_plan('PREPARED_READY')
        wrong=replace(prepared(),planned=replace(planned(),repo_id='other/repo'))
        with self.assertRaises(RuntimeError): build_execution_contract(p,prepared=wrong)
    def test_registered_context_must_not_be_supplied_when_target_absent(self):
        p=recovery_plan('PREPARED_READY')
        with self.assertRaises(RuntimeError):
            build_execution_contract(p,registered=registered())
    def test_recovery_cannot_skip_directly_to_archive(self):
        p=recovery_plan('PREPARATION_PARTIAL')
        forced=replace(p,next_review_stage='ARCHIVE_PROMOTION', stages=tuple(
          replace(x,status='READY') if x.name=='ARCHIVE_PROMOTION' else
          replace(x,status='WAITING') if x.status=='READY' else x for x in p.stages))
        for kw in (dict(planned=planned()), dict(planned=planned(),cache_evidence=evidence())):
            with self.subTest(kw=tuple(kw)),self.assertRaises(RuntimeError):
                build_execution_contract(forced,**kw)
    def test_cache_identity_is_validated_by_existing_adapter(self):
        from scripts.tools.incremental_runner_contracts import _check_evidence,plan_command
        _check_evidence(planned(),evidence())
        cmd=plan_command('archive_promotion',planned(),cache_evidence=evidence())
        self.assertIn('--expected-records',cmd)
        with self.assertRaises(RuntimeError):
            _check_evidence(planned(),replace(evidence(),planned_sha256='f'*64))
    def test_cache_evidence_disallowed_for_other_stage(self):
        with self.assertRaises(ValueError):
            build_execution_contract(make_plan(routed_new()),planned=planned(),cache_evidence=evidence())


class FrozenContractTests(unittest.TestCase):
    def test_cannot_mutate(self):
        c=build_execution_contract(make_plan(routed_skip()))
        with self.assertRaises(Exception): c.disposition='REVIEW_READ_ONLY_PLAN'
    def test_no_write_mode_argv(self):
        c=build_execution_contract(recovery_plan('MANIFEST_REGISTERED',got_reuse=1,got_candidates=2),registered=registered())
        for flag in ('--commit','--apply','--download','--promote','--publish','--register','--link','--run-all'):
            self.assertNotIn(flag,c.read_only_argv[0])
    def test_bad_preview_rejected(self):
        c=build_execution_contract(make_plan(routed_new()),planned=planned())
        for flag in ('--commit','--apply','--download','--promote','--publish','--register','--link','--run-all'):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                replace(c,read_only_argv=(("python","-m","scripts.tools.bad",flag,"--plan"),))
    def test_bad_preview_module_shape(self):
        c=build_execution_contract(make_plan(routed_new()),planned=planned())
        with self.assertRaises(ValueError):
            replace(c,read_only_argv=(("python","-m","not.tools.mod","--plan"),))
    def test_invalid_digest(self):
        c=build_execution_contract(make_plan(routed_new()),planned=planned())
        with self.assertRaises(ValueError): replace(c,context_sha256='main')
    def test_unknown_disposition(self):
        c=build_execution_contract(make_plan(routed_new()),planned=planned())
        with self.assertRaises(ValueError): replace(c,disposition='AUTHORIZED')
    def test_bad_stage_name(self):
        c=build_execution_contract(make_plan(routed_new()),planned=planned())
        with self.assertRaises(ValueError): replace(c,review_stage='DELETE')
    def test_never_run_subprocess_on_any_path(self):
        with mock.patch.object(subprocess,'run',side_effect=AssertionError('NO child processes')):
            build_execution_contract(make_plan(routed_new()),planned=planned())
            build_execution_contract(make_plan(routed_skip()))
            build_execution_contract(make_plan(routed_block()))
    def test_readonly_report(self):
        c=build_execution_contract(recovery_plan('MANIFEST_REGISTERED',got_reuse=1,got_candidates=2),registered=registered())
        s=format_contract(c)
        self.assertIn('UNEXECUTED --plan argv:',s)
        self.assertIn('Write authorized: NO',s)
    def test_no_side_effect_imports(self):
        import inspect
        from scripts.tools import incremental_execution_contract_m46 as code
        source=inspect.getsource(code)
        tree=ast.parse(source)
        imports=[node for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom))]
        self.assertFalse(any('psycopg' in ast.unparse(node) for node in imports))
        self.assertNotIn('subprocess.run(',source)
        self.assertNotIn('publish_checkpoint(',source)


if __name__=='__main__':
    unittest.main()
