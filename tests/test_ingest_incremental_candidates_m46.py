"""M4.6.3d.3 candidate coordinator offline unit tests; no DB/HF or file writes."""
from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import Mock, patch
import sys
import unittest

from scripts.tools import ingest_incremental_candidates as m
from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.populate_incremental_reuse import ReuseRequest

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = 'f' * 40
FP = 'a' * 64
PATH = '2026_2027_new/test.json'


def req():
    return ReuseRequest(Registration(REPO, 365, REV, FP, 1, 3, 2, 1, 1, 100, 1), 366, 12, 12)


def cli(mode='--plan'):
    return [
        '--baseline-snapshot', '365', '--target-snapshot-id', '366',
        '--target-revision', REV, '--expected-fingerprint', FP,
        '--expected-baseline-files', '1', '--expected-full-files', '3',
        '--expected-raw-files', '2', '--expected-reuse', '1',
        '--expected-files', '1', '--expected-bytes', '100',
        '--expected-records', '1', '--expected-baseline-records', '12',
        '--expected-reused-records', '12', mode,
    ]


class FakeCursor:
    def __init__(self, lock=True):
        self.lock = lock
        self.sql = []
        self.fetch = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None): self.sql.append((sql, params))
    def fetchone(self):
        if self.fetch: return self.fetch.pop(0)
        return (self.lock,)


class FakeConn:
    autocommit = False
    def __init__(self, cursor):
        self.cur = cursor
        self.committed = False
        self.rolled_back = False
    def __enter__(self): return self
    def __exit__(self, typ, *_):
        self.committed, self.rolled_back = typ is None, typ is not None
        return False
    def cursor(self): return self.cur


def bundle():
    version = NS(records=(("/0", "json_object", {"k":1}),))
    return m.CandidateBundle(req(), 'e'*40, {'raw_data/test.json': object()},
             {PATH: object()}, frozenset({'fotmob'}), NS(fingerprint=FP),
             {PATH: version}, (), (), (), 1)


class ParserTests(unittest.TestCase):
    def test_plan_parses(self):
        a = m.make_parser().parse_args(cli())
        m.check_mode(a, m.make_parser())
        self.assertEqual(m.build_request(a), req())
    def test_confirmed_apply(self):
        a = m.make_parser().parse_args(cli('--apply') + ['--confirm-candidate-write'])
        m.check_mode(a, m.make_parser())
    def test_unconfirmed_apply_blocked(self):
        with self.assertRaises(SystemExit):
            m.check_mode(m.make_parser().parse_args(cli('--apply')),m.make_parser())
    def test_confirmation_for_plan_blocked(self):
        with self.assertRaises(SystemExit):
            m.check_mode(m.make_parser().parse_args(cli()+['--confirm-candidate-write']),m.make_parser())
    def test_missing_args_blocked(self):
        with self.assertRaises(SystemExit): m.make_parser().parse_args(['--plan'])
    def test_mutually_exclusive_modes(self):
        with self.assertRaises(SystemExit): m.make_parser().parse_args(cli()+['--apply'])
    def test_invalid_req_before_db(self):
        a = m.make_parser().parse_args(cli())
        a.target_revision = 'main'
        with self.assertRaises(RuntimeError): m.build_request(a)


