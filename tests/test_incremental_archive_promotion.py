"""Offline-only integration cases for immutable archive object promotion."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.incremental_archive_promotion import (
    canonical_path, inspect_existing, inspect_cached, publish_immutable,
    audit_candidates,
)


class ArchivePromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        payload = json.dumps([{"v": 1}, {"v": 2}], separators=(",", ":")).encode()
        self.cache = self.root / "cached.json"
        self.cache.write_bytes(payload)
        self.sha = hashlib.sha256(payload).hexdigest()
        self.blob = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        self.identity = FileIdentity(size=len(payload), lfs_sha256=None, git_blob_id=self.blob)
        self.name = "2026_2027_new/matches/fotmob/abc.json"

    def candidate(self):
        return inspect_cached(self.root, self.name, self.cache, self.identity)

    def test_canonical_layout(self):
        dest, rel = canonical_path(self.root, self.sha)
        self.assertEqual(rel, f"raw-archive-v2/objects/{self.sha[:2]}/{self.sha}")
        self.assertEqual(dest, self.root / rel)

    def test_invalid_digest_rejected(self):
        with self.assertRaises(ValueError):
            canonical_path(self.root, "not-a-digest")

    def test_plan_is_read_only(self):
        entry = self.candidate()
        self.assertEqual(entry.action, "READY_NEW")
        self.assertFalse((self.root / "raw-archive-v2").exists())

    def test_promotion_publishes_verified_content(self):
        entry = self.candidate()
        action = publish_immutable(self.root, self.cache, self.identity, entry)
        self.assertEqual(action, "ARCHIVED_NEW")
        self.assertEqual((self.root / entry.archive_relative_path).read_bytes(), self.cache.read_bytes())

    def test_promotion_is_idempotent(self):
        entry = self.candidate()
        self.assertEqual(publish_immutable(self.root, self.cache, self.identity, entry), "ARCHIVED_NEW")
        self.assertEqual(publish_immutable(self.root, self.cache, self.identity, entry), "ALREADY_ARCHIVED")
        self.assertEqual(len(list((self.root / 'raw-archive-v2' / 'objects' / self.sha[:2]).iterdir())), 1)

    def test_existing_hash_mismatch_blocks(self):
        dest, _ = canonical_path(self.root, self.sha)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"bad bytes")
        with self.assertRaises(RuntimeError):
            self.candidate()
        self.assertEqual(dest.read_bytes(), b"bad bytes")

    def test_existing_identical_detected(self):
        entry = self.candidate()
        publish_immutable(self.root, self.cache, self.identity, entry)
        found = self.candidate()
        self.assertEqual(found.action, "ALREADY_ARCHIVED")
        self.assertEqual(inspect_existing(self.root, self.sha, len(self.cache.read_bytes()))[2], "ALREADY_ARCHIVED")

    def test_mutated_cache_blocks_before_publish(self):
        entry = self.candidate()
        self.cache.write_bytes(self.cache.read_bytes() + b"tamper")
        with self.assertRaises(ValueError):
            publish_immutable(self.root, self.cache, self.identity, entry)
        self.assertFalse((self.root / entry.archive_relative_path).exists())

    def test_wrong_git_blob_blocks(self):
        fake = FileIdentity(size=self.identity.size, git_blob_id="a" * 40, lfs_sha256=None)
        with self.assertRaises(ValueError):
            inspect_cached(self.root, self.name, self.cache, fake)

    def test_wrong_lfs_sha_blocks(self):
        fake = FileIdentity(size=self.identity.size, git_blob_id=None, lfs_sha256="a" * 64)
        with self.assertRaises(ValueError):
            inspect_cached(self.root, self.name, self.cache, fake)

    def test_path_traversal_blocks(self):
        for name in ("raw_data/../a.json", "raw_data/./a.json", "elsewhere/a.json", "raw_data/x.csv"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect_cached(self.root, name, self.cache, self.identity)

    def test_bad_json_blocks(self):
        bad = self.root / "bad.json"
        bad.write_bytes(b"{invalid")
        sha = hashlib.sha1(b"blob " + str(bad.stat().st_size).encode() + b"\0" + bad.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            inspect_cached(self.root, self.name, bad, FileIdentity(size=bad.stat().st_size, git_blob_id=sha, lfs_sha256=None))

    def test_candidate_is_immutable_input(self):
        from dataclasses import FrozenInstanceError
        c = self.candidate()
        with self.assertRaises(FrozenInstanceError):
            c.action = "NEW"

    def test_archive_symlink_blocks(self):
        outside = self.root / "outside"
        outside.mkdir()
        archive = self.root / 'raw-archive-v2'
        try:
            archive.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS disallows symlink creation")
        with self.assertRaises(RuntimeError):
            self.candidate()

    def test_archive_file_symlink_blocks(self):
        entry = self.candidate()
        target = self.root / entry.archive_relative_path
        target.parent.mkdir(parents=True)
        try:
            target.symlink_to(self.cache)
        except (OSError, NotImplementedError):
            self.skipTest("OS disallows symlink creation")
        with self.assertRaises(RuntimeError):
            self.candidate()

    def test_stage_failure_does_not_publish(self):
        entry = self.candidate()
        with patch('scripts.tools.incremental_archive_promotion.shutil.copyfileobj', side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                publish_immutable(self.root, self.cache, self.identity, entry)
        dest = self.root / entry.archive_relative_path
        self.assertFalse(dest.exists())
        self.assertEqual(list(dest.parent.glob('.stage-*')), [])


if __name__ == '__main__':
    unittest.main()
