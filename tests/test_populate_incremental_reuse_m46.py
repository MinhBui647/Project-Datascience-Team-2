"""M4.6.3c.1 offline regression. No HF requests, DB connections or writes.
Run: python -m unittest scripts.tools.test_populate_incremental_reuse_m46 -v
"""
from __future__ import annotations
import contextlib
import io
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from scripts.tools import populate_incremental_reuse as m
from scripts.tools import incremental_reuse_linker as l
from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.raw_sync_write_plan import ExistingFile

REPO = "Toibinguz/epl-premier-league-dataset"
BASE_REV = "e" * 40
REV = "f" * 40
MANIFEST_SHA = "a" * 64
MANIFEST_PATH = "raw-archive-v2/manifests/aa/" + MANIFEST_SHA + ".json"
BLOB = "b" * 40
SHA = "c" * 64
P = "raw_data/source/file.json"


def request(**changes):
    r = Registration(REPO, 365, REV, "d" * 64, 286, 304, 286, 272, 14, 5_458_820, 393)
    return replace(m.ReuseRequest(r, 366, 8_502_594, 8_502_201), **changes)


def zero_req():
    r = Registration(REPO, 365, REV, "d" * 64, 286, 306, 2, 0, 2, 100, 0)
    return m.ReuseRequest(r, 366, 8_502_594, 0)


def link():
    return l.ReuseLink(P, 1001, 100, BLOB, None)


def state(links=(None,), **kw):
    obs = () if links == (None,) else tuple(
        (x.relative_path, x.file_id, "linked", x.size_bytes, x.git_blob_id, x.lfs_sha256)
        for x in links)
    return replace(m.TargetState(REPO, REV, "in_progress", 304, 286, None, None, obs), **kw)


class RequestContracts(unittest.TestCase):
    def test_valid_pinned_request(self): request().validate()
    def test_zero_reuse_with_nonempty_candidates_valid(self): zero_req().validate()
    def test_zero_reuse_nonzero_records_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "Zero REUSE"):
            replace(zero_req(), expected_reused_records=1).validate()
    def test_bad_target_snapshot_id(self):
        for x in [0, -1, True, None]:
            with self.subTest(x=x), self.assertRaises(RuntimeError):
                replace(request(), target_snapshot_id=x).validate()
    def test_same_target_and_baseline_id_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "equals baseline"):
            replace(request(), target_snapshot_id=365).validate()
    def test_invalid_baseline_record_counts(self):
        for value in [-1, True, None]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                replace(request(), expected_baseline_records=value).validate()
    def test_reused_record_count_exceeds_baseline_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "exceed"):
            replace(request(), expected_reused_records=8_502_595).validate()
    def test_invalid_registration_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "SHA40"):
            replace(request(), registration=replace(request().registration, target_revision="main")).validate()


class ObservationState(unittest.TestCase):
    def _r(self):
        r = replace(request().registration, reuse_files=1, raw_files=15, candidate_files=14,
                    manifest_files=304, baseline_files=286)
        return m.ReuseRequest(r, 366, 8_502_594, 100)

    def _s(self, existing=(), **kw):
        return replace(state(existing), raw_files=15, **kw)

    def test_empty_observations_pending(self):
        self.assertEqual(m.validate_target_state(self._s(), (link(),), self._r()), (1,0))
    def test_idempotent_existing_link(self):
        self.assertEqual(m.validate_target_state(self._s((link(),)), (link(),), self._r()), (0,1))
    def test_zero_reuse_no_observations(self):
        s = replace(state(), manifest_files=306, raw_files=2)
        self.assertEqual(m.validate_target_state(s, (), zero_req()), (0,0))
    def test_wrong_status_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "state differs"):
            m.validate_target_state(self._s(status="completed"), (link(),), self._r())
    def test_manifest_already_registered_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "state differs"):
            m.validate_target_state(self._s(manifest_sha=MANIFEST_SHA), (link(),), self._r())
    def test_unexpected_candidate_observation_blocked(self):
        s = self._s()
        s = replace(s, observations=(("raw_data/extra.json", 9, "linked", 3, BLOB, None),))
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            m.validate_target_state(s, (link(),), self._r())
    def test_same_file_id_different_metadata_blocked(self):
        s = self._s((link(),))
        s = replace(s, observations=((P, 1001, "linked", 101, BLOB, None),))
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            m.validate_target_state(s, (link(),), self._r())
    def test_duplicate_target_observation_blocked(self):
        s = self._s((link(), link()))
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            m.validate_target_state(s, (link(),), self._r())
    def test_duplicate_reuse_ids_blocked(self):
        links = (link(), replace(link(), relative_path="raw_data/another.json"))
        with self.assertRaisesRegex(RuntimeError, "REUSE link count"):
            m.validate_target_state(self._s(), links, self._r())


