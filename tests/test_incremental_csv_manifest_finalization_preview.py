"""M4.CSV.3b.4g: CSV manifest/finalization PREVIEW only. No SQL/network/writes."""
from __future__ import annotations
import contextlib
import io
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from scripts.tools import prepare_incremental_manifest as prep
from scripts.tools import register_incremental_manifest as reg
from scripts.tools import finalize_incremental_snapshot as fin
from tests import test_prepare_incremental_manifest_m46 as ptest
from tests import test_register_incremental_manifest_m46 as rtest
from tests import test_finalize_incremental_snapshot_m46 as ftest


class CsvManifestCreationPreview(unittest.TestCase):
    def test_opt_in_plan_parser(self):
        args = prep.parse_args(ptest.argv('--plan', '--allow-csv-manifest-plan-only'))
        self.assertTrue(args.plan)
        self.assertTrue(args.allow_csv_manifest_plan_only)

    def test_json_plan_unchanged(self):
        args = prep.parse_args(ptest.argv('--plan'))
        self.assertFalse(args.allow_csv_manifest_plan_only)

    def test_csv_publish_refused_before_any_work(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            prep.parse_args(ptest.argv('--publish', '--confirm-manifest-archive-only',
                                       '--expected-manifest-sha256', 'e'*64,
                                       '--allow-csv-manifest-plan-only'))

    def _plan_main(self, csv):
        argv = ptest.argv('--plan', *(['--allow-csv-manifest-plan-only'] if csv else []))
        with patch.object(prep, 'load_context', return_value=ptest.context()) as loader, \
             patch.object(prep, 'fetch_full_tree', return_value=ptest.rows()), \
             patch.object(prep, 'build_manifest', return_value={}), \
             patch.object(prep, 'canonical_bytes', return_value=b'{}'), \
             patch.object(prep, 'inspect_manifest_outputs', return_value=('reports/m.json', 'raw-archive-v2/manifests/m.json', ('READY_NEW','READY_NEW'))), \
             patch.object(prep, 'publish_manifest', side_effect=AssertionError('No publish from plan')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prep.main(argv), 0)
            self.assertEqual(loader.call_count, 1)
            passed=loader.call_args.args[0]
            self.assertEqual(bool(getattr(passed, 'allow_csv_cache_only', False)), csv)

    def test_csv_read_only_planner_uses_cache_contract(self):
        self._plan_main(True)

    def test_json_read_only_planner_preserves_default_call(self):
        self._plan_main(False)


class CsvManifestRegistrationPreview(unittest.TestCase):
    def test_plan_flag(self):
        p=reg.make_parser()
        a=p.parse_args(rtest.cli()+['--allow-csv-manifest-registration-plan-only'])
        reg.check_mode(a,p)
        self.assertTrue(a.allow_csv_manifest_registration_plan_only)

    def test_json_flag_default_off(self):
        p=reg.make_parser();a=p.parse_args(rtest.cli());reg.check_mode(a,p)
        self.assertFalse(a.allow_csv_manifest_registration_plan_only)

    def test_csv_commit_forbidden(self):
        p=reg.make_parser();a=p.parse_args(rtest.cli('--commit')+
             ['--confirm-manifest-only','--allow-csv-manifest-registration-plan-only'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            reg.check_mode(a,p)

    def test_invalid_direct_audit_switch(self):
        with self.assertRaisesRegex(ValueError, 'CSV manifest audit'):
            reg.audit_database(None, rtest.request(), {}, {}, set(), allow_csv_planning=1)

    def _verify_forwarding(self, csv):
        req=rtest.request()
        r=req.reuse.registration
        state=NS(repo_id=r.repo_id,revision=r.target_revision,status='in_progress',
                 manifest_files=r.manifest_files,raw_files=r.raw_files,
                 manifest_sha=None,manifest_path=None)
        with patch.object(reg,'inspect_db',return_value=('1'*40,{},state)), \
             patch.object(reg,'manifest_identities',return_value={}), \
             patch.object(reg,'verify_plan',side_effect=RuntimeError('sentinel')) as verifier:
            cur=rtest.FakeCursor([('epl_raw_storage_v2',)])
            with self.assertRaisesRegex(RuntimeError,'sentinel'):
                reg.audit_database(cur,req,{}, {},set(),allow_csv_planning=csv)
            self.assertEqual(verifier.call_args.kwargs,{'allow_csv_planning':True} if csv else {})

    def test_csv_audit_propagates_optin(self):self._verify_forwarding(True)
    def test_json_audit_retains_old_signature(self):self._verify_forwarding(False)


class CsvFinalizationPreview(unittest.TestCase):
    def test_plan_flag(self):
        p=fin.make_parser();a=p.parse_args(ftest.cmd()+['--allow-csv-finalization-plan-only'])
        fin.check_mode(a,p)
        self.assertTrue(a.allow_csv_finalization_plan_only)

    def test_default_json(self):
        p=fin.make_parser();a=p.parse_args(ftest.cmd());fin.check_mode(a,p)
        self.assertFalse(a.allow_csv_finalization_plan_only)

    def test_csv_commit_forbidden(self):
        p=fin.make_parser()
        a=p.parse_args(ftest.cmd('--commit')+['--confirm-complete','--allow-csv-finalization-plan-only'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            fin.check_mode(a,p)

    def test_invalid_direct_audit_switch(self):
        with self.assertRaisesRegex(ValueError,'CSV finalization audit'):
            fin.audit_database(None,ftest.request(),{}, {},set(),allow_csv_planning='yes')

    def _verify_forwarding(self,csv):
        req=ftest.request();r=req.reuse.registration
        state=NS(repo_id=r.repo_id,revision=r.target_revision,status='in_progress',
                 manifest_files=r.manifest_files,raw_files=r.raw_files,
                 manifest_sha=req.manifest_sha256,manifest_path=req.archive_relative_path)
        with patch.object(fin,'inspect_db',return_value=('1'*40,{},state)), \
             patch.object(fin,'manifest_identities',return_value={}), \
             patch.object(fin,'verify_plan',side_effect=RuntimeError('sentinel')) as verifier:
            cur=ftest.Cursor(fetchones=[('epl_raw_storage_v2',)])
            with self.assertRaisesRegex(RuntimeError,'sentinel'):
                fin.audit_database(cur,req,{}, {},set(),allow_csv_planning=csv)
            self.assertEqual(verifier.call_args.kwargs,{'allow_csv_planning':True} if csv else {})

    def test_csv_audit_propagates_optin(self):self._verify_forwarding(True)
    def test_json_audit_retains_old_signature(self):self._verify_forwarding(False)

if __name__=='__main__':unittest.main()
