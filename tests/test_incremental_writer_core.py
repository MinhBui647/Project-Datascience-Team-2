from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.tools.incremental_writer_core import (
    PreparedJsonVersion, safe_relative_path, validate_prepared,
    verify_existing_version, check_observation, prepare_json_version,
)


def make_version(**kwargs):
    payload = {"repo_id": "Toibinguz/epl-premier-league-dataset",
        "relative_path": "2026_2027_new/matches/fotmob/a.json",
        "pinned_revision": "a"*40, "source_code": "fotmob", "size_bytes": 99,
        "content_sha256": "b"*64, "git_blob_id": "c"*40,
        "lfs_sha256": None,
        "archive_relative_path": "raw-archive-v2/objects/bb/"+"b"*64,
        "records": (("item:0", "json_item", {"id": 1}),)}
    payload.update(kwargs)
    return PreparedJsonVersion(**payload)


class TestWriterContract(unittest.TestCase):
    def test_valid_example(self):
        validate_prepared(make_version())
    def test_safe_path_rejects_traversal(self):
        for p in ("raw_data/../secrets.json", "../raw_data/x.json", "raw_data//x.json",
                  "raw_data/./x.json", "raw_data/x\\y.json", "clean/foo.json"):
            self.assertFalse(safe_relative_path(p), p)
    def test_bad_repo(self):
        with self.assertRaises(ValueError): validate_prepared(make_version(repo_id=""))
    def test_unsupported_extension(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(relative_path="raw_data/a.csv"))
    def test_requires_revision_pin(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(pinned_revision="main"))
    def test_json_size_guard(self):
        for count in (0, -1, 20*1024*1024, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                validate_prepared(make_version(size_bytes=count))
    def test_lfs_hash_guard(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(lfs_sha256="e"*64))
    def test_canonical_archive_guard(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(archive_relative_path="raw-archive-v2/objects/bb/other"))
    def test_missing_remote_identity(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(git_blob_id=None))
    def test_record_locators_unique(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(records=(("item:0", "json_item", {}),
                                                     ("item:0", "json_item", {}))))
    def test_invalid_json_kind(self):
        with self.assertRaises(ValueError):
            validate_prepared(make_version(records=(("a", "tar_json", {}),)))
    def test_existing_version_same_is_reused(self):
        v = make_version()
        row=(13, "json", v.size_bytes, v.archive_relative_path, "verified", "success", 1, "fotmob")
        self.assertEqual(verify_existing_version(row, v), (13,1))
    def test_existing_version_incomplete_rejected(self):
        v=make_version()
        with self.assertRaises(RuntimeError):
            verify_existing_version((13,"json",99,v.archive_relative_path,"verified","pending",None,"fotmob"),v)
    def test_existing_version_metadata_conflict(self):
        v=make_version()
        with self.assertRaises(RuntimeError):
            verify_existing_version((13,"json",99,"wrong","verified","success",1,"fotmob"),v)
    def test_observation_correct(self):
        v=make_version()
        check_observation((13,"linked",99,v.git_blob_id,None),file_id=13,version=v)
    def test_observation_mismatch_fail_closed(self):
        v=make_version()
        with self.assertRaises(RuntimeError):
            check_observation((14,"linked",99,v.git_blob_id,None),file_id=13,version=v)
    def test_prepare_version_integrated_real_parser(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            data=json.dumps([{"a":1},{"a":2}]).encode()
            sha=hashlib.sha256(data).hexdigest()
            archive=root / "raw-archive-v2" / "objects" / sha[:2] / sha
            archive.parent.mkdir(parents=True)
            archive.write_bytes(data)
            blob=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\x00"+data).hexdigest()
            identity=SimpleNamespace(size=len(data),git_blob_id=blob,lfs_sha256=None)
            v=prepare_json_version("repo", "raw_data/foo.json", "1"*40, "fotmob",archive,identity,root=root)
            self.assertEqual(len(v.records),2)
            self.assertEqual(v.content_sha256,sha)
    def test_prepare_version_rejects_tampered_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            data=b'{"a":1}'
            sha=hashlib.sha256(data).hexdigest()
            archive=root / "raw-archive-v2" / "objects" / sha[:2] / sha
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'{"a":2}')
            blob=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\x00"+data).hexdigest()
            identity=SimpleNamespace(size=len(data),git_blob_id=blob,lfs_sha256=None)
            with self.assertRaises(ValueError):
                prepare_json_version("repo", "raw_data/foo.json", "1"*40, "fotmob",archive,identity,root=root)

if __name__ == "__main__":
    unittest.main()

# SQL transaction contract, tested with scripted cursor responses (not real DB).
class WriterSqlCursor:
    def __init__(self, prepared, *, existing=None, status="in_progress"):
        self.prepared=prepared
        self.existing=existing
        self.status=status
        self.operations=[]
        self.next_row=None
        self.rowcount=-1
        self.batch=[]
    def execute(self, sql, params=None):
        s=" ".join(sql.split()).lower()
        self.operations.append((s, params))
        self.rowcount=-1
        if "select current_database()" in s:
            self.next_row=("epl_raw_storage_v2",)
        elif "select pg_advisory_xact_lock" in s:
            self.next_row=(None,)
        elif "select status from raw.snapshot_runs" in s:
            self.next_row=(self.status,)
        elif "select source_id from raw.raw_sources" in s:
            self.next_row=(7,)
        elif "from raw.raw_files f join raw.raw_sources" in s:
            self.next_row=self.existing
        elif "insert into raw.raw_files" in s:
            self.next_row=(27,)
        elif "update raw.raw_files set parse_state" in s:
            self.rowcount=1
            self.next_row=None
        elif "select count(*) from raw.raw_records" in s:
            self.next_row=(len(self.prepared.records),)
        elif "select file_id,presence_status,manifest_size_bytes" in s:
            self.next_row=(27,"linked",self.prepared.size_bytes,
                           self.prepared.git_blob_id,self.prepared.lfs_sha256)
        else:
            self.next_row=None
    def fetchone(self):
        return self.next_row
    def executemany(self, sql, params):
        self.batch=list(params)
        self.operations.append(("insert batch records", self.batch))


class TestWriterSqlContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        raw=json.dumps([{"a":1},{"a":2}]).encode("utf-8")
        self.sha=hashlib.sha256(raw).hexdigest()
        obj=self.root/"raw-archive-v2"/"objects"/self.sha[:2]/self.sha
        obj.parent.mkdir(parents=True)
        obj.write_bytes(raw)
        self.blob=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\x00"+raw).hexdigest()
        identity=SimpleNamespace(size=len(raw),git_blob_id=self.blob,lfs_sha256=None)
        self.prepared=prepare_json_version("repo","raw_data/fixture.json","1"*40,
                       "fotmob",obj,identity,root=self.root)
        self.obj=obj
    def _run_with_fake_psycopg(self, cur, prepared=None):
        import sys
        from unittest.mock import patch
        from types import ModuleType
        fake_pkg=ModuleType("psycopg")
        fake_pkg.__path__=[]
        fake_types=ModuleType("psycopg.types")
        fake_types.__path__=[]
        fake_json=ModuleType("psycopg.types.json")
        fake_json.Jsonb=lambda data: data
        with patch.dict(sys.modules, {"psycopg":fake_pkg,
                   "psycopg.types":fake_types,"psycopg.types.json":fake_json}):
            from scripts.tools.incremental_writer_core import write_prepared_version
            return write_prepared_version(cur, 9, prepared or self.prepared,root=self.root)
    def test_insert_new_version_and_records(self):
        cur=WriterSqlCursor(self.prepared)
        self.assertEqual(self._run_with_fake_psycopg(cur),("INSERTED_NEW",27,2))
        self.assertEqual(len(cur.batch),2)
        self.assertTrue(any("insert into raw.file_observations" in x for x,_ in cur.operations))
    def test_reuse_existing_version_without_records(self):
        v=self.prepared
        cur=WriterSqlCursor(v, existing=(27,"json",v.size_bytes,v.archive_relative_path,
                             "verified","success",2,"fotmob"))
        self.assertEqual(self._run_with_fake_psycopg(cur),("REUSED_EXISTING",27,0))
        self.assertFalse(any("insert into raw.raw_records" in x for x,_ in cur.operations))
    def test_non_inprogress_snapshot_rejected_before_insert(self):
        cur=WriterSqlCursor(self.prepared,status="completed")
        with self.assertRaises(RuntimeError):self._run_with_fake_psycopg(cur)
        self.assertFalse(any("insert into" in x for x,_ in cur.operations))
    def test_mutated_archive_rejected_before_sql(self):
        self.obj.write_bytes(b'{"a":0}')
        cur=WriterSqlCursor(self.prepared)
        with self.assertRaises(ValueError):self._run_with_fake_psycopg(cur)
        self.assertEqual(cur.operations,[])
    def test_mutated_parsed_payload_rejected_before_sql(self):
        from dataclasses import replace
        bad=replace(self.prepared, records=(("item:0","json_item",{"a":999}),
                                           self.prepared.records[1]))
        cur=WriterSqlCursor(self.prepared)
        with self.assertRaises(RuntimeError):self._run_with_fake_psycopg(cur, bad)
        self.assertEqual(cur.operations,[])