class PlannerGateTests(unittest.TestCase):
    def test_command_uses_dynamic_values(self):
        cmd = m.planner_command(req(), 'cache-folder')
        self.assertEqual(cmd[-1], '--plan')
        self.assertIn('--target-snapshot-id',cmd)
        self.assertEqual(cmd[cmd.index('--target-snapshot-id')+1], '366')
        self.assertEqual(cmd[cmd.index('--expected-reused-records')+1], '12')
        self.assertNotIn('--apply',cmd)
    def test_pass_marker_accepted(self):
        with patch.object(m.subprocess, 'run',return_value=NS(returncode=0, stdout=m.PLAN_MARKER+'\n', stderr='')):
            m.run_existing_plan(req(),'unused')
    def test_nonzero_exit_fails(self):
        with patch.object(m.subprocess, 'run',return_value=NS(returncode=2, stdout=m.PLAN_MARKER+'\n', stderr='fail')):
            with self.assertRaises(RuntimeError): m.run_existing_plan(req(),'unused')
    def test_missing_marker_fails(self):
        with patch.object(m.subprocess, 'run',return_value=NS(returncode=0, stdout='other PASS\n', stderr='')):
            with self.assertRaises(RuntimeError): m.run_existing_plan(req(),'unused')
    def test_plan_does_not_call_writer(self):
        args=cli()
        with patch.object(sys, 'argv',['candidate.py',*args]),\
             patch.object(m,'run_existing_plan') as plan, \
             patch.object(m,'build_verified_bundle') as build:
            self.assertEqual(m.main(),0)
        plan.assert_called_once()
        build.assert_not_called()
    def test_plan_preflight_failure_blocks_apply(self):
        args=cli('--apply')+['--confirm-candidate-write']
        with patch.object(sys,'argv',['candidate.py',*args]), \
             patch.object(m,'run_existing_plan',side_effect=RuntimeError('failed')), \
             patch.object(m,'build_verified_bundle') as build:
            with self.assertRaises(RuntimeError): m.main()
        build.assert_not_called()


class TransactionTests(unittest.TestCase):
    def patched_writer(self, fake_conn, result):
        psycopg=ModuleType('psycopg')
        psycopg.connect=Mock(return_value=fake_conn)
        core=ModuleType('scripts.tools.incremental_writer_core')
        core.write_prepared_version=result
        stack=ExitStack()
        stack.enter_context(patch.dict(sys.modules,{'psycopg':psycopg,
                   'scripts.tools.incremental_writer_core':core}))
        stack.enter_context(patch.object(m,'db_parameters',return_value={'dbname':'epl_raw_storage_v2'}))
        return stack
    def run_case(self, statuses, expected_result, write_action, *, lock=True):
        conn=FakeConn(FakeCursor(lock=lock))
        writer=Mock(return_value=write_action)
        with self.patched_writer(conn,writer), patch.object(m,'audit_locked_state',side_effect=statuses):
            out=m.ingest_single(PATH,bundle())
        self.assertEqual(out,expected_result)
        self.assertTrue(conn.committed)
        return writer,conn
    def test_new_candidate_commits(self):
        s=[{PATH:NS(action='READY_INSERT_NEW_VERSION',file_id=None)},
           {PATH:NS(action='LINKED_VERIFIED',file_id=23)}]
        w,conn=self.run_case(s,('INSERTED_NEW',1),('INSERTED_NEW',23,1))
        w.assert_called_once()
        sql=' '.join(q for q,_ in conn.cur.sql)
        self.assertIn('pg_try_advisory_xact_lock',sql)
    def test_existing_version_relinks(self):
        s=[{PATH:NS(action='VERSION_EXISTS_LINK_PENDING',file_id=23)},
           {PATH:NS(action='LINKED_VERIFIED',file_id=23)}]
        w,_=self.run_case(s,('REUSED_EXISTING',0),('REUSED_EXISTING',23,0))
        w.assert_called_once()
    def test_already_linked_skips_writer(self):
        conn=FakeConn(FakeCursor())
        w=Mock()
        with self.patched_writer(conn,w),patch.object(m,'audit_locked_state',return_value={PATH:NS(action='LINKED_VERIFIED',file_id=23)}):
            self.assertEqual(m.ingest_single(PATH,bundle()),('ALREADY_LINKED',0))
        w.assert_not_called()
        self.assertTrue(conn.committed)
    def test_writer_error_rolls_back(self):
        conn=FakeConn(FakeCursor())
        w=Mock(side_effect=RuntimeError('simulated fail'))
        with self.patched_writer(conn,w),patch.object(m,'audit_locked_state',return_value={PATH:NS(action='READY_INSERT_NEW_VERSION',file_id=None)}):
            with self.assertRaisesRegex(RuntimeError,'simulated fail'): m.ingest_single(PATH,bundle())
        self.assertTrue(conn.rolled_back)
    def test_wrong_writer_outcome_rolls_back(self):
        conn=FakeConn(FakeCursor())
        w=Mock(return_value=('REUSED_EXISTING',23,0))
        with self.patched_writer(conn,w),patch.object(m,'audit_locked_state',return_value={PATH:NS(action='READY_INSERT_NEW_VERSION',file_id=None)}):
            with self.assertRaisesRegex(RuntimeError,'outcome'): m.ingest_single(PATH,bundle())
        self.assertTrue(conn.rolled_back)
    def test_postwrite_mismatch_rolls_back(self):
        conn=FakeConn(FakeCursor())
        w=Mock(return_value=('INSERTED_NEW',23,1))
        with self.patched_writer(conn,w),patch.object(m,'audit_locked_state',side_effect=[{PATH:NS(action='READY_INSERT_NEW_VERSION',file_id=None)},
                          {PATH:NS(action='LINKED_VERIFIED',file_id=24)}]):
            with self.assertRaisesRegex(RuntimeError,'postwrite'): m.ingest_single(PATH,bundle())
        self.assertTrue(conn.rolled_back)
    def test_repo_lock_fails_closed(self):
        conn=FakeConn(FakeCursor(lock=False))
        w=Mock()
        with self.patched_writer(conn,w),patch.object(m,'audit_locked_state') as audit:
            with self.assertRaisesRegex(RuntimeError,'repository lock'): m.ingest_single(PATH,bundle())
        self.assertTrue(conn.rolled_back)
        w.assert_not_called()
        audit.assert_not_called()
    def test_invalid_candidate_path(self):
        with self.assertRaisesRegex(RuntimeError,'not present'):m.ingest_single('raw_data/other.json',bundle())


