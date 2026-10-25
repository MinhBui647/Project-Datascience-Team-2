"""Local immutable-archive restore tests; no PostgreSQL or network access."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.tools.restore_raw_archives import restore_objects


class RestoreRawArchivesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="EPL archive restore ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source checkout"
        self.target = self.root / "target checkout"
        self.source.mkdir()
        self.target.mkdir()

    def archive(self, content=b"raw,values\n1,2\n"):
        digest = hashlib.sha256(content).hexdigest()
        relative = f"raw-archive-v2/objects/{digest[:2]}/{digest}"
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return relative, digest, len(content)

    def restore(self, objects, **kwargs):
        return restore_objects(self.source, self.target, objects, **kwargs)

    def assert_unpublished(self):
        self.assertEqual(list(self.target.iterdir()), [])

    def test_preview_reports_missing_bytes_without_creating_destination(self):
        item = self.archive()
        result = self.restore([item])
        self.assertEqual(result["objects"], 1)
        self.assertEqual(result["missing_objects"], 1)
        self.assertEqual(result["bytes_to_copy"], item[2])
        self.assertEqual(result["restored"], 0)
        self.assert_unpublished()

    def test_restore_publishes_exact_verified_bytes_and_removes_temporary_files(self):
        content = b"\x00\xffpreserved raw bytes\r\n"
        item = self.archive(content)
        result = self.restore([item], restore=True)
        destination = self.target / item[0]
        self.assertEqual(result["restored"], 1)
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), item[1])
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_existing_verified_object_is_never_copied_or_overwritten(self):
        item = self.archive()
        destination = self.target / item[0]
        destination.parent.mkdir(parents=True)
        content = (self.source / item[0]).read_bytes()
        destination.write_bytes(content)
        before = destination.stat().st_mtime_ns
        with patch("scripts.tools.restore_raw_archives.shutil.copyfile",
                   side_effect=AssertionError("Existing immutable object must not be copied")):
            result = self.restore([item], restore=True)
        self.assertEqual(result["already_present"], 1)
        self.assertEqual(result["restored"], 0)
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(destination.stat().st_mtime_ns, before)

    def test_corrupt_existing_object_blocks_all_publication_and_stays_untouched(self):
        pending = self.archive(b"new object")
        existing = self.archive(b"old object")
        destination = self.target / existing[0]
        destination.parent.mkdir(parents=True)
        corrupt = b"BAD object"
        self.assertEqual(len(corrupt), existing[2])
        destination.write_bytes(corrupt)
        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.restore([pending, existing], restore=True)
        self.assertFalse((self.target / pending[0]).exists())
        self.assertEqual(destination.read_bytes(), corrupt)

    def test_bad_source_hash_blocks_before_any_object_is_published(self):
        valid = self.archive(b"first valid object")
        bad = self.archive(b"second bad object")
        (self.source / bad[0]).write_bytes(b"X" * bad[2])
        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.restore([valid, bad], restore=True)
        self.assert_unpublished()

    def test_traversal_and_noncanonical_paths_are_rejected(self):
        item = self.archive()
        invalid = [
            f"raw-archive-v2/objects/../{item[1]}",
            f"raw-archive-v2/objects/00/{item[1]}",
            f"../raw-archive-v2/objects/{item[1][:2]}/{item[1]}",
            str(self.source / item[0]),
        ]
        for relative in invalid:
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ValueError, "archive identity"):
                    self.restore([(relative, item[1], item[2])], restore=True)
                self.assert_unpublished()

    def test_missing_source_fails_without_publishing_earlier_valid_object(self):
        valid = self.archive(b"valid source object")
        missing = self.archive(b"missing source object")
        (self.source / missing[0]).unlink()
        with self.assertRaisesRegex(RuntimeError, "Source archive missing or wrong size"):
            self.restore([valid, missing], restore=True)
        self.assert_unpublished()

    def test_wrong_source_size_fails_in_preview_and_restore(self):
        item = self.archive()
        (self.source / item[0]).write_bytes(b"truncated")
        for restore in (False, True):
            with self.subTest(restore=restore):
                with self.assertRaisesRegex(RuntimeError, "wrong size"):
                    self.restore([item], restore=restore)
                self.assert_unpublished()


if __name__ == "__main__":
    unittest.main()