class FakeDB:
    def __init__(self, **overrides):
        self.values = dict(
            db=("epl_raw_storage_v2",),
            baseline=(365, BASE_REV),
            base=(REPO, BASE_REV, "completed", 286, MANIFEST_SHA, MANIFEST_PATH),
            latest=(365,),
            observations=[(P, "linked", 1001, SHA, 100, 7, "verified", "success")],
            target=(REPO, REV, "in_progress", 304, 286, None, None),
            target_obs=[],
        )
        self.values.update(overrides)
        self.last = ""
        self.calls = []

    def execute(self, sql, args=None):
        self.last = " ".join(sql.split())
        self.calls.append((self.last, args))

    def _which(self):
        q=self.last
        if "current_database()" in q: return "db"
        if "SELECT repo_id,hf_revision,status,raw_scope_file_count" in q: return "base"
        if "ORDER BY observed_at DESC" in q: return "latest"
        if "FROM raw.file_observations o" in q: return "observations"
        if "SELECT repo_id,hf_revision,status,manifest_file_count" in q: return "target"
        if "FROM raw.file_observations WHERE snapshot_id=%s" in q: return "target_obs"
        raise AssertionError("Unrecognized DB SELECT: " + q)

    def fetchone(self): return self.values[self._which()]
    def fetchall(self): return self.values[self._which()]


def dbreq():
    r = Registration(REPO, 365, REV, "d" * 64, 1, 2, 2, 1, 1, 10, 1)
    return m.ReuseRequest(r, 366, 7, 7)


class DBContract(unittest.TestCase):
    def test_expected_baseline_and_target_state(self):
        cur = FakeDB(base=(REPO, BASE_REV, "completed", 1, MANIFEST_SHA, MANIFEST_PATH),
                     target=(REPO, REV, "in_progress", 2, 2, None, None))
        rev, existing, target = m.inspect_db(cur, dbreq())
        self.assertEqual(rev, BASE_REV)
        self.assertEqual(existing[P].file_id, 1001)
        self.assertEqual(target.revision, REV)
        self.assertFalse(any("INSERT" in q or "UPDATE" in q for q, _ in cur.calls))

    def fail(self, pattern, **changes):
        vals = dict(base=(REPO, BASE_REV, "completed", 1, MANIFEST_SHA, MANIFEST_PATH),
                    target=(REPO, REV, "in_progress", 2, 2, None, None))
        vals.update(changes)
        with self.assertRaisesRegex(RuntimeError, pattern):
            m.inspect_db(FakeDB(**vals), dbreq())
    def test_wrong_database(self): self.fail("Wrong PostgreSQL", db=("wrong",))
    def test_missing_baseline(self): self.fail("Baseline snapshot", base=None)
    def test_no_baseline_manifest(self): self.fail("Baseline snapshot", base=(REPO, BASE_REV,"completed",1,None,None))
    def test_outdated_baseline(self): self.fail("no longer latest", latest=(367,))
    def test_malformed_baseline_observation(self):
        self.fail("Invalid/duplicate", observations=[(P,"linked",1001,SHA,100,7,"skipped","success")])
    def test_duplicate_baseline_file_id(self):
        self.fail("Invalid/duplicate", observations=[(P,"linked",1001,SHA,100,7,"verified","success"),
                        ("raw_data/another.json","linked",1001,SHA,100,7,"verified","success")])
    def test_baseline_record_count_drift(self):
        self.fail("counts changed", observations=[(P,"linked",1001,SHA,100,6,"verified","success")])
    def test_missing_target(self): self.fail("not found", target=None)


