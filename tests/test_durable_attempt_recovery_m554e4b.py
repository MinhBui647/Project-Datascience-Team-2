"""M5.5.4e.4b: journal-only recovery classification; never dispatch writers."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from scripts.orchestration.durable_attempt_recovery_audit import audit_attempt
from scripts.orchestration.write_attempt_guard import reserve_write_attempt, record_attempt_result

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "a" * 40
STAGE = "CANDIDATE_INGESTION"


class RecoveryJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m554e4b-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opts = dict(root=self.root, repo_id=REPO, target_revision=REV, stage=STAGE)

    def reserve(self):
        return reserve_write_attempt(
            **self.opts, context_sha256="b" * 64, intent_sha256="c" * 64,
            target_snapshot_id=368,
            argv=("python.exe", "-m", "scripts.tools.ingest_incremental_candidates", "--apply"),
        )

    def run_audit(self, verifier=None):
        return audit_attempt(**self.opts, verifier=verifier)

    @staticmethod
    def evidence(status):
        return lambda **kw: SimpleNamespace(stage=STAGE, status=status, write_authorized=False)

    def test_no_attempt_is_readonly_not_authorization(self):
        outcome = self.run_audit()
        self.assertEqual(outcome.state, "NO_LOCAL_ATTEMPT")
        self.assertFalse(outcome.write_authorized)
        self.assertFalse(outcome.retry_authorized)
        self.assertFalse((self.root / "reports").exists())

    def test_reserved_crash_blocks(self):
        self.reserve()
        outcome = self.run_audit()
        self.assertEqual(outcome.state, "AMBIGUOUS_ATTEMPT_BLOCKED")
        self.assertTrue(outcome.claim_present)
        self.assertFalse(outcome.result_present)

    def test_unverified_attempt_stays_manual_even_with_verified_evidence(self):
        claim = self.reserve()
        record_attempt_result(claim, status="UNVERIFIED_AFTER_ATTEMPT")
        outcome = self.run_audit(self.evidence("VERIFIED"))
        self.assertEqual(outcome.state, "MANUAL_RECONCILIATION_REQUIRED")
        self.assertFalse(outcome.retry_authorized)
        self.assertEqual(outcome.evidence_status, "VERIFIED")

    def test_verified_result_without_fresh_evidence_not_claimed_verified(self):
        claim = self.reserve()
        record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        self.assertEqual(self.run_audit().state, "RECORDED_VERIFIED_REQUIRES_EVIDENCE")

    def test_verified_result_with_verified_evidence_remains_review_only(self):
        claim = self.reserve()
        record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        outcome = self.run_audit(self.evidence("VERIFIED"))
        self.assertEqual(outcome.state, "EVIDENCE_CONSISTENT_MANUAL_REVIEW")
        self.assertFalse(outcome.retry_authorized)

    def test_verified_result_but_incomplete_evidence_conflict(self):
        claim = self.reserve()
        record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        outcome = self.run_audit(self.evidence("INCOMPLETE"))
        self.assertEqual(outcome.state, "BLOCKED_CONTRADICTORY_EVIDENCE")

    def test_orphan_result_is_blocked(self):
        claim = self.reserve()
        record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        claim.claim_path.unlink()  # test sandbox only
        self.assertEqual(self.run_audit().state, "BLOCKED_INVALID_JOURNAL")

    def test_result_claim_hash_mismatch_is_blocked(self):
        claim = self.reserve()
        path = record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["claim_sha256"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")  # sandbox only
        self.assertEqual(self.run_audit().state, "BLOCKED_INVALID_JOURNAL")

    def test_corrupt_claim_is_blocked(self):
        claim = self.reserve()
        claim.claim_path.write_bytes(b"not-json")  # sandbox only
        self.assertEqual(self.run_audit().state, "BLOCKED_INVALID_JOURNAL")

    def test_symlinked_claim_is_blocked(self):
        claim = self.reserve()
        external = self.root / "external"
        external.write_text("not-json")
        claim.claim_path.unlink()  # test sandbox only
        try:
            claim.claim_path.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this platform")
        self.assertEqual(self.run_audit().state, "BLOCKED_INVALID_JOURNAL")

    def test_untrusted_evidence_object_is_blocked(self):
        self.reserve()
        bad_evidence = lambda **kw: SimpleNamespace(stage=STAGE, status="VERIFIED", write_authorized=True)
        self.assertEqual(self.run_audit(bad_evidence).state, "BLOCKED_INVALID_JOURNAL")

    def test_evidence_reader_exception_is_blocked(self):
        self.reserve()
        def broken(**kwargs):
            raise RuntimeError("synthetic failure")
        self.assertEqual(self.run_audit(broken).state, "BLOCKED_AUDIT_FAILURE")

    def test_unrelated_stage_has_no_local_attempt(self):
        self.reserve()
        self.opts["stage"] = "REUSE_LINKING"
        outcome = self.run_audit()
        self.assertEqual(outcome.state, "NO_LOCAL_ATTEMPT")
        self.assertFalse(outcome.retry_authorized)

    def test_read_only_audit_does_not_mutate_claim_or_result(self):
        claim = self.reserve()
        result_path = record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        before = (claim.claim_path.read_bytes(), result_path.read_bytes())
        for _ in range(3):
            self.run_audit(self.evidence("VERIFIED"))
        self.assertEqual(before, (claim.claim_path.read_bytes(), result_path.read_bytes()))

    def test_invalid_revision_refused(self):
        self.opts["target_revision"] = "A" * 40
        self.assertEqual(self.run_audit().state, "BLOCKED_INVALID_JOURNAL")
        self.assertFalse((self.root / "reports").exists())


if __name__ == "__main__":
    unittest.main()