class AuditTests(unittest.TestCase):
    def test_locked_state_row_guard_blocks_completed(self):
        cur=FakeCursor()
        cur.fetch=[(REPO,REV,'completed',3,2,None,None)]
        with self.assertRaisesRegex(RuntimeError,'Target snapshot'):
            m.audit_locked_state(cur,bundle())
    def test_locked_state_row_guard_allows_correct(self):
        cur=FakeCursor()
        cur.fetch=[(REPO,REV,'in_progress',3,2,None,None)]
        with patch.object(m,'audit_state',return_value={PATH:NS(action='READY_INSERT_NEW_VERSION')}):
            res=m.audit_locked_state(cur,bundle())
        self.assertIn(PATH,res)
        self.assertIn('FOR UPDATE',cur.sql[0][0])
    def test_audit_state_baseline_revision_guard(self):
        with patch.object(m,'inspect_db',return_value=('d'*40,{},NS())),\
             patch.object(m,'verify_plan') as verify:
            with self.assertRaisesRegex(RuntimeError,'context invalid'):
                m.audit_state(FakeCursor(),bundle())
            verify.assert_not_called()
    def test_audit_state_plan_drift(self):
        b=bundle()
        with patch.object(m,'inspect_db',return_value=(b.baseline_revision,{},NS())),\
             patch.object(m,'verify_plan',return_value=(NS(fingerprint='incorrect'),())),\
             patch.object(m,'validate_candidate_plan') as validate:
            with self.assertRaisesRegex(RuntimeError,'plan drift'):m.audit_state(FakeCursor(),b)
            validate.assert_not_called()
    def test_final_audit_incomplete_blocks(self):
        fake_psycopg=ModuleType('psycopg')
        fake_psycopg.connect=Mock(return_value=FakeConn(FakeCursor()))
        with patch.dict(sys.modules,{'psycopg':fake_psycopg}),\
             patch.object(m,'read_only_args', return_value={'dbname':'epl_raw_storage_v2'}),\
             patch.object(m,'audit_state',return_value={PATH:NS(action='READY_INSERT_NEW_VERSION')}):
            with self.assertRaisesRegex(RuntimeError,'incomplete'):m.final_read_only_audit(bundle())
    def test_final_audit_complete(self):
        fake_psycopg=ModuleType('psycopg')
        fake_psycopg.connect=Mock(return_value=FakeConn(FakeCursor()))
        with patch.dict(sys.modules,{'psycopg':fake_psycopg}),\
             patch.object(m,'read_only_args', return_value={'dbname':'epl_raw_storage_v2'}),\
             patch.object(m,'audit_state',return_value={PATH:NS(action='LINKED_VERIFIED')}):
            self.assertEqual(m.final_read_only_audit(bundle()),(1,1))


if __name__ == '__main__':
    unittest.main()
