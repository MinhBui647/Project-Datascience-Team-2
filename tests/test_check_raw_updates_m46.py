"""M4.6.2c offline tests; run from the project root:

python -m unittest tests.test_check_raw_updates_m46 -v

Place check_raw_updates_m46.py content in scripts/tools/check_raw_updates.py.
"""
import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

try:
    from scripts.tools import check_raw_updates as subject
except ImportError:
    import check_raw_updates as subject

SHA = "a" * 64
REV = "b" * 40
REPO = "Toibinguz/epl-premier-league-dataset"
PATH = f"raw-archive-v2/manifests/aa/{SHA}.json"
ROW = (365, REV, "completed", 304, 286, SHA, PATH)


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=None):
        self.statements.append((sql, args))

    def fetchone(self):
        return self.rows.pop(0)


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


class BaselineSelectorUnitTests(unittest.TestCase):
    def test_accepts_valid_latest_snapshot(self):
        selected = subject.validate_baseline_selection(ROW)
        self.assertEqual((selected.snapshot_id, selected.hf_revision), (365, REV))
        self.assertEqual(selected.raw_scope_file_count, 286)

    def test_no_completed_snapshot_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "No completed baseline"):
            subject.validate_baseline_selection(None)

    def test_status_must_be_completed(self):
        row = (365, REV, "in_progress", 304, 286, SHA, PATH)
        with self.assertRaisesRegex(RuntimeError, "not completed"):
            subject.validate_baseline_selection(row)

    def test_invalid_revision_fails(self):
        with self.assertRaisesRegex(RuntimeError, "revision"):
            subject.validate_baseline_selection((365, "main", "completed", 304, 286, SHA, PATH))

    def test_missing_manifest_fails(self):
        with self.assertRaisesRegex(RuntimeError, "manifest SHA256"):
            subject.validate_baseline_selection((365, REV, "completed", 304, 286, None, PATH))

    def test_noncanonical_archive_fails(self):
        with self.assertRaisesRegex(RuntimeError, "noncanonical"):
            subject.validate_baseline_selection((365, REV, "completed", 304, 286, SHA, "bad-path"))

    def test_invalid_scope_fails(self):
        with self.assertRaisesRegex(RuntimeError, "raw_scope"):
            subject.validate_baseline_selection((365, REV, "completed", 304, 305, SHA, PATH))

    def test_bad_snapshot_id_fails(self):
        with self.assertRaisesRegex(RuntimeError, "snapshot_id"):
            subject.validate_baseline_selection((0, REV, "completed", 304, 286, SHA, PATH))

    def test_selector_is_parameterized_and_read_only(self):
        cursor = FakeCursor([("epl_raw_storage_v2",), ROW])
        connection = FakeConnection(cursor)
        fake_psycopg = types.SimpleNamespace(connect=lambda **kwargs: connection)
        config = {"DB_HOST": "localhost", "DB_PORT": "5432",
                  "DB_NAME": "epl_raw_storage_v2", "DB_USER": "postgres",
                  "DB_PASSWORD": "not-a-real-password"}
        fake_dotenv = types.SimpleNamespace(dotenv_values=lambda *a, **kw: config)
        saved = {}

        def capture_connect(**kwargs):
            saved.update(kwargs)
            return connection

        fake_psycopg.connect = capture_connect
        with patch.dict(sys.modules, {"psycopg": fake_psycopg, "dotenv": fake_dotenv}):
            result = subject.select_latest_completed_baseline(REPO)
        self.assertEqual(result.snapshot_id, 365)
        self.assertIn("default_transaction_read_only=on", saved["options"])
        self.assertIn("ORDER BY observed_at DESC, snapshot_id DESC", cursor.statements[-1][0])
        self.assertIn("status = 'completed'", cursor.statements[-1][0])
        self.assertEqual(cursor.statements[-1][1], (REPO,))
        self.assertNotIn("UPDATE", cursor.statements[-1][0])

    def test_db_guard_blocks_wrong_database(self):
        cursor = FakeCursor([("some_other_db",)])
        conn = FakeConnection(cursor)
        config = {"DB_HOST": "localhost", "DB_PORT": "5432",
                  "DB_NAME": "epl_raw_storage_v2", "DB_USER": "postgres",
                  "DB_PASSWORD": "not-a-real-password"}
        with patch.dict(sys.modules, {
            "psycopg": types.SimpleNamespace(connect=lambda **kw: conn),
            "dotenv": types.SimpleNamespace(dotenv_values=lambda *a, **kw: config)
        }):
            with self.assertRaisesRegex(RuntimeError, "Unexpected database"):
                subject.select_latest_completed_baseline(REPO)

    def test_baseline_only_does_not_access_hugging_face(self):
        selected = subject.BaselineSelection(365, REV, 304, 1, SHA, PATH)
        baseline = {"raw_data/sample.json": subject.FileIdentity(10, "c" * 40, None)}
        with patch.object(subject, "select_latest_completed_baseline", return_value=selected), \
             patch.object(subject, "load_baseline", return_value=(REV, baseline)), \
             patch.object(subject, "load_target", side_effect=AssertionError("HF must not be called")), \
             patch.object(sys, "argv", ["check_raw_updates.py", "--baseline-only"]), \
             redirect_stdout(io.StringIO()) as buf:
            code = subject.main()
        self.assertEqual(code, 0)
        self.assertIn("Selected snapshot: 365", buf.getvalue())
        self.assertIn("Hugging Face requests: NONE", buf.getvalue())

    def test_manual_baseline_override_preserved(self):
        baseline = {"raw_data/sample.json": subject.FileIdentity(10, "c" * 40, None)}
        with patch.object(subject, "select_latest_completed_baseline", side_effect=AssertionError("Auto selection not expected")), \
             patch.object(subject, "load_baseline", return_value=(REV, baseline)), \
             patch.object(sys, "argv", ["check_raw_updates.py", "--baseline-snapshot", "4", "--baseline-only"]), \
             redirect_stdout(io.StringIO()) as buf:
            code = subject.main()
        self.assertEqual(code, 0)
        self.assertIn("Selected snapshot: 4", buf.getvalue())
        self.assertIn("EXPLICIT_OVERRIDE", buf.getvalue())

    def test_baseline_drift_fails_closed(self):
        selected = subject.BaselineSelection(365, REV, 304, 286, SHA, PATH)
        with patch.object(subject, "select_latest_completed_baseline", return_value=selected), \
             patch.object(subject, "load_baseline", return_value=(REV, {})), \
             patch.object(sys, "argv", ["check_raw_updates.py", "--baseline-only"]):
            with self.assertRaisesRegex(RuntimeError, "Baseline changed"):
                subject.main()


if __name__ == "__main__":
    unittest.main()
