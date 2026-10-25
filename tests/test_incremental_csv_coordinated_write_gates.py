"""M4.CSV.3b.4j: explicit CSV stage-write switches. Pure mocks; no network/DB/write."""
from __future__ import annotations
import contextlib
import hashlib
import io
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from scripts.tools import register_incremental_snapshot as reg
from scripts.tools import populate_incremental_reuse as reuse
from scripts.tools import prepare_incremental_manifest as pub
from scripts.tools import register_incremental_manifest as manifest
from scripts.tools import finalize_incremental_snapshot as fin
from tests.test_incremental_csv_registration_reuse_preview import registration_cli, reuse_cli
from tests import test_prepare_incremental_manifest_m46 as pt
from tests import test_register_incremental_manifest_m46 as mt
from tests import test_finalize_incremental_snapshot_m46 as ft


class DummyDB:
    autocommit=False
    last_sql=""
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self
    def execute(self, *args): self.last_sql=str(args[0])
    def fetchone(self):
        return (366,) if "SELECT snapshot_id FROM raw.snapshot_runs" in self.last_sql else (True,)
    def fetchall(self): return []


def reject(fn):
    with contextlib.redirect_stderr(io.StringIO()), unittest.TestCase().assertRaises(SystemExit) as err:
        fn()
    assert err.exception.code == 2


class RegistrationCsvWrite(unittest.TestCase):
    def run_main(self, *flags):
        with patch.object(sys,'argv',['reg.py',*registration_cli(*flags)]), \
             patch.object(reg,'run_preflight',side_effect=RuntimeError('PRE_GATE_SENTINEL')) as pre, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'PRE_GATE_SENTINEL'):
                reg.main()
            return pre.call_args.kwargs

    def test_csv_write_forwards_csv_preflight_before_database(self):
        kw=self.run_main('--register','--confirm-snapshot-only','--allow-csv-registration-write')
        self.assertIs(kw['allow_csv_preflight_only'],True)

    def test_json_write_unchanged(self):
        kw=self.run_main('--register','--confirm-snapshot-only')
        self.assertNotIn('allow_csv_preflight_only',kw)

    def test_csv_write_reject_plan(self):
        reject(lambda: self.run_main('--plan','--allow-csv-registration-write'))

    def test_csv_write_reject_concurrent_preview(self):
        reject(lambda: self.run_main('--register','--confirm-snapshot-only',
                                     '--allow-csv-registration-plan-only','--allow-csv-registration-write'))

    def test_csv_write_requires_confirmation(self):
        reject(lambda: self.run_main('--register','--allow-csv-registration-write'))


class ReuseCsvLink(unittest.TestCase):
    def test_csv_link_validated_before_remote(self):
        for flags in [('--plan','--allow-csv-reuse-link'),
                      ('--link','--allow-csv-reuse-link'),
                      ('--link','--confirm-reuse-only','--allow-csv-reuse-plan-only','--allow-csv-reuse-link')]:
            with self.subTest(flags=flags), patch.object(sys,'argv',['reuse.py',*reuse_cli(*flags)]), \
                 patch.object(reuse,'load_baseline') as hf:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    reuse.main()
                hf.assert_not_called()

    def test_csv_link_executes_pinned_verify_plan_with_optin(self):
        m=types.ModuleType('psycopg')
        m.connect=lambda **kwargs: DummyDB()
        args=reuse_cli('--link','--confirm-reuse-only','--allow-csv-reuse-link')
        dummy=NS(registration=NS(repo_id='Toibinguz/epl-premier-league-dataset',target_revision='f'*40))
        with patch.dict(sys.modules,{'psycopg':m}), patch.object(sys,'argv',['reuse.py',*args]), \
             patch.object(reuse,'load_baseline',return_value=('a'*40,{})), \
             patch.object(reuse,'load_target',return_value=('f'*40,{},1)), \
             patch.object(reuse,'read_existing_source_codes',return_value=set()), \
             patch.object(reuse,'db_parameters',return_value={}), \
             patch.object(reuse,'inspect_db',return_value=('a'*40,{},object())), \
             patch.object(reuse,'verify_plan',side_effect=RuntimeError('CSV_VERIFY_SENTINEL')) as v:
            with self.assertRaisesRegex(RuntimeError,'CSV_VERIFY_SENTINEL'):
                reuse.main()
            self.assertEqual(v.call_args.kwargs,{'allow_csv_planning':True})

    def test_json_link_default_uses_prior_signature(self):
        m=types.ModuleType('psycopg');m.connect=lambda **kwargs:DummyDB()
        with patch.dict(sys.modules,{'psycopg':m}), \
             patch.object(sys,'argv',['reuse.py',*reuse_cli('--link','--confirm-reuse-only')]), \
             patch.object(reuse,'load_baseline',return_value=('a'*40,{})), \
             patch.object(reuse,'load_target',return_value=('f'*40,{},1)), \
             patch.object(reuse,'read_existing_source_codes',return_value=set()), \
             patch.object(reuse,'db_parameters',return_value={}), \
             patch.object(reuse,'inspect_db',return_value=('a'*40,{},object())), \
             patch.object(reuse,'verify_plan',side_effect=RuntimeError('JSON_VERIFY_SENTINEL')) as v:
            with self.assertRaisesRegex(RuntimeError,'JSON_VERIFY_SENTINEL'):
                reuse.main()
            self.assertEqual(v.call_args.kwargs,{})


