import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts.incremental.incremental_writer_core import (
    PreparedCsvVersion, PreparedJsonVersion, prepare_csv_version,
    verify_existing_version, validate_prepared, write_prepared_version,
)

REV = 'a'*40
PATH = '2026_2027_new/football_data_raw_2627.csv'
BODY = b'HomeTeam,AwayTeam\nArsenal,Chelsea\nChelsea,Arsenal\n'

class FakeCursor:
    def __init__(self, version, *, existing=None, database='epl_raw_storage_v2', status='in_progress'):
        self.version=version
        self.existing=existing
        self.database=database
        self.status=status
        self.nextrow=None
        self.calls=[]
        self.inserted=[]
        self.rowcount=0
    def execute(self, sql, args=None):
        self.calls.append((sql,args))
        s=' '.join(sql.split())
        self.rowcount=0
        if 'SELECT current_database()' in s: self.nextrow=(self.database,)
        elif 'pg_advisory_xact_lock' in s: self.nextrow=(True,)
        elif 'SELECT status FROM raw.snapshot_runs' in s: self.nextrow=(self.status,)
        elif 'SELECT source_id FROM raw.raw_sources' in s: self.nextrow=(7,)
        elif 'SELECT f.file_id,f.file_format' in s: self.nextrow=self.existing
        elif 'INSERT INTO raw.raw_files' in s:
            self.nextrow=(101,)
            self.inserted.append(('file',args))
        elif 'UPDATE raw.raw_files' in s: self.rowcount=1
        elif 'SELECT COUNT(*) FROM raw.raw_records' in s: self.nextrow=(len(self.version.records),)
        elif 'INSERT INTO raw.file_observations' in s:
            self.inserted.append(('observation',args))
        elif 'SELECT file_id,presence_status' in s:
            self.nextrow=(101 if self.existing is None else self.existing[0], 'linked',
                self.version.size_bytes, self.version.git_blob_id, self.version.lfs_sha256)
        else: raise AssertionError(f'Unexpected SQL: {s}')
        return self
    def executemany(self, sql, seq):
        self.inserted.append(('records',list(seq)))
    def fetchone(self): return self.nextrow
    def commit(self): raise AssertionError('writer must not commit')

class CsvWriterCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.sha=hashlib.sha256(BODY).hexdigest()
        self.git=hashlib.sha1(b'blob '+str(len(BODY)).encode()+b'\0'+BODY).hexdigest()
        self.obj=self.root/'raw-archive-v2'/'objects'/self.sha[:2]/self.sha
        self.obj.parent.mkdir(parents=True)
        self.obj.write_bytes(BODY)
        self.identity=SimpleNamespace(size=len(BODY),lfs_sha256=self.sha,git_blob_id=None)
    def prep(self, **kw):
        options=dict(repo_id='sample/repo',relative_path=PATH, revision=REV,
                     source_code='football_data',local_archive=self.obj,
                     remote_identity=self.identity,root=self.root)
        options.update(kw)
        return prepare_csv_version(**options)
    def existing(self, version):
        return (101, 'csv', version.size_bytes, version.archive_relative_path,
                'verified','success',len(version.records),version.source_code)
    def test_lfs_csv_prepare(self):
        v=self.prep()
        self.assertIsInstance(v,PreparedCsvVersion)
        self.assertEqual(len(v.records),2)
        self.assertEqual([r[0] for r in v.records], ['row:1','row:2'])
        self.assertEqual([r[1] for r in v.records],['csv_row','csv_row'])
        self.assertEqual(v.records[0][2]['HomeTeam'],'Arsenal')
    def test_git_only_prepare(self):
        identity=SimpleNamespace(size=len(BODY),lfs_sha256=None,git_blob_id=self.git)
        self.assertEqual(self.prep(remote_identity=identity).content_sha256,self.sha)
    def test_wrong_lfs_block(self):
        identity=SimpleNamespace(size=len(BODY),lfs_sha256='f'*64,git_blob_id=None)
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
            self.prep(remote_identity=identity)
    def test_wrong_git_block(self):
        identity=SimpleNamespace(size=len(BODY),lfs_sha256=None,git_blob_id='f'*40)
        with self.assertRaisesRegex(ValueError,'SHA1 mismatch'):
            self.prep(remote_identity=identity)
    def test_misplaced_archive_block(self):
        self.obj.rename(self.obj.parent/'renamed')
        with self.assertRaises(FileNotFoundError): self.prep()
    def test_header_only_block(self):
        body=b'HomeTeam,AwayTeam\n'
        sha=hashlib.sha256(body).hexdigest()
        dest=self.root/'raw-archive-v2'/'objects'/sha[:2]/sha
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(body)
        identity=SimpleNamespace(size=len(body),lfs_sha256=sha,git_blob_id=None)
        with self.assertRaisesRegex(RuntimeError, 'no data records'):
            self.prep(local_archive=dest,remote_identity=identity)
    def test_malformed_csv_header_block(self):
        body=b'HomeTeam,HomeTeam\nArsenal,Chelsea\n'
        sha=hashlib.sha256(body).hexdigest()
        dest=self.root/'raw-archive-v2'/'objects'/sha[:2]/sha
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(body)
        identity=SimpleNamespace(size=len(body),lfs_sha256=sha,git_blob_id=None)
        with self.assertRaisesRegex(ValueError, 'duplicate header'):
            self.prep(local_archive=dest,remote_identity=identity)
    def test_csv_type_cannot_disguise_json_kind(self):
        v=self.prep()
        v2=PreparedCsvVersion(**{**v.__dict__, 'records':(('root','json_object',{}),)})
        with self.assertRaisesRegex(ValueError,'Unsupported parser record'):
            validate_prepared(v2)
    def test_csv_type_cannot_disguise_json_path(self):
        v=self.prep()
        v2=PreparedCsvVersion(**{**v.__dict__,'relative_path':'raw_data/fotmob/new.json'})
        with self.assertRaisesRegex(ValueError,'Unsafe or unsupported raw path'):
            validate_prepared(v2)
    def test_existing_csv_version_matches(self):
        v=self.prep()
        self.assertEqual(verify_existing_version(self.existing(v),v), (101,2))
    def test_existing_json_version_conflicts(self):
        v=self.prep()
        row=list(self.existing(v));row[1]='json'
        with self.assertRaisesRegex(RuntimeError,'metadata conflict'):
            verify_existing_version(tuple(row),v)
    def dbcall(self, cur,version):
        j=types.ModuleType('psycopg.types.json');j.Jsonb=lambda x:x
        t=types.ModuleType('psycopg.types');t.json=j
        p=types.ModuleType('psycopg');p.types=t
        with patch.dict(sys.modules,{'psycopg':p,'psycopg.types':t,'psycopg.types.json':j}):
            return write_prepared_version(cur, 21, version, root=self.root)
    def test_csv_insert_with_fake_cursor_no_commit(self):
        v=self.prep();cur=FakeCursor(v)
        self.assertEqual(self.dbcall(cur,v), ('INSERTED_NEW',101,2))
        filed=[r for r in cur.inserted if r[0]=='file'][0]
        self.assertEqual(filed[1][3],'csv')
        self.assertEqual([r[2] for r in [x for x in cur.inserted if x[0]=='records'][0][1]],['csv_row','csv_row'])
        self.assertTrue(any(r[0]=='observation' for r in cur.inserted))
    def test_csv_reuse_with_fake_cursor_no_record_insert(self):
        v=self.prep();cur=FakeCursor(v,existing=self.existing(v))
        self.assertEqual(self.dbcall(cur,v),('REUSED_EXISTING',101,0))
        self.assertFalse(any(k in ('file','records') for k,_ in cur.inserted))
    def test_wrong_db_blocks_before_mutation(self):
        v=self.prep();cur=FakeCursor(v,database='other')
        with self.assertRaisesRegex(RuntimeError,'Wrong PostgreSQL database'):
            self.dbcall(cur,v)
        self.assertEqual(cur.inserted,[])
    def test_non_progress_snapshot_blocks_before_mutation(self):
        v=self.prep();cur=FakeCursor(v,status='completed')
        with self.assertRaisesRegex(RuntimeError,'in_progress'):
            self.dbcall(cur,v)
        self.assertEqual(cur.inserted,[])
    def test_archive_drift_blocks_before_sql(self):
        v=self.prep();self.obj.write_bytes(BODY+b'X')
        cur=FakeCursor(v)
        with self.assertRaises(ValueError): self.dbcall(cur,v)
        self.assertEqual(cur.calls,[])

if __name__=='__main__':unittest.main()
