import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.incremental.incremental_csv_adapter import (
    inspect_csv_archive,
)


class IncrementalCsvAdapterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.data = (
            b"team,score\n"
            b"Arsenal,2\n"
            b"Chelsea,1\n"
        )

        # An immutable archive may use a SHA-based filename
        # without its original .csv extension.
        self.path = Path(self.tmp.name) / "immutable_object"
        self.path.write_bytes(self.data)

        self.sha = hashlib.sha256(self.data).hexdigest()

        self.git_blob = hashlib.sha1(
            b"blob " + str(len(self.data)).encode() + b"\0"
            + self.data
        ).hexdigest()

    def inspect(self, **overrides):
        arguments = {
            "expected_size": len(self.data),
            "expected_lfs_sha256": self.sha,
        }
        arguments.update(overrides)

        return inspect_csv_archive(self.path, **arguments)

    def test_valid_lfs_sha256(self):
        result = self.inspect()

        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.content_sha256, self.sha)
        self.assertEqual(result.verification_method, "LFS_SHA256")
        self.assertFalse(result.write_authorized)

    def test_valid_git_blob_identity(self):
        result = self.inspect(
            expected_lfs_sha256=None,
            expected_git_blob_id=self.git_blob,
        )

        self.assertEqual(result.record_count, 2)
        self.assertEqual(
            result.verification_method,
            "GIT_BLOB_SHA1",
        )

    def test_lfs_identity_takes_precedence_over_pointer_hash(self):
        result = self.inspect(
            expected_git_blob_id="f" * 40
        )

        self.assertEqual(result.verification_method, "LFS_SHA256")

    def test_wrong_lfs_sha256_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.inspect(expected_lfs_sha256="f" * 64)

    def test_wrong_git_blob_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHA1 mismatch"):
            self.inspect(
                expected_lfs_sha256=None,
                expected_git_blob_id="f" * 40,
            )

    def test_missing_identity_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Missing verifiable"):
            self.inspect(
                expected_lfs_sha256=None,
                expected_git_blob_id=None,
            )

    def test_size_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            self.inspect(expected_size=len(self.data) + 1)

    def test_file_size_budget_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "byte budget"):
            self.inspect(max_file_bytes=len(self.data) - 1)

    def test_record_budget_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "record limit"):
            self.inspect(max_records=1)

    def test_tar_format_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "CSV only"):
            self.inspect(file_format="tar.gz")

    def test_invalid_record_budget_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_records"):
            self.inspect(max_records=0)

    def test_missing_archive_is_rejected(self):
        self.path.unlink()

        with self.assertRaises(FileNotFoundError):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
