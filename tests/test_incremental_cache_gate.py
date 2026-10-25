from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.incremental_sync import SyncAction, SyncPlan
from scripts.tools.incremental_cache_gate import (candidate_actions, check_cached_candidate,
    assess_results)


def make_fixture():
    body = json.dumps({"match_id": 7, "events": [1,2,3]}).encode()
    sha = hashlib.sha256(body).hexdigest()
    blob = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
    path = "2026_2027_new/matches/fotmob/1000.json"
    old_path = "2026_2027_new/fotmob_all_matches.json"
    action = SyncAction("UPSERT_VERSION_AFTER_REHASH", path, "NEW", None, "fotmob",
                        "json", len(body), None, blob)
    reuse = SyncAction("REUSE_FILE_ID", old_path, "UNCHANGED", 123,
                       None, None, 5, None, "1"*40)
    plan = SyncPlan((reuse, action), 1000, 999, 1, 2, len(body), "f"*64)
    return body, action, plan, {path:FileIdentity(len(body),blob,None),
                                old_path:FileIdentity(5,"1"*40,None)}


class TestCacheGate(unittest.TestCase):
    def test_actions(self):
        body,a,p,t = make_fixture()
        self.assertEqual((a,), candidate_actions(p,t))
    def test_bad_target_count(self):
        body,a,p,t = make_fixture()
        with self.assertRaises(ValueError): candidate_actions(p,{a.path:t[a.path]})
    def test_bad_path_set(self):
        body,a,p,t = make_fixture()
        t["raw_data/fotmob/fake.json"]=t.pop(a.path)
        with self.assertRaises(ValueError): candidate_actions(p,t)
    def test_bad_bytes(self):
        body,a,p,t = make_fixture()
        bad = SyncPlan(p.actions,p.baseline_records,p.reused_records,p.superseded_records,p.target_raw_count,0,p.fingerprint)
        with self.assertRaises(ValueError): candidate_actions(bad,t)
    def test_bad_format(self):
        body,a,p,t = make_fixture()
        fake = SyncAction(a.kind,a.path,a.category,a.baseline_file_id,a.source_code,'csv',a.expected_bytes,a.lfs_sha256,a.git_blob_id)
        bad = SyncPlan((p.actions[0],fake),p.baseline_records,p.reused_records,p.superseded_records,p.target_raw_count,p.candidate_bytes,p.fingerprint)
        with self.assertRaises(ValueError): candidate_actions(bad,t)
    def test_valid_local_json(self):
        body,a,p,t = make_fixture()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/'test.json';file.write_bytes(body)
            record=check_cached_candidate(a,t[a.path],file)
            self.assertEqual((record.records,record.bytes),(1,len(body)))
            self.assertEqual(assess_results(p,(record,),expected_record_count=1),1000)
    def test_modified_local_json(self):
        body,a,p,t = make_fixture()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/'test.json';file.write_bytes(body+b' ')
            with self.assertRaises(ValueError):check_cached_candidate(a,t[a.path],file)
    def test_wrong_hf_identity(self):
        body,a,p,t = make_fixture()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/'test.json';file.write_bytes(body)
            with self.assertRaises(ValueError):
                check_cached_candidate(a,FileIdentity(len(body),"0"*40,None),file)
    def test_wrong_expected_record_count(self):
        body,a,p,t = make_fixture()
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/'test.json';file.write_bytes(body)
            record=check_cached_candidate(a,t[a.path],file)
            with self.assertRaises(ValueError):assess_results(p,(record,),expected_record_count=2)
    def test_incomplete_checks(self):
        body,a,p,t = make_fixture()
        with self.assertRaises(ValueError):assess_results(p,tuple())
    def test_duplicate_checks(self):
        body,a,p,t = make_fixture()
        dummy=SimpleNamespace(path=a.path,records=1,bytes=len(body),sha256="a"*64)
        with self.assertRaises(ValueError):assess_results(p,(dummy,dummy))
    def test_byte_mismatch_result(self):
        body,a,p,t = make_fixture()
        dummy=SimpleNamespace(path=a.path,records=1,bytes=len(body)-1,sha256="a"*64)
        with self.assertRaises(ValueError):assess_results(p,(dummy,))


if __name__ == "__main__": unittest.main()
