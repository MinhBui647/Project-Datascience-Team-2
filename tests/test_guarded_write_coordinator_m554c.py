"""M5.5.4c isolated, offline coordinator tests; no DB, HF or child processes."""
import sys
import unittest
from unittest.mock import Mock, patch

from scripts.incremental.incremental_guarded_stage_executor import StageReview
from scripts.incremental.incremental_guarded_stage_planner import GuardedStagePlan
from scripts.incremental.incremental_execution_contract_m46 import ExecutionContract
from scripts.orchestration.csv_write_intent import STAGE_WRITERS
from scripts.orchestration.guarded_write_coordinator import review_stage_writer

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "a" * 40
CONTEXT = "b" * 64

COMMON = {
    "--repo-id": REPO, "--target-revision": REV,
    "--baseline-snapshot": "366", "--expected-fingerprint": "c" * 64,
    "--expected-baseline-files": "296", "--expected-full-files": "314",
    "--expected-raw-files": "296", "--expected-reuse": "293",
    "--expected-files": "3", "--expected-bytes": "2916818",
    "--expected-records": "72", "--target-snapshot-id": "367",
    "--expected-baseline-records": "8502604",
    "--expected-reused-records": "8502542",
    "--cache-dir": "reports/m4_6_content_cache",
    "--manifest-report": "reports/m4_6_manifests/" + REV + "/hf_manifest_v2.json",
    "--manifest-sha256": "d" * 64,
}


def command(stage, opts=None):
    opts = {**COMMON, **(opts or {})}
    argv = tuple(v for pair in opts.items() for v in pair)
    return (sys.executable, "-m", "scripts.tools." + STAGE_WRITERS[stage][0], *argv, "--plan")


def fake_review(stage="CANDIDATE_INGESTION", decision="RECOVERY_REVIEW_REQUIRED",
                disposition="REVIEW_READ_ONLY_PLAN", target_sid=367, blockers=()):
    # Typed mocks satisfy the contract but do not make any real M4 DB/HF calls.
    r = Mock(spec=StageReview)
    r.plan = Mock(spec=GuardedStagePlan)
    r.contract = Mock(spec=ExecutionContract)
    r.plan.repo_id = REPO
    r.plan.target_revision = REV
    r.plan.decision = decision
    r.plan.next_review_stage = stage
    r.plan.target_snapshot_id = target_sid
    r.plan.write_authorized = False
    r.plan.blockers = ()
    r.contract.review_stage = stage
    r.contract.disposition = disposition
    r.contract.blockers = blockers
    r.contract.context_sha256 = CONTEXT
    r.contract.write_authorized = False
    return r


