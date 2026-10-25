"""Offline tests for M4.5.3c, no HF, DB, network, or ingestion."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.validate_raw_contents import (
    check_jsonb_value, validate_local_json, verify_bytes,
)


class RawContentValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, name, body):
        path = self.root / name
        path.write_bytes(body)
        git = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
        sha = hashlib.sha256(body).hexdigest()
        return path, FileIdentity(len(body), git, sha)

    def test_json_root_object(self):
        f, meta = self.fixture("a.json", b'{"a":1,"nested":{"b":[1,2]}}')
        result = validate_local_json(f, meta)
        self.assertEqual(result.record_count, 1)
        self.assertEqual(result.first_locator, "root")
        self.assertEqual(result.identity_type, "LFS-SHA256")

    def test_json_root_list(self):
        f, meta = self.fixture("a.json", b'[{"x":1},{"x":2},{"x":3}]')
        result = validate_local_json(f, meta)
        self.assertEqual(result.record_count, 3)
        self.assertEqual(result.last_locator, "item:2")

    def test_json_scalar_preserved_by_parser(self):
        f, meta = self.fixture("a.json", b'"hello"')
        self.assertEqual(validate_local_json(f, meta).record_count, 1)

    def test_git_blob_identity_non_lfs(self):
        f, meta = self.fixture("a.json", b'{"a":1}')
        no_lfs = FileIdentity(meta.size, meta.git_blob_id, None)
        self.assertEqual(validate_local_json(f, no_lfs).identity_type, "GIT-BLOB-SHA1")

    def test_lfs_hash_mismatch_blocks(self):
        f, meta = self.fixture("a.json", b'{"a":1}')
        wrong = FileIdentity(meta.size, meta.git_blob_id, "0" * 64)
        with self.assertRaisesRegex(ValueError, "LFS SHA256 mismatch"):
            validate_local_json(f, wrong)

    def test_git_blob_mismatch_blocks(self):
        f, meta = self.fixture("a.json", b'{"a":1}')
        wrong = FileIdentity(meta.size, "0" * 40, None)
        with self.assertRaisesRegex(ValueError, "Git blob SHA1 mismatch"):
            validate_local_json(f, wrong)

    def test_size_mismatch_blocks(self):
        f, meta = self.fixture("a.json", b'{"a":1}')
        wrong = FileIdentity(meta.size + 1, meta.git_blob_id, meta.lfs_sha256)
        with self.assertRaisesRegex(ValueError, "Byte size mismatch"):
            validate_local_json(f, wrong)

    def test_file_budget_blocks(self):
        f, meta = self.fixture("a.json", b'{"a":1}')
        with self.assertRaisesRegex(ValueError, "exceeds configured"):
            validate_local_json(f, meta, max_file_bytes=4)

    def test_duplicate_json_keys_block(self):
        f, meta = self.fixture("a.json", b'{"a":1,"a":2}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON object key"):
            validate_local_json(f, meta)

    def test_invalid_json_blocks(self):
        f, meta = self.fixture("a.json", b'{"x":}')
        with self.assertRaisesRegex(ValueError, "JSON parse failed"):
            validate_local_json(f, meta)

    def test_nan_blocked(self):
        f, meta = self.fixture("a.json", b'{"x":NaN}')
        with self.assertRaisesRegex(ValueError, "Nonstandard JSON constant"):
            validate_local_json(f, meta)

    def test_zero_records_requires_review(self):
        f, meta = self.fixture("a.json", b'[]')
        with self.assertRaisesRegex(ValueError, "zero Raw records"):
            validate_local_json(f, meta)

    def test_embedded_null_in_jsonb_string_blocks(self):
        f, meta = self.fixture("a.json", b'{"x":"\\u0000"}')
        with self.assertRaisesRegex(ValueError, "NUL character"):
            validate_local_json(f, meta)

    def test_surrogate_in_jsonb_string_blocks(self):
        f, meta = self.fixture("a.json", b'{"x":"\\ud800"}')
        with self.assertRaisesRegex(ValueError, "Unpaired UTF-16 surrogate"):
            validate_local_json(f, meta)

    def test_no_content_identity_blocks(self):
        f, meta = self.fixture("a.json", b'{"x":1}')
        wrong = FileIdentity(meta.size, None, None)
        with self.assertRaisesRegex(ValueError, "No comparable"):
            validate_local_json(f, wrong)


if __name__ == "__main__":
    unittest.main()
