import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.check_raw_updates import FileIdentity, classify, compare_snapshots, in_raw_scope


def f(size=12, blob='aaa', lfs=None):
    return FileIdentity(size=size, git_blob_id=blob, lfs_sha256=lfs)


class RawUpdateDetectorTest(unittest.TestCase):
    def test_real_shape_counts_and_paths(self):
        old = {f'raw_data/old_{i}.csv': f(blob=str(i)) for i in range(272)}
        old.update({f'2026_2027_new/updated_{i}.json': f(blob=f'old{i}') for i in range(4)})
        new = dict(old)
        for i in range(4):
            new[f'2026_2027_new/updated_{i}.json'] = f(blob=f'new{i}')
        new.update({f'2026_2027_new/matches/fotmob/{i}.json': f(blob=f'created{i}') for i in range(10)})
        result = compare_snapshots(old, new)
        self.assertEqual({k: len(v) for k,v in result.items()},
                         {'UNCHANGED':272,'NEW':10,'CHANGED':4,'REMOVED':0,'REVIEW':0})

    def test_clean_only_commit_becomes_no_raw_changes(self):
        old = {'raw_data/a.csv': f()}
        self.assertEqual(compare_snapshots(old, old)['UNCHANGED'], ['raw_data/a.csv'])
        self.assertFalse(in_raw_scope('clean/new_data.parquet'))
        self.assertFalse(in_raw_scope('.gitattributes'))

    def test_same_size_different_hash_is_changed(self):
        self.assertEqual(classify(f(blob='a'), f(blob='b')), 'CHANGED')

    def test_same_size_without_comparable_hash_needs_review(self):
        self.assertEqual(classify(f(blob='a'), f(blob=None)), 'REVIEW')
        self.assertEqual(classify(f(blob=None), f(blob=None)), 'REVIEW')

    def test_lfs_hash_priority(self):
        self.assertEqual(classify(f(blob='x', lfs='a'), f(blob='y', lfs='a')), 'UNCHANGED')
        self.assertEqual(classify(f(blob='x', lfs='a'), f(blob='x', lfs='b')), 'CHANGED')

    def test_different_size_is_changed(self):
        self.assertEqual(classify(f(size=12, blob='a'), f(size=13, blob='b')), 'CHANGED')

    def test_inconsistent_metadata_requires_review(self):
        self.assertEqual(classify(f(size=12, blob='a'), f(size=13, blob='a')), 'REVIEW')

    def test_added_removed_are_not_record_deletions(self):
        old = {'raw_data/a.csv': f()}
        new = {'raw_data/b.csv': f()}
        result = compare_snapshots(old, new)
        self.assertEqual(result['NEW'], ['raw_data/b.csv'])
        self.assertEqual(result['REMOVED'], ['raw_data/a.csv'])

    def test_deterministic_sorting(self):
        old = {'raw_data/b.json': f(), 'raw_data/a.json': f()}
        self.assertEqual(compare_snapshots(old, old)['UNCHANGED'],
                         ['raw_data/a.json', 'raw_data/b.json'])

    def test_out_of_scope_rejected(self):
        with self.assertRaises(ValueError):
            compare_snapshots({'clean/a.json': f()}, {})

    def test_same_content_blob_and_size_unchanged(self):
        self.assertEqual(classify(f(size=42, blob='a'), f(size=42, blob='a')),
                         'UNCHANGED')


if __name__ == '__main__':
    unittest.main()
