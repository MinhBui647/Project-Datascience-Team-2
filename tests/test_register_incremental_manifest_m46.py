"""Offline M4.6.3e.2 manifest registration tests: NO HF, DB or archive writes."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
from pathlib import Path
from unittest.mock import patch
import contextlib
import hashlib
import io
import json
import tempfile
import unittest

from scripts.tools import register_incremental_manifest as m
from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.populate_incremental_reuse import ReuseRequest
from scripts.tools.incremental_reuse_linker import ReuseLink
from scripts.tools.check_raw_updates import FileIdentity

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = 'f'*40
FP = 'a'*64
BLOB1, BLOB2 = 'b'*40, 'c'*40
SHA1, SHA2 = 'd'*64, 'e'*64
P1 = 'raw_data/understat/history.csv'
P2 = '2026_2027_new/fpl_foo.json'


def request(*, reuse=1, candidate=1, records=2, raw=2, full=3, baseline_files=1,
            reused_records=12):
    r = Registration(REPO, 365, REV, FP, baseline_files, full, raw,
                     reuse, candidate, 5*candidate, records)
    return m.ManifestRequest(ReuseRequest(r, 366, 12, reused_records),
            'reports/m46/manifest.json', '9'*64)


def cli(mode='--plan'):
    return [
        '--baseline-snapshot', '365', '--target-snapshot-id', '366',
        '--target-revision', REV, '--expected-fingerprint', FP,
        '--expected-baseline-files', '1', '--expected-full-files', '3',
        '--expected-raw-files', '2', '--expected-reuse', '1',
        '--expected-files', '1', '--expected-bytes', '5',
        '--expected-records', '2', '--expected-baseline-records', '12',
        '--expected-reused-records', '12', '--manifest-report', 'reports/m46/manifest.json',
        '--manifest-sha256', '9'*64, mode,
    ]


def manifest():
    return dict(repo_id=REPO, repo_type='dataset', resolved_commit=REV,
        file_count=3, files=[
            dict(relative_path=P1, format='csv', size_bytes=100,
                 git_blob_id=BLOB1, lfs_sha256='', hf_revision=REV),
            dict(relative_path=P2, format='json', size_bytes=5,
                 git_blob_id=BLOB2, lfs_sha256=SHA2, hf_revision=REV),
            dict(relative_path='README.md', format='md', size_bytes=20,
                 git_blob_id=BLOB1, lfs_sha256='', hf_revision=REV),
        ])


def plan():
    return NS(actions=(NS(path=P1,kind='REUSE_FILE_ID',category='UNCHANGED'),
                       NS(path=P2,kind='UPSERT_VERSION_AFTER_REHASH',category='CHANGED',
                          file_format='json',source_code='fpl',expected_bytes=5,
                          git_blob_id=BLOB2,lfs_sha256=SHA2)), fingerprint=FP)


def rows():
    return [
        (P1, REPO, 'linked', 100, BLOB1, None,
         40, REPO, P1, 100, SHA1, 'raw-archive-v2/objects/dd/'+SHA1,
         'verified', 'success', 12, 10, 'understat', 'csv'),
        (P2, REPO, 'linked', 5, BLOB2, SHA2,
         41, REPO, P2, 5, SHA2, 'raw-archive-v2/objects/ee/'+SHA2,
         'verified', 'success', 2, 11, 'fpl', 'json'),
    ]


def links(): return (ReuseLink(P1, 40, 100, BLOB1, None),)


class FakeCursor:
    def __init__(self, fetched=()):
        self.values = list(fetched)
        self.queries = []
    def execute(self, sql, params=None): self.queries.append((sql,params))
    def fetchone(self): return self.values.pop(0)
    def fetchall(self): return self.values.pop(0)


class ParserTests(unittest.TestCase):
    def test_plan_parses(self):
        ap=m.make_parser(); a=ap.parse_args(cli()); m.check_mode(a,ap)
        self.assertEqual(m.build_request(a),request())
    def test_commit_confirmation(self):
        ap=m.make_parser(); a=ap.parse_args(cli('--commit')+['--confirm-manifest-only'])
        m.check_mode(a,ap)
    def test_commit_without_confirmation_rejected(self):
        ap=m.make_parser(); a=ap.parse_args(cli('--commit'))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): m.check_mode(a,ap)
    def test_plan_with_confirmation_rejected(self):
        ap=m.make_parser(); a=ap.parse_args(cli()+['--confirm-manifest-only'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): m.check_mode(a,ap)
    def test_missing_fields_fails(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            m.make_parser().parse_args(['--plan'])
    def test_bad_digest_fails(self):
        with self.assertRaises(RuntimeError): replace(request(),manifest_sha256='main').validate()
    def test_unsafe_report_location_fails(self):
        for p in ('reports/../secret.json','other/manifest.json','reports\\x.json',
                  '/etc/passwd','reports/x.csv','reports//a.json','reports/hi\x00.json'):
            with self.subTest(p=p), self.assertRaises(RuntimeError):
                replace(request(),report_relative_path=p).validate()
    def test_zero_reuse_request_contract(self):
        request(reuse=0,candidate=2,raw=2,full=3,reused_records=0).validate()


class ManifestFormatTests(unittest.TestCase):
    def test_valid_manifest_scope(self): self.assertEqual(set(m.check_manifest(manifest(),request())),{P1,P2})
    def test_revision_drift(self):
        doc=manifest(); doc['resolved_commit']='0'*40
        with self.assertRaisesRegex(RuntimeError,'revision'): m.check_manifest(doc,request())
    def test_file_revision_drift(self):
        doc=manifest(); doc['files'][1]['hf_revision']='0'*40
        with self.assertRaisesRegex(RuntimeError,'revision'): m.check_manifest(doc,request())
    def test_extra_nonraw_blocked(self):
        doc=manifest(); doc['files'][-1]['relative_path']='private/notes.txt'
        with self.assertRaisesRegex(RuntimeError,'non-Raw'): m.check_manifest(doc,request())
    def test_path_traversal_blocked(self):
        doc=manifest(); doc['files'][0]['relative_path']='raw_data/../secret.csv'
        with self.assertRaisesRegex(RuntimeError,'Unsafe'): m.check_manifest(doc,request())
    def test_duplicate_manifest_path_blocked(self):
        doc=manifest(); doc['files'][1]['relative_path']=P1
        with self.assertRaisesRegex(RuntimeError,'duplicate'): m.check_manifest(doc,request())
    def test_bad_raw_format_blocked(self):
        doc=manifest(); doc['files'][1]['format']='parquet'
        with self.assertRaisesRegex(RuntimeError,'format'): m.check_manifest(doc,request())
    def test_negative_size_blocked(self):
        doc=manifest(); doc['files'][1]['size_bytes']=-1
        with self.assertRaisesRegex(RuntimeError,'size'): m.check_manifest(doc,request())
    def test_invalid_remote_ids_blocked(self):
        doc=manifest(); doc['files'][1]['git_blob_id']='g'*40
        with self.assertRaisesRegex(RuntimeError,'identity'): m.check_manifest(doc,request())
    def test_local_immutable_manifest_roundtrip_and_corrupt(self):
        doc=manifest(); body=json.dumps(doc,ensure_ascii=False).encode('utf8')
        sha=hashlib.sha256(body).hexdigest()
        req=replace(request(),manifest_sha256=sha)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); rep=root/req.report_relative_path; arc=root/req.archive_relative_path
            rep.parent.mkdir(parents=True); arc.parent.mkdir(parents=True)
            rep.write_bytes(body); arc.write_bytes(body)
            raw,size=m.load_verified_manifest(req,root=root)
            self.assertEqual(set(raw),{P1,P2}); self.assertEqual(size,len(body))
            arc.write_bytes(body+b'corrupt')
            with self.assertRaisesRegex(RuntimeError,'SHA256'): m.load_verified_manifest(req,root=root)
    def test_report_symlink_blocked(self):
        doc=manifest(); body=json.dumps(doc).encode(); sha=hashlib.sha256(body).hexdigest()
        req=replace(request(),manifest_sha256=sha)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); rep=root/req.report_relative_path; arc=root/req.archive_relative_path
            rep.parent.mkdir(parents=True);arc.parent.mkdir(parents=True);arc.write_bytes(body)
            external=root/'real.json'; external.write_bytes(body)
            try:
                rep.symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest('Windows symlink privilege unavailable')
            with self.assertRaisesRegex(RuntimeError,'symlink'): m.load_verified_manifest(req,root=root)
    def test_manifest_directory_symlink_escape_blocked(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outer:
            root=Path(td)
            try:
                (root/'reports').symlink_to(Path(outer),target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('Windows symlink privilege unavailable')
            (Path(outer)/'m46').mkdir(); (Path(outer)/'m46/manifest.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'outside'): m._safe_file(root,'reports/m46/manifest.json','reports/')


class ObservationTests(unittest.TestCase):
    def test_reuse_and_candidates_by_plan_not_file_id_differences(self):
        cand,summary=m.validate_observations(rows(),m.check_manifest(manifest(),request()),plan(),links(),request())
        self.assertEqual(cand,{41:2}); self.assertEqual(summary.record_references,14)
    def test_candidate_baseline_file_id_allowed_when_target_action_changed(self):
        # Candidate may relink a previously stored version with its own old file_id.
        shifted=rows(); shifted[1]=(*shifted[1][:6], 12345, *shifted[1][7:])
        got,_=m.validate_observations(shifted,m.check_manifest(manifest(),request()),plan(),links(),request())
        self.assertEqual(got,{12345:2})
    def test_missing_observation_blocked(self):
        with self.assertRaisesRegex(RuntimeError,'coverage'):
            m.validate_observations(rows()[:1],m.check_manifest(manifest(),request()),plan(),links(),request())
    def test_wrong_candidate_source_blocked(self):
        rr=rows();rr[1]=(*rr[1][:16], 'weather',rr[1][17])
        with self.assertRaisesRegex(RuntimeError,'source'):
            m.validate_observations(rr,m.check_manifest(manifest(),request()),plan(),links(),request())
    def test_reuse_file_id_drift(self):
        rr=rows();rr[0]=(*rr[0][:6],999,*rr[0][7:])
        with self.assertRaisesRegex(RuntimeError,'REUSE'):
            m.validate_observations(rr,m.check_manifest(manifest(),request()),plan(),links(),request())
    def test_duplicate_file_id_rejected(self):
        rr=rows();rr[1]=(*rr[1][:6],40,*rr[1][7:])
        with self.assertRaisesRegex(RuntimeError,'duplicate'):
            m.validate_observations(rr,m.check_manifest(manifest(),request()),plan(),links(),request())
    def test_remote_git_mismatch_blocked(self):
        rr=rows();rr[1]=(*rr[1][:4],'0'*40,*rr[1][5:])
        with self.assertRaisesRegex(RuntimeError,'identity'):
            m.validate_observations(rr,m.check_manifest(manifest(),request()),plan(),links(),request())
    def test_candidate_zero_records_blocked(self):
        rr=rows();rr[1]=(*rr[1][:14],0,*rr[1][15:])
        with self.assertRaisesRegex(RuntimeError,'record'):
            m.validate_observations(rr,m.check_manifest(manifest(),request()),plan(),links(),request())
    def test_missing_candidate_physical_records_blocked(self):
        cur=FakeCursor([[(41,1,1,0)]])
        with self.assertRaisesRegex(RuntimeError,'Physical'):
            m.audit_physical_candidates(cur,{41:2},2)
    def test_physical_records_valid(self):
        cur=FakeCursor([[(41,2,2,0)]])
        m.audit_physical_candidates(cur,{41:2},2)
    def test_duplicate_record_locators_blocked(self):
        cur=FakeCursor([[(41,2,1,0)]])
        with self.assertRaisesRegex(RuntimeError,'Physical'):
            m.audit_physical_candidates(cur,{41:2},2)


class TransactionTests(unittest.TestCase):
    def test_lock_conflict_before_manifest_read_or_update(self):
        cur=FakeCursor([(False,)])
        with self.assertRaisesRegex(RuntimeError,'Concurrent'):
            m.register_in_transaction(cur,request(),{}, {},set())
        self.assertEqual(len(cur.queries),1)
    def test_target_row_lock_failure(self):
        cur=FakeCursor([(True,),None])
        with self.assertRaisesRegex(RuntimeError,'row lock'):
            m.register_in_transaction(cur,request(),{}, {},set())
        self.assertEqual(len(cur.queries),2)
    def test_commit_updates_only_two_fields_when_all_gates_pass(self):
        req=request();cur=FakeCursor([(True,),(366,),
            (366,'in_progress',req.manifest_sha256,req.archive_relative_path)])
        with patch.object(m,'load_verified_manifest',return_value=({'ok':1},123)),\
             patch.object(m,'audit_database',return_value=NS(observations=2)):
            result=m.register_in_transaction(cur,req,{'ok':1},{},set())
        self.assertEqual(result.observations,2)
        sql=cur.queries[-1][0]
        self.assertIn('SET manifest_sha256=%s,manifest_archive_path=%s',sql)
        self.assertNotIn('status=\'completed\'',sql)
        self.assertEqual(len([q for q,p in cur.queries if q.strip().startswith('UPDATE')]),1)
        self.assertIn('manifest_sha256 IS NULL',sql)
    def test_manifest_changed_under_lock_rolls_back_without_update(self):
        cur=FakeCursor([(True,),(366,)])
        with patch.object(m,'load_verified_manifest',return_value=({'tampered':1},2)):
            with self.assertRaisesRegex(RuntimeError,'changed'):
                m.register_in_transaction(cur,request(),{'ok':1},{},set())
        self.assertEqual(len(cur.queries),2)
    def test_database_ready_state_rejects_registered_manifest(self):
        req=request();r=req.reuse.registration
        state=NS(repo_id=REPO,revision=REV,status='in_progress',
                 manifest_files=3,raw_files=2,manifest_sha='0'*64,manifest_path='exists')
        with patch.object(m,'inspect_db',return_value=('1'*40,{},state)):
            cur=FakeCursor([('epl_raw_storage_v2',)])
            with self.assertRaisesRegex(RuntimeError,'not manifest-registration-ready'):
                m.audit_database(cur,req,{}, {},set())
    def test_wrong_database_rejected(self):
        cur=FakeCursor([('wrong_database',)])
        with self.assertRaisesRegex(RuntimeError,'Wrong PostgreSQL'):
            m.audit_database(cur,request(),{}, {},set())


if __name__ == '__main__': unittest.main()