class ManifestCsvPublish(unittest.TestCase):
    def test_csv_plan_flag_still_blocked_in_publish(self):
        reject(lambda:pub.parse_args(pt.argv('--publish','--confirm-manifest-archive-only',
             '--expected-manifest-sha256','e'*64,'--allow-csv-manifest-plan-only')))

    def test_csv_publish_requires_pinned_confirmation(self):
        for flags in [('--plan','--allow-csv-manifest-publish'),
                      ('--publish','--allow-csv-manifest-publish'),
                      ('--publish','--confirm-manifest-archive-only',
                       '--expected-manifest-sha256','e'*64,'--allow-csv-manifest-publish',
                       '--allow-csv-manifest-plan-only')]:
            with self.subTest(flags=flags): reject(lambda:pub.parse_args(pt.argv(*flags)))

    def test_csv_publish_uses_opt_in_context_and_immutable_publisher(self):
        digest=hashlib.sha256(b'{}').hexdigest()
        args=pt.argv('--publish','--confirm-manifest-archive-only',
                     '--expected-manifest-sha256',digest,'--allow-csv-manifest-publish')
        with patch.object(pub,'load_context',return_value=pt.context()) as loader, \
             patch.object(pub,'fetch_full_tree',return_value=pt.rows()), \
             patch.object(pub,'build_manifest',return_value={}), \
             patch.object(pub,'canonical_bytes',return_value=b'{}'), \
             patch.object(pub,'inspect_manifest_outputs',return_value=('reports/m.json','raw-archive-v2/manifests/m.json',('READY_NEW','READY_NEW'))), \
             patch.object(pub,'publish_manifest',return_value=('r','a','CREATED_NEW','CREATED_NEW')) as publisher, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pub.main(args),0)
            self.assertTrue(loader.call_args.args[0].allow_csv_cache_only)
            publisher.assert_called_once()

    def test_json_publish_has_original_context_shape(self):
        digest=hashlib.sha256(b'{}').hexdigest()
        args=pt.argv('--publish','--confirm-manifest-archive-only',
                     '--expected-manifest-sha256',digest)
        with patch.object(pub,'load_context',return_value=pt.context()) as loader, \
             patch.object(pub,'fetch_full_tree',return_value=pt.rows()), \
             patch.object(pub,'build_manifest',return_value={}), \
             patch.object(pub,'canonical_bytes',return_value=b'{}'), \
             patch.object(pub,'inspect_manifest_outputs',return_value=('reports/m.json','raw-archive-v2/manifests/m.json',('READY_NEW','READY_NEW'))), \
             patch.object(pub,'publish_manifest',return_value=('r','a','CREATED_NEW','CREATED_NEW')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pub.main(args),0)
            self.assertFalse(loader.call_args.args[0].allow_csv_manifest_publish)
            self.assertFalse(getattr(loader.call_args.args[0],'allow_csv_cache_only',False))


