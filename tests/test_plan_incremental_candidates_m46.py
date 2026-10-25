"""M4.6.3d.2 read-only candidate planner offline regression; no DB/HF access.
Run from project root: python -m unittest scripts.tools.test_plan_incremental_candidates_m46 -v
"""
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

from scripts.tools import plan_incremental_candidates as m
from scripts.tools.incremental_reuse_linker import ReuseLink
from scripts.tools.incremental_writer_core import PreparedJsonVersion
from scripts.tools.incremental_sync import SyncAction, SyncPlan
from scripts.tools.register_incremental_snapshot import Registration
from scripts.tools.populate_incremental_reuse import ReuseRequest, TargetState

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "f" * 40
BASE = "e" * 40
FINGER = "a" * 64
SHA = "b" * 64
BLOB = "c" * 40
P = "2026_2027_new/matches/example.json"
U = "raw_data/espn/old.json"
ARCHIVE = "raw-archive-v2/objects/bb/" + SHA


def req(*, reuse=1):
    reg = Registration(REPO, 365, REV, FINGER, 1, 3, 1 + reuse, reuse, 1, 100, 1)
    return ReuseRequest(reg, 366, 12, 12 if reuse else 0)


def version():
    return PreparedJsonVersion(REPO, P, REV, "fotmob", 100, SHA, BLOB, None,
                               ARCHIVE, (("/0", "json_object", {"id": 1}),))


def link():
    return ReuseLink(U, 88, 50, BLOB, None)


def obs_reuse():
    return (U, 88, "linked", 50, BLOB, None)


def obs_candidate():
    return (P, 99, "linked", 100, BLOB, None)


def state(*, reuse=1, rows=None):
    r = req(reuse=reuse).registration
    if rows is None: rows = (obs_reuse(),) if reuse else ()
    return TargetState(REPO, REV, "in_progress", r.manifest_files,
                       r.raw_files, None, None, tuple(rows))


def pplan(*, reuse=1):
    v = version()
    actions = [SyncAction("UPSERT_VERSION_AFTER_REHASH", P, "NEW", None,
                           v.source_code, "json", v.size_bytes, None, v.git_blob_id)]
    if reuse:
        actions.insert(0, SyncAction("REUSE_FILE_ID", U, "UNCHANGED", 88,
                                     None, None, 50, None, BLOB))
    return SyncPlan(tuple(actions), 12, 12 if reuse else 0,
                    0 if reuse else 12, 1 + reuse, 100, FINGER)


def all_args(*, reuse=1, already=False):
    r = req(reuse=reuse)
    prepared = {P: version()}
    st = m.CandidateStatus(P, 99 if already else None,
                           "LINKED_VERIFIED" if already else "READY_INSERT_NEW_VERSION", 1, 100)
    return dict(req=r, plan=pplan(reuse=reuse), target_snapshot_state=state(reuse=reuse),
                actions=(NS(path=P, source_code="fotmob"),),
                archived=(NS(relative_path=P, action="ALREADY_ARCHIVED", sha256=SHA,
                             byte_count=100, record_count=1, archive_relative_path=ARCHIVE),),
                prepared=prepared, statuses=(st,), linked_reuse=reuse,
                linked_candidates=int(already), excluded=3-(1+reuse))