class PlanVerification(unittest.TestCase):
    def _plan(self):
        return SimpleNamespace(fingerprint=dbreq().registration.fingerprint,
                               target_raw_count=2, baseline_records=7,
                               reused_records=7, candidate_bytes=10,
                               count=lambda typ: {"UNCHANGED":1,"NEW":1,"CHANGED":0,
                                                  "REMOVED":0,"REVIEW":0}[typ])
    def test_valid_plan(self):
        with patch.object(m, "assemble_plan", return_value=self._plan()), \
             patch.object(m, "build_reuse_links", return_value=(link(),)):
            plan, links = m.verify_plan(dbreq(), {}, {P:1,"b":2}, 0, {}, set())
        self.assertEqual(len(links),1)
    def test_fingerprint_drift_blocked(self):
        p = self._plan(); p.fingerprint = "e"*64
        with patch.object(m, "assemble_plan", return_value=p), self.assertRaisesRegex(RuntimeError,"fingerprint"):
            m.verify_plan(dbreq(), {}, {P:1,"b":2}, 0, {}, set())
    def test_removed_blocked(self):
        p = self._plan(); p.count = lambda k: {"UNCHANGED":1,"NEW":1,"CHANGED":0,"REMOVED":1,"REVIEW":0}[k]
        with patch.object(m, "assemble_plan", return_value=p), self.assertRaisesRegex(RuntimeError,"counts changed"):
            m.verify_plan(dbreq(), {}, {P:1,"b":2}, 0, {}, set())
    def test_full_manifest_mismatch_blocked(self):
        with patch.object(m, "assemble_plan", return_value=self._plan()), self.assertRaisesRegex(RuntimeError,"counts changed"):
            m.verify_plan(dbreq(), {}, {P:1,"b":2}, 1, {}, set())
    def test_zero_reuse_plan(self):
        req = zero_req()
        p = SimpleNamespace(fingerprint=req.registration.fingerprint, target_raw_count=2,
             baseline_records=req.expected_baseline_records, reused_records=0,
             candidate_bytes=100,
             count=lambda typ: {"UNCHANGED":0,"NEW":2,"CHANGED":0,"REMOVED":0,"REVIEW":0}[typ])
        with patch.object(m,"assemble_plan",return_value=p),\
             patch.object(m,"build_reuse_links",return_value=()):
            _, links = m.verify_plan(req, {}, {"x":1,"y":2}, 304, {}, set())
        self.assertEqual(links, ())


class LinkerContract(unittest.TestCase):
    def test_empty_batch_rechecks_snapshot_and_baseline(self):
        class Cursor:
            last=""
            calls=[]
            def execute(self, sql, args=None):
                self.last=" ".join(sql.split()); self.calls.append(self.last)
            def fetchone(self):
                if "current_database" in self.last: return ("epl_raw_storage_v2",)
                if "SELECT status, hf_revision" in self.last: return ("in_progress",REV)
                if "SELECT status FROM" in self.last: return ("completed",)
                raise AssertionError(self.last)
        c=Cursor()
        self.assertEqual(l.link_reused_versions(c, baseline_snapshot_id=365,
                         target_snapshot_id=366,repo_id=REPO,target_revision=REV,links=()), (0,0))
        self.assertTrue(any("FOR UPDATE" in x for x in c.calls))
        self.assertFalse(any("INSERT INTO" in x for x in c.calls))

    def test_empty_batch_wrong_target_blocks(self):
        class Cursor:
            def execute(self, sql, args=None): self.last=sql
            def fetchone(self):
                if "current_database" in self.last: return ("epl_raw_storage_v2",)
                return ("completed",REV)
        with self.assertRaisesRegex(RuntimeError,"not in_progress"):
            l.link_reused_versions(Cursor(),baseline_snapshot_id=365,target_snapshot_id=366,
                                   repo_id=REPO,target_revision=REV,links=())
    def test_malformed_links_type_blocked(self):
        with self.assertRaisesRegex(ValueError,"expected a tuple"):
            l.link_reused_versions(None,baseline_snapshot_id=365,target_snapshot_id=366,
                                   repo_id=REPO,target_revision=REV,links=[])


