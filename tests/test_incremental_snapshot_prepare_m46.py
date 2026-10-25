"""Offline unit tests for M4.6.3b.1.

Run from project root:
    python -m unittest scripts.tools.test_incremental_snapshot_prepare_m46 -v
No Hugging Face access, DB connection or archive writes.
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from scripts.tools import incremental_snapshot_prepare as m


SHA = "a" * 64
PATH1 = "2026_2027_new/matches/a.json"
PATH2 = "raw_data/demo/b.json"
PATH3 = "raw_data/demo/c.json"


@dataclass(frozen=True)
class Action:
    path: str
    kind: str
    category: str


@dataclass(frozen=True)
class Archive:
    relative_path: str
    action: str = "ALREADY_ARCHIVED"
    byte_count: int = 20
    record_count: int = 1
    sha256: str = SHA
    archive_relative_path: str = "raw-archive-v2/objects/aa/" + SHA


def make_case(reuse=1, *, candidate_records=1, already=False):
    actions = []
    links = []
    if reuse:
        actions = [Action(PATH2, "REUSE_FILE_ID", "UNCHANGED")]
        links = [SimpleNamespace(relative_path=PATH2, file_id=4)]
    actions.append(Action(PATH1, "UPSERT_VERSION_AFTER_REHASH", "NEW"))
    plan = SimpleNamespace(
        actions=tuple(actions), fingerprint=SHA, candidate_bytes=20,
        target_raw_count=1 + reuse, reused_records=42 if reuse else 0,
    )
    plan.count = lambda category: sum(a.category == category for a in plan.actions)
    archives = [Archive(PATH1, record_count=candidate_records)]
    existing_versions = {(PATH1, SHA): "REUSE_VERIFIED" if already else None}
    kwargs = dict(excluded=18, links=links, archives=archives,
                  existing_versions=existing_versions, expected_fingerprint=SHA,
                  expected_reuse=reuse, expected_candidates=1,
                  expected_bytes=20, expected_records=candidate_records)
    return plan, kwargs


class ExpectedBudgetTests(unittest.TestCase):
    def test_original_positive_counts(self):
        m.validate_expected_counts(reuse=272, candidates=14,
                                   candidate_bytes=5458820, candidate_records=393)

    def test_zero_reuse_is_valid(self):
        m.validate_expected_counts(reuse=0, candidates=1,
                                   candidate_bytes=20, candidate_records=1)

    def test_zero_records_valid_for_empty_json(self):
        m.validate_expected_counts(reuse=0, candidates=1,
                                   candidate_bytes=2, candidate_records=0)

    def test_noop_is_not_a_snapshot(self):
        with self.assertRaisesRegex(RuntimeError, "candidates > 0"):
            m.validate_expected_counts(reuse=286, candidates=0,
                                       candidate_bytes=0, candidate_records=0)

    def test_negative_counts_rejected(self):
        for key in ("reuse", "candidates", "candidate_bytes", "candidate_records"):
            values = dict(reuse=1, candidates=1, candidate_bytes=10, candidate_records=2)
            values[key] = -1
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                m.validate_expected_counts(**values)

    def test_bool_counts_rejected(self):
        for key in ("reuse", "candidates", "candidate_bytes", "candidate_records"):
            values = dict(reuse=1, candidates=1, candidate_bytes=10, candidate_records=2)
            values[key] = True
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "not bool"):
                m.validate_expected_counts(**values)


class PreparationContractTests(unittest.TestCase):
    def test_valid_mix_reuse_and_candidate(self):
        plan, kw = make_case(1)
        result = m.validate_preparation(plan, **kw)
        self.assertEqual((result.raw_files, result.reused, result.candidates), (2, 1, 1))
        self.assertEqual((result.total_files, result.projected_records), (20, 43))

    def test_valid_zero_reuse(self):
        plan, kw = make_case(0)
        result = m.validate_preparation(plan, **kw)
        self.assertEqual((result.reused, result.candidates, result.projected_records), (0, 1, 1))

    def test_valid_zero_parser_records(self):
        plan, kw = make_case(0, candidate_records=0)
        self.assertEqual(m.validate_preparation(plan, **kw).projected_records, 0)

    def test_valid_existing_version(self):
        plan, kw = make_case(1, already=True)
        self.assertEqual(m.validate_preparation(plan, **kw).already_registered_versions, 1)

    def test_wrong_fingerprint_rejected(self):
        plan, kw = make_case(1)
        kw["expected_fingerprint"] = "b" * 64
        with self.assertRaisesRegex(RuntimeError, "fingerprint mismatch"):
            m.validate_preparation(plan, **kw)

    def test_removed_path_blocks(self):
        plan, kw = make_case(0)
        plan.actions = plan.actions + (Action(PATH3, "NOTIFY_ONLY_NO_DELETE", "REMOVED"),)
        with self.assertRaisesRegex(RuntimeError, "Removed/review"):
            m.validate_preparation(plan, **kw)

    def test_unarchived_candidate_blocks(self):
        plan, kw = make_case(1)
        kw["archives"] = [Archive(PATH1, action="NOT_ARCHIVED")]
        with self.assertRaisesRegex(RuntimeError, "not been archived"):
            m.validate_preparation(plan, **kw)

    def test_candidate_wrong_path_blocks(self):
        plan, kw = make_case(1)
        kw["archives"] = [Archive(PATH3)]
        with self.assertRaisesRegex(RuntimeError, "paths differ"):
            m.validate_preparation(plan, **kw)

    def test_reuse_wrong_path_blocks(self):
        plan, kw = make_case(1)
        kw["links"] = [SimpleNamespace(relative_path=PATH3, file_id=4)]
        with self.assertRaisesRegex(RuntimeError, "paths differ"):
            m.validate_preparation(plan, **kw)

    def test_archive_metadata_invalid_blocks(self):
        plan, kw = make_case(0)
        kw["archives"] = [Archive(PATH1, sha256="bad")]
        with self.assertRaisesRegex(RuntimeError, "archive metadata"):
            m.validate_preparation(plan, **kw)

    def test_existing_audit_missing_blocks(self):
        plan, kw = make_case(0)
        kw["existing_versions"] = {}
        with self.assertRaisesRegex(RuntimeError, "audit incomplete"):
            m.validate_preparation(plan, **kw)

    def test_existing_audit_extra_blocks(self):
        plan, kw = make_case(0)
        kw["existing_versions"][(PATH3, SHA)] = None
        with self.assertRaisesRegex(RuntimeError, "audit incomplete"):
            m.validate_preparation(plan, **kw)

    def test_record_count_drift_blocks(self):
        plan, kw = make_case(1)
        kw["expected_records"] = 2
        with self.assertRaisesRegex(RuntimeError, "record count drift"):
            m.validate_preparation(plan, **kw)

    def test_bytes_drift_blocks(self):
        plan, kw = make_case(1)
        kw["expected_bytes"] = 22
        with self.assertRaisesRegex(RuntimeError, "byte count drift"):
            m.validate_preparation(plan, **kw)

    def test_incomplete_action_coverage_blocks(self):
        plan, kw = make_case(1)
        plan.actions = (plan.actions[0],)
        with self.assertRaisesRegex(RuntimeError, "target plan actions"):
            m.validate_preparation(plan, **kw)

    def test_readonly_metadata_query_uses_expected_database(self):
        # No actual DB: test the query sequence and guarded archive version lookup.
        class Cursor:
            def __init__(self):
                self.queries = []
                self.one = iter([("epl_raw_storage_v2",), None])
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def execute(self, query, params=None): self.queries.append((query, params))
            def fetchone(self): return next(self.one)
            def fetchall(self): return []
        class Conn:
            def __init__(self): self.c = Cursor()
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def cursor(self): return self.c
        connection = Conn()
        connection_args = {}
        def fake_connect(**kwargs):
            connection_args.update(kwargs)
            return connection
        fake_psycopg = SimpleNamespace(connect=fake_connect)
        fake_dotenv = SimpleNamespace(dotenv_values=lambda *a, **kw: dict(
            DB_HOST="localhost", DB_PORT="5432", DB_NAME="epl_raw_storage_v2",
            DB_USER="test", DB_PASSWORD="test"))
        with patch.dict(sys.modules, {"psycopg": fake_psycopg, "dotenv": fake_dotenv}):
            res = m.read_target_db_state("Toibinguz/epl-premier-league-dataset",
                                         "1"*40, [Archive(PATH1)])
        self.assertEqual(res, {(PATH1, SHA): None})
        self.assertIn("default_transaction_read_only=on", connection_args["options"])
        self.assertEqual(connection_args["dbname"], "epl_raw_storage_v2")
        self.assertIn("FROM raw.snapshot_runs", "\n".join(q for q, _ in connection.c.queries))

    def test_sql_state_gate_blocks_existing_target(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def execute(self, *a): pass
            def fetchone(self):
                self.calls += 1
                return [("epl_raw_storage_v2",), (365, "completed")][self.calls - 1]
            def __init__(self): self.calls = 0
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def cursor(self): return Cursor()
        mods = {"psycopg": SimpleNamespace(connect=lambda **kw: Connection()),
                "dotenv": SimpleNamespace(dotenv_values=lambda *a, **kw: dict(
                     DB_HOST="localhost", DB_PORT="5432", DB_NAME="epl_raw_storage_v2",
                     DB_USER="test", DB_PASSWORD="test"))}
        with patch.dict(sys.modules, mods), self.assertRaisesRegex(RuntimeError, "already exists"):
            m.read_target_db_state("Toibinguz/epl-premier-league-dataset", "1"*40, [Archive(PATH1)])

    def test_sql_state_gate_blocks_another_in_progress(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def execute(self, *a): pass
            def __init__(self): self.calls = 0
            def fetchone(self):
                self.calls += 1
                return [("epl_raw_storage_v2",), None][self.calls - 1]
            def fetchall(self): return [(366, "f"*40, "in_progress")]
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def cursor(self): return Cursor()
        mods = {"psycopg": SimpleNamespace(connect=lambda **kw: Connection()),
                "dotenv": SimpleNamespace(dotenv_values=lambda *a, **kw: dict(
                     DB_HOST="localhost", DB_PORT="5432", DB_NAME="epl_raw_storage_v2",
                     DB_USER="test", DB_PASSWORD="test"))}
        with patch.dict(sys.modules, mods), self.assertRaisesRegex(RuntimeError, "in_progress"):
            m.read_target_db_state("Toibinguz/epl-premier-league-dataset", "1"*40, [Archive(PATH1)])

    def test_legacy_m45_success_marker_preserved(self):
        from pathlib import Path
        source = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn("M4.5.4c.3i SNAPSHOT PREPARATION PLAN: PASS (NOT APPROVAL TO COMMIT)", source)
        self.assertIn('print("REUSE file IDs:", result.reused)', source)
        self.assertIn('print("Candidate parser records:", result.candidate_records)', source)

    def test_cli_rejects_zero_candidates_without_network(self):
        argv = ["incremental_snapshot_prepare.py", "--baseline-snapshot", "365",
                "--target-revision", "b" * 40, "--expected-fingerprint", SHA,
                "--expected-files", "0", "--expected-bytes", "0",
                "--expected-records", "0", "--expected-reuse", "286", "--plan"]
        with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                m.main()
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
