#!/usr/bin/env python3
"""M4.6.4d.6c.2a offline Guarded Stage Planner fail-closed tests."""
from __future__ import annotations

from dataclasses import replace
from io import StringIO
import ast
from pathlib import Path
import unittest
from unittest import mock

from scripts.tools.incremental_execution_context import checkpoint_digest
from scripts.tools.incremental_recovery_audit import RecoveryAssessment
from scripts.tools.incremental_readonly_router import RoutingOutcome
from scripts.tools.plan_incremental_orchestrator import ReadOnlyOutcome
from tests.test_incremental_execution_context_m46 import planned
from scripts.tools.incremental_guarded_stage_planner import (
    STAGE_ORDER, RECOVERY_NEXT, STAGE_STATUS, StageEntry, GuardedStagePlan,
    _matrix, _valid_recovery_counts, build_stage_plan_from_route,
    plan_stages_read_only, format_stage_report, parse_args, main,
)

REPO = "Toibinguz/epl-premier-league-dataset"
BASE = "1" * 40
TARGET = "2" * 40


def audit(state="NO_REGISTERED_SNAPSHOT", sid=None, *, reuse=1, got_reuse=0,
          candidates=2, got_candidates=0, blockers=()):
    return RecoveryAssessment(state, sid, reuse, got_reuse, candidates,
                              got_candidates, "MANUAL_REVIEW", False, tuple(blockers))


def routed_new():
    p = planned()
    po = ReadOnlyOutcome("PLAN_NEW_REVISION", 365, BASE, TARGET, None, p)
    return RoutingOutcome("PLAN_NEW_REVISION", TARGET,
                          audit(), planner=po)


def routed_recovery(state, *, sid=None, got_reuse=0, got_candidates=0,
                    reuse=1, candidates=2, blockers=()):
    if sid is None and state not in ("PREPARATION_PARTIAL", "PREPARED_READY"):
        sid = 366
    return RoutingOutcome("RECOVERY_REVIEW_REQUIRED", TARGET,
                          audit(state, sid, reuse=reuse, got_reuse=got_reuse,
                                candidates=candidates, got_candidates=got_candidates,
                                blockers=blockers))


def routed_skip(decision="SKIP_ALREADY_CURRENT", state="COMPLETED", sid=365):
    return RoutingOutcome(decision, TARGET, audit(state, sid))


def routed_block(decision="BLOCKED_RECOVERY_REVIEW", *, blockers=("checkpoint corrupted",)):
    return RoutingOutcome(decision, TARGET, audit("BLOCKED_REVIEW", 366, blockers=blockers),
                          explanation="Manual review required")


def make_plan(route):
    return build_stage_plan_from_route(repo_id=REPO, target_revision=TARGET, routed=route)


def entry(p, name):
    return p.stages[STAGE_ORDER.index(name)]