class RealLinkerRegression(unittest.TestCase):
    def test_build_links_zero_reuse_all_candidates(self):
        from scripts.tools.incremental_sync import SyncAction, SyncPlan
        from scripts.tools.check_raw_updates import FileIdentity
        items = ("raw_data/a.json", "raw_data/b.json")
        actions = tuple(SyncAction("UPSERT_VERSION_AFTER_REHASH", path, "NEW",
                                    None, "source", "json", 50, None, BLOB)
                        for path in items)
        plan = SyncPlan(actions, 0, 0, 0, 2, 100, "d"*64)
        target = {path: FileIdentity(50, BLOB, None) for path in items}
        self.assertEqual(l.build_reuse_links(plan, target), ())

    def test_existing_observation_remains_idempotent(self):
        class Cursor:
            rowcount = 0
            def execute(self, sql, args=None):
                self.sql=" ".join(sql.split())
            def fetchone(self):
                q=self.sql
                if "current_database" in q: return ("epl_raw_storage_v2",)
                if "SELECT status, hf_revision" in q: return ("in_progress",REV)
                if "SELECT status FROM raw.snapshot_runs" in q: return ("completed",)
                if "JOIN raw.raw_files f" in q:
                    return (1001,"linked",100,"verified","success",BLOB,None,SHA)
                if "FROM raw.file_observations WHERE snapshot_id=%s" in q:
                    return (1001,"linked",100,BLOB,None)
                raise AssertionError(q)
        c=Cursor()
        self.assertEqual(l.link_reused_versions(c, baseline_snapshot_id=365,
                         target_snapshot_id=366,repo_id=REPO,target_revision=REV,
                         links=(link(),)), (0,1))

    def test_baseline_identity_mismatch_blocks_insertion(self):
        class Cursor:
            def __init__(self): self.inserted=False
            def execute(self,sql,args=None):
                self.sql=" ".join(sql.split())
                if "INSERT INTO" in self.sql: self.inserted=True
            def fetchone(self):
                if "current_database" in self.sql: return ("epl_raw_storage_v2",)
                if "SELECT status, hf_revision" in self.sql: return ("in_progress",REV)
                if "SELECT status FROM raw.snapshot_runs" in self.sql: return ("completed",)
                if "JOIN raw.raw_files f" in self.sql:
                    return (1001,"linked",100,"verified","success","a"*40,None,SHA)
                raise AssertionError(self.sql)
        c=Cursor()
        with self.assertRaisesRegex(RuntimeError, "identities differ"):
            l.link_reused_versions(c,baseline_snapshot_id=365,target_snapshot_id=366,
                                   repo_id=REPO,target_revision=REV,links=(link(),))
        self.assertFalse(c.inserted)


class CLIRegression(unittest.TestCase):
    def test_plan_mode_never_invokes_writer(self):
        r=request()
        args=["populate_incremental_reuse.py", "--baseline-snapshot","365",
              "--target-snapshot-id","366", "--target-revision",REV,
              "--expected-fingerprint",r.registration.fingerprint,
              "--expected-baseline-files","286", "--expected-full-files","304",
              "--expected-raw-files","286", "--expected-reuse","272",
              "--expected-files","14", "--expected-bytes","5458820",
              "--expected-records","393", "--expected-baseline-records","8502594",
              "--expected-reused-records","8502201", "--plan"]
        conn=MagicMock()
        conn.__enter__.return_value=conn
        connection=SimpleNamespace(connect=lambda **kw:conn)
        with patch.object(sys,"argv",args),patch.dict(sys.modules,{"psycopg":connection}),\
             patch.object(m,"load_baseline",return_value=(BASE_REV,{})),\
             patch.object(m,"load_target",return_value=(REV,{},18)),\
             patch.object(m,"read_existing_source_codes",return_value=set()),\
             patch.object(m,"inspect_db",return_value=(BASE_REV,{},state())),\
             patch.object(m,"verify_plan",return_value=(SimpleNamespace(fingerprint=r.registration.fingerprint,reused_records=8_502_201),())),\
             patch.object(m,"validate_target_state",return_value=(272,0)),\
             patch.object(m,"db_parameters",return_value={"dbname":"epl_raw_storage_v2","options":""}),\
             patch.object(m,"link_reused_versions") as writer,\
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(),0)
        writer.assert_not_called()

    def test_link_requires_confirmation(self):
        args=["populate_incremental_reuse.py", "--link"]
        with patch.object(sys,"argv",args), contextlib.redirect_stderr(io.StringIO()),\
             self.assertRaises(SystemExit) as cm:
            m.main()
        self.assertEqual(cm.exception.code,2)


if __name__ == '__main__':
    unittest.main()
