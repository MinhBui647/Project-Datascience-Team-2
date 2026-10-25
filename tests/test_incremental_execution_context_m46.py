#!/usr/bin/env python3
"""M4.6.4d.2 offline tests: no HF, PostgreSQL, archive, or ingestion access."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from scripts.tools.incremental_execution_context import (
    CandidateSpec, CandidateReceipt, PlannedContext, PreparedContext, RegisteredContext,
    build_planned, build_prepared, build_registered, cli_arguments,
    checkpoint_digest, checkpoint_envelope, publish_checkpoint, read_checkpoint,
    _canonical, _checkpoint_rel, _decode,
)

BASE = "1" * 40
TARGET = "2" * 40
FINGERPRINT = "f" * 64
GIT_A = "a" * 40
LFS_B = "b" * 64
SHA_A = "c" * 64


def spec_a():
    return CandidateSpec("2026_2027_new/fpl_fixtures_latest.json", "CHANGED", 10,
                         "fpl", GIT_A, None)


def spec_b():
    return CandidateSpec("raw_data/fotmob/fotmob_test.json", "NEW", 20,
                         "fotmob", None, LFS_B)


def planned():
    return PlannedContext(
        repo_id="Toibinguz/epl-premier-league-dataset", baseline_snapshot_id=365,
        baseline_revision=BASE, target_revision=TARGET,
        plan_fingerprint=FINGERPRINT, expected_baseline_files=3,
        expected_full_files=4, expected_raw_files=3, expected_reuse=1,
        expected_files=2, expected_bytes=30, expected_baseline_records=100,
        expected_reused_records=80, candidates=(spec_a(), spec_b()),
    )


def receipts():
    return (CandidateReceipt(spec_a().path, 10, SHA_A, 2),
            CandidateReceipt(spec_b().path, 20, LFS_B, 3))


def manifest(p=None):
    p = p or planned()
    files = [
        {"relative_path": "README.md", "format": "md", "hf_revision": TARGET, "size_bytes": 10},
        {"relative_path": spec_a().path, "format": "json", "hf_revision": TARGET,
         "size_bytes": 10, "git_blob_id": GIT_A, "lfs_sha256": ""},
        {"relative_path": "raw_data/understat/other.csv", "format": "csv", "hf_revision": TARGET,
         "size_bytes": 200, "git_blob_id": GIT_A, "lfs_sha256": ""},
        {"relative_path": spec_b().path, "format": "json", "hf_revision": TARGET,
         "size_bytes": 20, "git_blob_id": "", "lfs_sha256": LFS_B},
    ]
    return {"repo_id": p.repo_id, "repo_type": "dataset", "resolved_commit": TARGET,
            "file_count": 4, "files": files}


def payload(m=None):
    return (json.dumps(m or manifest(), sort_keys=True) + "\n").encode("utf-8")


def prepared():
    return build_prepared(planned(), receipts(), manifest_bytes=payload())


def registered():
    pre = prepared()
    p = pre.planned
    return build_registered(pre, target_snapshot_id=366, db_repo_id=p.repo_id,
                            db_revision=p.target_revision, db_status="in_progress",
                            db_full_files=p.expected_full_files, db_raw_files=p.expected_raw_files)


def fake_download_context():
    a = SimpleNamespace(path=spec_a().path, category="CHANGED",
                        kind="UPSERT_VERSION_AFTER_REHASH", file_format="json",
                        expected_bytes=10, source_code="fpl", git_blob_id=GIT_A,
                        lfs_sha256=None)
    b = SimpleNamespace(path=spec_b().path, category="NEW",
                        kind="UPSERT_VERSION_AFTER_REHASH", file_format="json",
                        expected_bytes=20, source_code="fotmob", git_blob_id=None,
                        lfs_sha256=LFS_B)
    reused = SimpleNamespace(path="raw_data/understat/other.csv", category="UNCHANGED",
                             kind="REUSE_FILE_ID")
    class Plan:
        actions = (a, b, reused)
        target_raw_count = 3
        candidate_bytes = 30
        baseline_records = 100
        reused_records = 80
        superseded_records = 20
        fingerprint = FINGERPRINT
        def count(self, name):
            return sum(x.category == name for x in self.actions)
    return SimpleNamespace(repo_id=planned().repo_id, baseline_snapshot=365,
                           baseline_revision=BASE, target_revision=TARGET,
                           plan=Plan(), target={a.path: SimpleNamespace(size=10, git_blob_id=GIT_A,
                                                                       lfs_sha256=None),
                                                b.path: SimpleNamespace(size=20, git_blob_id=None,
                                                                       lfs_sha256=LFS_B),
                                                reused.path: SimpleNamespace(size=200)},
                           actions=(a, b), excluded=1)


class CandidateSpecTests(unittest.TestCase):
    def test_valid(self): self.assertEqual(spec_a().category, "CHANGED")
    def test_reject_traversal(self):
        with self.assertRaises(ValueError): replace(spec_a(), path="raw_data/../oops.json")
    def test_reject_absolute(self):
        with self.assertRaises(ValueError): replace(spec_a(), path="/raw_data/a.json")
    def test_reject_backslash(self):
        with self.assertRaises(ValueError): replace(spec_a(), path="raw_data\\a.json")
    def test_reject_outside_scope(self):
        with self.assertRaises(ValueError): replace(spec_a(), path="clean/a.json")
    def test_reject_other_format(self):
        csv_spec = replace(spec_a(), path="raw_data/a.csv")
        self.assertEqual(csv_spec.path, "raw_data/a.csv")
        with self.assertRaises(ValueError):
            replace(spec_a(), path="raw_data/a.tar.gz")
    def test_reject_category(self):
        with self.assertRaises(ValueError): replace(spec_a(), category="REMOVED")
    def test_reject_zero_bytes(self):
        with self.assertRaises(ValueError): replace(spec_a(), expected_bytes=0)
    def test_reject_bool_bytes(self):
        with self.assertRaises(ValueError): replace(spec_a(), expected_bytes=True)
    def test_reject_unknown_source(self):
        with self.assertRaises(ValueError): replace(spec_a(), source_code="Bad-Source")
    def test_reject_missing_remote_identity(self):
        with self.assertRaises(ValueError): replace(spec_a(), git_blob_id=None)
    def test_reject_uppercase_hash(self):
        with self.assertRaises(ValueError): replace(spec_a(), git_blob_id="A"*40)
    def test_lfs_identity(self): self.assertEqual(spec_b().lfs_sha256, LFS_B)
    def test_receipt_malformed_hash(self):
        with self.assertRaises(ValueError): replace(receipts()[0], content_sha256="abc")
    def test_receipt_empty_record_count(self):
        with self.assertRaises(ValueError): replace(receipts()[0], records=0)


class PlannedTests(unittest.TestCase):
    def test_valid(self): self.assertEqual(planned().expected_raw_files, 3)
    def test_wrong_plan_fingerprint(self):
        with self.assertRaises(ValueError): replace(planned(), plan_fingerprint="invalid")
    def test_same_baseline_revision(self):
        with self.assertRaises(ValueError): replace(planned(), target_revision=BASE)
    def test_bad_snapshot(self):
        with self.assertRaises(ValueError): replace(planned(), baseline_snapshot_id=0)
    def test_wrong_candidate_count(self):
        with self.assertRaises(ValueError): replace(planned(), expected_files=3)
    def test_wrong_candidate_bytes(self):
        with self.assertRaises(ValueError): replace(planned(), expected_bytes=29)
    def test_wrong_scope_sum(self):
        with self.assertRaises(ValueError): replace(planned(), expected_raw_files=5)
    def test_negative_reuse(self):
        with self.assertRaises(ValueError): replace(planned(), expected_reuse=-1)
    def test_reuse_zero_allowed(self):
        p=replace(planned(), expected_reuse=0, expected_raw_files=2)
        self.assertEqual(p.expected_reuse, 0)
    def test_negative_reused_records(self):
        with self.assertRaises(ValueError): replace(planned(), expected_reused_records=-1)
    def test_reused_records_exceed_baseline(self):
        with self.assertRaises(ValueError): replace(planned(), expected_reused_records=101)
    def test_candidate_duplicate(self):
        with self.assertRaises(ValueError): replace(planned(), candidates=(spec_a(), spec_a()))
    def test_candidate_unsorted(self):
        with self.assertRaises(ValueError): replace(planned(), candidates=(spec_b(), spec_a()))
    def test_reject_full_less_than_raw(self):
        with self.assertRaises(ValueError): replace(planned(), expected_full_files=2)
    def test_adapt_download_context(self):
        self.assertEqual(build_planned(fake_download_context(), expected_baseline_files=3,
                                       expected_full_files=4), planned())
    def test_adapt_wrong_full_count(self):
        with self.assertRaises(ValueError): build_planned(fake_download_context(), expected_baseline_files=3,
                                                          expected_full_files=5)
    def test_adapt_removed_blocks(self):
        c=fake_download_context()
        c.plan.actions=(c.plan.actions[0], c.plan.actions[1],
                        SimpleNamespace(path="foo", category="REMOVED", kind="NOTIFY_ONLY_NO_DELETE"))
        with self.assertRaises(ValueError): build_planned(c, expected_baseline_files=3, expected_full_files=4)
    def test_adapt_metadata_drift(self):
        c=fake_download_context()
        c.target[spec_a().path].size=11
        with self.assertRaises(ValueError): build_planned(c, expected_baseline_files=3, expected_full_files=4)
    def test_adapt_record_accounting_drift(self):
        c=fake_download_context()
        c.plan.superseded_records=19
        with self.assertRaises(ValueError): build_planned(c, expected_baseline_files=3, expected_full_files=4)


class PreparedTests(unittest.TestCase):
    def test_valid(self):
        c=prepared()
        self.assertEqual(c.expected_records, 5)
        self.assertEqual(c.expected_record_references, 85)
        self.assertEqual(c.manifest_sha256, hashlib.sha256(payload()).hexdigest())
    def test_missing_receipt(self):
        with self.assertRaises(ValueError): build_prepared(planned(), receipts()[:1], manifest_bytes=payload())
    def test_extra_receipt(self):
        with self.assertRaises(ValueError): build_prepared(planned(), receipts() + receipts()[:1], manifest_bytes=payload())
    def test_wrong_candidate_size(self):
        with self.assertRaises(ValueError): build_prepared(planned(),
            (replace(receipts()[0], size_bytes=11), receipts()[1]), manifest_bytes=payload())
    def test_lfs_mismatch(self):
        with self.assertRaises(ValueError): build_prepared(planned(),
            (receipts()[0], replace(receipts()[1], content_sha256=SHA_A)), manifest_bytes=payload())
    def test_wrong_expected_record_references(self):
        with self.assertRaises(ValueError): replace(prepared(), expected_record_references=80)
    def test_wrong_manifest_path(self):
        with self.assertRaises(ValueError): replace(prepared(), manifest_report="reports/evil.json")
    def test_wrong_manifest_archive_path(self):
        with self.assertRaises(ValueError): replace(prepared(), manifest_archive="elsewhere")
    def test_malformed_manifest_bytes(self):
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=b"oops")
    def test_wrong_manifest_repo(self):
        m=manifest(); m["repo_id"]="different"
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_wrong_manifest_revision(self):
        m=manifest(); m["resolved_commit"]=BASE
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_wrong_manifest_count(self):
        m=manifest(); m["file_count"]=5
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_wrong_raw_scope(self):
        m=manifest(); m["files"][2]["relative_path"]="clean/other.csv"
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_duplicate_manifest_path(self):
        m=manifest(); m["files"][2]["relative_path"]=spec_a().path
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_manifest_bad_identity(self):
        m=manifest(); m["files"][1]["git_blob_id"]="f"*40
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_manifest_wrong_format(self):
        m=manifest(); m["files"][1]["format"]="csv"
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_manifest_wrong_candidate_bytes(self):
        m=manifest(); m["files"][1]["size_bytes"]=11
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))
    def test_manifest_unsafe_file_path(self):
        m=manifest(); m["files"][2]["relative_path"]="../oops.csv"
        with self.assertRaises(ValueError): build_prepared(planned(), receipts(), manifest_bytes=payload(m))


class RegisteredTests(unittest.TestCase):
    def test_valid(self): self.assertEqual(registered().target_snapshot_id, 366)
    def test_wrong_target_id(self):
        with self.assertRaises(ValueError): replace(registered(), target_snapshot_id=365)
    def test_wrong_status(self):
        with self.assertRaises(ValueError): replace(registered(), target_status="completed")
    def test_db_revision_drift(self):
        p=prepared()
        with self.assertRaises(ValueError): build_registered(p, target_snapshot_id=366,
            db_repo_id=p.planned.repo_id, db_revision=BASE, db_status="in_progress",
            db_full_files=4, db_raw_files=3)
    def test_db_count_drift(self):
        p=prepared()
        with self.assertRaises(ValueError): build_registered(p, target_snapshot_id=366,
            db_repo_id=p.planned.repo_id, db_revision=TARGET, db_status="in_progress",
            db_full_files=5, db_raw_files=3)
    def test_manifest_already_registered_blocks_transition(self):
        p=prepared()
        with self.assertRaises(ValueError): build_registered(p, target_snapshot_id=366,
            db_repo_id=p.planned.repo_id, db_revision=TARGET, db_status="in_progress",
            db_full_files=4, db_raw_files=3, db_manifest_sha256=p.manifest_sha256)


class CLIContractTests(unittest.TestCase):
    def _flags(self, c, step):
        args=cli_arguments(c, step)
        return dict(zip(args[::2], args[1::2]))
    def test_download_flags(self):
        d=self._flags(planned(), "download")
        self.assertEqual(d["--expected-files"], "2")
        self.assertNotIn("--expected-records", d)
    def test_manifest_preparation_flags(self):
        d=self._flags(planned(), "manifest_preparation")
        self.assertEqual(d["--expected-full-files"], "4")
        self.assertEqual(d["--expected-raw-files"], "3")
    def test_archive_requires_prepared(self):
        with self.assertRaises(ValueError): cli_arguments(planned(), "archive_promotion")
    def test_archive_includes_records(self):
        d=self._flags(prepared(), "archive_promotion")
        self.assertEqual(d["--expected-records"], "5")
    def test_snapshot_preparation_flags(self):
        d=self._flags(prepared(), "snapshot_preparation")
        self.assertEqual(d["--expected-records"], "5")
        self.assertEqual(d["--expected-reuse"], "1")
        self.assertNotIn("--expected-full-files", d)
    def test_snapshot_registration_flags(self):
        d=self._flags(prepared(), "snapshot_registration")
        self.assertEqual(d["--expected-baseline-files"], "3")
    def test_reuse_requires_registered(self):
        with self.assertRaises(ValueError): cli_arguments(prepared(), "reuse")
    def test_reuse_flags(self):
        d=self._flags(registered(), "reuse")
        self.assertEqual(d["--target-snapshot-id"], "366")
        self.assertEqual(d["--expected-baseline-records"], "100")
        self.assertEqual(d["--expected-reused-records"], "80")
    def test_candidate_planner_flags(self):
        self.assertEqual(self._flags(registered(), "candidate_planner")["--expected-records"], "5")
    def test_candidate_ingestion_flags(self):
        self.assertEqual(self._flags(registered(), "candidate_ingestion")["--expected-reuse"], "1")
    def test_manifest_registration_flags(self):
        d=self._flags(registered(), "manifest_registration")
        self.assertEqual(d["--manifest-sha256"], prepared().manifest_sha256)
    def test_finalization_flags(self):
        d=self._flags(registered(), "finalization")
        self.assertEqual(d["--manifest-report"], prepared().manifest_report)
    def test_dangerous_confirmation_flags_absent(self):
        for s,c in (("download",planned()), ("archive_promotion",prepared()),
                    ("manifest_preparation", planned()), ("finalization",registered())):
            with self.subTest(s=s):
                args=cli_arguments(c,s)
                self.assertFalse(any(v in args for v in
                    ("--commit", "--apply", "--register", "--download", "--promote", "--publish")))
    def test_unknown_step(self):
        with self.assertRaises(ValueError): cli_arguments(planned(), "random_command")


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_planned_roundtrip(self):
        c=planned()
        rel,result=publish_checkpoint(self.root,c)
        self.assertEqual(result,"CREATED_NEW")
        self.assertEqual(read_checkpoint(self.root,TARGET,"planned"),c)
        self.assertIn(TARGET,rel)
    def test_planned_idempotent_retry(self):
        publish_checkpoint(self.root,planned())
        self.assertEqual(publish_checkpoint(self.root,planned())[1],"ALREADY_VERIFIED")
    def test_prepared_requires_parent(self):
        with self.assertRaises(FileNotFoundError): publish_checkpoint(self.root,prepared())
    def test_registered_requires_prepared(self):
        publish_checkpoint(self.root,planned())
        with self.assertRaises(FileNotFoundError): publish_checkpoint(self.root,registered())
    def test_full_three_stage_roundtrip(self):
        p=planned(); q=prepared(); r=registered()
        for obj in (p,q,r):
            publish_checkpoint(self.root,obj)
        self.assertEqual(read_checkpoint(self.root,TARGET,"prepared"),q)
        self.assertEqual(read_checkpoint(self.root,TARGET,"registered"),r)
    def test_parent_hashes(self):
        p=planned(); q=prepared(); r=registered()
        self.assertEqual(checkpoint_envelope(q)["parent_sha256"],checkpoint_digest(p))
        self.assertEqual(checkpoint_envelope(r)["parent_sha256"],checkpoint_digest(q))
    def test_digest_repeatable(self):
        self.assertEqual(checkpoint_digest(planned()),checkpoint_digest(planned()))
    def test_different_registered_id_different_digest(self):
        self.assertNotEqual(checkpoint_digest(registered()),
                            checkpoint_digest(replace(registered(),target_snapshot_id=367)))
    def test_refuse_overwrite_with_different_bytes(self):
        publish_checkpoint(self.root, planned())
        with self.assertRaises(RuntimeError): publish_checkpoint(self.root,
            replace(planned(),plan_fingerprint="e"*64))
    def test_detect_tamper(self):
        path=self.root / _checkpoint_rel(TARGET,"planned")
        publish_checkpoint(self.root,planned())
        path.write_bytes(path.read_bytes().replace(b'"expected_raw_files":3', b'"expected_raw_files":4'))
        with self.assertRaises(RuntimeError): read_checkpoint(self.root,TARGET,"planned")
    def test_detect_broken_parent_link(self):
        publish_checkpoint(self.root,planned()); publish_checkpoint(self.root,prepared())
        path=self.root / _checkpoint_rel(TARGET,"planned")
        path.write_text("{}",encoding="utf-8")
        with self.assertRaises(RuntimeError): read_checkpoint(self.root,TARGET,"prepared")
    def test_symlink_checkpoint_is_rejected(self):
        p=self.root / _checkpoint_rel(TARGET,"planned")
        p.parent.mkdir(parents=True,exist_ok=True)
        elsewhere=self.root/"elsewhere.txt"; elsewhere.write_text("hi")
        try: p.symlink_to(elsewhere)
        except (OSError,NotImplementedError): self.skipTest("Symlink unavailable on this OS")
        with self.assertRaises(RuntimeError): publish_checkpoint(self.root,planned())
    def test_symlink_ancestor_is_rejected(self):
        reports=self.root/"reports"
        elsewhere=self.root/"different"; elsewhere.mkdir()
        try: reports.symlink_to(elsewhere, target_is_directory=True)
        except (OSError,NotImplementedError): self.skipTest("Symlink unavailable on this OS")
        with self.assertRaises(RuntimeError): publish_checkpoint(self.root,planned())
    def test_noncanonical_json_is_rejected(self):
        publish_checkpoint(self.root,planned())
        path=self.root/_checkpoint_rel(TARGET,"planned")
        envelope=json.loads(path.read_bytes()); path.write_text(json.dumps(envelope,indent=2),encoding="utf-8")
        with self.assertRaises(RuntimeError): read_checkpoint(self.root,TARGET,"planned")
    def test_unknown_schema_version_rejected(self):
        publish_checkpoint(self.root,planned())
        path=self.root/_checkpoint_rel(TARGET,"planned")
        envelope=json.loads(path.read_bytes()); envelope["schema_version"]=99
        path.write_bytes(_canonical(envelope))
        with self.assertRaises(RuntimeError): read_checkpoint(self.root,TARGET,"planned")
    def test_nonexistent_checkpoint_does_not_create_dirs(self):
        with self.assertRaises(FileNotFoundError): read_checkpoint(self.root,TARGET,"planned")
        self.assertFalse((self.root/"reports").exists())
    def test_reject_nested_unknown_fields(self):
        p=planned()
        d=checkpoint_envelope(p)["payload"]
        d["extra"]="a"
        with self.assertRaises(ValueError): _decode("planned",d)
    def test_reject_nested_invalid_candidate(self):
        d=checkpoint_envelope(planned())["payload"]
        d["candidates"][0]["expected_bytes"]=-1
        with self.assertRaises(ValueError): _decode("planned",d)
    def test_paths_distinct_between_revisions(self):
        self.assertNotEqual(_checkpoint_rel(TARGET,"planned"),_checkpoint_rel(BASE,"planned"))
    def test_writes_only_checkpoints(self):
        publish_checkpoint(self.root,planned()); publish_checkpoint(self.root,prepared()); publish_checkpoint(self.root,registered())
        files = set(self.root.rglob("*.json"))
        expected = {self.root / _checkpoint_rel(TARGET, stage)
                    for stage in ("planned", "prepared", "registered")}
        self.assertEqual(files, expected)


if __name__ == "__main__":
    unittest.main()
