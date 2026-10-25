"""R1.5c: Additional offline contract regressions.

No real PostgreSQL connection, HF request, archive or checkpoint writes.
"""

import contextlib
import io
import unittest

from types import SimpleNamespace
from unittest.mock import patch

from scripts.tools import incremental_reuse_linker as linker
from scripts.tools import register_incremental_snapshot as registration
from scripts.tools import populate_incremental_reuse as reuse

from scripts.tools.incremental_sync import SyncAction, SyncPlan
from scripts.tools.check_raw_updates import FileIdentity

from tests import test_register_incremental_snapshot_m46 as reg_fixture
from tests import test_populate_incremental_reuse_m46 as reuse_fixture


class FinalContractRegression(unittest.TestCase):

    def test_mixed_plan_selects_only_reuse_paths(self):
        blob = "b" * 40

        actions = (
            SyncAction(
                "REUSE_FILE_ID", "raw_data/a.json",
                "UNCHANGED", 101, None, None,
                50, None, blob
            ),
            SyncAction(
                "REUSE_FILE_ID", "raw_data/b.json",
                "UNCHANGED", 102, None, None,
                50, None, blob
            ),
            SyncAction(
                "UPSERT_VERSION_AFTER_REHASH",
                "raw_data/c.json",
                "NEW", None, "fotmob", "json",
                50, None, blob
            ),
        )

        plan = SyncPlan(
            actions=actions,
            baseline_records=0,
            reused_records=0,
            superseded_records=0,
            target_raw_count=3,
            candidate_bytes=50,
            fingerprint="d" * 64,
        )

        target = {
            item.path: FileIdentity(50, blob, None)
            for item in actions
        }

        links = linker.build_reuse_links(plan, target)

        self.assertEqual(len(links), 2)
        self.assertEqual(
            [item.file_id for item in links],
            [101, 102],
        )
        self.assertEqual(
            {item.relative_path for item in links},
            {"raw_data/a.json", "raw_data/b.json"},
        )

    def test_baseline_file_count_drift_blocks_before_insert(self):
        f = reg_fixture

        cursor = f.FakeCursor(
            base=(
                365, f.REPO, f.BASE_REV,
                "completed", 285, f.SHA, f.PATH
            )
        )

        with self.assertRaisesRegex(
            RuntimeError, "Baseline snapshot"
        ):
            registration.register_in_transaction(
                cursor,
                f.valid_req(),
            )

        self.assertFalse(cursor.inserted)
        self.assertFalse(
            any(
                "INSERT INTO raw.snapshot_runs" in sql
                for sql, _ in cursor.executed
            )
        )

    def test_preflight_missing_required_line_blocks(self):
        f = reg_fixture
        req = f.valid_req()

        output = f.PreflightTests()._stdout(req)

        missing_line = (
            f"Candidate parser records: "
            f"{req.candidate_records}"
        )

        self.assertIn(missing_line, output)

        incomplete = "\n".join(
            line
            for line in output.splitlines()
            if line != missing_line
        )

        fake_result = SimpleNamespace(
            stdout=incomplete,
            stderr="",
            returncode=0,
        )

        with patch.object(
            registration.subprocess,
            "run",
            return_value=fake_result,
        ) as runner:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Preparation gate failed",
                ):
                    registration.run_preflight(req)

        runner.assert_called_once()

    def test_reuse_plan_reports_zero_insertions(self):
        req = reuse_fixture.request()
        state = reuse_fixture.state()

        plan = SimpleNamespace(
            fingerprint=req.registration.fingerprint,
            reused_records=req.expected_reused_records,
        )

        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            reuse.print_plan(
                req,
                state,
                plan,
                pending=272,
                done=0,
            )

        text = output.getvalue()

        self.assertIn(
            "Raw versions / raw records inserted: 0 / 0",
            text,
        )
        self.assertIn("REUSE pending: 272", text)
        self.assertNotIn("commit", text.lower())


if __name__ == "__main__":
    unittest.main()
