import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.orchestration.commit_watcher import watch_once


REPO = "Toibinguz/epl-premier-league-dataset"
CURRENT = "708a15b7589e8c43347827b13e6e9ac8def871b0"
NEW_REVISION = "a" * 40


def summary(**changes):
    values = {
        "status": "NO_STAGE_REPORTED",
        "m4_decision": "SKIP_ALREADY_CURRENT",
        "recovery_state": "COMPLETED",
        "next_review_stage": None,
        "blockers": [],
        "m4_write_authorized": False,
        "m5_write_executed": False,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def run_watcher(sha, result):
    with patch(
        "scripts.orchestration.commit_watcher.plan_once",
        return_value=result,
    ):
        return watch_once(
            repo_id=REPO,
            revision_ref="main",
            resolver=lambda repo, ref: sha,
            planner=lambda **kwargs: None,
            baseline_snapshot=367,
        )


class WatcherIntegrationContractTests(unittest.TestCase):

    def test_completed_current_revision_is_skipped(self):
        result = run_watcher(CURRENT, summary())

        self.assertEqual(result.state, "SKIPPED_BY_M4")
        self.assertEqual(result.pinned_revision, CURRENT)
        self.assertTrue(result.resolution_performed)

    def test_new_revision_requiring_review(self):
        result = run_watcher(
            NEW_REVISION,
            summary(
                status="MANUAL_REVIEW_REQUIRED",
                m4_decision="REVIEW_REQUIRED",
                recovery_state="NOT_STARTED",
                next_review_stage="PLAN",
            ),
        )

        self.assertEqual(result.state, "REVIEW_REQUIRED")
        self.assertEqual(result.pinned_revision, NEW_REVISION)
        self.assertNotEqual(result.pinned_revision, CURRENT)

    def test_new_revision_with_unknown_decision(self):
        result = run_watcher(
            NEW_REVISION,
            summary(
                m4_decision="UNKNOWN",
                recovery_state="NOT_STARTED",
            ),
        )

        self.assertEqual(result.state, "UNRESOLVED")

    def test_incomplete_current_revision_not_skipped(self):
        result = run_watcher(
            CURRENT,
            summary(recovery_state="IN_PROGRESS"),
        )

        self.assertEqual(result.state, "UNRESOLVED")

    def test_blocked_revision_remains_blocked(self):
        result = run_watcher(
            NEW_REVISION,
            summary(
                status="BLOCKED",
                recovery_state="FAILED",
                blockers=["Snapshot validation failed"],
            ),
        )

        self.assertEqual(result.state, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
