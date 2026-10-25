"""M4.CSV.3b.4e: CSV candidate writer opt-in is a distinct, guarded CLI permission.

Mock-only: no PostgreSQL network, immutable archive writes, or HF requests.
"""
from __future__ import annotations
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
import sys

from scripts.tools import ingest_incremental_candidates as m
from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.populate_incremental_reuse import ReuseRequest

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "f" * 40
FINGERPRINT = "a" * 64


def cli(mode="--plan"):
    return ["--baseline-snapshot", "365", "--target-snapshot-id", "366",
        "--target-revision", REV, "--expected-fingerprint", FINGERPRINT,
        "--expected-baseline-files", "1", "--expected-full-files", "3",
        "--expected-raw-files", "2", "--expected-reuse", "1",
        "--expected-files", "1", "--expected-bytes", "100",
        "--expected-records", "1", "--expected-baseline-records", "12",
        "--expected-reused-records", "12", mode]


def request():
    return ReuseRequest(Registration(REPO, 365, REV, FINGERPRINT,
                                     1, 3, 2, 1, 1, 100, 1), 366, 12, 12)


class CsvWriteGateTests(unittest.TestCase):
    def parse(self, tokens):
        p = m.make_parser()
        a = p.parse_args(tokens)
        m.check_mode(a, p)
        return a

    def rejects(self, tokens):
        with self.assertRaises(SystemExit):
            self.parse(tokens)

    def test_01_json_plan_unchanged(self):
        a = self.parse(cli())
        self.assertFalse(a.allow_csv_candidate_write)
        self.assertFalse(a.allow_csv_candidate_plan_only)

    def test_02_json_apply_unchanged(self):
        a = self.parse(cli("--apply") + ["--confirm-candidate-write"])
        self.assertTrue(a.apply)
        self.assertFalse(a.allow_csv_candidate_write)

    def test_03_csv_write_requires_apply(self):
        self.rejects(cli() + ["--allow-csv-candidate-write"])

    def test_04_csv_write_requires_confirmation(self):
        self.rejects(cli("--apply") + ["--allow-csv-candidate-write"])

    def test_05_csv_write_forbidden_with_plan(self):
        self.rejects(cli() + ["--allow-csv-candidate-write", "--confirm-candidate-write"])

    def test_06_plan_only_flag_forbidden_with_apply(self):
        self.rejects(cli("--apply") + ["--confirm-candidate-write", "--allow-csv-candidate-plan-only"])

    def test_07_mutually_exclusive_csv_flags(self):
        self.rejects(cli("--apply") + ["--confirm-candidate-write",
                                      "--allow-csv-candidate-plan-only", "--allow-csv-candidate-write"])

    def test_08_csv_write_opt_in_valid(self):
        a = self.parse(cli("--apply") + ["--confirm-candidate-write", "--allow-csv-candidate-write"])
        self.assertTrue(a.allow_csv_candidate_write)

    def test_09_csv_plan_only_valid(self):
        a = self.parse(cli() + ["--allow-csv-candidate-plan-only"])
        self.assertFalse(a.apply)

    def test_10_default_child_is_plan_only(self):
        command = m.planner_command(request(), "reports/cache")
        self.assertEqual(command[-1], "--plan")
        self.assertNotIn("--allow-csv-candidate-plan-only", command)
        self.assertNotIn("--apply", command)

    def test_11_csv_child_is_still_plan_only(self):
        command = m.planner_command(request(), "reports/cache", allow_csv_candidate_plan_only=True)
        self.assertEqual(command[-1], "--plan")
        self.assertIn("--allow-csv-candidate-plan-only", command)
        self.assertNotIn("--apply", command)

    def test_12_candidate_bundle_json_default(self):
        b = m.CandidateBundle(request(), 'e'*40, {}, {}, frozenset(), object(), {}, (), (), (), 1)
        self.assertIs(b.allow_csv, False)

    def test_13_candidate_bundle_wrong_flag_blocked(self):
        with self.assertRaises(ValueError):
            m.CandidateBundle(request(), 'e'*40, {}, {}, frozenset(), object(), {}, (), (), (), 1, allow_csv='yes')

    def test_14_build_bundle_wrong_flag_before_side_effect(self):
        with self.assertRaises(ValueError):
            m.build_verified_bundle(request(), "reports/cache", allow_csv=1)

    def test_15_csv_plan_cannot_execute_writer(self):
        tokens = cli() + ["--allow-csv-candidate-plan-only"]
        with patch.object(sys, 'argv', ['coord.py', *tokens]), \
             patch.object(m, 'run_existing_plan') as planner, \
             patch.object(m, 'build_verified_bundle') as builder, \
             patch.object(m, 'ingest_single') as writer:
            self.assertEqual(m.main(), 0)
        planner.assert_called_once()
        self.assertTrue(planner.call_args.kwargs.get('allow_csv_candidate_plan_only'))
        builder.assert_not_called()
        writer.assert_not_called()

    def _apply_with_mocks(self, tokens):
        prepared = {'2026_2027_new/football_data_raw_2627.csv': object()}
        dummy = NS(request=request(), prepared=prepared)
        with patch.object(sys, 'argv', ['coord.py', *tokens]), \
             patch.object(m, 'run_existing_plan') as planner, \
             patch.object(m, 'build_verified_bundle', return_value=dummy) as builder, \
             patch.object(m, 'setup_error_log'), \
             patch.object(m, 'ingest_single', return_value=('INSERTED_NEW', 1)) as writer, \
             patch.object(m, 'final_read_only_audit', return_value=(1, 1)):
            rc = m.main()
        return rc, planner, builder, writer

    def test_16_csv_apply_dispatches_guarded_bundle(self):
        rc, planner, builder, writer = self._apply_with_mocks(
            cli('--apply') + ['--confirm-candidate-write', '--allow-csv-candidate-write'])
        self.assertEqual(rc, 0)
        self.assertTrue(planner.call_args.kwargs.get('allow_csv_candidate_plan_only'))
        self.assertTrue(builder.call_args.kwargs.get('allow_csv'))
        self.assertEqual(writer.call_count, 1)

    def test_17_json_apply_does_not_opt_into_csv(self):
        rc, planner, builder, writer = self._apply_with_mocks(
            cli('--apply') + ['--confirm-candidate-write'])
        self.assertEqual(rc, 0)
        self.assertFalse(planner.call_args.kwargs.get('allow_csv_candidate_plan_only', False))
        self.assertFalse(builder.call_args.kwargs.get('allow_csv', False))
        self.assertEqual(writer.call_count, 1)

    def test_18_sql_guard_retains_in_progress_requirement(self):
        b = m.CandidateBundle(request(), 'e'*40, {}, {}, frozenset(), object(), {}, (), (), (), 1, allow_csv=True)
        class Cur:
            def execute(self, *a): pass
            def fetchone(self): return (REPO, REV, 'completed', 3, 2, None, None)
        with self.assertRaisesRegex(RuntimeError, 'Target snapshot'):
            m.audit_locked_state(Cur(), b)

if __name__ == '__main__':
    unittest.main()
