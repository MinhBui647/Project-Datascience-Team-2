"""Offline tests for M4.6.2d.3 incremental sync orchestration.

Run from project root:
    python -m unittest scripts.tools.test_incremental_sync_m46 -v
Never connects to PostgreSQL or Hugging Face (all external calls are mocked).
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

try:
    from scripts.tools import incremental_sync as subject
    from scripts.tools import check_raw_updates as detector
except ImportError:
    import incremental_sync as subject
    import check_raw_updates as detector

REPO = "Toibinguz/epl-premier-league-dataset"
BASE_REV = "1" * 40
OLD_REV = "2" * 40
NEW_REV = "3" * 40
SHA = "a" * 64
BASE_FILES = {"raw_data/demo.json": detector.FileIdentity(10, "f" * 40, None)}
BASE_SELECTION = detector.BaselineSelection(
    365, BASE_REV, 304, 1, SHA, f"raw-archive-v2/manifests/aa/{SHA}.json"
)


class RawPreflightTests(unittest.TestCase):
    def test_no_raw_change_returns_skip(self):
        self.assertEqual(subject.decide_preflight_action(BASE_FILES, BASE_FILES),
                         "SKIP_NO_RAW_CHANGES")

    def test_new_file_returns_plan(self):
        target = {**BASE_FILES, "raw_data/added.json": detector.FileIdentity(4, "d" * 40, None)}
        self.assertEqual(subject.decide_preflight_action(BASE_FILES, target), "PLAN_RAW_CHANGES")

    def test_removed_only_is_not_silently_skipped(self):
        self.assertEqual(subject.decide_preflight_action(BASE_FILES, {}), "PLAN_RAW_CHANGES")

    def test_ambiguous_identity_blocks(self):
        target = {"raw_data/demo.json": detector.FileIdentity(10, None, None)}
        self.assertEqual(subject.decide_preflight_action(BASE_FILES, target),
                         "BLOCKED_METADATA_REVIEW")


class OrchestratorTests(unittest.TestCase):
    def run_cli(self, target_rev=BASE_REV, target_state=None, *,
                auto=True, baseline_files=None, remote_files=None,
                baseline_revision=BASE_REV, state_decision=None):
        baseline_files = BASE_FILES if baseline_files is None else baseline_files
        argv = ["incremental_sync.py", "--target-revision", target_rev, "--plan"]
        if not auto:
            argv += ["--baseline-snapshot", "4"]
        with patch.object(sys, "argv", argv), \
             patch.object(subject, "select_latest_completed_baseline", return_value=BASE_SELECTION) as select, \
             patch.object(subject, "load_baseline", return_value=(baseline_revision, baseline_files)) as baseline, \
             patch.object(subject, "read_target_snapshot_state", return_value=target_state) as state, \
             patch.object(subject, "load_target", return_value=(target_rev, remote_files or {}, 18)) as remote, \
             patch.object(subject, "read_db_state") as db, \
             patch.object(subject, "read_existing_source_codes") as sources, \
             patch.object(subject, "assemble_plan") as assemble, \
             redirect_stdout(io.StringIO()) as output:
            if state_decision is not None:
                with patch.object(subject, "classify_target_state", return_value=state_decision):
                    code = subject.main()
            else:
                code = subject.main()
        return code, output.getvalue(), dict(select=select, baseline=baseline,
             state=state, remote=remote, db=db, sources=sources, assemble=assemble)

    def test_current_revision_skips_without_hf(self):
        code, out, calls = self.run_cli(BASE_REV, detector.TargetSnapshotState(365, "completed"))
        self.assertEqual(code, 0)
        self.assertIn("SKIP_ALREADY_CURRENT", out)
        calls["remote"].assert_not_called()
        calls["db"].assert_not_called()

    def test_historical_revision_skips_without_hf(self):
        code, out, calls = self.run_cli(OLD_REV, detector.TargetSnapshotState(4, "completed"))
        self.assertEqual(code, 0)
        self.assertIn("SKIP_ALREADY_PROCESSED", out)
        calls["remote"].assert_not_called()
        calls["assemble"].assert_not_called()

    def test_in_progress_blocks_without_hf(self):
        code, out, calls = self.run_cli(NEW_REV, detector.TargetSnapshotState(500, "in_progress"))
        self.assertEqual(code, 2)
        self.assertIn("RECOVERY_REQUIRED", out)
        calls["remote"].assert_not_called()

    def test_failed_blocks_without_hf(self):
        code, out, calls = self.run_cli(NEW_REV, detector.TargetSnapshotState(500, "failed"))
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED_EXISTING_TARGET_STATE", out)
        calls["remote"].assert_not_called()

    def test_new_revision_no_raw_changes_skips(self):
        code, out, calls = self.run_cli(NEW_REV, None, remote_files=BASE_FILES)
        self.assertEqual(code, 0)
        self.assertIn("SKIP_NO_RAW_CHANGES", out)
        calls["remote"].assert_called_once_with(REPO, NEW_REV, already_resolved=True)
        calls["db"].assert_not_called()
        calls["sources"].assert_not_called()

    def test_new_revision_with_review_blocks(self):
        ambig = {"raw_data/demo.json": detector.FileIdentity(10, None, None)}
        code, out, calls = self.run_cli(NEW_REV, None, remote_files=ambig)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED_METADATA_REVIEW", out)
        calls["db"].assert_not_called()

    def test_new_revision_produces_read_only_action_plan(self):
        new_path = "raw_data/demo/added.json"
        new_files = {**BASE_FILES, new_path: detector.FileIdentity(5, "d" * 40, None)}
        existing = {
            "raw_data/demo.json": subject.ExistingFile(
                file_id=19, sha256="e" * 64, size=10, record_count=8
            )
        }
        argv = ["incremental_sync.py", "--target-revision", NEW_REV, "--plan"]
        with patch.object(sys, "argv", argv), \
             patch.object(subject, "select_latest_completed_baseline", return_value=BASE_SELECTION), \
             patch.object(subject, "load_baseline", return_value=(BASE_REV, BASE_FILES)), \
             patch.object(subject, "read_target_snapshot_state", return_value=None), \
             patch.object(subject, "load_target", return_value=(NEW_REV, new_files, 18)), \
             patch.object(subject, "read_db_state", return_value=(BASE_REV, existing)) as db, \
             patch.object(subject, "read_existing_source_codes", return_value={"demo"}), \
             redirect_stdout(io.StringIO()) as output:
            code = subject.main()
        self.assertEqual(code, 0)
        self.assertIn("Raw change decision: PLAN_RAW_CHANGES", output.getvalue())
        self.assertIn("NEW version candidates: 1", output.getvalue())
        self.assertIn("REUSE existing file_id: 1", output.getvalue())
        self.assertIn("Database writes: NONE", output.getvalue())
        db.assert_called_once_with(365, REPO, NEW_REV)

    def test_explicit_baseline_skips_auto_selection(self):
        code, out, calls = self.run_cli(OLD_REV, detector.TargetSnapshotState(99, "completed"), auto=False)
        self.assertEqual(code, 0)
        self.assertIn("EXPLICIT_OVERRIDE", out)
        calls["select"].assert_not_called()
        calls["baseline"].assert_called_once_with(4, REPO)

    def test_auto_selection_baseline_drift_blocks(self):
        with self.assertRaisesRegex(RuntimeError, "Baseline drift"):
            self.run_cli(NEW_REV, None, baseline_revision="f" * 40)

    def test_invalid_sha_rejected_before_db(self):
        with patch.object(sys, "argv", ["incremental_sync.py", "--target-revision", "main", "--plan"]), \
             patch.object(subject, "select_latest_completed_baseline") as select:
            with self.assertRaises(SystemExit):
                subject.main()
        select.assert_not_called()

    def test_missing_plan_rejected(self):
        with patch.object(sys, "argv", ["incremental_sync.py", "--target-revision", NEW_REV]), \
             patch.object(subject, "select_latest_completed_baseline") as select:
            with self.assertRaises(SystemExit):
                subject.main()
        select.assert_not_called()


if __name__ == "__main__":
    unittest.main()
