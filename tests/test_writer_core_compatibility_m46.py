"""M4.6.3d.4 — offline safety checks for Writer Core V1 compatibility.

Run at project root:
 python -m unittest scripts.tools.test_writer_core_compatibility_m46 -v
Tests do NOT connect to PostgreSQL, Hugging Face or write archives.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace as NS
from pathlib import Path
from unittest.mock import patch
from scripts.tools import incremental_snapshot_prepare as prep
from scripts.tools.incremental_writer_core import (
    PreparedJsonVersion, validate_prepared, MAX_RECORDS,
)

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = '1' * 40
SHA = 'a' * 64
PATH = 'raw_data/demo/example.json'
REL = f'raw-archive-v2/objects/{SHA[:2]}/{SHA}'


def archive(count=1, path=PATH):
    return NS(relative_path=path, record_count=count, sha256=SHA,
              byte_count=20, archive_relative_path=REL)


def action(path=PATH):
    return NS(path=path, source_code='demo')


def prepared(count=1):
    return NS(content_sha256=SHA, size_bytes=20, records=[('1','json_item',{'a':1})]*count)


class WriterCoreContractTests(unittest.TestCase):
    def test_writer_rejects_empty_record_tuple(self):
        v = PreparedJsonVersion(REPO,PATH,REV,'demo',20,SHA,None,SHA,REL,())
        with self.assertRaisesRegex(ValueError, 'record count'):
            validate_prepared(v)

    def test_writer_accepts_one_valid_record(self):
        v = PreparedJsonVersion(REPO,PATH,REV,'demo',20,SHA,None,SHA,REL,
                                (('0','json_object',{'a':1}),))
        validate_prepared(v)

    def test_preparation_abstraction_allows_zero_but_writer_gate_blocks(self):
        prep.validate_expected_counts(reuse=0, candidates=1,
                                      candidate_bytes=20, candidate_records=0)
        with self.assertRaisesRegex(RuntimeError, 'Writer Core V1'):
            prep.verify_writer_v1_compatibility(REPO, REV, {PATH:NS()},
                                                [action()], [archive(0)])

    def test_writer_gate_accepts_positive_json(self):
        with patch('scripts.tools.incremental_writer_core.prepare_json_version',
                   return_value=prepared(1)) as fn:
            n = prep.verify_writer_v1_compatibility(REPO,REV,{PATH:NS()},
                                                    [action()], [archive()],
                                                    root=Path('/not-a-real-root'))
        self.assertEqual(n, 1)
        self.assertEqual(fn.call_count, 1)

    def test_writer_gate_blocks_negative_or_boolean_record_count(self):
        for n in (-1, True, None, '1'):
            with self.subTest(n=n), self.assertRaisesRegex(RuntimeError, 'Writer Core V1'):
                prep.verify_writer_v1_compatibility(REPO,REV,{PATH:NS()},
                                                    [action()], [archive(n)])

    def test_writer_gate_blocks_too_many_records(self):
        with self.assertRaisesRegex(RuntimeError, 'Writer Core V1'):
            prep.verify_writer_v1_compatibility(REPO,REV,{PATH:NS()},
                                                [action()], [archive(MAX_RECORDS+1)])

    def test_writer_gate_blocks_mismatched_archive_paths(self):
        with self.assertRaisesRegex(RuntimeError, 'path coverage'):
            prep.verify_writer_v1_compatibility(REPO,REV,{PATH:NS()},
                                                [action()], [archive(path='other.json')])

    def test_writer_gate_blocks_missing_target_identity(self):
        with self.assertRaisesRegex(RuntimeError, 'absent from pinned target'):
            prep.verify_writer_v1_compatibility(REPO,REV,{},[action()],[archive()])

    def test_writer_gate_blocks_mismatched_parser_count(self):
        with patch('scripts.tools.incremental_writer_core.prepare_json_version',
                   return_value=prepared(2)):
            with self.assertRaisesRegex(RuntimeError, 'content mismatch'):
                prep.verify_writer_v1_compatibility(REPO,REV,{PATH:NS()},
                                                    [action()],[archive(1)])

    def test_writer_gate_blocks_mismatched_hash(self):
        fake = prepared(1)
        fake.content_sha256 = 'b'*64
        with patch('scripts.tools.incremental_writer_core.prepare_json_version',
                   return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'content mismatch'):
                prep.verify_writer_v1_compatibility(REPO,REV,{PATH:NS()},
                                                    [action()],[archive(1)])

    def test_gate_is_called_before_database_inspection_in_preparation(self):
        source = Path(prep.__file__).read_text(encoding='utf-8')
        a = source.index('    writer_records = verify_writer_v1_compatibility(')
        b = source.index('    existing_versions = read_target_db_state(',a)
        self.assertLess(a,b)


if __name__ == '__main__':
    unittest.main()
