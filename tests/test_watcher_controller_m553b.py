"""M5.5.3b: Watcher -> real Controller integration.

M4 planner results and HF resolver are injected.
No actual Hugging Face or PostgreSQL connections.
"""

import unittest
from types import SimpleNamespace

from scripts.orchestration.commit_watcher import watch_once


REPO = "Toibinguz/epl-premier-league-dataset"
CURRENT = "708a15b7589e8c43347827b13e6e9ac8def871b0"
NEW = "a" * 40


def m4_result(revision, **changes):
    values = dict(
        repo_id=REPO,
        target_revision=revision,
        decision="PLAN_NEW_REVISION",
        recovery_state="NO_REGISTERED_SNAPSHOT",
        next_review_stage="DISCOVERY",
        blockers=(),
        stages=("DISCOVERY",),
        read_only_previews=(),
        planned_context_sha256=None,
        write_authorized=False,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def run_integration(revision, result):
    calls = {"resolver": 0, "planner": 0}

    def resolver(repo_id, revision_ref):
        calls["resolver"] += 1
        assert repo_id == REPO
        assert revision_ref == "main"
        return revision

    def planner(*, repo_id, target_revision, baseline_snapshot):
        calls["planner"] += 1
        assert repo_id == REPO
        assert target_revision == revision
        assert baseline_snapshot == 367

        if isinstance(result, Exception):
            raise result

        return result

    outcome = watch_once(
        repo_id=REPO,
        revision_ref="main",
        resolver=resolver,
        planner=planner,
        baseline_snapshot=367,
    )

    assert calls == {"resolver": 1, "planner": 1}
    assert outcome.pinned_revision == revision
    assert outcome.controller.target_revision == revision
    assert outcome.resolution_performed is True
    assert outcome.controller.m5_write_executed is False

    return outcome


class WatcherControllerIntegrationTests(unittest.TestCase):

    def test_current_completed_revision(self):
        outcome = run_integration(
            CURRENT,
            m4_result(
                CURRENT,
                decision="SKIP_ALREADY_CURRENT",
                recovery_state="COMPLETED",
                next_review_stage=None,
                stages=(),
            ),
        )
        self.assertEqual(outcome.state, "SKIPPED_BY_M4")
        self.assertEqual(outcome.controller.status, "NO_STAGE_REPORTED")
        self.assertFalse(outcome.controller.m4_write_authorized)

    def test_new_revision_requires_review(self):
        outcome = run_integration(
            NEW,
            m4_result(NEW),
        )
        self.assertEqual(outcome.state, "REVIEW_REQUIRED")
        self.assertEqual(
            outcome.controller.status,
            "MANUAL_REVIEW_REQUIRED",
        )
        self.assertEqual(outcome.controller.next_review_stage, "DISCOVERY")
        self.assertEqual(outcome.controller.stage_count, 1)
        self.assertFalse(outcome.controller.m4_write_authorized)

    def test_new_revision_with_blocker(self):
        outcome = run_integration(
            NEW,
            m4_result(
                NEW,
                decision="BLOCKED",
                recovery_state="BLOCKED_REVIEW",
                blockers=("Invalid source evidence",),
                next_review_stage=None,
                stages=(),
            ),
        )
        self.assertEqual(outcome.state, "BLOCKED")
        self.assertEqual(outcome.controller.status, "BLOCKED")

    def test_new_revision_without_stage_is_unresolved(self):
        outcome = run_integration(
            NEW,
            m4_result(
                NEW,
                decision="UNKNOWN",
                next_review_stage=None,
                stages=(),
            ),
        )
        self.assertEqual(outcome.state, "UNRESOLVED")

    def test_m4_target_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "different target revision"):
            run_integration(
                NEW,
                m4_result(CURRENT),
            )

    def test_missing_m4_contract_field_is_rejected(self):
        invalid = m4_result(NEW)
        del invalid.decision

        with self.assertRaisesRegex(ValueError, "missing required field"):
            run_integration(NEW, invalid)

    def test_unexpected_planner_failure_is_not_hidden(self):
        with self.assertRaisesRegex(RuntimeError, "Synthetic planner failure"):
            run_integration(
                NEW,
                RuntimeError("Synthetic planner failure"),
            )

    def test_legacy_json_only_boundary_fails_closed(self):
        error = RuntimeError(
            "Candidate validation blocked: "
            "Incremental phase 1 accepts JSON files only"
        )

        outcome = run_integration(NEW, error)

        self.assertEqual(outcome.state, "BLOCKED")
        self.assertEqual(outcome.controller.status, "BLOCKED")
        self.assertEqual(outcome.controller.stage_count, 0)
        self.assertFalse(outcome.controller.m4_write_authorized)


if __name__ == "__main__":
    unittest.main()