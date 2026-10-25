"""M5.5.5c.3e.2: E2E Router alignment integration, fully offline.

Never calls M4, database, Hugging Face, or a writer. All services injected.
"""
import tempfile
import unittest
from pathlib import Path

from test_end_to_end_readonly_m555b import make_case, REPO, REV
from scripts.orchestration.end_to_end_readonly import (
    review_end_to_end, preview_exit_code,
)
from scripts.orchestration.preparation_evidence_resolver import StageResolution


def resolution(state, m4_stage, evidence_stage, *, detail="synthetic fixture", verified=(),
               ev=None, journal=None, repo=REPO, rev=REV):
    return StageResolution(
        state=state, repo_id=repo, target_revision=rev,
        m4_next_stage=m4_stage, evidence_next_stage=evidence_stage,
        verified_stages=tuple(verified), evidence_statuses=ev or {},
        journal_statuses=journal or {}, detail=detail,
    )


class RouterAlignmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_case(self, *, m4_stage="MANIFEST_PREPARATION", planned=True,
                 result=None, fail=None, watcher_state="REVIEW_REQUIRED",
                 decision="RECOVERY_REVIEW_REQUIRED", recovery="PREPARATION_PARTIAL"):
        services, calls = make_case(
            stage=m4_stage, decision=decision, recovery=recovery,
            watcher_state=watcher_state,
            controller_status="MANUAL_REVIEW_REQUIRED",
            checkpoints=((object() if planned else None), None, None),
        )
        counts = {"resolution": 0}

        def resolver(**kw):
            counts["resolution"] += 1
            self.assertEqual(kw["repo_id"], REPO)
            self.assertEqual(kw["target_revision"], REV)
            self.assertEqual(kw["root"], self.root)
            self.assertEqual(kw["baseline_snapshot"], 367)
            if fail:
                raise fail
            return result

        view = review_end_to_end(repo_id=REPO, revision_ref="main",
                                 baseline_snapshot=367, root=self.root,
                                 preparation_resolver=resolver, **services)
        self.assertFalse(view.write_authorized)
        self.assertFalse(view.write_executed)
        self.assertFalse(view.retry_authorized)
        self.assertFalse(view.stage_advanced)
        self.assertEqual(calls["planner"], 1)
        self.assertEqual(calls["watcher"], 1)
        self.assertEqual(calls["resolver"], 1)
        return view, counts, calls

    def test_manifest_evidence_agrees_review_only(self):
        r=resolution("REVIEW_STAGE_READ_ONLY", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION",
                     ev={"MANIFEST_PREPARATION":"INCOMPLETE"})
        view,count,_=self.run_case(result=r)
        self.assertEqual(view.state, "REVIEW_PREPARATION")
        self.assertEqual(view.preparation_resolution, "REVIEW_STAGE_READ_ONLY")
        self.assertEqual(view.next_review_stage, "MANIFEST_PREPARATION")
        self.assertEqual(view.evidence_next_stage, "MANIFEST_PREPARATION")
        self.assertEqual(view.preparation_evidence_statuses, r.evidence_statuses)
        self.assertEqual(count["resolution"], 1)

    def test_sticky_manifest_cache_alignment_cannot_override(self):
        r=resolution("ROUTER_ALIGNMENT_REQUIRED", "MANIFEST_PREPARATION", "CACHE_DOWNLOAD",
                     verified=("MANIFEST_PREPARATION",),
                     ev={"MANIFEST_PREPARATION":"VERIFIED", "CACHE_DOWNLOAD":"INCOMPLETE"},
                     journal={"MANIFEST_PREPARATION":"VERIFIED_AFTER_WRITE"})
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state, "ROUTER_ALIGNMENT_REQUIRED")
        self.assertEqual(view.next_review_stage, "MANIFEST_PREPARATION")
        self.assertEqual(view.evidence_next_stage, "CACHE_DOWNLOAD")
        self.assertIn("ROUTER_ALIGNMENT_REQUIRED", " ".join(view.blockers))
        self.assertEqual(view.verified_preparation_stages, ("MANIFEST_PREPARATION",))
        self.assertEqual(preview_exit_code(view.state), 2)

    def test_sticky_manifest_archive_alignment_cannot_override(self):
        r=resolution("ROUTER_ALIGNMENT_REQUIRED", "MANIFEST_PREPARATION", "ARCHIVE_PROMOTION",
                     verified=("MANIFEST_PREPARATION", "CACHE_DOWNLOAD"))
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state, "ROUTER_ALIGNMENT_REQUIRED")
        self.assertEqual(view.evidence_next_stage, "ARCHIVE_PROMOTION")
        self.assertEqual(view.next_review_stage, "MANIFEST_PREPARATION")

    def test_prepared_alignment_cannot_override(self):
        r=resolution("ROUTER_ALIGNMENT_REQUIRED", "MANIFEST_PREPARATION", "PREPARED",
                     verified=("MANIFEST_PREPARATION", "CACHE_DOWNLOAD", "ARCHIVE_PROMOTION"))
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state, "ROUTER_ALIGNMENT_REQUIRED")
        self.assertEqual(view.evidence_next_stage, "PREPARED")

    def test_ambiguous_attempt_blocks(self):
        r=resolution("BLOCKED_ATTEMPT_REVIEW", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION",
                     journal={"MANIFEST_PREPARATION":"RESERVED_OUTCOME_UNKNOWN"})
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED_ATTEMPT_REVIEW")
        self.assertEqual(preview_exit_code(view.state),2)

    def test_manual_m4_without_journal_stays_manual(self):
        r=resolution("MANUAL_RECONCILIATION_REQUIRED", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION",
                     ev={"MANIFEST_PREPARATION":"VERIFIED"},
                     journal={"MANIFEST_PREPARATION":"NO_LOCAL_ATTEMPT"})
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"MANUAL_RECONCILIATION_REQUIRED")
        self.assertEqual(preview_exit_code(view.state),2)

    def test_out_of_order_attempt_blocks(self):
        r=resolution("BLOCKED_OUT_OF_ORDER_ATTEMPT", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION")
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED_OUT_OF_ORDER_ATTEMPT")

    def test_evidence_blocked_stays_blocked(self):
        r=resolution("BLOCKED", "MANIFEST_PREPARATION", None)
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED")

    def test_resolver_failure_blocks(self):
        view,_,_=self.run_case(fail=RuntimeError("mock failed"))
        self.assertEqual(view.state,"BLOCKED")
        self.assertIn("mock failed", " ".join(view.blockers))

    def test_wrong_result_type_blocked(self):
        view,_,_=self.run_case(result=object())
        self.assertEqual(view.state,"BLOCKED")

    def test_result_wrong_repo_blocked(self):
        r=resolution("REVIEW_STAGE_READ_ONLY", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION", repo="other")
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED")

    def test_result_wrong_revision_blocked(self):
        r=resolution("REVIEW_STAGE_READ_ONLY", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION", rev="b"*40)
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED")

    def test_second_m4_review_drift_blocks(self):
        r=resolution("REVIEW_STAGE_READ_ONLY", "CACHE_DOWNLOAD", "CACHE_DOWNLOAD")
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED")

    def test_resolver_unexpected_state_blocks(self):
        r=resolution("UNRECOGNIZED", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION")
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED")

    def test_not_called_if_planned_missing(self):
        r=resolution("REVIEW_STAGE_READ_ONLY", "MANIFEST_PREPARATION", "MANIFEST_PREPARATION")
        view,count,_=self.run_case(result=r,planned=False)
        self.assertEqual(view.state,"BLOCKED")
        self.assertEqual(count["resolution"],0)

    def test_not_called_for_planned_checkpoint(self):
        r=resolution("REVIEW_STAGE_READ_ONLY", "PLANNED", "MANIFEST_PREPARATION")
        view,count,_=self.run_case(m4_stage="PLANNED", planned=False, result=r,
                                   decision="PLAN_NEW_REVISION", recovery="NO_REGISTERED_SNAPSHOT")
        self.assertEqual(view.state,"REVIEW_CHECKPOINT")
        self.assertEqual(count["resolution"],0)

    def test_completed_skips_without_resolver(self):
        services, calls=make_case(stage=None, decision="SKIP_ALREADY_CURRENT",
                                  recovery="COMPLETED", watcher_state="SKIPPED_BY_M4",
                                  controller_status="NO_STAGE_REPORTED")
        def should_not_run(**kw):
            self.fail("Completed revision invoked evidence resolver")
        view=review_end_to_end(repo_id=REPO, revision_ref="main",
                               baseline_snapshot=367, root=self.root,
                               preparation_resolver=should_not_run, **services)
        self.assertEqual(view.state,"SKIPPED_CURRENT")
        self.assertIsNone(view.evidence_next_stage)
        self.assertEqual(calls["checkpoints"],0)
        self.assertEqual(preview_exit_code(view.state),0)

    def test_prepared_agrees_checkpoint_only(self):
        r=resolution("REVIEW_PREPARED", "PREPARED", "PREPARED")
        view,_,_=self.run_case(m4_stage="PREPARED",result=r)
        self.assertEqual(view.state,"REVIEW_CHECKPOINT")
        self.assertEqual(view.preparation_resolution,"REVIEW_PREPARED")

    def test_prepared_conflict_stays_blocked(self):
        r=resolution("ROUTER_ALIGNMENT_REQUIRED", "PREPARED", "ARCHIVE_PROMOTION")
        view,_,_=self.run_case(m4_stage="PREPARED",result=r)
        self.assertEqual(view.state,"ROUTER_ALIGNMENT_REQUIRED")
        self.assertEqual(preview_exit_code(view.state),2)

    def test_verified_prepared_checkpoint_cannot_auto_advance(self):
        r=resolution("PREPARATION_ALREADY_CLOSED", "MANIFEST_PREPARATION", None)
        view,_,_=self.run_case(result=r)
        self.assertEqual(view.state,"BLOCKED_PREPARATION")

    def test_allowlisted_exit_codes_only(self):
        for state in ("SKIPPED_CURRENT","REVIEW_CHECKPOINT","REVIEW_DB_WRITE","REVIEW_PREPARATION"):
            self.assertEqual(preview_exit_code(state),0)
        for state in ("ROUTER_ALIGNMENT_REQUIRED","MANUAL_RECONCILIATION_REQUIRED",
                      "BLOCKED_ATTEMPT_REVIEW","BLOCKED", "UNKNOWN"):
            self.assertEqual(preview_exit_code(state),2)


if __name__ == "__main__":
    unittest.main()
