"""M4.CSV.3b.4d | CSV candidate read-only contracts; never connect to DB/HF."""
from __future__ import annotations
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch
from scripts.tools import populate_incremental_reuse as reuse
from scripts.tools import plan_incremental_candidates as planner
from scripts.tools import ingest_incremental_candidates as coordinator
from scripts.tools.incremental_writer_core import PreparedCsvVersion
from tests.test_plan_incremental_candidates_m46 import all_args, version, pplan
from tests.test_ingest_incremental_candidates_m46 import req, cli

CSV = "2026_2027_new/football_data_raw_2627.csv"


def candidate_case():
    params = all_args()
    old = params["plan"].actions[-1]
    v = version()
    csv = PreparedCsvVersion(v.repo_id, CSV, v.pinned_revision, v.source_code,
                             v.size_bytes, v.content_sha256, v.git_blob_id,
                             v.lfs_sha256, v.archive_relative_path,
                             (("row:1", "csv_row", {"FTHG": "1"}),))
    actions = tuple(replace(a, path=CSV, file_format="csv") if a.path == old.path else a
                    for a in params["plan"].actions)
    params["plan"] = replace(params["plan"], actions=actions)
    params["prepared"] = {CSV: csv}
    params["actions"] = (NS(path=CSV, source_code=csv.source_code),)
    params["archived"] = (NS(relative_path=CSV, action="ALREADY_ARCHIVED",
                             sha256=csv.content_sha256, byte_count=csv.size_bytes,
                             record_count=1, archive_relative_path=csv.archive_relative_path),)
    params["statuses"] = (planner.CandidateStatus(CSV, None, "READY_INSERT_NEW_VERSION",1,100),)
    return params


class SharedPlanTests(unittest.TestCase):
    def make_plan(self):
        from tests.test_populate_incremental_reuse_m46 import dbreq
        from types import SimpleNamespace as NS
        req = dbreq()
        fake = NS(fingerprint=req.registration.fingerprint,
                  target_raw_count=2, baseline_records=7, reused_records=7,
                  candidate_bytes=10,
                  count=lambda k: {'UNCHANGED':1,'NEW':1,'CHANGED':0,'REMOVED':0,'REVIEW':0}[k])
        return req, fake

    def test_csv_optin_reconstruction(self):
        r, p = self.make_plan()
        with patch.object(reuse,'assemble_plan',return_value=p) as assemble, \
             patch.object(reuse,'build_reuse_links',return_value=(object(),)):
            reuse.verify_plan(r,{}, {'p':1,'q':2},0,{},set(),allow_csv_planning=True)
        self.assertIs(assemble.call_args.kwargs['allow_csv_planning'], True)

    def test_default_reconstruction_does_not_enable_csv(self):
        r, p = self.make_plan()
        with patch.object(reuse,'assemble_plan',return_value=p) as assemble, \
             patch.object(reuse,'build_reuse_links',return_value=(object(),)):
            reuse.verify_plan(r,{}, {'p':1,'q':2},0,{},set())
        self.assertEqual(assemble.call_args.kwargs,{})

    def test_bad_csv_optin_type_blocked(self):
        r, p = self.make_plan()
        with patch.object(reuse,'assemble_plan',return_value=p) as assemble, \
             self.assertRaisesRegex(ValueError,'CSV planning'):
            reuse.verify_plan(r,{}, {'p':1,'q':2},0,{},set(),allow_csv_planning=1)
        assemble.assert_not_called()

class CandidatePlanTests(unittest.TestCase):
    def test_csv_candidate_valid(self):
        self.assertEqual(planner.validate_candidate_plan(**candidate_case()), 13)

    def test_csv_with_json_dto_blocked(self):
        args = candidate_case()
        v = args['prepared'][CSV]
        args['prepared'] = {CSV: version().__class__(v.repo_id,v.relative_path,
             v.pinned_revision,v.source_code,v.size_bytes,v.content_sha256,
             v.git_blob_id,v.lfs_sha256,v.archive_relative_path,v.records)}
        with self.assertRaisesRegex(RuntimeError,'content/DB'):
            planner.validate_candidate_plan(**args)

    def test_wrong_format_blocked(self):
        args = candidate_case()
        args['plan'] = replace(args['plan'], actions=tuple(
            replace(a,file_format='tar.gz') if a.path==CSV else a
            for a in args['plan'].actions))
        with self.assertRaisesRegex(RuntimeError,'content/DB'):
            planner.validate_candidate_plan(**args)

    def test_json_case_still_valid(self):
        self.assertEqual(planner.validate_candidate_plan(**all_args()),13)

    def test_planner_optin_readonly_flag(self):
        a=planner.make_parser().parse_args(cli()[:-1] + ['--allow-csv-candidate-plan-only','--plan'])
        self.assertTrue(a.allow_csv_candidate_plan_only)

    def test_planner_no_write_flags(self):
        self.assertNotIn('--apply',planner.make_parser().format_help())

class CoordinatorTests(unittest.TestCase):
    def test_optin_plan_generates_forwarded_flag(self):
        cmd=coordinator.planner_command(req(),'reports/cache',allow_csv_candidate_plan_only=True)
        self.assertEqual(cmd[-1],'--plan')
        self.assertEqual(cmd[-2],'--allow-csv-candidate-plan-only')
        self.assertNotIn('--apply',cmd)

    def test_default_plan_still_unchanged(self):
        self.assertNotIn('--allow-csv-candidate-plan-only',coordinator.planner_command(req(),'reports/cache'))

    def test_coordinator_apply_rejects_csv(self):
        parser=coordinator.make_parser()
        args=parser.parse_args(cli('--apply')+['--confirm-candidate-write','--allow-csv-candidate-plan-only'])
        with self.assertRaises(SystemExit):
            coordinator.check_mode(args,parser)

    def test_plan_optin_never_builds_bundle(self):
        argv=['coordinator.py',*cli(),'--allow-csv-candidate-plan-only']
        with patch.object(sys,'argv',argv),patch.object(coordinator,'run_existing_plan') as plan, \
             patch.object(coordinator,'build_verified_bundle') as build:
            self.assertEqual(coordinator.main(),0)
        self.assertTrue(plan.call_args.kwargs['allow_csv_candidate_plan_only'])
        build.assert_not_called()

    def test_wrong_switch_type_blocked(self):
        with self.assertRaisesRegex(ValueError,'CSV candidate planning'):
            coordinator.planner_command(req(),'reports/cache',allow_csv_candidate_plan_only='yes')

    def test_flagged_planner_process_passed_without_write(self):
        marker=coordinator.PLAN_MARKER+'\n'
        with patch.object(coordinator.subprocess,'run',return_value=NS(returncode=0,stdout=marker,stderr='')) as run:
            coordinator.run_existing_plan(req(),'reports/cache',allow_csv_candidate_plan_only=True)
        argv=run.call_args.args[0]
        self.assertIn('--allow-csv-candidate-plan-only',argv)
        self.assertNotIn('--apply',argv)

if __name__ == '__main__': unittest.main()
