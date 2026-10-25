"""Offline pure-function tests; no DB, network, ingestion, or file writes."""
import unittest
from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.raw_sync_preflight import inspect_entry

H = 'a' * 64
B = 'b' * 40


class RawSyncPreflightTest(unittest.TestCase):
    def test_new_fotmob(self):
        e = inspect_entry('2026_2027_new/matches/fotmob/5795476.json', 'NEW', FileIdentity(123, B, None))
        self.assertEqual((e.source_code, e.file_format, e.errors), ('fotmob', 'json', ()))

    def test_new_understat(self):
        e = inspect_entry('2026_2027_new/matches/understat/31240.json', 'NEW', FileIdentity(123, B, None))
        self.assertEqual((e.source_code, e.file_format, e.errors), ('understat', 'json', ()))

    def test_changed_fpl(self):
        e = inspect_entry('2026_2027_new/fpl_bootstrap_latest.json', 'CHANGED', FileIdentity(123, B, None))
        self.assertEqual((e.source_code, e.file_format, e.errors), ('fpl', 'json', ()))

    def test_changed_fotmob(self):
        e = inspect_entry('2026_2027_new/fotmob_fixtures_2026_2027.json', 'CHANGED', FileIdentity(123, B, None))
        self.assertEqual(e.source_code, 'fotmob')

    def test_out_of_scope_source(self):
        self.assertIn('unclassified-source', ' '.join(inspect_entry('clean/file.json', 'NEW', FileIdentity(1, B, None)).errors))

    def test_bad_path_traversal(self):
        self.assertIn('unsafe-relative-path', inspect_entry('2026_2027_new/../x.json', 'NEW', FileIdentity(1, B, None)).errors)

    def test_missing_identity(self):
        self.assertIn('missing-content-identity', inspect_entry('2026_2027_new/fpl_x.json', 'NEW', FileIdentity(1, None, None)).errors)

    def test_invalid_git_blob(self):
        self.assertIn('invalid-git-blob-id', inspect_entry('2026_2027_new/fpl_x.json', 'NEW', FileIdentity(1, 'bad', None)).errors)

    def test_missing_size(self):
        self.assertIn('missing-or-nonpositive-size', inspect_entry('2026_2027_new/fpl_x.json', 'NEW', FileIdentity(None, B, None)).errors)

    def test_format_blocked(self):
        self.assertIn('unsupported-format', ' '.join(inspect_entry('2026_2027_new/fpl_x.parquet', 'NEW', FileIdentity(1, B, None)).errors))

    def test_no_ingest_unchanged(self):
        self.assertIn('unexpected-ingest-category', inspect_entry('2026_2027_new/fpl_x.json', 'UNCHANGED', FileIdentity(1, B, None)).errors)

    def test_accept_valid_lfs(self):
        self.assertEqual(inspect_entry('2026_2027_new/understat_raw_2026_2027.json', 'CHANGED', FileIdentity(234, B, H)).errors, ())

    def test_large_json_warning(self):
        e = inspect_entry('2026_2027_new/fpl_x.json', 'NEW', FileIdentity(300*1024*1024, B, None))
        self.assertFalse(e.errors)
        self.assertTrue(e.warnings)


if __name__ == '__main__':
    unittest.main()
