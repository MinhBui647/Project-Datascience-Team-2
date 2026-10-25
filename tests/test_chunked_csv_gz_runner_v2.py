"""Offline safety/compatibility checks for the R3 unified CSV.GZ runner.

Run from project root: python -m unittest discover -s tests -p 'test_chunked_csv_gz_runner_v2.py' -v
No PostgreSQL, Hugging Face or production raw archive required.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "tools"


def load_module(stem):
    path = SCRIPTS / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    return module


class UnifiedRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.new = load_module('chunked_csv_gz_runner')
        cls.legacy_names = {
            'appearances': 'm3_7_8_chunked_csv_gz_v2',
            'game_events': 'm3_7_9_game_events_chunked_v2',
            'game_lineups': 'm3_7_10_game_lineups_chunked_v2',
        }
        cls.old = {k: load_module(v) for k, v in cls.legacy_names.items()
                   if (SCRIPTS / f'{v}.py').exists()}
        cls.golden = {
            'appearances': (
                'raw_data/transfermarkt/tfm_appearances_full.csv.gz',
                'm3_7_6_tfm_appearances_full_preflight.json',
                1894350, 198398405, 64*1024**2, 'm3_7_8_chunked_csv_gz_v2'),
            'game_events': (
                'raw_data/transfermarkt/tfm_game_events_full.csv.gz',
                'm3_7_6_tfm_game_events_full_preflight.json',
                1274469, 179098694, 64*1024**2, 'm3_7_9_game_events_chunked_v2'),
            'game_lineups': (
                'raw_data/transfermarkt/tfm_game_lineups_full.csv.gz',
                'm3_7_6_tfm_game_lineups_full_preflight.json',
                3179016, 415978884, 160*1024**2, 'm3_7_10_game_lineups_chunked_v2'),
        }

    def test_profiles_match_historic_constants(self):
        for key, expected in self.golden.items():
            with self.subTest(key=key):
                p = self.new.PROFILES[key]
                self.assertEqual((p.target, p.preflight_name, p.expected_rows,
                                  p.expected_uncompressed_bytes,
                                  p.max_compressed_bytes, p.marker), expected)
                if key in self.old:
                    old = self.old[key]
                    self.assertEqual((p.target, p.preflight_name, p.expected_rows,
                                      p.expected_uncompressed_bytes,
                                      p.max_compressed_bytes, p.marker),
                                     (old.TARGET, old.REPORT.name, old.EXPECTED_ROWS,
                                      old.EXPECTED_UNCOMPRESSED_BYTES,
                                      old.MAX_COMPRESSED_BYTES, old.MARKER))

    def test_offline_plans_match_legacy(self):
        with tempfile.TemporaryDirectory(prefix='r3_chunked_test_') as temp:
            temp_root = Path(temp)
            reports = temp_root / 'reports'
            reports.mkdir()
            rows = []
            for i in range(273):
                rows.append({
                    'relative_path': f'other/fake_{i:03d}.csv',
                    'hf_revision': self.new.REV,
                    'format': 'csv', 'group': 'raw_data',
                    'source_hint': 'other', 'size_bytes': '10',
                    'lfs_sha256': 'a'*64,
                })
            for i, (key, p) in enumerate(self.new.PROFILES.items()):
                rows.append({
                    'relative_path': p.target,
                    'hf_revision': self.new.REV,
                    'format': 'csv.gz', 'group': 'raw_data',
                    'source_hint': 'transfermarkt', 'size_bytes': str(10_000 + i),
                    'lfs_sha256': chr(ord('b') + i) * 64,
                })
            rows.sort(key=lambda r: r['relative_path'])
            catalog = reports / 'raw_scope_catalog_v2.csv'
            with catalog.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
            for key, p in self.new.PROFILES.items():
                index = next(i for i, entry in enumerate(rows) if entry['relative_path'] == p.target)
                entry = rows[index]
                report = {
                    'repo_id': self.new.REPO, 'revision': self.new.REV,
                    'relative_path': p.target, 'index': index,
                    'sha256': entry['lfs_sha256'],
                    'compressed_bytes': int(entry['size_bytes']),
                    'decompressed_bytes': p.expected_uncompressed_bytes,
                    'raw_parser_records': p.expected_rows,
                    'status': 'pass'
                }
                (reports / p.preflight_name).write_text(json.dumps(report), encoding='utf-8')
            with patch.object(self.new, 'ROOT', temp_root), patch.object(self.new, 'CATALOG', catalog):
                for key, p in self.new.PROFILES.items():
                    with self.subTest(key=key):
                        new_plan = self.new.load_plan(p)
                        if key in self.old:
                            old = self.old[key]
                            with patch.object(old, 'CATALOG', catalog), patch.object(old, 'REPORT', reports / p.preflight_name):
                                self.assertEqual(old.load_plan(), new_plan)
                        with redirect_stdout(io.StringIO()) as stream:
                            self.new.render_plan(new_plan, 10000, p)
                        printed = stream.getvalue()
                        self.assertIn(p.target, printed)
                        self.assertIn(f'{p.expected_rows}', printed)
                # A mismatched preflight must be rejected before DB/network.
                first = self.new.PROFILES['appearances']
                bad_path = reports / first.preflight_name
                bad = json.loads(bad_path.read_text(encoding='utf-8'))
                bad['raw_parser_records'] -= 1
                bad_path.write_text(json.dumps(bad), encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'Preflight report mismatch'):
                    self.new.load_plan(first)

    def test_partial_checkpoint_marker_backward_compatible(self):
        class FakeCursor:
            def __init__(self, row): self.row=row
            def execute(self, *_): pass
            def fetchone(self): return self.row
        for key, p in self.new.PROFILES.items():
            with self.subTest(key=key):
                row = (4, 'csv.gz', 100, 'raw-archive-v2/objects/aa/example',
                       'verified', 'partial', 15,
                       {'chunked_ingester': p.marker,
                        'expected_preflight_rows': p.expected_rows})
                cursor = FakeCursor(row)
                self.assertEqual(
                    self.new.check_previous_file(cursor,'a'*64,100,'raw-archive-v2/objects/aa/example',p),
                    (4, 'partial',15))
                row_bad = (*row[:-1], {'chunked_ingester': 'wrong', 'expected_preflight_rows': p.expected_rows})
                with self.assertRaisesRegex(RuntimeError, 'refuse takeover'):
                    self.new.check_previous_file(FakeCursor(row_bad),'a'*64,100,'raw-archive-v2/objects/aa/example',p)


if __name__ == '__main__':
    unittest.main()
