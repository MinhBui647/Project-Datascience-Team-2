"""M5.5.4e.3: durable fail-closed fence, no real writer/DB/HF calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.orchestration.write_attempt_guard import (
    reserve_write_attempt, record_attempt_result, WriteAttemptBlocked,
)

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "a" * 40
CTX = "b" * 64
INTENT = "c" * 64
ARGV = ("python.exe", "-m", "scripts.tools.ingest_incremental_candidates", "--apply")


class DurableAttemptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="m5-write-ledger-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.options = dict(
            root=self.root, repo_id=REPO, target_revision=REV,
            stage="CANDIDATE_INGESTION", context_sha256=CTX,
            intent_sha256=INTENT, target_snapshot_id=367, argv=ARGV,
        )

    def claim(self, **updates):
        return reserve_write_attempt(**{**self.options, **updates})

    def test_first_claim_is_durable_and_pinned(self):
        claim = self.claim()
        self.assertTrue(claim.claim_path.is_file())
        blob = claim.claim_path.read_bytes()
        self.assertEqual(hashlib.sha256(blob).hexdigest(), claim.claim_sha256)
        data = json.loads(blob)
        self.assertEqual(data["status"], "RESERVED_OUTCOME_UNKNOWN")
        self.assertEqual(data["target_revision"], REV)
        self.assertEqual(data["target_snapshot_id"], 367)
        self.assertEqual(data["stage"], "CANDIDATE_INGESTION")
        self.assertEqual(data["intent_sha256"], INTENT)

    def test_existing_reservation_blocks_relaunch(self):
        claim = self.claim()
        with self.assertRaisesRegex(WriteAttemptBlocked, "Existing write attempt"):
            self.claim()
        self.assertTrue(claim.claim_path.is_file())

    def test_context_intent_and_argv_cannot_bypass_same_stage(self):
        self.claim()
        with self.assertRaises(WriteAttemptBlocked):
            self.claim(context_sha256="d" * 64, intent_sha256="e" * 64,
                       argv=("another-python", "-m", "other"))

    def test_other_stage_has_independent_guard(self):
        self.claim()
        other = self.claim(stage="REUSE_LINKING")
        self.assertTrue(other.claim_path.is_file())

    def test_crash_before_outcome_recording_blocks_retry(self):
        claim = self.claim()
        self.assertFalse(claim.claim_path.with_name("CANDIDATE_INGESTION.result.json").exists())
        with self.assertRaises(WriteAttemptBlocked):
            self.claim()

    def test_immutable_success_result_still_blocks_retry(self):
        claim = self.claim()
        result = record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        self.assertTrue(result.is_file())
        self.assertEqual(json.loads(result.read_text())["status"], "VERIFIED_AFTER_WRITE")
        with self.assertRaises(WriteAttemptBlocked):
            self.claim()
        with self.assertRaises(WriteAttemptBlocked):
            record_attempt_result(claim, status="UNVERIFIED_AFTER_ATTEMPT")

    def test_unknown_result_keeps_durable_block(self):
        claim = self.claim()
        result = record_attempt_result(claim, status="UNVERIFIED_AFTER_ATTEMPT")
        self.assertEqual(json.loads(result.read_text())["status"], "UNVERIFIED_AFTER_ATTEMPT")
        with self.assertRaises(WriteAttemptBlocked):
            self.claim()

    def test_corrupt_claim_rejected_not_reused(self):
        claim = self.claim()
        claim.claim_path.write_bytes(b"broken")
        with self.assertRaisesRegex(WriteAttemptBlocked, "changed unexpectedly"):
            record_attempt_result(claim, status="VERIFIED_AFTER_WRITE")
        with self.assertRaises(WriteAttemptBlocked):
            self.claim()

    def test_invalid_sha_does_not_reserve(self):
        with self.assertRaises(WriteAttemptBlocked):
            self.claim(target_revision="A" * 40)
        self.assertFalse((self.root / "reports" / "m5_write_attempts").exists())

    def test_exclusive_claim_cross_thread(self):
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()
        def attempt():
            barrier.wait(timeout=5)
            try:
                self.claim()
                value = "reserved"
            except WriteAttemptBlocked:
                value = "blocked"
            with lock:
                results.append(value)
        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=7)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(results), ["blocked", "reserved"])

    def test_fsync_failure_preserves_fence(self):
        with patch("scripts.orchestration.write_attempt_guard.os.fsync", side_effect=OSError("disk error")):
            with self.assertRaises(WriteAttemptBlocked):
                self.claim()
        with self.assertRaises(WriteAttemptBlocked):
            self.claim()

    def test_symlinked_journal_parent_is_refused(self):
        external = self.root / "external"
        external.mkdir()
        reports = self.root / "reports"
        try:
            reports.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Environment does not allow symlinks")
        with self.assertRaisesRegex(WriteAttemptBlocked, "symlink"):
            self.claim()


if __name__ == "__main__":
    unittest.main()