class ObservationCoverage(unittest.TestCase):
    def test_valid_pending(self):
        self.assertEqual(m.validate_observation_coverage((obs_reuse(),), (link(),),
                              {P: version()}, req=req()), (1, 0))
    def test_partial_retry(self):
        self.assertEqual(m.validate_observation_coverage((obs_reuse(), obs_candidate()),
                         (link(),), {P: version()}, req=req()), (1, 1))
    def test_zero_reuse(self):
        self.assertEqual(m.validate_observation_coverage((), (), {P: version()},
                         req=req(reuse=0)), (0, 0))
    def test_wrong_reuse_metadata(self):
        row = (U, 88, "linked", 51, BLOB, None)
        with self.assertRaisesRegex(RuntimeError, "REUSE observation"):
            m.validate_observation_coverage((row,), (link(),), {P: version()}, req=req())
    def test_candidate_metadata_drift(self):
        row = (P, 99, "linked", 101, BLOB, None)
        with self.assertRaisesRegex(RuntimeError, "Candidate observation"):
            m.validate_observation_coverage((obs_reuse(), row), (link(),),
                                            {P: version()}, req=req())
    def test_duplicate_observation_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            m.validate_observation_coverage((obs_reuse(), obs_reuse()), (link(),),
                                            {P: version()}, req=req())
    def test_out_of_scope_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "Unexpected observation"):
            m.validate_observation_coverage((obs_reuse(), ("raw_data/other.json", 1,
                            "linked", 10, BLOB, None)), (link(),), {P: version()}, req=req())
    def test_incomplete_reuse_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "not fully linked"):
            m.validate_observation_coverage((), (link(),), {P: version()}, req=req())
    def test_invalid_row_structure(self):
        with self.assertRaisesRegex(RuntimeError, "structure"):
            m.validate_observation_coverage(((P, 99, "linked"),), (link(),),
                                            {P: version()}, req=req())


class CandidatePlanValidation(unittest.TestCase):
    def test_valid_pending_candidate(self):
        self.assertEqual(m.validate_candidate_plan(**all_args()), 13)
    def test_valid_already_linked(self):
        a = all_args(already=True)
        a["target_snapshot_state"] = state(rows=(obs_reuse(), obs_candidate()))
        self.assertEqual(m.validate_candidate_plan(**a), 13)
    def test_zero_reuse_supported(self):
        self.assertEqual(m.validate_candidate_plan(**all_args(reuse=0)), 1)
    def fail(self, regex, **updates):
        a = all_args()
        a.update(updates)
        with self.assertRaisesRegex(RuntimeError, regex):
            m.validate_candidate_plan(**a)
    def test_wrong_fingerprint(self):
        self.fail("fingerprint", plan=replace(pplan(), fingerprint="d"*64))
    def test_completed_target_blocked(self):
        self.fail("Target snapshot", target_snapshot_state=replace(state(), status="completed"))
    def test_registered_manifest_blocked(self):
        self.fail("Target snapshot", target_snapshot_state=replace(state(), manifest_sha="a"*64))
    def test_manifest_count_drift(self):
        self.fail("non-Raw count", excluded=999)
    def test_duplicate_status_blocked(self):
        a = all_args(); a["statuses"] = (a["statuses"][0], a["statuses"][0])
        self.fail("coverage/count", statuses=a["statuses"])
    def test_unarchived_candidate_blocked(self):
        a = all_args()
        self.fail("content/DB", archived=(replace_ns(a["archived"][0], action="NOT_VERIFIED"),))
    def test_source_code_drift(self):
        self.fail("content/DB", actions=(NS(path=P, source_code="incorrect"),))
    def test_status_count_drift(self):
        a = all_args()
        self.fail("parsed record count", statuses=(replace(a["statuses"][0], record_count=3),))
    def test_candidate_link_mismatch(self):
        self.fail("Target observations", linked_candidates=1)
    def test_disallow_empty_records(self):
        a = all_args()
        a["prepared"] = {P: replace(version(), records=())}
        a["statuses"] = (m.CandidateStatus(P,None,"READY_INSERT_NEW_VERSION",0,100),)
        a["archived"] = (replace_ns(a["archived"][0], record_count=0),)
        a["req"] = replace(req(), registration=replace(req().registration, candidate_records=0))
        with self.assertRaisesRegex(RuntimeError, "Empty JSON"):
            m.validate_candidate_plan(**a)


def replace_ns(obj, **changes):
    return NS(**{**vars(obj), **changes})