class DefinitionsTests(unittest.TestCase):
    def test_exact_stage_order(self):
        self.assertEqual(len(STAGE_ORDER), 11)
        self.assertEqual(STAGE_ORDER[0], "ROUTE")
        self.assertEqual(STAGE_ORDER[-1], "FINALIZATION")
        self.assertEqual(len(STAGE_ORDER), len(set(STAGE_ORDER)))
    def test_status_vocabulary(self):
        self.assertEqual(STAGE_STATUS, {"READY", "WAITING", "COMPLETED", "BLOCKED", "SKIPPED"})
    def test_stage_entry_invalid_name(self):
        with self.assertRaises(ValueError): StageEntry("COMMIT_ALL", "READY")
    def test_stage_entry_invalid_status(self):
        with self.assertRaises(ValueError): StageEntry("ROUTE", "APPROVED")
    def test_matrix_route_completed(self):
        p = _matrix(route_status="COMPLETED", ready="PLANNED")
        self.assertEqual(p[0].status, "COMPLETED")
        self.assertEqual(p[1].status, "READY")
        self.assertTrue(all(e.status == "WAITING" for e in p[2:]))
    def test_matrix_completed_through(self):
        p = _matrix(route_status="COMPLETED", ready="CANDIDATE_INGESTION",
                    completed_through="REUSE_LINKING")
        self.assertTrue(all(e.status == "COMPLETED" for e in p[:8]))
        self.assertEqual(p[8].status, "READY")
    def test_invalid_matrix(self):
        for kw in ({"route_status":"DONE"}, {"route_status":"COMPLETED","ready":"UNKNOWN"},
                   {"route_status":"COMPLETED","completed_through":"BAD"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): _matrix(**kw)
    def test_plan_rejects_write_authorization(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_new()), write_authorized=True)
    def test_plan_rejects_missing_stage(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_new()), stages=())
    def test_plan_rejects_duplicate_stage(self):
        p=make_plan(routed_new())
        with self.assertRaises(ValueError): replace(p, stages=p.stages[:-1] + (p.stages[-2],))
    def test_plan_rejects_next_stage_not_ready(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_new()),next_review_stage="FINALIZATION")
    def test_plan_rejects_unknown_next_stage(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_new()),next_review_stage="DO_SOMETHING")
    def test_plan_rejects_invalid_repo(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_new()),repo_id="   ")
    def test_plan_rejects_invalid_revision(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_new()),target_revision="main")
    def test_preview_cannot_contain_write_command(self):
        p=make_plan(routed_new())
        for flag in ("--apply", "--commit", "--promote", "--download", "--run-all"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                replace(p, read_only_previews=(("python", "-m", "scripts.tools.bad", flag,"--plan"),))
    def test_preview_only_new_plan(self):
        with self.assertRaises(ValueError): replace(make_plan(routed_skip()),read_only_previews=(('python','-m','x','--plan'),))


class NewRevisionTests(unittest.TestCase):
    def test_initial_plan_state(self):
        p = make_plan(routed_new())
        self.assertEqual(p.decision, "PLAN_NEW_REVISION")
        self.assertEqual(p.recovery_state, "NO_REGISTERED_SNAPSHOT")
        self.assertIsNone(p.target_snapshot_id)
        self.assertEqual(p.next_review_stage,"PLANNED")
        self.assertEqual(entry(p,"ROUTE").status,"COMPLETED")
        self.assertEqual(entry(p,"PLANNED").status,"READY")
        self.assertTrue(all(x.status == "WAITING" for x in p.stages[2:]))
        self.assertFalse(p.write_authorized)
    def test_pinned_hash_from_actual_context(self):
        self.assertEqual(make_plan(routed_new()).planned_context_sha256,checkpoint_digest(planned()))
    def test_only_two_readonly_commands(self):
        cmds = make_plan(routed_new()).read_only_previews
        self.assertEqual(len(cmds),2)
        self.assertIn('scripts.tools.prepare_incremental_manifest',cmds[0])
        self.assertIn('scripts.tools.download_incremental_candidates',cmds[1])
        for cmd in cmds:
            self.assertEqual(cmd[-1],'--plan')
            self.assertNotIn('--commit',cmd)
            self.assertNotIn('--apply',cmd)
    def test_unregistered_plan_cannot_come_from_recovery(self):
        b=routed_new()
        with self.assertRaises(ValueError): make_plan(replace(b,recovery=audit("PREPARATION_PARTIAL")))
    def test_plan_target_id_must_be_absent(self):
        with self.assertRaises(RuntimeError): make_plan(replace(routed_new(),recovery=audit(sid=366)))
    def test_context_identity_drift(self):
        p=routed_new()
        wrong_ctx=replace(p.planner.planned,repo_id="other/repo")
        wrong_po=replace(p.planner,planned=wrong_ctx)
        with self.assertRaises(RuntimeError): make_plan(replace(p,planner=wrong_po))
    def test_absence_with_blockers_not_plannable(self):
        with self.assertRaises(RuntimeError): make_plan(replace(routed_new(),recovery=audit(blockers=('stale target',))))
    def test_mismatched_requested_identity(self):
        with self.assertRaises(RuntimeError):
            build_stage_plan_from_route(repo_id=REPO,target_revision="3"*40,routed=routed_new())
    def test_invalid_routing_type(self):
        with self.assertRaises(TypeError): build_stage_plan_from_route(repo_id=REPO,target_revision=TARGET,routed=None)
    def test_no_writer_invoked_when_planning(self):
        with mock.patch('subprocess.run',side_effect=AssertionError('NO subprocess')):
            with mock.patch('scripts.tools.incremental_execution_context.publish_checkpoint',
                            side_effect=AssertionError('NO checkpoint')):
                p=make_plan(routed_new())
        self.assertEqual(p.next_review_stage,'PLANNED')


class RecoveryStateTests(unittest.TestCase):
    def test_recovery_states_complete_coverage(self):
        self.assertEqual(set(RECOVERY_NEXT),{
            'PREPARATION_PARTIAL','PREPARED_READY','REGISTERED_PENDING_REUSE',
            'REUSE_COMPLETE','CANDIDATES_PARTIAL','CANDIDATES_COMPLETE',
            'MANIFEST_REGISTERED'})
    def test_recovery_matrix_for_all_states(self):
        scenarios = {
            'PREPARATION_PARTIAL':(None,0,0,'MANIFEST_PREPARATION'),
            'PREPARED_READY':(None,0,0,'SNAPSHOT_REGISTRATION'),
            'REGISTERED_PENDING_REUSE':(366,0,0,'REUSE_LINKING'),
            'REUSE_COMPLETE':(366,1,0,'CANDIDATE_INGESTION'),
            'CANDIDATES_PARTIAL':(366,1,1,'CANDIDATE_INGESTION'),
            'CANDIDATES_COMPLETE':(366,1,2,'MANIFEST_REGISTRATION'),
            'MANIFEST_REGISTERED':(366,1,2,'FINALIZATION'),
        }
        for state,(sid,reuse,candidates,nxt) in scenarios.items():
            with self.subTest(state=state):
                route=routed_recovery(state,sid=sid,got_reuse=reuse,got_candidates=candidates)
                p=make_plan(route)
                self.assertEqual(p.next_review_stage,nxt)
                self.assertEqual(entry(p,nxt).status,'READY')
                self.assertEqual(entry(p,'ROUTE').status,'COMPLETED')
                self.assertFalse(p.read_only_previews)
                self.assertFalse(p.write_authorized)
                pos=STAGE_ORDER.index(nxt)
                self.assertTrue(all(e.status=='COMPLETED' for e in p.stages[1:pos]))
                self.assertTrue(all(e.status=='WAITING' for e in p.stages[pos+1:]))
    def test_preparation_partial_not_claim_manifest_completed(self):
        p=make_plan(routed_recovery('PREPARATION_PARTIAL'))
        self.assertEqual(entry(p,'MANIFEST_PREPARATION').status,'READY')
        self.assertIn('re-audit',entry(p,'MANIFEST_PREPARATION').explanation)
        self.assertEqual(entry(p,'ARCHIVE_PROMOTION').status,'WAITING')
    def test_prepared_ready_has_no_snapshot(self):
        p=make_plan(routed_recovery('PREPARED_READY'))
        self.assertIsNone(p.target_snapshot_id)
        self.assertEqual(entry(p,'PREPARED').status,'COMPLETED')
        self.assertEqual(entry(p,'SNAPSHOT_REGISTRATION').status,'READY')
    def test_partial_candidates_do_not_complete_stage(self):
        p=make_plan(routed_recovery('CANDIDATES_PARTIAL',got_reuse=1,got_candidates=1))
        self.assertEqual(entry(p,'CANDIDATE_INGESTION').status,'READY')
        self.assertIn('missing files',entry(p,'CANDIDATE_INGESTION').explanation)
        self.assertEqual(entry(p,'MANIFEST_REGISTRATION').status,'WAITING')
    def test_recovery_requires_registered_snapshot_for_db_stages(self):
        with self.assertRaises(RuntimeError): make_plan(routed_recovery('REUSE_COMPLETE',sid=0,got_reuse=1))
    def test_missing_snapshot_for_registered_stage(self):
        r=routed_recovery('REUSE_COMPLETE',got_reuse=1)
        with self.assertRaises(RuntimeError):make_plan(replace(r,recovery=replace(r.recovery,target_snapshot_id=None)))
    def test_unregistered_prep_has_db_counts(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('PREPARED_READY',got_reuse=1))
    def test_pending_reuse_must_be_incomplete(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('REGISTERED_PENDING_REUSE',got_reuse=1))
    def test_pending_reuse_must_not_have_candidates(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('REGISTERED_PENDING_REUSE',got_candidates=1))
    def test_reuse_complete_requires_full_reuse(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('REUSE_COMPLETE',got_reuse=0))
    def test_reuse_complete_candidates_zero(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('REUSE_COMPLETE',got_reuse=1,got_candidates=1))
    def test_candidates_partial_requires_positive_partial(self):
        for x in (0,2):
            with self.subTest(candidates=x), self.assertRaises(RuntimeError):
                make_plan(routed_recovery('CANDIDATES_PARTIAL',got_reuse=1,got_candidates=x))
    def test_candidates_complete_must_match_expected(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('CANDIDATES_COMPLETE',got_reuse=1,got_candidates=1))
    def test_manifest_registered_must_have_all_candidates(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('MANIFEST_REGISTERED',got_reuse=1,got_candidates=1))
    def test_verified_more_than_expected(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('REUSE_COMPLETE',got_reuse=2))
    def test_negative_recovery_count(self):
        with self.assertRaises(RuntimeError):make_plan(routed_recovery('REUSE_COMPLETE',got_reuse=-1))
    def test_boolean_recovery_count(self):
        r=routed_recovery('REUSE_COMPLETE',got_reuse=1)
        with self.assertRaises(RuntimeError):make_plan(replace(r,recovery=replace(r.recovery,verified_reuse=True)))
    def test_no_writes_from_recovery_path(self):
        r=routed_recovery('MANIFEST_REGISTERED',got_reuse=1,got_candidates=2)
        with mock.patch('subprocess.run',side_effect=AssertionError):
            self.assertEqual(make_plan(r).next_review_stage,'FINALIZATION')


class SkipAndBlockTests(unittest.TestCase):
    def test_completed_current_downstream_not_reaudited(self):
        p=make_plan(routed_skip())
        self.assertEqual(entry(p,'ROUTE').status,'COMPLETED')
        self.assertTrue(all(x.status=='SKIPPED' for x in p.stages[1:]))
        self.assertIsNone(p.next_review_stage)
        self.assertIn('not re-audited',format_stage_report(p))
    def test_completed_historical(self):
        p=make_plan(routed_skip('SKIP_ALREADY_PROCESSED'))
        self.assertEqual(p.decision,'SKIP_ALREADY_PROCESSED')
    def test_no_raw_change(self):
        p=make_plan(routed_skip('SKIP_NO_RAW_CHANGES','NO_REGISTERED_SNAPSHOT',None))
        self.assertTrue(all(x.status=='SKIPPED' for x in p.stages[1:]))
    def test_unrecognized_skip_reason_blocked(self):
        with self.assertRaises(RuntimeError): make_plan(routed_skip('SKIP_NO_RAW_CHANGES','COMPLETED',365))
    def test_unregistered_cannot_skip_current(self):
        with self.assertRaises(RuntimeError):make_plan(routed_skip('SKIP_ALREADY_CURRENT','NO_REGISTERED_SNAPSHOT',None))
    def test_skip_with_blockers_rejected(self):
        with self.assertRaises(RuntimeError):make_plan(routed_skip('SKIP_ALREADY_CURRENT','COMPLETED',365)) if False else make_plan(
            replace(routed_skip(),recovery=audit('COMPLETED',365,blockers=('mismatch',))))
    def test_blocked_makes_every_stage_blocked(self):
        p=make_plan(routed_block())
        self.assertTrue(all(x.status=='BLOCKED' for x in p.stages))
        self.assertIsNone(p.next_review_stage)
        self.assertEqual(p.blockers,('checkpoint corrupted','Manual review required'))
        self.assertNotIn('command previews',format_stage_report(p))
    def test_blocked_explicit_baseline(self):
        r=RoutingOutcome('BLOCKED_EXPLICIT_BASELINE_RECOVERY',TARGET,
                         audit('PREPARED_READY',None),explanation='Do not change baseline')
        self.assertEqual(make_plan(r).decision,'BLOCKED_EXPLICIT_BASELINE_RECOVERY')
    def test_unknown_decision_fails_closed(self):
        r=RoutingOutcome('SOMETHING_ELSE',TARGET,audit())
        with self.assertRaisesRegex(RuntimeError,'Unrecognized'):
            make_plan(r)
    def test_unknown_recovery_state_under_recovery_review_fails_closed(self):
        r=RoutingOutcome('RECOVERY_REVIEW_REQUIRED',TARGET,audit('UNDEFINED',366))
        with self.assertRaisesRegex(RuntimeError,'unsupported'):
            make_plan(r)
    def test_recovery_blockers_not_ignored(self):
        r=routed_recovery('REUSE_COMPLETE',got_reuse=1,blockers=('missing archive',))
        with self.assertRaises(RuntimeError):make_plan(r)


class ApiAndCliTests(unittest.TestCase):
    def setUp(self):
        err=mock.patch('sys.stderr',new=StringIO())
        err.start();self.addCleanup(err.stop)
    def test_plan_calls_router_exactly_once(self):
        route=mock.Mock(return_value=routed_new())
        p=plan_stages_read_only(repo_id=REPO,target_revision=TARGET,router=route)
        self.assertEqual(p.decision,'PLAN_NEW_REVISION')
        route.assert_called_once_with(repo_id=REPO,target_revision=TARGET,
                                      baseline_snapshot=None,readers=None)
    def test_plan_passes_explicit_baseline(self):
        route=mock.Mock(return_value=routed_block())
        plan_stages_read_only(repo_id=REPO,target_revision=TARGET,baseline_snapshot=365,
                              readers='mock',router=route)
        self.assertEqual(route.call_args.kwargs['baseline_snapshot'],365)
    def test_prevalidation_no_router_calls(self):
        for vals in ({'repo_id':'','target_revision':TARGET},
                     {'repo_id':REPO,'target_revision':'main'},
                     {'repo_id':REPO,'target_revision':TARGET,'baseline_snapshot':0},
                     {'repo_id':REPO,'target_revision':TARGET,'baseline_snapshot':True}):
            r=mock.Mock(side_effect=AssertionError('router called'))
            with self.subTest(vals=vals),self.assertRaises(ValueError):
                plan_stages_read_only(router=r,**vals)
            r.assert_not_called()
    def test_parser_requires_plan(self):
        with self.assertRaises(SystemExit):parse_args(['--target-revision',TARGET])
    def test_parser_rejects_write_options(self):
        for flag in ('--commit','--apply','--stage','--resume','--download','--run-all',
                     '--approve-stage','--publish','--force'):
            with self.subTest(flag=flag),self.assertRaises(SystemExit):
                parse_args(['--target-revision',TARGET,'--plan',flag])
    def test_parser_rejects_bad_revision(self):
        for bad in ('main','3'*41,'A'*40,''):
            with self.subTest(bad=bad),self.assertRaises(SystemExit):
                parse_args(['--target-revision',bad,'--plan'])
    def test_parser_rejects_blank_repo(self):
        with self.assertRaises(SystemExit):parse_args(['--target-revision',TARGET,'--plan','--repo-id',' '])
    def test_parser_rejects_baseline_zero(self):
        with self.assertRaises(SystemExit):parse_args(['--target-revision',TARGET,'--plan','--baseline-snapshot','0'])
    def test_parser_accepts_valid(self):
        args=parse_args(['--target-revision',TARGET,'--plan'])
        self.assertEqual(args.target_revision,TARGET)
        self.assertEqual(args.repo_id,REPO)
    def test_parser_help_no_import_db(self):
        with mock.patch('sys.stdout',new=StringIO()),self.assertRaises(SystemExit) as cm:
            parse_args(['--help'])
        self.assertEqual(cm.exception.code,0)
    def test_report_type(self):
        with self.assertRaises(TypeError):format_stage_report(None)
    def test_report_includes_no_write_message(self):
        report=format_stage_report(make_plan(routed_new()))
        self.assertIn('Write authorized: NO',report)
        self.assertIn('Subprocesses: NONE',report)
        self.assertIn('NOT WRITTEN',report)
        self.assertIn('MANUAL READ-ONLY REVIEW',report)
    def test_main_new_route(self):
        output=[]
        rc=main(['--target-revision',TARGET,'--plan'],
                router=lambda **kwargs:routed_new(),output=output.append)
        self.assertEqual(rc,0)
        self.assertIn('PLAN_NEW_REVISION',output[0])
    def test_main_blocked_route(self):
        output=[]
        rc=main(['--target-revision',TARGET,'--plan'],
                router=lambda **kwargs:routed_block(),output=output.append)
        self.assertEqual(rc,2)
        self.assertIn('BLOCKED',output[0])
    def test_main_corruption_fails_closed(self):
        output=[]
        rc=main(['--target-revision',TARGET,'--plan'],
                router=lambda **kwargs:None,output=output.append)
        self.assertEqual(rc,2)
        self.assertIn('BLOCKED (NO WRITES)',output[0])
    def test_no_file_effect_in_pure_planner(self):
        before=set((Path(__file__).resolve().parents[1] / 'scripts' / 'tools').glob('*.json'))
        make_plan(routed_new())
        after=set((Path(__file__).resolve().parents[1] / 'scripts' / 'tools').glob('*.json'))
        self.assertEqual(before,after)
    def test_no_exec_related_imports(self):
        source=(Path(__file__).resolve().parents[1] / 'scripts' / 'tools' / 'incremental_guarded_stage_planner.py').read_text(encoding='utf-8')
        parsed=ast.parse(source)
        imported=[]
        for node in ast.walk(parsed):
            if isinstance(node,ast.Import):
                imported.extend(alias.name for alias in node.names)
            if isinstance(node,ast.ImportFrom) and node.module:
                imported.append(node.module)
        for denied in ('subprocess','psycopg','huggingface_hub','requests','shutil'):
            self.assertNotIn(denied,imported)

if __name__ == '__main__':
    unittest.main()
