"""M5.5.3c: Real Router/Stage Planner/Controller/Watcher integration.

External state readers, HF resolution and new-revision plan
evidence are injected. No production DB or HF access.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.orchestration.commit_watcher import watch_once
from scripts.incremental.incremental_readonly_router import (
    route_read_only,
)
from scripts.incremental.incremental_guarded_stage_planner import (
    plan_stages_read_only,
)
from scripts.incremental.incremental_recovery_audit import (
    RecoveryAssessment,
)

REPO = "Toibinguz/epl-premier-league-dataset"
CURRENT = "708a15b7589e8c43347827b13e6e9ac8def871b0"
NEW = "a" * 40


def assessment(state, snapshot_id, reuse=293, verified_reuse=0,
               candidates=3, verified_candidates=0, blockers=()):
    return RecoveryAssessment(
        recovery_state=state,
        target_snapshot_id=snapshot_id,
        expected_reuse=reuse,
        verified_reuse=verified_reuse,
        expected_candidates=candidates,
        verified_candidates=verified_candidates,
        next_step="MANUAL_REVIEW",
        blockers=blockers,
    )


def run_pipeline(revision, recovery, baseline=367, latest=367):
    calls = {"resolve": 0, "route": 0}

    readers = SimpleNamespace(
        select_baseline=lambda repo: SimpleNamespace(snapshot_id=latest),
        read_target_state=lambda repo, target: None,
    )

    def resolver(repo, ref):
        calls["resolve"] += 1
        assert repo == REPO and ref == "main"
        return revision

    def recovery_auditor(**kwargs):
        calls["route"] += 1
        assert kwargs["repo_id"] == REPO
        assert kwargs["target_revision"] == revision
        return recovery

    def router(**kwargs):
        return route_read_only(
            **kwargs,
            recovery_auditor=recovery_auditor,
        )

    def planner(**kwargs):
        return plan_stages_read_only(
            **kwargs,
            readers=readers,
            router=router,
        )

    result = watch_once(
        repo_id=REPO,
        revision_ref="main",
        baseline_snapshot=baseline,
        resolver=resolver,
        planner=planner,
    )

    assert calls == {"resolve": 1, "route": 1}
    assert result.pinned_revision == revision
    assert result.controller.target_revision == revision
    assert result.controller.m5_write_executed is False

    return result


class RealM4IntegrationTests(unittest.TestCase):

    def test_completed_current_revision(self):
        recovery = assessment(
            "COMPLETED", 367,
            verified_reuse=293,
            verified_candidates=3,
        )
        result = run_pipeline(CURRENT, recovery)

        self.assertEqual(result.state, "SKIPPED_BY_M4")
        self.assertEqual(
            result.controller.m4_decision,
            "SKIP_ALREADY_CURRENT",
        )
        self.assertIsNone(result.controller.next_review_stage)
        self.assertFalse(result.controller.m4_write_authorized)

    def test_registered_target_requires_recovery_review(self):
        recovery = assessment(
            "REGISTERED_PENDING_REUSE", 368,
        )
        result = run_pipeline(
            NEW, recovery, baseline=None,
        )

        self.assertEqual(result.state, "REVIEW_REQUIRED")
        self.assertEqual(
            result.controller.m4_decision,
            "RECOVERY_REVIEW_REQUIRED",
        )
        self.assertEqual(
            result.controller.next_review_stage,
            "REUSE_LINKING",
        )
        self.assertFalse(result.controller.m4_write_authorized)

    def test_explicit_baseline_cannot_override_recovery(self):
        recovery = assessment(
            "REGISTERED_PENDING_REUSE", 368,
        )
        result = run_pipeline(
            NEW, recovery, baseline=367,
        )

        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(
            result.controller.m4_decision,
            "BLOCKED_EXPLICIT_BASELINE_RECOVERY",
        )
        self.assertFalse(result.controller.m4_write_authorized)

    def test_blocked_recovery_stays_blocked(self):
        recovery = assessment(
            "BLOCKED_REVIEW", 368,
            blockers=("Synthetic validation failure",),
        )
        result = run_pipeline(
            NEW, recovery, baseline=None,
        )

        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(result.controller.status, "BLOCKED")
        self.assertTrue(result.controller.blockers)

    def test_new_revision_reaches_planned_stage(self):
        recovery = assessment(
            "NO_REGISTERED_SNAPSHOT", None,
            reuse=0, candidates=0,
        )

        planned = SimpleNamespace(
            repo_id=REPO,
            target_revision=NEW,
        )
        synthetic_plan = SimpleNamespace(
            decision="PLAN_NEW_REVISION",
            planned=planned,
        )

        def preview_command(step, context):
            assert context is planned
            return (
                sys.executable,
                "-m",
                "scripts.tools." + step,
                "--plan",
            )

        with patch(
            "scripts.incremental.incremental_readonly_router.plan_read_only",
            return_value=synthetic_plan,
        ) as new_plan, patch(
            "scripts.incremental.incremental_guarded_stage_planner.plan_command",
            side_effect=preview_command,
        ), patch(
            "scripts.incremental.incremental_guarded_stage_planner.checkpoint_digest",
            return_value="b" * 64,
        ):
            result = run_pipeline(
                NEW, recovery, baseline=367,
            )

        new_plan.assert_called_once()
        self.assertEqual(result.state, "REVIEW_REQUIRED")
        self.assertEqual(
            result.controller.m4_decision,
            "PLAN_NEW_REVISION",
        )
        self.assertEqual(
            result.controller.next_review_stage,
            "PLANNED",
        )
        self.assertEqual(
            result.controller.planned_context_sha256,
            "b" * 64,
        )
        self.assertEqual(
            result.controller.read_only_preview_count, 2,
        )
        self.assertFalse(result.controller.m4_write_authorized)


if __name__ == "__main__":
    unittest.main()