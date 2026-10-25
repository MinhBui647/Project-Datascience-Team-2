"""M5.5.5b offline tests for the read-only orchestration preview.
No real M4/HF/PostgreSQL module imported. All I/O services are injected.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.orchestration.end_to_end_readonly import (
    STAGE_ORDER, PipelinePreview, review_end_to_end,
)

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "708a15b7589e8c43347827b13e6e9ac8def871b0"
NEW = "a" * 40


def make_case(*, stage="PLANNED", decision="PLAN_NEW_REVISION",
              recovery="NO_REGISTERED_SNAPSHOT", watcher_state="REVIEW_REQUIRED",
              controller_status="MANUAL_REVIEW_REQUIRED", checkpoints=(None, None, None),
              journal="NO_LOCAL_ATTEMPT", blocker=(), m4_authorized=False,
              matrix_override=None, checkpoint_error=None, journal_error=None):
    calls = {"watcher": 0, "planner": 0, "resolver": 0, "checkpoints": 0, "journal": 0}
    items = tuple(SimpleNamespace(name=name, status="READY" if name == stage else "SKIPPED")
                  for name in STAGE_ORDER) if matrix_override is None else matrix_override
    plan = SimpleNamespace(stages=items)

    def resolver(repo, ref):
        calls["resolver"] += 1
        assert repo == REPO and ref == "main"
        return REV

    def planner(*, repo_id, target_revision, baseline_snapshot):
        calls["planner"] += 1
        assert repo_id == REPO and target_revision == REV and baseline_snapshot == 367
        return plan

    def watcher(*, repo_id, revision_ref, baseline_snapshot, resolver, planner):
        calls["watcher"] += 1
        pin = resolver(repo_id, revision_ref)
        p = planner(repo_id=repo_id, target_revision=pin, baseline_snapshot=baseline_snapshot)
        summary = SimpleNamespace(
            repo_id=repo_id, target_revision=pin, status=controller_status,
            m4_decision=decision, recovery_state=recovery,
            next_review_stage=stage, blockers=blocker,
            m4_write_authorized=m4_authorized, m5_write_executed=False,
            stage_count=len(p.stages),
        )
        return SimpleNamespace(controller=summary, pinned_revision=pin,
                               state=watcher_state)

    def checkpoint_reader(root, revision):
        calls["checkpoints"] += 1
        if checkpoint_error:
            raise checkpoint_error
        return checkpoints

    def journal_auditor(**kw):
        calls["journal"] += 1
        if journal_error:
            raise journal_error
        return SimpleNamespace(state=journal, write_authorized=False,
                               retry_authorized=False, write_executed=False)

    return dict(resolver=resolver, planner=planner, watcher=watcher,
                checkpoint_reader=checkpoint_reader, journal_auditor=journal_auditor), calls


class EndToEndReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_case(self, **kwargs):
        services, calls = make_case(**kwargs)
        result = review_end_to_end(repo_id=REPO, revision_ref="main",
                                  baseline_snapshot=367, root=self.root, **services)
        self.assertFalse(result.write_authorized)
        self.assertFalse(result.write_executed)
        self.assertFalse(result.retry_authorized)
        self.assertFalse(result.stage_advanced)
        self.assertEqual(calls["planner"], 1)
        self.assertEqual(calls["resolver"], 1)
        self.assertEqual(calls["watcher"], 1)
        self.assertEqual(len(result.stages), 11)
        return result, calls

    def test_completed_current_revision_without_checkpoints_skips(self):
        result, calls = self.run_case(stage=None, decision="SKIP_ALREADY_CURRENT",
                                      recovery="COMPLETED", watcher_state="SKIPPED_BY_M4",
                                      controller_status="NO_STAGE_REPORTED")
        self.assertEqual(result.state, "SKIPPED_CURRENT")
        self.assertEqual(calls["checkpoints"], 0)
        self.assertEqual(calls["journal"], 0)

    def test_new_revision_starts_at_checkpoint_review(self):
        result, calls = self.run_case()
        self.assertEqual(result.state, "REVIEW_CHECKPOINT")
        self.assertEqual(result.next_review_stage, "PLANNED")
        self.assertEqual(result.checkpoints["planned"], "ABSENT")
        self.assertEqual(calls["journal"], 0)

    def test_manifest_preparation_needs_planned_checkpoint(self):
        result, _ = self.run_case(stage="MANIFEST_PREPARATION")
        self.assertEqual(result.state, "BLOCKED")
        self.assertIn("planned", " ".join(result.blockers))

    def test_manifest_preparation_is_review_only(self):
        result, _ = self.run_case(stage="MANIFEST_PREPARATION",
                                   checkpoints=(object(), None, None))
        self.assertEqual(result.state, "REVIEW_PREPARATION")

    def test_prepared_requires_planned_checkpoint(self):
        result, _ = self.run_case(stage="PREPARED", checkpoints=(object(), None, None))
        self.assertEqual(result.state, "REVIEW_CHECKPOINT")

    def test_snapshot_registration_requires_prepared_checkpoint(self):
        result, _ = self.run_case(stage="SNAPSHOT_REGISTRATION",
                                   checkpoints=(object(), None, None))
        self.assertEqual(result.state, "BLOCKED")
        self.assertIn("prepared", " ".join(result.blockers))

    def test_candidate_ingestion_requires_registered_checkpoint(self):
        result, _ = self.run_case(stage="CANDIDATE_INGESTION",
                                   checkpoints=(object(), object(), None))
        self.assertEqual(result.state, "BLOCKED")
        self.assertIn("registered", " ".join(result.blockers))

    def test_db_write_with_no_local_attempt_is_review_only(self):
        result, calls = self.run_case(stage="CANDIDATE_INGESTION",
                                      checkpoints=(object(), object(), object()))
        self.assertEqual(result.state, "REVIEW_DB_WRITE")
        self.assertEqual(result.journal_state, "NO_LOCAL_ATTEMPT")
        self.assertEqual(calls["journal"], 1)

    def test_existing_reserved_claim_blocks(self):
        result, _ = self.run_case(stage="CANDIDATE_INGESTION",
                                  checkpoints=(object(), object(), object()),
                                  journal="AMBIGUOUS_ATTEMPT_BLOCKED")
        self.assertEqual(result.state, "BLOCKED_ATTEMPT_REVIEW")

    def test_existing_verified_claim_requires_manual_review(self):
        result, _ = self.run_case(stage="FINALIZATION",
                                  checkpoints=(object(), object(), object()),
                                  journal="RECORDED_VERIFIED_REQUIRES_EVIDENCE")
        self.assertEqual(result.state, "BLOCKED_ATTEMPT_REVIEW")

    def test_unknown_watcher_state_blocks(self):
        result, calls = self.run_case(stage=None, watcher_state="UNRESOLVED",
                                      controller_status="NO_STAGE_REPORTED")
        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(calls["checkpoints"], 0)

    def test_explicit_m4_write_authorization_is_refused(self):
        result, calls = self.run_case(m4_authorized=True)
        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(calls["checkpoints"], 0)

    def test_checkpoint_read_failure_blocks(self):
        result, _ = self.run_case(checkpoint_error=RuntimeError("synthetic missing chain"))
        self.assertEqual(result.state, "BLOCKED")

    def test_journal_read_failure_blocks(self):
        result, _ = self.run_case(stage="REUSE_LINKING",
                                  checkpoints=(object(), object(), object()),
                                  journal_error=RuntimeError("synthetic journal error"))
        self.assertEqual(result.state, "BLOCKED")

    def test_wrong_stage_matrix_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "matrix"):
            self.run_case(matrix_override=(SimpleNamespace(name="ROUTE", status="READY"),))

    def test_bad_baseline_rejected(self):
        services, calls = make_case()
        with self.assertRaisesRegex(ValueError, "baseline"):
            review_end_to_end(repo_id=REPO, revision_ref="main",
                              baseline_snapshot=0, root=self.root, **services)
        self.assertEqual(calls["watcher"], 0)

    def test_no_write_authority_can_be_constructed(self):
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            PipelinePreview(state="BLOCKED", repo_id=REPO, target_revision=REV,
                            requested_ref="main", watcher_state="BLOCKED",
                            m4_decision="BLOCKED", recovery_state="BLOCKED_REVIEW",
                            next_review_stage=None, stage_kind=None, next_action="",
                            checkpoints={}, journal_state=None, stages=(),
                            blockers=(), write_authorized=True)


if __name__ == "__main__":
    unittest.main()
