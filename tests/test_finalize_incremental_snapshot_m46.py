"""M4.6.3e.4 offline finalization tests; no HF request, PostgreSQL or archive writes outside temp dirs."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import contextlib
import hashlib
import io
import tempfile
import unittest

from scripts.tools import finalize_incremental_snapshot as m
from scripts.tools.register_incremental_manifest import ManifestRequest
from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.populate_incremental_reuse import ReuseRequest
from scripts.tools.incremental_reuse_linker import ReuseLink

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = 'f'*40
FINGERPRINT = 'a'*64
BLOB1 = 'b'*40
BLOB2 = 'c'*40
SHA1 = 'd'*64
SHA2 = 'e'*64
P1 = 'raw_data/history.csv'
P2 = '2026_2027_new/fpl.json'


def request(reuse=1, candidate=1, raw_count=2, reused_records=12, candidate_records=2):
    reg = Registration(REPO, 365, REV, FINGERPRINT, 1, raw_count+1, raw_count,
                       reuse, candidate, 5*candidate, candidate_records)
    return ManifestRequest(ReuseRequest(reg, 366, 12, reused_records),
                           'reports/m46/manifest.json', '9'*64)


def cmd(mode='--plan'):
    return ['--baseline-snapshot','365','--target-snapshot-id','366',
            '--target-revision',REV,'--expected-fingerprint',FINGERPRINT,
            '--expected-baseline-files','1','--expected-full-files','3',
            '--expected-raw-files','2','--expected-reuse','1',
            '--expected-files','1','--expected-bytes','5',
            '--expected-records','2','--expected-baseline-records','12',
            '--expected-reused-records','12',
            '--manifest-report','reports/m46/manifest.json',
            '--manifest-sha256','9'*64,mode]


def raw():
    return {
        P1: dict(relative_path=P1, format='csv', size_bytes=100,
                 git_blob_id=BLOB1, lfs_sha256=''),
        P2: dict(relative_path=P2, format='json', size_bytes=5,
                 git_blob_id=BLOB2, lfs_sha256=SHA2),
    }


def rows():
    return [
        (P1, REPO, 'linked', 100, BLOB1, None,
         40, REPO, P1, 100, SHA1, 'raw-archive-v2/objects/dd/'+SHA1,
         'verified', 'success', 12, 10, 'understat', 'csv'),
        (P2, REPO, 'linked', 5, BLOB2, SHA2,
         41, REPO, P2, 5, SHA2, 'raw-archive-v2/objects/ee/'+SHA2,
         'verified', 'success', 2, 11, 'fpl', 'json'),
    ]


def plan():
    return NS(actions=(NS(path=P1, kind='REUSE_FILE_ID', category='UNCHANGED'),
                       NS(path=P2, kind='UPSERT_VERSION_AFTER_REHASH', category='CHANGED',
                          file_format='json', source_code='fpl', expected_bytes=5,
                          git_blob_id=BLOB2, lfs_sha256=SHA2)), fingerprint=FINGERPRINT)


def links():
    return (ReuseLink(P1, 40, 100, BLOB1, None),)


class Cursor:
    def __init__(self, fetchones=(), fetchalls=()):
        self.ones = list(fetchones)
        self.alls = list(fetchalls)
        self.queries = []
    def execute(self, sql, params=None):
        self.queries.append((sql, params))
    def fetchone(self):
        return self.ones.pop(0)
    def fetchall(self):
        return self.alls.pop(0)


class CliTests(unittest.TestCase):
    def test_plan_builds_identical_manifest_execution_context(self):
        p=m.make_parser(); a=p.parse_args(cmd());m.check_mode(a,p)
        self.assertEqual(m.build_request(a),request())
    def test_commit_requires_confirmation(self):
        p=m.make_parser(); a=p.parse_args(cmd('--commit'))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):m.check_mode(a,p)
    def test_commit_confirmed(self):
        p=m.make_parser(); a=p.parse_args(cmd('--commit')+['--confirm-complete'])
        m.check_mode(a,p)
    def test_plan_forbids_confirmation(self):
        p=m.make_parser(); a=p.parse_args(cmd()+['--confirm-complete'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):m.check_mode(a,p)
    def test_missing_target_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):m.make_parser().parse_args(['--plan'])
    def test_zero_reuse_context_supported(self):
        request(reuse=0,candidate=2,raw_count=2,reused_records=0).validate()


class SnapshotRowTests(unittest.TestCase):
    def setUp(self):
        self.req=request()
        self.correct=(REPO,REV,'in_progress',3,2,'9'*64,self.req.archive_relative_path)
    def test_valid(self):m.verify_snapshot_row(self.correct,self.req)
    def test_completed_refused(self):
        with self.assertRaisesRegex(RuntimeError,'drift'):m.verify_snapshot_row((*self.correct[:2],'completed',*self.correct[3:]),self.req)
    def test_manifest_not_registered_refused(self):
        with self.assertRaisesRegex(RuntimeError,'manifest'):m.verify_snapshot_row((*self.correct[:5],None,None),self.req)
    def test_digest_drift_refused(self):
        with self.assertRaisesRegex(RuntimeError,'manifest'):m.verify_snapshot_row((*self.correct[:5],'a'*64,self.correct[-1]),self.req)
    def test_revision_drift_refused(self):
        with self.assertRaisesRegex(RuntimeError,'drift'):m.verify_snapshot_row((REPO,'0'*40,*self.correct[2:]),self.req)
    def test_raw_count_drift_refused(self):
        with self.assertRaisesRegex(RuntimeError,'drift'):m.verify_snapshot_row((*self.correct[:4],3,*self.correct[5:]),self.req)


class ItemTests(unittest.TestCase):
    def test_valid_by_action_not_old_file_id(self):
        objects, versions, candidates=m.build_items(rows(),raw(),plan(),links(),request())
        self.assertEqual(len(objects),2)
        self.assertEqual(versions[40]['record_count'],12)
        self.assertEqual(candidates,(41,))
    def test_sha_hex_normalized_for_archive_scan(self):
        rr=rows()
        rr[1]=(*rr[1][:10], SHA2.upper(), *rr[1][11:])
        objects, _, _ = m.build_items(rr,raw(),plan(),links(),request())
        self.assertEqual(next(x['sha256'] for x in objects if x['relative_path']==P2), SHA2)

    def test_candidate_past_version_accepted(self):
        rr=rows();rr[1]=(*rr[1][:6],999,*rr[1][7:])
        _,versions,ids=m.build_items(rr,raw(),plan(),links(),request())
        self.assertEqual(ids,(999,));self.assertEqual(versions[999]['record_count'],2)
    def test_missing_observation_rejected(self):
        with self.assertRaises(RuntimeError):m.build_items(rows()[:1],raw(),plan(),links(),request())
    def test_duplicate_file_id_rejected(self):
        rr=rows();rr[1]=(*rr[1][:6],40,*rr[1][7:])
        with self.assertRaises(RuntimeError):m.build_items(rr,raw(),plan(),links(),request())
    def test_bad_manifest_meta_rejected(self):
        rr=rows();rr[1]=(*rr[1][:9],999,*rr[1][10:])
        with self.assertRaises(RuntimeError):m.build_items(rr,raw(),plan(),links(),request())
    def test_bad_record_count_rejected(self):
        rr=rows();rr[1]=(*rr[1][:14],3,*rr[1][15:])
        with self.assertRaises(RuntimeError):m.build_items(rr,raw(),plan(),links(),request())
    def test_candidate_count_guard(self):
        with self.assertRaises(RuntimeError):m.build_items(rows(),raw(),plan(),links(),request(candidate_records=3))


class PhysicalTests(unittest.TestCase):
    def setUp(self):
        self.req=request()
        self.versions={40:dict(path=P1,format='csv',record_count=12),
                       41:dict(path=P2,format='json',record_count=2)}
    def test_valid_csv_and_json(self):
        cur=Cursor(fetchalls=[[(40,'csv_row',False,12),(41,'json_item',False,2)],
                              [(41,2,2,0)]])
        self.assertEqual(m.audit_physical_records(cur,self.versions,(41,),self.req),(14,0))
        self.assertEqual(len(cur.queries),2)
    def test_reject_wrong_kind(self):
        cur=Cursor(fetchalls=[[(40,'tar_json',True,12),(41,'json_item',False,2)]])
        with self.assertRaisesRegex(RuntimeError,'kind'):m.audit_physical_records(cur,self.versions,(41,),self.req)
    def test_missing_records(self):
        cur=Cursor(fetchalls=[[(40,'csv_row',False,11),(41,'json_item',False,2)]])
        with self.assertRaisesRegex(RuntimeError,'record count'):m.audit_physical_records(cur,self.versions,(41,),self.req)
    def test_duplicate_candidate_locators(self):
        cur=Cursor(fetchalls=[[(40,'csv_row',False,12),(41,'json_item',False,2)],[(41,2,1,0)]])
        with self.assertRaisesRegex(RuntimeError,'locator|integrity'):m.audit_physical_records(cur,self.versions,(41,),self.req)
    def test_candidate_member_id_blocked(self):
        cur=Cursor(fetchalls=[[(40,'csv_row',False,12),(41,'json_item',False,2)],[(41,2,2,1)]])
        with self.assertRaisesRegex(RuntimeError,'integrity'):m.audit_physical_records(cur,self.versions,(41,),self.req)
    def test_tar_members_dynamic(self):
        versions={40:dict(path='raw_data/new_archive.tar.gz',format='tar.gz',record_count=12),
                  41:self.versions[41]}
        cur=Cursor(fetchalls=[[(40,'tar_json',True,12),(41,'json_item',False,2)],
                              [(41,2,2,0)],[(40,'success',12)]])
        self.assertEqual(m.audit_physical_records(cur,versions,(41,),self.req),(14,12))
        self.assertEqual(len(cur.queries),3)
    def test_tar_missing_member_fails(self):
        versions={40:dict(path='raw_data/new_archive.tar.gz',format='tar.gz',record_count=12),
                  41:self.versions[41]}
        cur=Cursor(fetchalls=[[(40,'tar_json',True,12),(41,'json_item',False,2)],
                              [(41,2,2,0)],[(40,'success',11)]])
        with self.assertRaisesRegex(RuntimeError,'TAR member'):m.audit_physical_records(cur,versions,(41,),self.req)
    def test_tar_invalid_state_fails(self):
        versions={40:dict(path='raw_data/new_archive.tar.gz',format='tar.gz',record_count=12),
                  41:self.versions[41]}
        cur=Cursor(fetchalls=[[(40,'tar_json',True,12),(41,'json_item',False,2)],
                              [(41,2,2,0)],[(40,'error',12)]])
        with self.assertRaisesRegex(RuntimeError,'TAR archive'):m.audit_physical_records(cur,versions,(41,),self.req)
    def test_zero_candidate_supports_nonzero_reuse(self):
        req=request(reuse=1,candidate=0,raw_count=1,candidate_records=0)
        versions={40:self.versions[40]}
        cur=Cursor(fetchalls=[[(40,'csv_row',False,12)]])
        self.assertEqual(m.audit_physical_records(cur,versions,(),req),(12,0))
        self.assertEqual(len(cur.queries),1)
    def test_wrong_total_fails(self):
        cur=Cursor(fetchalls=[[(40,'csv_row',False,12),(41,'json_item',False,2)]])
        with self.assertRaisesRegex(RuntimeError,'references'):
            m.audit_physical_records(cur,self.versions,(41,),request(reused_records=11))


class ReconciliationTests(unittest.TestCase):
    def test_full_audit_returns_dynamic_record_totals(self):
        req=request()
        state=NS(repo_id=REPO,revision=REV,status='in_progress',manifest_files=3,
                 raw_files=2,manifest_sha='9'*64,manifest_path=req.archive_relative_path)
        cur=Cursor(fetchones=[('epl_raw_storage_v2',)],fetchalls=[rows()])
        with patch.object(m,'inspect_db',return_value=('1'*40,{},state)), \
             patch.object(m,'verify_plan',return_value=(plan(),links())), \
             patch.object(m,'audit_physical_records',return_value=(14,0)):
            s=m.audit_database(cur,req,raw(),{},set())
        self.assertEqual((s.records,s.observations,s.reused,s.candidates),(14,2,1,1))
        self.assertEqual(len(s.objects),2)
    def test_bad_database_fails_closed(self):
        cur=Cursor(fetchones=[('another_db',)])
        with self.assertRaisesRegex(RuntimeError,'database'):m.audit_database(cur,request(),raw(),{},set())
    def test_unregistered_manifest_fails(self):
        req=request()
        state=NS(repo_id=REPO,revision=REV,status='in_progress',manifest_files=3,
                 raw_files=2,manifest_sha=None,manifest_path=None)
        cur=Cursor(fetchones=[('epl_raw_storage_v2',)])
        with patch.object(m,'inspect_db',return_value=('1'*40,{},state)),self.assertRaisesRegex(RuntimeError,'manifest'):
            m.audit_database(cur,req,raw(),{},set())
    def test_plan_fingerprint_drift_fails(self):
        req=request()
        state=NS(repo_id=REPO,revision=REV,status='in_progress',manifest_files=3,
                 raw_files=2,manifest_sha='9'*64,manifest_path=req.archive_relative_path)
        cur=Cursor(fetchones=[('epl_raw_storage_v2',)])
        wrong=NS(fingerprint='0'*64)
        with patch.object(m,'inspect_db',return_value=('1'*40,{},state)), \
             patch.object(m,'verify_plan',return_value=(wrong,links())), \
             self.assertRaisesRegex(RuntimeError,'fingerprint'):
            m.audit_database(cur,req,raw(),{},set())


class ArchiveTests(unittest.TestCase):
    def test_real_archive_git_blob_sha1(self):
        body = b'{"hello":"world"}\n'
        sha = hashlib.sha256(body).hexdigest()
        blob = hashlib.sha1(
            f"blob {len(body)}\0".encode("ascii") + body
        ).hexdigest()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rel = f"raw-archive-v2/objects/{sha[:2]}/{sha}"
            path = root / rel
            path.parent.mkdir(parents=True)
            path.write_bytes(body)

            item = dict(
                relative_path="raw_data/file.json",
                archive_path=rel,
                sha256=sha,
                size_bytes=len(body),
                lfs_sha256="",
                git_blob_id=blob,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                receipt = m.scan_archives((item,), root=root)

            self.assertEqual(receipt.total_bytes, len(body))

            invalid = dict(item, git_blob_id="0" * 40)

            with self.assertRaisesRegex(RuntimeError, "Git blob SHA1"):
                m.scan_archives((invalid,), root=root)

    def test_real_archive_hash_roundtrip(self):
        body=b'{"hello":"world"}\n'
        sha=hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rel=f'raw-archive-v2/objects/{sha[:2]}/{sha}'
            p=root/rel;p.parent.mkdir(parents=True);p.write_bytes(body)
            item=dict(relative_path='raw_data/file.json',archive_path=rel,sha256=sha,
                      size_bytes=len(body),lfs_sha256=sha,git_blob_id='')
            with contextlib.redirect_stdout(io.StringIO()):receipt=m.scan_archives((item,),root=root)
            self.assertEqual(receipt.total_bytes,len(body))
            self.assertEqual(receipt.objects,(item,))
            p.write_bytes(body+b'corruption')
            with self.assertRaises(RuntimeError):m.scan_archives((item,),root=root)
    def test_missing_archive_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'raw-archive-v2/objects').mkdir(parents=True)
            item=dict(archive_path='raw-archive-v2/objects/aa/'+'a'*64)
            with self.assertRaises((RuntimeError,FileNotFoundError)):
                m.scan_archives((item,),root=root)
    def test_archive_symlink_fails_if_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);obj=root/'raw-archive-v2/objects';obj.mkdir(parents=True)
            target=root/'target';target.write_bytes(b'abc')
            link=obj/'a';link.mkdir()
            p=link/('a'*64)
            try:p.symlink_to(target)
            except (OSError,NotImplementedError):self.skipTest('Windows symlink privilege unavailable')
            with self.assertRaises(RuntimeError):m.scan_archives((dict(archive_path=f'raw-archive-v2/objects/a/{"a"*64}'),),root=root)


class CommitTests(unittest.TestCase):
    def setUp(self):
        self.req=request()
        self.objects=(dict(relative_path=P1,archive_path='raw-archive-v2/objects/dd/'+SHA1,sha256=SHA1,size_bytes=100),
                      dict(relative_path=P2,archive_path='raw-archive-v2/objects/ee/'+SHA2,sha256=SHA2,size_bytes=5))
        self.summary=m.FinalAudit(self.objects,14,2,1,1,2,0)
        self.receipt=m.ArchiveReceipt(self.objects,105)
    def test_valid_commit_only_updates_status(self):
        cur=Cursor(fetchones=[(True,), (366,), (366,'completed')])
        with patch.object(m,'load_verified_manifest',return_value=(raw(),123)), \
             patch.object(m,'audit_database',return_value=self.summary):
            got=m.finalize_in_transaction(cur,self.req,raw(),{},set(),self.receipt)
        self.assertEqual(got,self.summary)
        updates=[(sql,params) for sql,params in cur.queries if sql.lstrip().startswith('UPDATE')]
        self.assertEqual(len(updates),1)
        self.assertIn("status='completed'",updates[0][0])
        self.assertNotIn('raw.raw_records',updates[0][0])
    def test_advisory_lock_failure(self):
        cur=Cursor(fetchones=[(False,)])
        with self.assertRaisesRegex(RuntimeError,'lock'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),self.receipt)
        self.assertFalse(any('UPDATE' in q[0] for q in cur.queries))
    def test_target_row_lock_failure(self):
        cur=Cursor(fetchones=[(True,),None])
        with self.assertRaisesRegex(RuntimeError,'lock'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),self.receipt)
    def test_manifest_drift_before_commit(self):
        cur=Cursor(fetchones=[(True,),(366,)])
        with patch.object(m,'load_verified_manifest',return_value=({},123)), \
             self.assertRaisesRegex(RuntimeError,'Manifest'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),self.receipt)
    def test_archive_scan_incomplete_before_commit(self):
        cur=Cursor(fetchones=[(True,),(366,)])
        with patch.object(m,'load_verified_manifest',return_value=(raw(),123)), \
             patch.object(m,'audit_database',return_value=self.summary), \
             self.assertRaisesRegex(RuntimeError,'Archive scan'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),m.ArchiveReceipt(self.objects,3))
    def test_archive_version_set_drift_before_commit(self):
        cur=Cursor(fetchones=[(True,),(366,)])
        with patch.object(m,'load_verified_manifest',return_value=(raw(),123)), \
             patch.object(m,'audit_database',return_value=self.summary), \
             self.assertRaisesRegex(RuntimeError,'Archive scan'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),m.ArchiveReceipt(self.objects[:1],100))
    def test_status_update_conflict(self):
        cur=Cursor(fetchones=[(True,),(366,),None])
        with patch.object(m,'load_verified_manifest',return_value=(raw(),123)), \
             patch.object(m,'audit_database',return_value=self.summary), \
             self.assertRaisesRegex(RuntimeError,'update rejected'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),self.receipt)
    def test_missing_receipt_fails_before_sql(self):
        cur=Cursor()
        with self.assertRaisesRegex(RuntimeError,'receipt'):
            m.finalize_in_transaction(cur,self.req,raw(),{},set(),None)
        self.assertFalse(cur.queries)


if __name__=='__main__':unittest.main()
