"""Offline fault-injection tests for the completed snapshot auditor.

No real PostgreSQL connection or archive access. All evidence inputs are faked.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

from scripts.tools.incremental_execution_context import (
    CandidateReceipt, CandidateSpec, PlannedContext,
    PreparedContext, RegisteredContext,
)
from scripts.tools.incremental_completed_snapshot_audit import audit_completed_snapshot

REPO = "example/epl"
BASE = "raw_data/demo/history.csv"
NEW = "raw_data/demo/new.json"
NEW_CSV = "raw_data/demo/new.csv"
REV = "a" * 40
OLD_REV = "b" * 40
SHA = "f" * 64
NEW_SHA = "c" * 64
BASE_SHA = "e" * 64
BASE_GIT = "d" * 40
MANIFEST_REL = f"raw-archive-v2/manifests/{SHA[:2]}/{SHA}.json"


def context(candidate_path=NEW):
    candidate = CandidateSpec(candidate_path, "NEW", 5, "demo", None, NEW_SHA)
    planned = PlannedContext(
        repo_id=REPO, baseline_snapshot_id=10,
        baseline_revision=OLD_REV, target_revision=REV,
        plan_fingerprint="1" * 64,
        expected_baseline_files=1, expected_full_files=2,
        expected_raw_files=2, expected_reuse=1,
        expected_files=1, expected_bytes=5,
        expected_baseline_records=2, expected_reused_records=2,
        candidates=(candidate,),
    )
    prepared = PreparedContext(
        planned=planned,
        candidate_receipts=(CandidateReceipt(candidate_path, 5, NEW_SHA, 1),),
        expected_records=1, expected_record_references=3,
        manifest_sha256=SHA,
        manifest_report=f"reports/m4_6_manifests/{REV}/hf_manifest_v2.json",
        manifest_archive=MANIFEST_REL,
    )
    return RegisteredContext(prepared, 20)


class FakeDB:
    def __init__(self):
        self.base_status = "completed"
        self.target_status = "completed"
        self.target_manifest_sha = SHA
        self.readonly = "on"
        self.raw = {
            BASE: {"size_bytes": 7, "format": "csv", "git_blob_id": BASE_GIT,
                   "lfs_sha256": None},
            NEW: {"size_bytes": 5, "format": "json", "git_blob_id": None,
                  "lfs_sha256": NEW_SHA},
        }
        self.observations = [
            (BASE, 100, REPO, "linked", 7, BASE_GIT, None, REPO, BASE, 7,
             BASE_SHA, f"raw-archive-v2/objects/{BASE_SHA[:2]}/{BASE_SHA}",
             "verified", "success", 2, "csv", "demo"),
            (NEW, 200, REPO, "linked", 5, None, NEW_SHA, REPO, NEW, 5,
             NEW_SHA, f"raw-archive-v2/objects/{NEW_SHA[:2]}/{NEW_SHA}",
             "verified", "success", 1, "json", "demo"),
        ]
        self.physical = [
            (100, "csv_row", False, 2),
            (200, "json_object", False, 1),
        ]
        self.candidate_physical = [(200, 1, 1, 0)]
        self.scan_mode = "valid"
        self.queries = []
        self.connections = 0

    def baseline(self):
        return (10, REPO, OLD_REV, self.base_status, 1, 1, None, None)

    def target(self):
        return (20, REPO, REV, self.target_status, 2, 2,
                self.target_manifest_sha, MANIFEST_REL)

    def archive_scan(self, objects, *, root):
        if self.scan_mode == "error":
            raise RuntimeError("Corrupt archive detected")
        if self.scan_mode == "drift":
            self.target_status = "in_progress"
        size = sum(item["size_bytes"] for item in objects)
        if self.scan_mode == "bytes":
            size -= 1
        return SimpleNamespace(objects=objects, total_bytes=size)

    def connect(self, **kwargs):
        self.connections += 1
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self.db)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, args=None):
        q = " ".join(sql.lower().split())
        if not q.startswith("select"):
            raise AssertionError("Non-SELECT SQL in read-only auditor")
        self.db.queries.append(q)
        if "current_database()" in q:
            self.rows = [("epl_raw_storage_v2", self.db.readonly)]
        elif "from raw.snapshot_runs" in q and " in (" in q:
            self.rows = [self.db.baseline(), self.db.target()]
        elif "from raw.snapshot_runs" in q:
            self.rows = [self.db.target()]
        elif "from raw.file_observations" in q and "f.record_count" in q and "o.manifest_size_bytes" not in q:
            self.rows = [(BASE, 100, 2, REPO, "linked")]
        elif "from raw.file_observations" in q:
            self.rows = list(self.db.observations)
        elif "from raw.raw_records" in q and "distinct record_locator" in q:
            self.rows = list(self.db.candidate_physical)
        elif "from raw.raw_records" in q:
            self.rows = list(self.db.physical)
        elif "from raw.archive_members" in q:
            self.rows = []
        else:
            raise AssertionError("Unexpected SQL: " + q[:120])

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class CompletedSnapshotAuditFaultTests(TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.reg = context()
        self.patches = [
            patch("scripts.tools.incremental_completed_snapshot_audit.verified_manifest",
                  side_effect=lambda root, prepared: self.db.raw),
            patch("scripts.tools.incremental_completed_snapshot_audit.db_parameters",
                  return_value={"dbname": "mock"}),
            patch("scripts.tools.incremental_completed_snapshot_audit.scan_archives",
                  side_effect=self.db.archive_scan),
            patch("psycopg.connect", side_effect=self.db.connect),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def run_audit(self):
        return audit_completed_snapshot(self.reg, root=Path.cwd())

    def reject(self, phrase=None):
        with self.assertRaises((RuntimeError, ValueError)) as caught:
            self.run_audit()
        if phrase:
            self.assertIn(phrase.lower(), str(caught.exception).lower())

    def test_valid_evidence_passes(self):
        text = self.run_audit()
        self.assertIn("snapshot #20", text)
        self.assertEqual(self.db.connections, 2)
        self.assertTrue(all(q.startswith("select") for q in self.db.queries))

    def use_csv_candidate(self):
        self.reg = context(NEW_CSV)
        self.db.raw[NEW_CSV] = {**self.db.raw.pop(NEW), "format": "csv"}
        row = list(self.db.observations[1])
        row[0] = row[8] = NEW_CSV
        row[15] = "csv"
        self.db.observations[1] = tuple(row)
        self.db.physical[1] = (200, "csv_row", False, 1)

    def test_completed_csv_candidate_with_csv_rows_passes(self):
        self.use_csv_candidate()
        text = self.run_audit()
        self.assertIn("snapshot #20", text)
        self.assertEqual(self.db.connections, 2)
        self.assertTrue(all(q.startswith("select") for q in self.db.queries))

    def test_csv_candidate_cannot_be_registered_as_json(self):
        self.use_csv_candidate()
        # Manifest and DB agree with each other but contradict the pinned path.
        self.db.raw[NEW_CSV]["format"] = "json"
        row = list(self.db.observations[1])
        row[15] = "json"
        self.db.observations[1] = tuple(row)
        self.db.physical[1] = (200, "json_object", False, 1)
        self.reject("Candidate version/receipt mismatch")

    def test_csv_candidate_rejects_json_record_kind(self):
        self.use_csv_candidate()
        self.db.physical[1] = (200, "json_object", False, 1)
        self.reject("record kind/member mismatch")

    def test_csv_candidate_rejects_archive_member_reference(self):
        self.use_csv_candidate()
        self.db.physical[1] = (200, "csv_row", True, 1)
        self.reject("record kind/member mismatch")

    def test_baseline_not_completed(self):
        self.db.base_status = "in_progress"
        self.reject("Baseline")

    def test_target_not_completed(self):
        self.db.target_status = "in_progress"
        self.reject("Target")

    def test_manifest_drift(self):
        self.db.target_manifest_sha = "0" * 64
        self.reject("Target")

    def test_readonly_database_required(self):
        self.db.readonly = "off"
        self.reject("read-only")

    def test_missing_observation(self):
        self.db.observations.pop()
        self.reject("observation")

    def test_duplicate_file_id(self):
        row = list(self.db.observations[1]); row[1] = 100
        self.db.observations[1] = tuple(row)
        self.reject("duplicate")

    def test_candidate_receipt_mismatch(self):
        row = list(self.db.observations[1]); row[10] = BASE_SHA
        row[11] = f"raw-archive-v2/objects/{BASE_SHA[:2]}/{BASE_SHA}"
        self.db.observations[1] = tuple(row)
        self.reject("receipt")

    def test_invalid_record_kind(self):
        self.db.physical[0] = (100, "json_object", False, 2)
        self.reject("kind")

    def test_physical_record_count_drift(self):
        self.db.physical[1] = (200, "json_object", False, 2)
        self.reject("record count")

    def test_candidate_locator_collision(self):
        self.db.candidate_physical = [(200, 1, 0, 0)]
        self.reject("locator")

    def test_corrupt_archive_blocks(self):
        self.db.scan_mode = "error"
        self.reject("Corrupt archive")

    def test_incomplete_scan_blocks(self):
        self.db.scan_mode = "bytes"
        self.reject("receipt")

    def test_snapshot_status_changed_during_scan(self):
        self.db.scan_mode = "drift"
        self.reject("changed during")

    def test_missing_manifest_blocks(self):
        with patch("scripts.tools.incremental_completed_snapshot_audit.verified_manifest",
                   return_value=None):
            self.reject("manifest unavailable")


if __name__ == "__main__":
    main()
