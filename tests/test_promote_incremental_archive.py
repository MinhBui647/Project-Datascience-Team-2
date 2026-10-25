"""Offline promotion coordinator unit tests."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.incremental_archive_promotion import inspect_cached, publish_immutable
from scripts.tools.promote_incremental_archive import check_promotion_contract, promote_batch


@dataclass
class PlanFake:
    fingerprint: str
    target_raw_count: int
    unchanged: int
    def count(self, category):
        return self.unchanged if category == "UNCHANGED" else 0


class PromoteCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.paths = ["2026_2027_new/matches/fotmob/test-a.json",
                      "2026_2027_new/matches/fotmob/test-b.json"]
        self.local_files = {}
        self.target = {}
        candidates = []
        for i, name in enumerate(self.paths):
            body = json.dumps({"id": i}).encode("utf8")
            file = self.root / (f"sample-{i}.json")
            file.write_bytes(body)
            sha1 = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
            identity = FileIdentity(size=len(body), git_blob_id=sha1, lfs_sha256=None)
            self.local_files[name] = file
            self.target[name] = identity
            candidates.append(inspect_cached(self.root, name, file, identity))
        self.candidates = tuple(candidates)
        self.fp = "a" * 64
        self.plan = PlanFake(self.fp, 274, 272)
        self.bytes = sum(c.byte_count for c in self.candidates)
        self.records = sum(c.record_count for c in self.candidates)

    def gate(self, **overrides):
        data = dict(expected_fingerprint=self.fp, expected_files=2,
                    expected_bytes=self.bytes, expected_records=self.records)
        data.update(overrides)
        check_promotion_contract(self.plan, self.candidates, **data)

    def test_valid_gate(self):
        self.gate()

    def test_wrong_fingerprint_fails(self):
        with self.assertRaises(ValueError): self.gate(expected_fingerprint="b" * 64)

    def test_invalid_fingerprint_format_fails(self):
        with self.assertRaises(ValueError): self.gate(expected_fingerprint="bad")

    def test_wrong_file_count_fails(self):
        with self.assertRaises(ValueError): self.gate(expected_files=3)

    def test_wrong_byte_count_fails(self):
        with self.assertRaises(ValueError): self.gate(expected_bytes=self.bytes + 1)

    def test_wrong_record_count_fails(self):
        with self.assertRaises(ValueError): self.gate(expected_records=self.records + 1)

    def test_incomplete_target_fails(self):
        with self.assertRaises(ValueError):
            check_promotion_contract(PlanFake(self.fp, 275, 272), self.candidates,
               expected_fingerprint=self.fp, expected_files=2,
               expected_bytes=self.bytes, expected_records=self.records)

    def test_duplicate_candidate_path_fails(self):
        with self.assertRaises(ValueError):
            check_promotion_contract(PlanFake(self.fp, 274, 272),
               (self.candidates[0], self.candidates[0]),
               expected_fingerprint=self.fp, expected_files=2,
               expected_bytes=2*self.candidates[0].byte_count,
               expected_records=2*self.candidates[0].record_count)

    def test_first_publish_and_retry(self):
        self.gate()
        self.assertEqual(promote_batch(self.root, self.candidates, self.local_files, self.target), (2, 0))
        self.assertEqual(promote_batch(self.root, self.candidates, self.local_files, self.target), (0, 2))

    def test_partial_failure_resume(self):
        counter = 0
        def fails_second(*args):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise OSError("simulated disk full")
            return publish_immutable(*args)
        with self.assertRaises(OSError):
            promote_batch(self.root, self.candidates, self.local_files, self.target, publisher=fails_second)
        self.assertTrue((self.root / self.candidates[0].archive_relative_path).exists())
        self.assertFalse((self.root / self.candidates[1].archive_relative_path).exists())
        self.assertEqual(promote_batch(self.root, self.candidates, self.local_files, self.target), (1, 1))

    def test_mutated_cache_blocks_promotion(self):
        self.local_files[self.paths[0]].write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            promote_batch(self.root, self.candidates, self.local_files, self.target)
        self.assertFalse((self.root / self.candidates[0].archive_relative_path).exists())

    def test_existing_object_tamper_blocks_promotion(self):
        obj = self.root / self.candidates[0].archive_relative_path
        obj.parent.mkdir(parents=True)
        obj.write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            promote_batch(self.root, self.candidates, self.local_files, self.target)
        self.assertEqual(obj.read_bytes(), b"corrupt")

    def test_missing_cache_path_fails_before_publication(self):
        with self.assertRaises(ValueError):
            promote_batch(self.root, self.candidates, {self.paths[0]: self.local_files[self.paths[0]]}, self.target)

    def test_missing_target_identity_fails_before_publication(self):
        with self.assertRaises(ValueError):
            promote_batch(self.root, self.candidates, self.local_files, {self.paths[0]: self.target[self.paths[0]]})

    def test_unknown_publisher_result_fails(self):
        with self.assertRaises(RuntimeError):
            promote_batch(self.root, self.candidates, self.local_files, self.target,
                          publisher=lambda *a: "BOGUS")


if __name__ == "__main__": unittest.main()
