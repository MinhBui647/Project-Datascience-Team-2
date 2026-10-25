"""M4.CSV.3b.4f: offline guarded snapshot registration / REUSE CSV previews."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import sys

from scripts.tools import register_incremental_snapshot as reg
from scripts.tools import populate_incremental_reuse as reuse

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = 'f' * 40
FINGERPRINT = 'a' * 64


def make_registration():
    return reg.Registration(REPO, 365, REV, FINGERPRINT, 1, 2, 1, 0, 1, 20, 2)


def registration_cli(*extra):
    return [
        '--baseline-snapshot', '365', '--target-revision', REV,
        '--expected-fingerprint', FINGERPRINT, '--repo-id', REPO,
        '--expected-baseline-files', '1', '--expected-full-files', '2',
        '--expected-raw-files', '1', '--expected-reuse', '0',
        '--expected-files', '1', '--expected-bytes', '20',
        '--expected-records', '2', *extra,
    ]


def reuse_cli(*extra):
    return [
        '--baseline-snapshot', '365', '--target-snapshot-id', '366',
        '--target-revision', REV, '--expected-fingerprint', FINGERPRINT,
        '--repo-id', REPO, '--expected-baseline-files', '1',
        '--expected-full-files', '2', '--expected-raw-files', '1',
        '--expected-reuse', '0', '--expected-files', '1',
        '--expected-bytes', '20', '--expected-records', '2',
        '--expected-baseline-records', '10', '--expected-reused-records', '0',
        *extra,
    ]


class SnapshotGate(TestCase):
    def test_default_mode_unchanged(self):
        reg.check_csv_registration_mode(allow_csv=False, plan=False)
        reg.check_csv_registration_mode(allow_csv=False, plan=True)

    def test_csv_registration_write_forbidden(self):
        with self.assertRaisesRegex(ValueError, 'read-only'):
            reg.check_csv_registration_mode(allow_csv=True, plan=False)

    def test_csv_plan_allowed(self):
        reg.check_csv_registration_mode(allow_csv=True, plan=True)

    def test_wrong_flag_types_rejected(self):
        for a, b in [(1, True), (True, 1), (None, True)]:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                reg.check_csv_registration_mode(allow_csv=a, plan=b)

    def test_default_preflight_argv_unchanged(self):
        command = reg.preflight_command(make_registration())
        self.assertEqual(command[-1], '--plan')
        self.assertNotIn('--allow-csv-preflight-only', command)

    def test_csv_preflight_argv_includes_opt_in(self):
        command = reg.preflight_command(make_registration(), allow_csv_preflight_only=True)
        self.assertEqual(command[-2:], ['--allow-csv-preflight-only', '--plan'])
        self.assertEqual(command.count('--allow-csv-preflight-only'), 1)

    def test_invalid_preflight_switch(self):
        with self.assertRaises(ValueError):
            reg.preflight_command(make_registration(), allow_csv_preflight_only=1)

    def test_invalid_cli_register_csv_rejected_before_preflight(self):
        args = registration_cli('--register', '--confirm-snapshot-only',
                                '--allow-csv-registration-plan-only')
        with patch.object(sys, 'argv', ['register_incremental_snapshot.py', *args]), \
             patch.object(reg, 'run_preflight') as pre, \
             redirect_stderr(StringIO()), self.assertRaises(SystemExit) as ctx:
            reg.main()
        self.assertEqual(ctx.exception.code, 2)
        pre.assert_not_called()

    def test_csv_plan_forwards_switch_without_register(self):
        args = registration_cli('--plan', '--allow-csv-registration-plan-only')
        with patch.object(sys, 'argv', ['register_incremental_snapshot.py', *args]), \
             patch.object(reg, 'run_preflight') as pre, \
             patch.object(reg, 'register_in_transaction') as writer, \
             redirect_stdout(StringIO()):
            outcome = reg.main()
        self.assertEqual(outcome, 0)
        self.assertTrue(pre.call_args.kwargs['allow_csv_preflight_only'])
        writer.assert_not_called()

    def test_legacy_plan_preserves_preflight_call_shape(self):
        args = registration_cli('--plan')
        with patch.object(sys, 'argv', ['register_incremental_snapshot.py', *args]), \
             patch.object(reg, 'run_preflight') as pre, \
             redirect_stdout(StringIO()):
            outcome = reg.main()
        self.assertEqual(outcome, 0)
        self.assertEqual(pre.call_args.kwargs, {'cache_dir': 'reports/m4_6_content_cache'})


class ReuseGate(TestCase):
    def test_default_mode_unchanged(self):
        reuse.check_csv_reuse_mode(allow_csv=False, plan=True)
        reuse.check_csv_reuse_mode(allow_csv=False, plan=False)

    def test_csv_link_forbidden(self):
        with self.assertRaisesRegex(ValueError, 'read-only'):
            reuse.check_csv_reuse_mode(allow_csv=True, plan=False)

    def test_csv_preview_allowed(self):
        reuse.check_csv_reuse_mode(allow_csv=True, plan=True)

    def test_wrong_flag_types_rejected(self):
        for a, b in [(1, True), (True, 1), (None, True)]:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                reuse.check_csv_reuse_mode(allow_csv=a, plan=b)

    def test_csv_link_cli_blocks_before_db_and_hf(self):
        args = reuse_cli('--link', '--confirm-reuse-only', '--allow-csv-reuse-plan-only')
        with patch.object(sys, 'argv', ['populate_incremental_reuse.py', *args]), \
             patch.object(reuse, 'load_target') as hf, \
             redirect_stderr(StringIO()), self.assertRaises(SystemExit) as ctx:
            reuse.main()
        self.assertEqual(ctx.exception.code, 2)
        hf.assert_not_called()

    def test_csv_verify_plan_forwards_opt_in(self):
        r = make_registration()
        req = reuse.ReuseRequest(r, 366, 10, 0)
        plan = SimpleNamespace(fingerprint=FINGERPRINT, target_raw_count=1,
            candidate_bytes=20, baseline_records=10, reused_records=0,
            count=lambda cat: {'NEW':1, 'CHANGED':0, 'UNCHANGED':0,
                               'REMOVED':0, 'REVIEW':0}.get(cat, 0))
        with patch.object(reuse, 'assemble_plan', return_value=plan) as assembler, \
             patch.object(reuse, 'build_reuse_links', return_value=[]):
            result, links = reuse.verify_plan(req, {}, {"raw_data/a.csv":object()}, 1, {}, set(),
                                              allow_csv_planning=True)
        self.assertIs(result, plan)
        self.assertEqual(links, [])
        self.assertEqual(assembler.call_args.kwargs, {'allow_csv_planning': True})
