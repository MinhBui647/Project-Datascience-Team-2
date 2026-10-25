"""Offline read-only CSV archive and writer compatibility, no database calls."""
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import TestCase, mock

from scripts.tools import incremental_archive_promotion as archive
from scripts.tools import incremental_snapshot_prepare as snapshot
from scripts.tools.check_raw_updates import FileIdentity

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = 'a'*40
PATH = '2026_2027_new/football_data_raw_2627.csv'

class ArchiveBridgeTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.content = b'Date,HomeTeam,AwayTeam\n2026-08-21,Arsenal,Chelsea\n2026-08-22,Liverpool,Everton\n'
        self.csv = self.root / 'verified_input'
        self.csv.write_bytes(self.content)
        self.sha = hashlib.sha256(self.content).hexdigest()
        self.identity = FileIdentity(size=len(self.content), git_blob_id=None, lfs_sha256=self.sha)

    def test_csv_opt_in_yields_two_rows(self):
        result = archive.inspect_cached(self.root, PATH, self.csv, self.identity, allow_csv=True)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.sha256, self.sha)
        self.assertEqual(result.action, 'READY_NEW')

    def test_csv_not_enabled_by_default(self):
        with self.assertRaisesRegex(ValueError, 'JSON candidates'):
            archive.inspect_cached(self.root, PATH, self.csv, self.identity)

    def test_tar_not_enabled(self):
        with self.assertRaises(ValueError):
            archive.inspect_cached(self.root, PATH + '.tar.gz', self.csv, self.identity, allow_csv=True)

    def test_hash_mismatch_rejected(self):
        bad = FileIdentity(len(self.content), None, '0'*64)
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            archive.inspect_cached(self.root, PATH, self.csv, bad, allow_csv=True)

    def test_unsafe_path_rejected(self):
        with self.assertRaises(ValueError):
            archive.inspect_cached(self.root, 'raw_data/../evil.csv', self.csv, self.identity, allow_csv=True)

    def test_invalid_switch_rejected(self):
        with self.assertRaises(ValueError):
            archive.inspect_cached(self.root, PATH, self.csv, self.identity, allow_csv=1)

    def test_reject_oversized_csv_even_if_hash_valid(self):
        with mock.patch.object(archive, 'MAX_CSV_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'byte budget'):
                archive.inspect_cached(self.root, PATH, self.csv, self.identity, allow_csv=True)

    def test_existing_archive_readback_immutable(self):
        object_path, _, _ = archive.inspect_existing(self.root, self.sha, len(self.content), max_file_bytes=1024)
        object_path.parent.mkdir(parents=True)
        object_path.write_bytes(self.content)
        result = archive.inspect_cached(self.root, PATH, self.csv, self.identity, allow_csv=True)
        self.assertEqual(result.action, 'ALREADY_ARCHIVED')
        self.assertEqual(result.archive_relative_path, f'raw-archive-v2/objects/{self.sha[:2]}/{self.sha}')

    def test_corrupted_existing_archive_refused(self):
        obj, _, _ = archive.inspect_existing(self.root, self.sha, len(self.content), max_file_bytes=1024)
        obj.parent.mkdir(parents=True)
        obj.write_bytes(b'evil')
        with self.assertRaisesRegex(RuntimeError, 'corrupted'):
            archive.inspect_cached(self.root, PATH, self.csv, self.identity, allow_csv=True)


class WriterPreflightBridgeTests(TestCase):
    def setUp(self):
        self.item = NS(relative_path=PATH, record_count=2, sha256='f'*64, byte_count=123,
                       archive_relative_path='raw-archive-v2/objects/ff/' + 'f'*64)
        self.action = NS(path=PATH, source_code='football_data')
        self.target = {PATH: object()}

    def test_csv_preflight_requires_opt_in(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsupported format'):
            snapshot.verify_writer_v1_compatibility(REPO, REV, self.target, [self.action],
                                                    [self.item], root=Path('/does/not/matter'))

    def test_csv_preflight_calls_csv_core(self):
        prepared = NS(content_sha256=self.item.sha256, size_bytes=123, records=(1,2))
        with mock.patch('scripts.tools.incremental_writer_core.prepare_csv_version', return_value=prepared) as csv_core, \
             mock.patch('scripts.tools.incremental_writer_core.prepare_json_version') as json_core:
            count = snapshot.verify_writer_v1_compatibility(REPO, REV, self.target, [self.action],
                                                            [self.item], allow_csv=True)
        self.assertEqual(count, 2)
        csv_core.assert_called_once()
        json_core.assert_not_called()

    def test_mismatched_core_archive_count_blocked(self):
        bad = NS(content_sha256='0'*64, size_bytes=123, records=(1,2))
        with mock.patch('scripts.tools.incremental_writer_core.prepare_csv_version', return_value=bad):
            with self.assertRaisesRegex(RuntimeError, 'content mismatch'):
                snapshot.verify_writer_v1_compatibility(REPO, REV, self.target, [self.action],
                                                        [self.item], allow_csv=True)

    def test_invalid_writer_switch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'opt-in'):
            snapshot.verify_writer_v1_compatibility(REPO, REV, self.target, [self.action],
                                                    [self.item], allow_csv=1)

    def test_csv_zero_records_blocked(self):
        self.item.record_count = 0
        with self.assertRaisesRegex(RuntimeError, 'requires 1'):
            snapshot.verify_writer_v1_compatibility(REPO, REV, self.target, [self.action],
                                                    [self.item], allow_csv=True)
