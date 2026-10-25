"""M4.6.2d.2 offline target-state tests (no real database/HF calls).

Run from project root:
    python -m unittest tests.test_check_raw_updates_m46 tests.test_target_state_gate_m46 -v
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

REPO = "Toibinguz/epl-premier-league-dataset"
BASE = "0" * 39 + "1"
OTHER = "b" * 40
SHA = "a" * 64
PATH = f"raw-archive-v2/manifests/aa/{SHA}.json"
SELECTION = subject.BaselineSelection(365, BASE, 304, 1, SHA, PATH)
FILES = {"raw_data/demo.json": subject.FileIdentity(5, "f" * 40, None)}


class FakeCursor:
    def __init__(self, rows):
        self.answers = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.answers.pop(0)

    def fetchall(self):
        return self.answers.pop(0)


class FakeConnection:
    def __init__(self, cursor):
        self.cur = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


class TargetDecisionTests(unittest.TestCase):
    def action(self, target=OTHER, state=None):
        return subject.classify_target_state(365, BASE, target, state)

    def test_current_revision_is_skipped(self):
        self.assertEqual(self.action(BASE, subject.TargetSnapshotState(365, "completed")),
                         "SKIP_ALREADY_CURRENT")

    def test_current_revision_inconsistent_missing_db_row_is_error(self):
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            self.action(BASE, None)

    def test_current_revision_wrong_snapshot_is_error(self):
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            self.action(BASE, subject.TargetSnapshotState(4, "completed"))

    def test_absent_target_is_new_revision(self):
        self.assertEqual(self.action(), "PLAN_NEW_REVISION")

    def test_completed_other_revision_is_skipped(self):
        self.assertEqual(self.action(state=subject.TargetSnapshotState(4, "completed")),
                         "SKIP_ALREADY_PROCESSED")

    def test_in_progress_requires_recovery(self):
        self.assertEqual(self.action(state=subject.TargetSnapshotState(401, "in_progress")),
                         "RECOVERY_REQUIRED")

    def test_discovered_and_failed_are_blocked(self):
        for status in ("discovered", "failed", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(self.action(state=subject.TargetSnapshotState(401, status)),
                                 "BLOCKED_EXISTING_TARGET_STATE")

    def test_invalid_revision_refused(self):
        with self.assertRaisesRegex(RuntimeError, "SHA40"):
            self.action(target="main")


class TargetStateSqlTests(unittest.TestCase):
    def mocked_read(self, responses, db="epl_raw_storage_v2"):
        cursor = FakeCursor(responses)
        conn = FakeConnection(cursor)
        config = {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": db,
                  "DB_USER": "postgres", "DB_PASSWORD": "not-real"}
        kwargs = {}

        def fake_connect(**kw):
            kwargs.update(kw)
            return conn

        with patch.dict(sys.modules, {
            "psycopg": types.SimpleNamespace(connect=fake_connect),
            "dotenv": types.SimpleNamespace(dotenv_values=lambda *args, **kw: config),
        }):
            if db != "epl_raw_storage_v2":
                with self.assertRaisesRegex(RuntimeError, "unexpected database"):
                    subject.read_target_snapshot_state(REPO, OTHER)
                return None, cursor, kwargs
            result = subject.read_target_snapshot_state(REPO, OTHER)
        return result, cursor, kwargs

    def test_target_read_only_and_parameterized(self):
        state, cursor, kwargs = self.mocked_read([("epl_raw_storage_v2",), [(4, "completed")]])
        self.assertEqual(state, subject.TargetSnapshotState(4, "completed"))
        self.assertIn("default_transaction_read_only=on", kwargs["options"])
        self.assertEqual(cursor.statements[-1][1], (REPO, OTHER))
        self.assertIn("raw.snapshot_runs", cursor.statements[-1][0])

    def test_absent_target(self):
        state, _, _ = self.mocked_read([("epl_raw_storage_v2",), []])
        self.assertIsNone(state)

    def test_duplicate_target_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            self.mocked_read([("epl_raw_storage_v2",), [(4, "completed"), (365, "completed")]])

    def test_wrong_database_config_rejected(self):
        self.mocked_read([], db="other_db")

    def test_invalid_target_not_sent_to_db(self):
        with self.assertRaisesRegex(RuntimeError, "pinned"):
            subject.read_target_snapshot_state(REPO, "main")


class TargetStateCliTests(unittest.TestCase):
    def invoke(self, target, state, *, state_only=True):
        argv = ["check_raw_updates.py", "--target-revision", target]
        if state_only:
            argv.append("--target-state-only")
        with patch.object(subject, "select_latest_completed_baseline", return_value=SELECTION), \
             patch.object(subject, "load_baseline", return_value=(BASE, FILES)), \
             patch.object(subject, "read_target_snapshot_state", return_value=state), \
             patch.object(subject, "resolve_target_revision", return_value=target), \
             patch.object(subject, "load_target", side_effect=AssertionError("HF tree not allowed")), \
             patch.object(sys, "argv", argv), \
             redirect_stdout(io.StringIO()) as out:
            code = subject.main()
        return code, out.getvalue()

    def test_current_revision_target_state_only_no_hf(self):
        code, output = self.invoke(BASE, subject.TargetSnapshotState(365, "completed"))
        self.assertEqual(code, 0)
        self.assertIn("SKIP_ALREADY_CURRENT", output)
        self.assertIn("Hugging Face requests: NONE", output)

    def test_old_completed_target_is_skipped(self):
        code, output = self.invoke(OTHER, subject.TargetSnapshotState(4, "completed"))
        self.assertEqual(code, 0)
        self.assertIn("SKIP_ALREADY_PROCESSED", output)

    def test_in_progress_is_blocked(self):
        code, output = self.invoke(OTHER, subject.TargetSnapshotState(999, "in_progress"))
        self.assertEqual(code, 2)
        self.assertIn("RECOVERY_REQUIRED", output)

    def test_discovered_is_blocked(self):
        code, output = self.invoke(OTHER, subject.TargetSnapshotState(999, "discovered"))
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED_EXISTING_TARGET_STATE", output)

    def test_missing_target_is_plan_ready(self):
        code, output = self.invoke(OTHER, None)
        self.assertEqual(code, 0)
        self.assertIn("PLAN_NEW_REVISION", output)

    def test_normal_plan_short_circuits_completed_without_hf_tree(self):
        code, output = self.invoke(OTHER, subject.TargetSnapshotState(4, "completed"),
                                   state_only=False)
        self.assertEqual(code, 0)
        self.assertIn("Hugging Face file-tree requests: NONE", output)


if __name__ == "__main__":
    unittest.main()
