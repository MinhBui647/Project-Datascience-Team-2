#!/usr/bin/env python3
"""M4.6.4d.4b: offline tests only; no PostgreSQL/HF calls or production writes."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.tools.incremental_execution_context import (
    CandidateReceipt, CandidateSpec, PlannedContext, PreparedContext,
    RegisteredContext, publish_checkpoint,
)
from scripts.tools.incremental_recovery_audit import (
    SnapshotEvidence, ObservationEvidence, PhysicalEvidence, DatabaseEvidence,
    RecoveryAssessment, read_checkpoints, verified_manifest,
    verify_candidate_archives, classify_registered, audit_recovery,
    parse_args, format_report, main, read_db_evidence,
)

REPO = "Toibinguz/epl-premier-league-dataset"
BASE = "1"*40
REV = "2"*40
FP = "f"*64
A = "2026_2027_new/fpl_fixtures_latest.json"
B = "raw_data/fotmob/match.json"
C = "raw_data/understat/old.csv"
BODY_A = b'{"a":1}'
BODY_B = b'{"b":[1,2]}'
SHA_A = hashlib.sha256(BODY_A).hexdigest()
SHA_B = hashlib.sha256(BODY_B).hexdigest()
GIT_A = "a"*40
GIT_C = "c"*40


def planned():
    return PlannedContext(
        repo_id=REPO, baseline_snapshot_id=365, baseline_revision=BASE,
        target_revision=REV, plan_fingerprint=FP,
        expected_baseline_files=2, expected_full_files=4,
        expected_raw_files=3, expected_reuse=1, expected_files=2,
        expected_bytes=len(BODY_A)+len(BODY_B), expected_baseline_records=100,
        expected_reused_records=80,
        candidates=(CandidateSpec(A, "CHANGED", len(BODY_A), "fpl", GIT_A, None),
                    CandidateSpec(B, "NEW", len(BODY_B), "fotmob", None, SHA_B)),
    )


def manifest_bytes():
    m = {"repo_id": REPO, "repo_type": "dataset", "resolved_commit": REV,
         "file_count": 4,
         "files": [
             {"relative_path": "README.md", "format": "md", "hf_revision": REV, "size_bytes": 5},
             {"relative_path": A, "format": "json", "hf_revision": REV, "size_bytes": len(BODY_A),
              "git_blob_id": GIT_A, "lfs_sha256": ""},
             {"relative_path": B, "format": "json", "hf_revision": REV, "size_bytes": len(BODY_B),
              "git_blob_id": "", "lfs_sha256": SHA_B},
             {"relative_path": C, "format": "csv", "hf_revision": REV, "size_bytes": 200,
              "git_blob_id": GIT_C, "lfs_sha256": ""}]}
    return (json.dumps(m, sort_keys=True, separators=(",", ":"))+"\n").encode()


def prepared():
    p = planned()
    digest = hashlib.sha256(manifest_bytes()).hexdigest()
    return PreparedContext(p,
        (CandidateReceipt(A, len(BODY_A), SHA_A, 2),
         CandidateReceipt(B, len(BODY_B), SHA_B, 3)),
        5, 85, digest,
        f"reports/m4_6_manifests/{REV}/hf_manifest_v2.json",
        f"raw-archive-v2/manifests/{digest[:2]}/{digest}.json")


def registered():
    return RegisteredContext(prepared(), 366)


def snapshot_baseline():
    return SnapshotEvidence(365, REPO, BASE, "completed", 3, 2, "e"*64,
                           f"raw-archive-v2/manifests/ee/{'e'*64}.json")


def snapshot_target(*, sha=None, archive=None, status="in_progress"):
    return SnapshotEvidence(366, REPO, REV, status, 4, 3, sha, archive)


def obs_reuse(*, file_id=500, baseline_file_id=500):
    sha="d"*64
    return ObservationEvidence(C, REPO, "linked", 200, GIT_C, None,
        file_id, REPO, C, "csv", 200, sha,
        f"raw-archive-v2/objects/dd/{sha}", "verified", "success", 80,
        "understat", baseline_file_id, 200, GIT_C, None)


def obs_a(*, file_id=501, baseline_file_id=400):
    return ObservationEvidence(A, REPO, "linked", len(BODY_A), GIT_A, None,
        file_id, REPO, A, "json", len(BODY_A), SHA_A,
        f"raw-archive-v2/objects/{SHA_A[:2]}/{SHA_A}", "verified", "success", 2,
        "fpl", baseline_file_id, len(BODY_A)-1, "a"*40, None)


def obs_b(*, file_id=502):
    return ObservationEvidence(B, REPO, "linked", len(BODY_B), None, SHA_B,
        file_id, REPO, B, "json", len(BODY_B), SHA_B,
        f"raw-archive-v2/objects/{SHA_B[:2]}/{SHA_B}", "verified", "success", 3,
        "fotmob", None, None, None, None)


def db_evidence(observations=(), *, sha=None, archive=None, status="in_progress",
                latest=365, phys_override=None):
    obs=tuple(observations)
    physical=tuple(PhysicalEvidence(x.file_id, x.record_count, x.record_count, 0)
                   for x in obs if x.path in (A,B))
    return DatabaseEvidence(latest, BASE, snapshot_baseline(),
                            snapshot_target(sha=sha, archive=archive, status=status),
                            obs, physical if phys_override is None else tuple(phys_override), 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.joinpath("reports").mkdir()
        self.root.joinpath("raw-archive-v2").mkdir()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def stages(self, count):
        seq = (planned(), prepared(), registered())
        for ctx in seq[:count]:
            publish_checkpoint(self.root, ctx)

    def manifest_files(self, *, report=True, archive=True):
        p = prepared()
        if report:
            self.write(p.manifest_report, manifest_bytes())
        if archive:
            self.write(p.manifest_archive, manifest_bytes())

    def archive_files(self, *, a=True, b=True):
        if a:
            self.write(f"raw-archive-v2/objects/{SHA_A[:2]}/{SHA_A}", BODY_A)
        if b:
            self.write(f"raw-archive-v2/objects/{SHA_B[:2]}/{SHA_B}", BODY_B)

    def audit(self, status="in_progress", observations=(), *, data=None):
        state=SimpleNamespace(snapshot_id=366, status=status) if status else None
        provider = mock.Mock(return_value=data or db_evidence(observations))
        result = audit_recovery(repo_id=REPO, target_revision=REV, root=self.root,
                                state_reader=mock.Mock(return_value=state),
                                evidence_reader=provider)
        return result, provider


class CheckpointTests(Fixture):
    def test_empty_checkpoints(self): self.assertEqual(read_checkpoints(self.root, REV), (None,None,None))
    def test_only_planned(self):
        self.stages(1)
        self.assertEqual(read_checkpoints(self.root, REV)[0], planned())
    def test_two_stages(self):
        self.stages(2)
        self.assertEqual(read_checkpoints(self.root, REV)[1], prepared())
    def test_three_stages(self):
        self.stages(3)
        self.assertEqual(read_checkpoints(self.root, REV)[2], registered())
    def test_missing_parent_checkpoint(self):
        self.stages(3)
        (self.root / f"reports/m4_6_runs/{REV}/planned.json").unlink()
        with self.assertRaises(RuntimeError): read_checkpoints(self.root, REV)
    def test_corrupt_checkpoint(self):
        self.stages(1)
        p=self.root / f"reports/m4_6_runs/{REV}/planned.json"
        p.write_bytes(p.read_bytes()+b" ")
        with self.assertRaises(RuntimeError): read_checkpoints(self.root, REV)
    def test_wrong_stage_chain(self):
        self.stages(3)
        (self.root/f"reports/m4_6_runs/{REV}/prepared.json").unlink()
        with self.assertRaises(RuntimeError): read_checkpoints(self.root, REV)
    def test_other_revision_has_no_checkpoints(self):
        self.stages(1)
        self.assertEqual(read_checkpoints(self.root, BASE),(None,None,None))


class ManifestTests(Fixture):
    def test_missing_manifest_is_incomplete(self): self.assertIsNone(verified_manifest(self.root,prepared()))
    def test_missing_archive_is_incomplete(self):
        self.manifest_files(archive=False)
        self.assertIsNone(verified_manifest(self.root,prepared()))
    def test_valid_manifest(self):
        self.manifest_files()
        self.assertEqual(set(verified_manifest(self.root,prepared())), {A,B,C})
    def test_corrupt_report(self):
        self.manifest_files()
        self.write(prepared().manifest_report, b"bad")
        with self.assertRaises(RuntimeError): verified_manifest(self.root,prepared())
    def test_corrupt_archive(self):
        self.manifest_files()
        self.write(prepared().manifest_archive, b"bad")
        with self.assertRaises(RuntimeError): verified_manifest(self.root,prepared())
    def test_identity_mismatch(self):
        self.manifest_files()
        data=json.loads(manifest_bytes()); data["repo_id"]="evil"
        corrupt=(json.dumps(data,sort_keys=True)+"\n").encode()
        self.write(prepared().manifest_report,corrupt)
        self.write(prepared().manifest_archive,corrupt)
        with self.assertRaises(RuntimeError): verified_manifest(self.root,prepared())
    def test_archive_objects_missing(self):
        self.assertEqual(set(verify_candidate_archives(self.root,prepared())), {A,B})
    def test_partial_archive_objects(self):
        self.archive_files(a=True,b=False)
        self.assertEqual(verify_candidate_archives(self.root,prepared()), (B,))
    def test_all_archive_objects(self):
        self.archive_files()
        self.assertEqual(verify_candidate_archives(self.root,prepared()), ())
    def test_corrupted_candidate_archive(self):
        self.archive_files()
        self.write(f"raw-archive-v2/objects/{SHA_A[:2]}/{SHA_A}", b"x"*len(BODY_A))
        with self.assertRaises(RuntimeError): verify_candidate_archives(self.root,prepared())
    def test_wrong_candidate_size(self):
        self.archive_files()
        self.write(f"raw-archive-v2/objects/{SHA_A[:2]}/{SHA_A}", b"bad")
        with self.assertRaises(RuntimeError): verify_candidate_archives(self.root,prepared())


class ClassifyTests(unittest.TestCase):
    def raw(self): return {x["relative_path"]: x for x in json.loads(manifest_bytes())["files"] if x["relative_path"].startswith(("raw_data/","2026_2027_new/"))}
    def classify(self, obs=(), **kwargs): return classify_registered(registered(),db_evidence(obs,**kwargs),self.raw())
    def test_pending_reuse(self):
        self.assertEqual(self.classify().recovery_state,"REGISTERED_PENDING_REUSE")
    def test_reuse_complete(self):
        self.assertEqual(self.classify((obs_reuse(),)).recovery_state,"REUSE_COMPLETE")
    def test_candidates_partial(self):
        a=self.classify((obs_reuse(),obs_a()))
        self.assertEqual((a.recovery_state,a.verified_candidates),("CANDIDATES_PARTIAL",1))
    def test_candidates_complete(self):
        self.assertEqual(self.classify((obs_reuse(),obs_a(),obs_b())).recovery_state,"CANDIDATES_COMPLETE")
    def test_manifest_registered(self):
        p=prepared()
        self.assertEqual(self.classify((obs_reuse(),obs_a(),obs_b()),sha=p.manifest_sha256,archive=p.manifest_archive).recovery_state,"MANIFEST_REGISTERED")
    def test_no_write_authorized(self):
        self.assertFalse(self.classify((obs_reuse(),)).write_authorized)
    def test_manual_reuse_next_step(self):
        self.assertEqual(self.classify().next_step,"REUSE_PLAN_REVIEW")
    def test_manual_candidate_next_step(self):
        self.assertEqual(self.classify((obs_reuse(),)).next_step,"CANDIDATE_PLAN_REVIEW")
    def test_wrong_latest_baseline(self):
        with self.assertRaises(RuntimeError): self.classify(latest=364)
    def test_wrong_target_status(self):
        with self.assertRaises(RuntimeError): self.classify(status="completed")
    def test_wrong_baseline_count(self):
        e=db_evidence()
        e=replace(e,baseline=replace(e.baseline,raw_files=3))
        with self.assertRaises(RuntimeError): classify_registered(registered(),e,self.raw())
    def test_wrong_baseline_record_references(self):
        e=replace(db_evidence(),baseline_record_references=99)
        with self.assertRaises(RuntimeError): classify_registered(registered(),e,self.raw())
    def test_reuse_wrong_baseline_id(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(baseline_file_id=9),))
    def test_candidate_precedes_reuse(self):
        with self.assertRaises(RuntimeError): self.classify((obs_a(),))
    def test_candidate_existing_version_still_candidate(self):
        # Even if it used a file version with the same ID as baseline.
        self.assertEqual(self.classify((obs_reuse(),obs_a(file_id=400,baseline_file_id=400))).verified_candidates,1)
    def test_duplicate_observation(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),obs_reuse()))
    def test_foreign_observation(self):
        with self.assertRaises(RuntimeError): self.classify((replace(obs_reuse(),path="raw_data/foreign.csv"),))
    def test_invalid_raw_archive_location(self):
        with self.assertRaises(RuntimeError): self.classify((replace(obs_reuse(),archive_path="tmp/fake"),))
    def test_candidate_wrong_record_count(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),replace(obs_a(),record_count=7)))
    def test_candidate_wrong_source(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),replace(obs_a(),source_code="wrong")))
    def test_candidate_missing_physical(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),obs_a()),phys_override=())
    def test_candidate_extra_physical(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),),phys_override=(PhysicalEvidence(999,1,1,0),))
    def test_candidate_duplicate_locators(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),obs_a()),phys_override=(PhysicalEvidence(501,2,1,0),))
    def test_candidate_with_tar_member(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),obs_a()),phys_override=(PhysicalEvidence(501,2,2,1),))
    def test_manifest_registered_too_early(self):
        p=prepared()
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),),sha=p.manifest_sha256,archive=p.manifest_archive)
    def test_partial_manifest_identity(self):
        with self.assertRaises(RuntimeError): self.classify((),sha=prepared().manifest_sha256)
    def test_wrong_manifest_sha(self):
        with self.assertRaises(RuntimeError): self.classify((obs_reuse(),obs_a(),obs_b()),sha="e"*64,archive=prepared().manifest_archive)
    def test_unexpected_snapshot_identity(self):
        e=db_evidence()
        e=replace(e,target=replace(e.target,repo_id="other"))
        with self.assertRaises(RuntimeError): classify_registered(registered(),e,self.raw())
    def test_candidate_and_reuse_sum(self):
        a=self.classify((obs_reuse(),obs_a(),obs_b()))
        self.assertEqual((a.verified_reuse,a.verified_candidates),(1,2))


class AuditTests(Fixture):
    def ready(self, count=3):
        self.stages(count)
        self.manifest_files()
        self.archive_files()
    def test_completed_skips_without_checkpoints(self):
        result,reader=self.audit(status="completed")
        self.assertEqual(result.recovery_state,"COMPLETED")
        reader.assert_not_called()
    def test_absent_no_checkpoints(self):
        result,reader=self.audit(status=None)
        self.assertEqual(result.recovery_state,"NO_REGISTERED_SNAPSHOT")
        reader.assert_not_called()
    def test_absent_planned_only(self):
        self.stages(1)
        result,_=self.audit(status=None)
        self.assertEqual(result.recovery_state,"PREPARATION_PARTIAL")
    def test_absent_prepared_no_local_manifest(self):
        self.stages(2)
        result,_=self.audit(status=None)
        self.assertEqual(result.recovery_state,"PREPARATION_PARTIAL")
    def test_absent_prepared_partial_archive(self):
        self.stages(2);self.manifest_files();self.archive_files(b=False)
        result,_=self.audit(status=None)
        self.assertEqual(result.recovery_state,"PREPARATION_PARTIAL")
    def test_absent_prepared_ready(self):
        self.stages(2);self.manifest_files();self.archive_files()
        result,_=self.audit(status=None)
        self.assertEqual(result.recovery_state,"PREPARED_READY")
    def test_absent_registered_checkpoint_blocked(self):
        self.ready()
        result,_=self.audit(status=None)
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_target_exists_missing_checkpoint_blocked(self):
        result,reader=self.audit()
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
        reader.assert_not_called()
    def test_target_exists_only_prepared_blocked(self):
        self.stages(2)
        result,_=self.audit()
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_target_exists_ready_to_reuse(self):
        self.ready()
        result,_=self.audit()
        self.assertEqual(result.recovery_state,"REGISTERED_PENDING_REUSE")
    def test_target_exists_reuse_complete(self):
        self.ready()
        result,_=self.audit(observations=(obs_reuse(),))
        self.assertEqual(result.recovery_state,"REUSE_COMPLETE")
    def test_target_exists_partial_candidates(self):
        self.ready()
        result,_=self.audit(observations=(obs_reuse(),obs_a()))
        self.assertEqual(result.recovery_state,"CANDIDATES_PARTIAL")
    def test_target_exists_candidates_complete(self):
        self.ready()
        result,_=self.audit(observations=(obs_reuse(),obs_a(),obs_b()))
        self.assertEqual(result.recovery_state,"CANDIDATES_COMPLETE")
    def test_target_exists_manifest_registered(self):
        self.ready();p=prepared()
        e=db_evidence((obs_reuse(),obs_a(),obs_b()),sha=p.manifest_sha256,archive=p.manifest_archive)
        result,_=self.audit(data=e)
        self.assertEqual(result.recovery_state,"MANIFEST_REGISTERED")
    def test_target_changed_between_reads(self):
        self.ready()
        e=db_evidence();e=replace(e,target=replace(e.target,revision="f"*40))
        result,_=self.audit(data=e)
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_missing_candidate_archive_blocked(self):
        self.stages(3);self.manifest_files();self.archive_files(b=False)
        result,_=self.audit()
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_corrupted_local_manifest_blocked(self):
        self.ready()
        self.write(prepared().manifest_report,b"X")
        result,_=self.audit()
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_bad_repo_checkpoint_blocked(self):
        self.stages(1)
        result=audit_recovery(repo_id="another-repo",target_revision=REV,root=self.root,
                              state_reader=lambda *a: None,
                              evidence_reader=mock.Mock())
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_in_progress_with_missing_prepared(self):
        self.stages(3)
        (self.root/f"reports/m4_6_runs/{REV}/prepared.json").unlink()
        result,_=self.audit()
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_wrong_existing_target_status(self):
        result,_=self.audit(status="failed")
        self.assertEqual(result.recovery_state,"BLOCKED_REVIEW")
    def test_all_assessments_not_authorized(self):
        self.ready()
        result,_=self.audit()
        self.assertFalse(result.write_authorized)
    def test_report_warns_no_writes(self):
        self.ready()
        result,_=self.audit()
        self.assertIn("Write authorized: NO",format_report(result))
    def test_blocker_printed(self):
        result,_=self.audit()
        self.assertIn("BLOCKER:",format_report(result))


class CliTests(unittest.TestCase):
    def test_plan_only(self): self.assertTrue(parse_args(["--target-revision",REV,"--plan"]).plan)
    def test_missing_plan(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit): parse_args(["--target-revision",REV])
    def test_floating_ref(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit): parse_args(["--target-revision","main","--plan"])
    def test_reject_apply(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit): parse_args(["--target-revision",REV,"--apply"])
    def test_reject_non_lowercase(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit): parse_args(["--target-revision","A"*40,"--plan"])
    def test_invalid_assessment_write(self):
        with self.assertRaises(ValueError): RecoveryAssessment("COMPLETED",None,0,0,0,0,"SKIP",True)


if __name__ == "__main__":
    unittest.main()
