"""Offline tests for completed-snapshot evidence validation."""
from dataclasses import replace
import unittest

from scripts.tools.incremental_completed_snapshot_evidence import (
    CompletionExpected,
    CompletionObserved,
    validate_completed_evidence,
)


def fixture():
    expected = CompletionExpected(
        snapshot_id=366,
        baseline_id=365,
        repo_id="repo/epl",
        revision="a" * 40,
        baseline_revision="b" * 40,
        full_files=314,
        raw_files=296,
        baseline_raw_files=286,
        reused_files=282,
        candidate_files=14,
        candidate_records=393,
        reused_records=8502211,
        record_references=8502604,
        manifest_sha256="c" * 64,
        manifest_archive="raw-archive-v2/manifests/c/manifest.json",
        archive_bytes=836981312,
    )

    observed = CompletionObserved(
        baseline_row=(365, "repo/epl", "b" * 40, "completed", 286),
        target_row=(
            366, "repo/epl", "a" * 40, "completed",
            314, 296, "c" * 64,
            "raw-archive-v2/manifests/c/manifest.json"
        ),
        observations=296,
        reused_files=282,
        candidate_files=14,
        candidate_records=393,
        reused_records=8502211,
        physical_records=8502604,
        tar_members=10753,
        archive_objects=296,
        archive_bytes=836981312,
        manifest_hash_verified=True,
        archive_hashes_verified=True,
    )

    return expected, observed


class CompletedSnapshotEvidenceTests(unittest.TestCase):

    def assert_rejected(self, expected, observed):
        with self.assertRaises(ValueError):
            validate_completed_evidence(expected, observed)

    def test_completed_snapshot_accepted(self):
        expected, observed = fixture()
        result = validate_completed_evidence(expected, observed)
        self.assertIn("snapshot #366", result)

    def test_target_not_completed(self):
        expected, observed = fixture()
        row = list(observed.target_row)
        row[3] = "in_progress"
        self.assert_rejected(
            expected, replace(observed, target_row=tuple(row))
        )

    def test_baseline_drift(self):
        expected, observed = fixture()
        row = list(observed.baseline_row)
        row[2] = "d" * 40
        self.assert_rejected(
            expected, replace(observed, baseline_row=tuple(row))
        )

    def test_manifest_drift(self):
        expected, observed = fixture()
        row = list(observed.target_row)
        row[6] = "d" * 64
        self.assert_rejected(
            expected, replace(observed, target_row=tuple(row))
        )

    def test_missing_observation(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, observations=295)
        )

    def test_candidate_count_drift(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, candidate_files=13)
        )

    def test_candidate_record_drift(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, candidate_records=392)
        )

    def test_physical_record_drift(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, physical_records=8502603)
        )

    def test_archive_object_missing(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, archive_objects=295)
        )

    def test_archive_bytes_mismatch(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, archive_bytes=836981311)
        )

    def test_no_manifest_hash_proof(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, manifest_hash_verified=False)
        )

    def test_no_archive_hash_proof(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, archive_hashes_verified=False)
        )

    def test_bool_is_not_valid_count(self):
        expected, observed = fixture()
        self.assert_rejected(
            expected, replace(observed, archive_objects=True)
        )

    def test_malformed_evidence(self):
        expected, observed = fixture()
        self.assert_rejected(expected, None)


if __name__ == "__main__":
    unittest.main()