class FakeCursor:
    def __init__(self, versions=(), records=0, obs=()):
        self.versions, self.records, self.obs = versions, records, obs
        self.sql = ""
        self.queries = []
    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.queries.append(self.sql)
    def fetchall(self):
        if "FROM raw.raw_files f" in self.sql:
            return list(self.versions)
        if "FROM raw.file_observations" in self.sql:
            return list(self.obs)
        raise AssertionError(self.sql)
    def fetchone(self):
        if "FROM raw.raw_records" in self.sql:
            return (self.records,)
        raise AssertionError(self.sql)


class DBAudit(unittest.TestCase):
    def audit(self, cur):
        return m.audit_candidate_db(cur, snapshot_id=366, repo_id=REPO, prepared={P: version()})
    def test_new_version_pending(self):
        cur = FakeCursor()
        self.assertEqual(self.audit(cur)[0].action, "READY_INSERT_NEW_VERSION")
        self.assertTrue(all(q.startswith("SELECT") for q in cur.queries))
    def test_existing_version_pending_link(self):
        expected = (99, "json", 100, ARCHIVE, "verified", "success", 1, "fotmob")
        cur = FakeCursor(versions=(expected,), records=1)
        self.assertEqual(self.audit(cur)[0].action, "VERSION_EXISTS_LINK_PENDING")
    def test_existing_linked_version(self):
        expected = (99, "json", 100, ARCHIVE, "verified", "success", 1, "fotmob")
        cur = FakeCursor(versions=(expected,), records=1,
                         obs=((99,"linked",100,BLOB,None),))
        self.assertEqual(self.audit(cur)[0].action, "LINKED_VERIFIED")
    def test_orphan_observation_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "Orphan"):
            self.audit(FakeCursor(obs=((99,"linked",100,BLOB,None),)))
    def test_duplicate_versions_blocked(self):
        good = (99, "json", 100, ARCHIVE, "verified", "success", 1, "fotmob")
        with self.assertRaisesRegex(RuntimeError, "Duplicate immutable"):
            self.audit(FakeCursor(versions=(good, good)))
    def test_corrupt_raw_record_count_blocked(self):
        good = (99, "json", 100, ARCHIVE, "verified", "success", 1, "fotmob")
        with self.assertRaisesRegex(RuntimeError, "Physical record count"):
            self.audit(FakeCursor(versions=(good,), records=0))
    def test_invalid_snapshot_id(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid candidate audit"):
            m.audit_candidate_db(FakeCursor(), snapshot_id=0, repo_id=REPO, prepared={P: version()})


class CLI(unittest.TestCase):
    def test_missing_required_params(self):
        with self.assertRaises(SystemExit):
            m.make_parser().parse_args(["--plan"])
    def test_no_write_flags_supported(self):
        parser = m.make_parser()
        self.assertNotIn("--apply", parser.format_help())
        self.assertNotIn("--commit", parser.format_help())
    def test_valid_request(self):
        args = m.make_parser().parse_args([
            "--baseline-snapshot", "365", "--target-snapshot-id", "366",
            "--target-revision", REV, "--expected-fingerprint", FINGER,
            "--expected-baseline-files", "1", "--expected-full-files", "3",
            "--expected-raw-files", "2", "--expected-reuse", "1",
            "--expected-files", "1", "--expected-bytes", "100",
            "--expected-records", "1", "--expected-baseline-records", "12",
            "--expected-reused-records", "12", "--plan"
        ])
        self.assertEqual(m.build_request(args).target_snapshot_id, 366)
    def test_invalid_revision_rejected(self):
        with self.assertRaises(RuntimeError):
            m.build_request(NS(repo_id=REPO, baseline_snapshot=365, target_revision="main",
                    expected_fingerprint=FINGER, expected_baseline_files=1,
                    expected_full_files=3, expected_raw_files=2, expected_reuse=1,
                    expected_files=1, expected_bytes=100, expected_records=1,
                    expected_baseline_records=12, expected_reused_records=12,
                    target_snapshot_id=366))


if __name__ == "__main__":
    unittest.main()
