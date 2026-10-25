"""M5.5.4e.2: isolate repeat-invocation gap after an ambiguous M4 writer result.

Uses the previously approved M5.5.4d test fixtures, NOT any production DB/HF
connections, subprocesses or checkpoint writers. The former expectedFailure
is now a mandatory passing regression protecting against duplicate launch.
"""

import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scripts.orchestration.single_stage_write_executor import (
    WriteGateError,
    execute_one_stage,
    make_write_plan,
)
from test_single_stage_write_executor_m554d import (
    CACHE, CTX, REPO, REV, ROOT,
    FakeStageReview, mocked_m4_preview_validation,
    review, reviewers, verifier_states,
)


class AmbiguousWriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        for target, replacement in (
            ("scripts.orchestration.guarded_write_coordinator.StageReview", FakeStageReview),
            ("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands", mocked_m4_preview_validation),
            ("scripts.orchestration.single_stage_write_executor.validate_read_only_commands", mocked_m4_preview_validation),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.stage_review = review()
        self.sandbox = tempfile.TemporaryDirectory(prefix="m554e3-recovery-test-")
        self.addCleanup(self.sandbox.cleanup)
        self.kwargs = dict(
            repo_id=REPO,
            target_revision=REV,
            stage="CANDIDATE_INGESTION",
            baseline_snapshot=366,
            context_sha256=CTX,
            candidate_cache=CACHE,
            reviewer=reviewers(self.stage_review),
            root=Path(self.sandbox.name),
        )
        intent_plan = make_write_plan(**self.kwargs)
        self.assertEqual(intent_plan.state, "READY_FOR_EXPLICIT_APPROVAL")
        self.kwargs.update(
            approved_stage="CANDIDATE_INGESTION",
            expected_intent_sha256=intent_plan.intent_sha256,
        )

    def test_verified_pre_evidence_refuses_any_write(self):
        launcher = Mock()
        with self.assertRaises(WriteGateError) as result:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("VERIFIED"),
                launcher=launcher,
            )
        self.assertFalse(result.exception.attempted)
        launcher.assert_not_called()

    def test_blocked_pre_evidence_refuses_any_write(self):
        launcher = Mock()
        with self.assertRaises(WriteGateError) as result:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("BLOCKED"),
                launcher=launcher,
            )
        self.assertFalse(result.exception.attempted)
        launcher.assert_not_called()

    def test_partial_first_write_then_verified_prevents_second_launch(self):
        launcher = Mock(return_value=Mock(returncode=0))
        with self.assertRaises(WriteGateError) as first:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("INCOMPLETE", "INCOMPLETE"),
                launcher=launcher,
            )
        self.assertTrue(first.exception.attempted)
        self.assertEqual(launcher.call_count, 1)

        with self.assertRaises(WriteGateError) as second:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("VERIFIED"),
                launcher=launcher,
            )
        self.assertFalse(second.exception.attempted)
        self.assertEqual(launcher.call_count, 1)

    def test_timeout_then_blocked_evidence_prevents_second_launch(self):
        launcher = Mock(side_effect=subprocess.TimeoutExpired("writer", 1))
        with self.assertRaises(WriteGateError) as first:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("INCOMPLETE"),
                launcher=launcher,
            )
        self.assertTrue(first.exception.attempted)
        self.assertEqual(launcher.call_count, 1)

        with self.assertRaises(WriteGateError) as second:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("BLOCKED"),
                launcher=launcher,
            )
        self.assertFalse(second.exception.attempted)
        self.assertEqual(launcher.call_count, 1)

    def test_ambiguous_incomplete_must_not_launch_second_time_without_journal(self):
        """A reserved durable claim blocks a second approved invocation."""
        launcher = Mock(return_value=Mock(returncode=0))
        with self.assertRaises(WriteGateError) as first:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("INCOMPLETE", "INCOMPLETE"),
                launcher=launcher,
            )
        self.assertTrue(first.exception.attempted)
        self.assertEqual(launcher.call_count, 1)
        with self.assertRaises(WriteGateError) as second:
            execute_one_stage(
                **self.kwargs,
                verifier=verifier_states("INCOMPLETE", "INCOMPLETE"),
                launcher=launcher,
            )
        self.assertFalse(second.exception.attempted)
        self.assertIn("Durable write-attempt guard", str(second.exception))
        self.assertEqual(launcher.call_count, 1)


if __name__ == "__main__":
    unittest.main()
