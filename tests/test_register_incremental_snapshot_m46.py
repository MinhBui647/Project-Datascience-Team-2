"""Offline regression for M4.6.3b.2 Registration; no network or database writes.

Run in VSCode project root:
  python -m unittest scripts.tools.test_register_incremental_snapshot_m46 -v
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from scripts.tools import register_incremental_snapshot as m

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "f" * 40
BASE_REV = "e" * 40
SHA = "a" * 64
PATH = "raw-archive-v2/manifests/aa/" + SHA + ".json"


def valid_req(**changes):
    original = m.Registration(REPO, 365, REV, "b" * 64,
                              286, 304, 286, 272, 14, 5_458_820, 393)
    return replace(original, **changes)


class FakeCursor:
    """Return results according to read queries; INSERT is never executed in failure cases."""
    def __init__(self, **override):
        self.state = dict(db=("epl_raw_storage_v2",), lock=(True,),
                          base=(365, REPO, BASE_REV, "completed", 286, SHA, PATH),
                          latest=(365,), obs=(286, 286, 286, 286),
                          target=None, active=[], inserted=(366,))
        self.state.update(override)
        self.last = None
        self.executed = []

    def execute(self, sql, args=None):
        self.last = " ".join(sql.split())
        self.executed.append((self.last, args))

    def _type(self):
        q = self.last
        if "SELECT current_database()" in q: return "db"
        if "pg_try_advisory_xact_lock" in q: return "lock"
        if "WHERE snapshot_id=%s FOR SHARE" in q: return "base"
        if "ORDER BY observed_at DESC,snapshot_id DESC" in q: return "latest"
        if "COUNT(DISTINCT o.relative_path)" in q: return "obs"
        if "WHERE repo_id=%s AND hf_revision=%s FOR UPDATE" in q: return "target"
        if "status='in_progress' FOR UPDATE" in q: return "active"
        if "INSERT INTO raw.snapshot_runs" in q: return "inserted"
        raise AssertionError(f"Unexpected SQL: {q}")

    def fetchone(self):
        return self.state[self._type()]

    def fetchall(self):
        return self.state[self._type()]

    @property
    def inserted(self):
        return any("INSERT INTO raw.snapshot_runs" in sql for sql, _ in self.executed)


class ValidationTests(unittest.TestCase):
    def test_original_case_272_reuse_14_candidates(self):
        valid_req().validate()

    def test_zero_reuse_valid(self):
        valid_req(raw_files=5, manifest_files=6, baseline_files=286, reuse_files=0,
                  candidate_files=5, candidate_bytes=500, candidate_records=0).validate()

    def test_zero_candidate_records_valid(self):
        valid_req(candidate_records=0).validate()

    def test_zero_candidates_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "candidates > 0"):
            valid_req(candidate_files=0, candidate_bytes=0, raw_files=272).validate()

    def test_negative_counts_blocked(self):
        for field in ("reuse_files", "candidate_files", "candidate_bytes", "candidate_records"):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                valid_req(**{field: -1}).validate()

    def test_bool_counts_blocked(self):
        for field in ("baseline_files", "manifest_files", "raw_files", "reuse_files", "candidate_files"):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                valid_req(**{field: True}).validate()

    def test_bad_repository_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "repository"):
            valid_req(repo_id="a/b").validate()

    def test_bad_revision_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "SHA40"):
            valid_req(target_revision="main").validate()

    def test_bad_fingerprint_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "fingerprint"):
            valid_req(fingerprint="invalid").validate()

    def test_invalid_baseline_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "baseline"):
            valid_req(baseline_snapshot=0).validate()

    def test_reuse_exceeds_baseline_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "exceeds baseline"):
            valid_req(baseline_files=100).validate()

    def test_manifest_coverage_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "coverage"):
            valid_req(raw_files=280).validate()

    def test_full_manifest_smaller_than_raw_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "coverage"):
            valid_req(manifest_files=200).validate()


class PreflightTests(unittest.TestCase):
    def test_argv_preserves_pinned_context(self):
        command = m.preflight_command(valid_req())
        self.assertEqual(command[0], sys.executable)
        self.assertIn("--plan", command)
        self.assertEqual(command[command.index("--repo-id")+1], REPO)
        self.assertEqual(command[command.index("--target-revision")+1], REV)
        self.assertEqual(command[command.index("--expected-reuse")+1], "272")

    def _stdout(self, req):
        return "\n".join((
            "M4.5.4c.3i SNAPSHOT PREPARATION PLAN: PASS (NOT APPROVAL TO COMMIT)",
            f"Full HF manifest file count: {req.manifest_files}",
            f"Target Raw files: {req.raw_files}",
            f"REUSE file IDs: {req.reuse_files}",
            f"Candidate archive objects VERIFIED: {req.candidate_files}",
            f"Archive verified bytes: {req.candidate_bytes}",
            f"Candidate parser records: {req.candidate_records}",
            "Target snapshot: ABSENT; ID will be assigned by PostgreSQL",
        )) + "\n"

    def test_matching_preflight_passes(self):
        req = valid_req()
        with patch.object(m.subprocess, "run", return_value=SimpleNamespace(
                stdout=self._stdout(req), stderr="", returncode=0)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                m.run_preflight(req)
        self.assertEqual(run.call_args.kwargs["cwd"], m.ROOT)

    def test_preflight_mismatched_count_blocks(self):
        req = valid_req()
        output = self._stdout(req).replace("Target Raw files: 286", "Target Raw files: 285")
        with patch.object(m.subprocess, "run", return_value=SimpleNamespace(
                stdout=output, stderr="", returncode=0)):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "Preparation gate failed"):
                m.run_preflight(req)

    def test_nonzero_subprocess_exit_blocks_even_if_stdout_looks_good(self):
        req = valid_req()
        with patch.object(m.subprocess, "run", return_value=SimpleNamespace(
                stdout=self._stdout(req), stderr="", returncode=2)):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                m.run_preflight(req)

    def test_preflight_accepts_zero_reuse_and_zero_record_counts(self):
        req = valid_req(reuse_files=0, candidate_files=286,
                        candidate_bytes=5000, candidate_records=0)
        with patch.object(m.subprocess, "run", return_value=SimpleNamespace(
                stdout=self._stdout(req), stderr="", returncode=0)):
            with contextlib.redirect_stdout(io.StringIO()):
                m.run_preflight(req)


class TransactionGuardTests(unittest.TestCase):
    def test_valid_transaction_inserts_one_snapshot_only(self):
        cur = FakeCursor()
        self.assertEqual(m.register_in_transaction(cur, valid_req()), 366)
        statements = [q for q, _ in cur.executed]
        self.assertEqual(sum("INSERT INTO" in q for q in statements), 1)
        self.assertTrue(any("pg_try_advisory_xact_lock" in q for q in statements))
        self.assertTrue(any("ORDER BY observed_at DESC,snapshot_id DESC" in q for q in statements))
        self.assertTrue(any("FOR UPDATE" in q for q in statements))
        self.assertEqual(cur.executed[-1][1], (REPO, REV, 304, 286))

    def blocked(self, pattern, **state):
        cur = FakeCursor(**state)
        with self.assertRaisesRegex(RuntimeError, pattern):
            m.register_in_transaction(cur, valid_req())
        self.assertFalse(cur.inserted)

    def test_wrong_db_blocked(self): self.blocked("Wrong database", db=("other",))
    def test_lock_busy_blocked(self): self.blocked("lock", lock=(False,))
    def test_missing_baseline_blocked(self): self.blocked("Baseline snapshot", base=None)
    def test_baseline_not_completed_blocked(self):
        self.blocked("Baseline snapshot", base=(365, REPO, BASE_REV, "in_progress", 286, SHA, PATH))
    def test_missing_baseline_manifest_blocked(self):
        self.blocked("Baseline snapshot", base=(365, REPO, BASE_REV, "completed", 286, None, None))
    def test_wrong_manifest_path_blocked(self):
        self.blocked("Baseline snapshot", base=(365, REPO, BASE_REV, "completed", 286, SHA, "wrong"))
    def test_target_same_as_baseline_blocked(self):
        self.blocked("equals baseline", base=(365, REPO, REV, "completed", 286, SHA, PATH))
    def test_stale_baseline_blocked(self): self.blocked("no longer the latest", latest=(366,))
    def test_invalid_observation_states_blocked(self):
        self.blocked("observations", obs=(286, 286, 286, 285))
    def test_duplicate_file_ids_blocked(self):
        self.blocked("observations", obs=(286, 286, 285, 286))
    def test_target_already_exists_blocked(self):
        self.blocked("already registered", target=(366, "completed"))
    def test_another_inprogress_blocked(self):
        self.blocked("in_progress", active=[(366, REV)])
    def test_insert_return_id_invalid_blocked(self):
        cur = FakeCursor(inserted=(0,))
        with self.assertRaisesRegex(RuntimeError, "Failed to obtain"):
            m.register_in_transaction(cur, valid_req())
        self.assertTrue(cur.inserted)  # caller transaction MUST roll back on exception


class CliTests(unittest.TestCase):
    def _argv(self, mode, *extra):
        return ["register_incremental_snapshot.py", "--baseline-snapshot", "365",
                "--target-revision", REV, "--expected-fingerprint", "b" * 64,
                "--expected-baseline-files", "286", "--expected-full-files", "304",
                "--expected-raw-files", "286", "--expected-reuse", "272",
                "--expected-files", "14", "--expected-bytes", "5458820",
                "--expected-records", "393", mode, *extra]

    def test_plan_never_starts_transaction(self):
        with patch.object(sys, "argv", self._argv("--plan")), \
             patch.object(m, "run_preflight") as gate, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(), 0)
        gate.assert_called_once()

    def test_register_requires_explicit_confirmation(self):
        with patch.object(sys, "argv", self._argv("--register")), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            m.main()
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
