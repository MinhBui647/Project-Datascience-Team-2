
import unittest

from scripts.orchestration.controller import plan_once
from scripts.orchestration.commit_watcher import (
    watch_once,
    report_dict,
)

REPO = "sample/epl-dataset"
SHA = "a" * 40

ERROR = (
    "Candidate validation blocked: "
    "2026_2027_new/football_data_raw_2627.csv: "
    "Incremental phase 1 accepts JSON files only"
)


def failing_planner(**kwargs):
    raise RuntimeError(ERROR)


class CandidateValidationBlockTests(unittest.TestCase):

    def test_known_csv_limit_becomes_blocked(self):
        result = plan_once(
            repo_id=REPO,
            target_revision=SHA,
            planner=failing_planner,
        )

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.m4_decision, "NOT_PRODUCED")
        self.assertEqual(result.recovery_state, "NOT_EVALUATED")
        self.assertEqual(result.blockers, (ERROR,))
        self.assertIsNone(result.next_review_stage)
        self.assertFalse(result.m4_write_authorized)
        self.assertFalse(result.m5_write_executed)

    def test_watcher_preserves_blocker(self):
        calls = []

        def planner(**kwargs):
            calls.append(kwargs)
            raise RuntimeError(ERROR)

        def forbidden_resolver(*args):
            self.fail("Pinned SHA must bypass resolver")

        outcome = watch_once(
            repo_id=REPO,
            revision_ref=SHA,
            resolver=forbidden_resolver,
            planner=planner,
        )

        self.assertEqual(outcome.state, "BLOCKED")
        self.assertEqual(len(calls), 1)
        self.assertIn(
            "football_data_raw_2627.csv",
            report_dict(outcome)["controller"]["blockers"][0],
        )

    def test_unrelated_runtime_error_is_preserved(self):
        def other_failure(**kwargs):
            raise RuntimeError("Unexpected infrastructure failure")

        with self.assertRaisesRegex(
            RuntimeError, "Unexpected infrastructure failure"
        ):
            plan_once(
                repo_id=REPO,
                target_revision=SHA,
                planner=other_failure,
            )

    def test_other_validation_error_remains_unhandled(self):
        def invalid_source(**kwargs):
            raise RuntimeError(
                "Candidate validation blocked: "
                "Source 'unknown' absent from DB registry"
            )

        with self.assertRaisesRegex(
            RuntimeError, "Source 'unknown'"
        ):
            plan_once(
                repo_id=REPO,
                target_revision=SHA,
                planner=invalid_source,
            )


if __name__ == "__main__":
    unittest.main()