class ManifestCsvRegistration(unittest.TestCase):
    def parse(self,*flags):
        p=manifest.make_parser(); a=p.parse_args(mt.cli(flags[0])+list(flags[1:]));manifest.check_mode(a,p);return a

    def test_csv_commit_valid_flag_combo(self):
        a=self.parse('--commit','--confirm-manifest-only','--allow-csv-manifest-registration-write')
        self.assertTrue(a.allow_csv_manifest_registration_write)

    def test_csv_plan_flag_still_blocked_commit(self):
        reject(lambda:self.parse('--commit','--confirm-manifest-only','--allow-csv-manifest-registration-plan-only'))

    def test_csv_write_invalid_modes(self):
        for x in [('--plan','--allow-csv-manifest-registration-write'),
                  ('--commit','--allow-csv-manifest-registration-write'),
                  ('--commit','--confirm-manifest-only','--allow-csv-manifest-registration-plan-only',
                   '--allow-csv-manifest-registration-write')]:
            with self.subTest(x=x):reject(lambda:self.parse(*x))

    def test_csv_locked_transaction_rechecks_csv_plan(self):
        req=mt.request()
        cur=DummyDB()
        with patch.object(manifest,'load_verified_manifest',return_value=({},0)), \
             patch.object(manifest,'audit_database',side_effect=RuntimeError('CSV_AUDIT_SENTINEL')) as audit:
            with self.assertRaisesRegex(RuntimeError,'CSV_AUDIT_SENTINEL'):
                manifest.register_in_transaction(cur,req,{}, {},set(),allow_csv_planning=True)
            self.assertEqual(audit.call_args.kwargs,{'allow_csv_planning':True})

    def test_json_locked_transaction_retains_old_audit_signature(self):
        req=mt.request();cur=DummyDB()
        with patch.object(manifest,'load_verified_manifest',return_value=({},0)), \
             patch.object(manifest,'audit_database',side_effect=RuntimeError('JSON_AUDIT_SENTINEL')) as audit:
            with self.assertRaisesRegex(RuntimeError,'JSON_AUDIT_SENTINEL'):
                manifest.register_in_transaction(cur,req,{}, {},set())
            self.assertEqual(audit.call_args.kwargs,{})

    def test_invalid_direct_write_mode_refused(self):
        with self.assertRaises(ValueError):
            manifest.register_in_transaction(DummyDB(),mt.request(),{},{},set(),allow_csv_planning='yes')


class CsvFinalization(unittest.TestCase):
    def parse(self,*flags):
        p=fin.make_parser();a=p.parse_args(ft.cmd(flags[0])+list(flags[1:]));fin.check_mode(a,p);return a

    def test_csv_commit_flag_valid(self):
        self.assertTrue(self.parse('--commit','--confirm-complete','--allow-csv-finalization-write').allow_csv_finalization_write)

    def test_csv_finalization_plan_still_readonly(self):
        reject(lambda:self.parse('--commit','--confirm-complete','--allow-csv-finalization-plan-only'))

    def test_csv_write_requires_commit_confirmation_and_exclusive_flag(self):
        for flags in [('--plan','--allow-csv-finalization-write'),
                      ('--commit','--allow-csv-finalization-write'),
                      ('--commit','--confirm-complete','--allow-csv-finalization-write','--allow-csv-finalization-plan-only')]:
            with self.subTest(flags=flags):reject(lambda:self.parse(*flags))

    def test_csv_finalization_transaction_reaudits_csv_after_archive_scan(self):
        req=ft.request()
        receipt=fin.ArchiveReceipt(({'archive':'x'},),1)
        cur=DummyDB()
        with patch.object(fin,'load_verified_manifest',return_value=({},0)), \
             patch.object(fin,'audit_database',side_effect=RuntimeError('CSV_FINAL_SENTINEL')) as audit:
            with self.assertRaisesRegex(RuntimeError,'CSV_FINAL_SENTINEL'):
                fin.finalize_in_transaction(cur,req,{}, {},set(),receipt,allow_csv_planning=True)
            self.assertEqual(audit.call_args.kwargs,{'allow_csv_planning':True})

    def test_json_finalization_transaction_keeps_old_audit_call(self):
        req=ft.request()
        receipt=fin.ArchiveReceipt(({'archive':'x'},),1)
        with patch.object(fin,'load_verified_manifest',return_value=({},0)), \
             patch.object(fin,'audit_database',side_effect=RuntimeError('JSON_FINAL_SENTINEL')) as audit:
            with self.assertRaisesRegex(RuntimeError,'JSON_FINAL_SENTINEL'):
                fin.finalize_in_transaction(DummyDB(),req,{}, {},set(),receipt)
            self.assertEqual(audit.call_args.kwargs,{})

    def test_invalid_write_permission_type_fails_closed(self):
        req=ft.request();receipt=fin.ArchiveReceipt(({'archive':'x'},),1)
        with patch.object(fin,'load_verified_manifest',return_value=({},0)):
            with self.assertRaises(ValueError):
                fin.finalize_in_transaction(DummyDB(),req,{}, {},set(),receipt,allow_csv_planning='yes')

if __name__=='__main__':unittest.main()
