"""Offline pure-contract tests for M4.6.4d.6c.2b.2b.1.

Run at project root: python -m unittest -v tests.test_incremental_post_execution_verifier
(or directly: python -m unittest -v <path_to_file>)
"""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from scripts.tools.incremental_post_execution_verifier import (
    VerificationResult, classify_db_stage,
)


def audit(state="CANDIDATES_COMPLETE", reuse=2, seen_reuse=None,
          candidates=3, seen_candidates=None, sid=42, blockers=(), write=False):
    # Use states that a real Recovery Audit could emit; contradictory fixtures
    # must be separate negative tests rather than pretending to be successful.
    if seen_reuse is None:
        seen_reuse = 1 if state == "REGISTERED_PENDING_REUSE" else reuse
    if seen_candidates is None:
        seen_candidates = (0 if state in ("REGISTERED_PENDING_REUSE", "REUSE_COMPLETE")
                           else 1 if state == "CANDIDATES_PARTIAL" else candidates)
    return SimpleNamespace(recovery_state=state, expected_reuse=reuse,
                           verified_reuse=seen_reuse,
                           expected_candidates=candidates,
                           verified_candidates=seen_candidates,
                           target_snapshot_id=sid, blockers=blockers,
                           write_authorized=write)


class PostExecutionVerifierContractTests(unittest.TestCase):
    def test_never_authorizes_writes(self):
        with self.assertRaises(ValueError):
            VerificationResult("PLANNED", "VERIFIED", write_authorized=True)

    def test_verification_cannot_have_blockers(self):
        with self.assertRaises(ValueError):
            VerificationResult("PLANNED", "VERIFIED", blockers=("problem",))

    def test_registration_readback_sufficient(self):
        v = classify_db_stage("SNAPSHOT_REGISTRATION", audit("REGISTERED_PENDING_REUSE"), target_id=42)
        self.assertEqual(v.status, "VERIFIED")

    def test_snapshot_identity_must_match(self):
        v = classify_db_stage("SNAPSHOT_REGISTRATION", audit(), target_id=99)
        self.assertEqual(v.status, "INCOMPLETE")

    def test_reuse_incomplete(self):
        v = classify_db_stage("REUSE_LINKING", audit("REGISTERED_PENDING_REUSE", seen_reuse=1), target_id=42)
        self.assertEqual(v.status, "INCOMPLETE")

    def test_reuse_verified(self):
        v = classify_db_stage("REUSE_LINKING", audit("REUSE_COMPLETE"), target_id=42)
        self.assertEqual(v.status, "VERIFIED")

    def test_candidates_partial(self):
        v = classify_db_stage("CANDIDATE_INGESTION", audit("CANDIDATES_PARTIAL", seen_candidates=1), target_id=42)
        self.assertEqual(v.status, "INCOMPLETE")

    def test_candidates_complete(self):
        v = classify_db_stage("CANDIDATE_INGESTION", audit(), target_id=42)
        self.assertEqual(v.status, "VERIFIED")

    def test_manifest_registered(self):
        v = classify_db_stage("MANIFEST_REGISTRATION", audit("MANIFEST_REGISTERED"), target_id=42)
        self.assertEqual(v.status, "VERIFIED")

    def test_manifest_not_yet_registered(self):
        v = classify_db_stage("MANIFEST_REGISTRATION", audit("CANDIDATES_COMPLETE"), target_id=42)
        self.assertEqual(v.status, "INCOMPLETE")

    def test_blocked_audit(self):
        v = classify_db_stage("REUSE_LINKING", audit("BLOCKED_REVIEW", blockers=("hash mismatch",)), target_id=42)
        self.assertEqual(v.status, "BLOCKED")

    def test_reject_write_authorized_audit(self):
        v = classify_db_stage("CANDIDATE_INGESTION", audit(write=True), target_id=42)
        self.assertEqual(v.status, "BLOCKED")

    def test_completed_cannot_use_short_circuit_as_proof(self):
        v = classify_db_stage("FINALIZATION", audit("COMPLETED"), target_id=42)
        self.assertEqual(v.status, "INCOMPLETE")

    def test_no_forged_finalization(self):
        v = classify_db_stage("FINALIZATION", audit("MANIFEST_REGISTERED"), target_id=42)
        self.assertEqual(v.status, "INCOMPLETE")

    def test_unknown_stage(self):
        with self.assertRaises(ValueError):
            classify_db_stage("PLANNED", audit(), target_id=42)


if __name__ == "__main__":
    unittest.main()