class GuardedWriteCoordinatorTests(unittest.TestCase):
    def test_valid_candidate_stage_ready_but_never_authorized(self):
        r = fake_review()
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                   return_value=(command("CANDIDATE_INGESTION"),)) as validator:
            out = review_stage_writer(review=r, stage="CANDIDATE_INGESTION",
                                      expected_context_sha256=CONTEXT,
                                      expected_candidate_cache="reports/m4_6_content_cache")
        validator.assert_called_once_with(r)
        self.assertEqual(out.state, "READY_FOR_SEPARATE_WRITE_GATE")
        self.assertFalse(out.write_authorized)
        self.assertFalse(out.write_executed)
        self.assertEqual(out.csv_flag, "--allow-csv-candidate-write")

    def test_all_five_stage_mappings(self):
        for stage in STAGE_WRITERS:
            with self.subTest(stage=stage):
                r = fake_review(stage=stage)
                with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                           return_value=(command(stage),)):
                    out = review_stage_writer(review=r, stage=stage,
                                              expected_context_sha256=CONTEXT,
                                      expected_candidate_cache="reports/m4_6_content_cache")
                self.assertEqual(out.state, "READY_FOR_SEPARATE_WRITE_GATE")

    def test_skip_never_dispatches_preview(self):
        r = fake_review(decision="SKIP_ALREADY_CURRENT")
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands") as v:
            out = review_stage_writer(review=r, stage="CANDIDATE_INGESTION")
        self.assertEqual(out.state, "SKIPPED")
        v.assert_not_called()

    def test_blocked_never_dispatches_preview(self):
        r = fake_review(decision="BLOCKED_RECOVERY_REVIEW", disposition="BLOCKED")
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands") as v:
            out = review_stage_writer(review=r, stage="CANDIDATE_INGESTION")
        self.assertEqual(out.state, "BLOCKED")
        v.assert_not_called()

    def test_wrong_stage_fails(self):
        r = fake_review()
        with self.assertRaisesRegex(RuntimeError, "differs"):
            review_stage_writer(review=r, stage="FINALIZATION",
                                expected_context_sha256=CONTEXT,
                                      expected_candidate_cache="reports/m4_6_content_cache")

    def test_wrong_context_hash_fails(self):
        r = fake_review()
        with self.assertRaisesRegex(RuntimeError, "context SHA256"):
            review_stage_writer(review=r, stage="CANDIDATE_INGESTION",
                                expected_context_sha256="f" * 64)

    def test_positive_m4_authorization_fails(self):
        r = fake_review()
        r.plan.write_authorized = True
        with self.assertRaisesRegex(RuntimeError, "authorized"):
            review_stage_writer(review=r, stage="CANDIDATE_INGESTION")

    def test_missing_evidence_remains_read_only(self):
        r = fake_review(disposition="AWAITING_EVIDENCE")
        out = review_stage_writer(review=r, stage="CANDIDATE_INGESTION")
        self.assertEqual(out.state, "AWAITING_EVIDENCE")
        self.assertFalse(out.write_authorized)

    def test_unknown_preview_module_is_refused(self):
        r = fake_review()
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                   return_value=(command("REUSE_LINKING"),)):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                review_stage_writer(review=r, stage="CANDIDATE_INGESTION",
                                    expected_context_sha256=CONTEXT,
                                      expected_candidate_cache="reports/m4_6_content_cache")

    def test_candidate_missing_cache_refused(self):
        r = fake_review()
        opts = {k: v for k, v in COMMON.items() if k != "--cache-dir"}
        call = tuple(v for pair in opts.items() for v in pair)
        command_no_cache = (sys.executable, "-m",
                            "scripts.tools.ingest_incremental_candidates", *call, "--plan")
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                   return_value=(command_no_cache,)):
            with self.assertRaisesRegex(ValueError, "cache-dir"):
                review_stage_writer(review=r, stage="CANDIDATE_INGESTION",
                                    expected_context_sha256=CONTEXT,
                                    expected_candidate_cache="reports/m4_6_content_cache")

    def test_stale_cache_rejected(self):
        r = fake_review()
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                   return_value=(command("CANDIDATE_INGESTION", {"--cache-dir": "reports/m4_5_content_cache"}),)):
            with self.assertRaisesRegex(RuntimeError, "Candidate cache"):
                review_stage_writer(review=r, stage="CANDIDATE_INGESTION",
                                    expected_context_sha256=CONTEXT,
                                    expected_candidate_cache="reports/m4_6_content_cache")

    def test_unpinned_cache_rejected(self):
        r = fake_review()
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                   return_value=(command("CANDIDATE_INGESTION"),)):
            with self.assertRaisesRegex(RuntimeError, "Candidate cache"):
                review_stage_writer(review=r, stage="CANDIDATE_INGESTION",
                                    expected_context_sha256=CONTEXT)

    def test_skip_with_blocker_is_blocked(self):
        r = fake_review(decision="SKIP_ALREADY_CURRENT")
        r.plan.blockers = ("unexpected",)
        out = review_stage_writer(review=r, stage="CANDIDATE_INGESTION")
        self.assertEqual(out.state, "BLOCKED")

    def test_snapshot_mismatch_fails(self):
        r = fake_review(stage="REUSE_LINKING", target_sid=367)
        with patch("scripts.orchestration.guarded_write_coordinator.validate_read_only_commands",
                   return_value=(command("REUSE_LINKING", {"--target-snapshot-id": "368"}),)):
            with self.assertRaisesRegex(RuntimeError, "snapshot ID drift"):
                review_stage_writer(review=r, stage="REUSE_LINKING",
                                    expected_context_sha256=CONTEXT,
                                      expected_candidate_cache="reports/m4_6_content_cache")

    def test_no_unknown_stage(self):
        r = fake_review()
        with self.assertRaisesRegex(ValueError, "Unknown"):
            review_stage_writer(review=r, stage="DELETE_ALL_RAW")

    def test_unsupported_stage_remains_unapproved(self):
        r = fake_review(stage="ARCHIVE_PROMOTION")
        out = review_stage_writer(review=r, stage="ARCHIVE_PROMOTION",
                                  expected_context_sha256=CONTEXT,
                                      expected_candidate_cache="reports/m4_6_content_cache")
        self.assertEqual(out.state, "UNSUPPORTED_PREPARATION_STAGE")
        self.assertFalse(out.write_executed)

    def test_type_validation(self):
        with self.assertRaises(TypeError):
            review_stage_writer(review=object(), stage="CANDIDATE_INGESTION")


if __name__ == "__main__":
    unittest.main()
