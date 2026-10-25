"""Offline branch tests for completed-snapshot FINALIZATION verifier integration.

Run from project root: python -B -m unittest -v tests.test_incremental_completed_verifier_integration
No database, archive or HF access: all readers are injected or mocked.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.tools.incremental_post_execution_verifier import verify_read_only


REPO = "Toibinguz/epl-premier-league-dataset"
REV = "d3648ed988914f238fa60cd68feda26118d4cbb9"
SNAPSHOT = 366
EVIDENCE = (
    "Completed snapshot #366; observations=296; records=8502604; "
    "archive_objects=296; archive_bytes=836981312"
)


def contexts():
    planned = SimpleNamespace(repo_id=REPO, target_revision=REV)
    prepared = SimpleNamespace(planned=planned)
    registered = SimpleNamespace(prepared=prepared, target_snapshot_id=SNAPSHOT)
    return planned, prepared, registered


def recovery(**changes):
    fields = dict(
        recovery_state="COMPLETED", target_snapshot_id=SNAPSHOT,
        write_authorized=False, blockers=(),
        expected_reuse=282, verified_reuse=282,
        expected_candidates=14, verified_candidates=14,
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


class CompletedVerifierIntegrationTests(unittest.TestCase):
    def run_verification(self, *, stage="FINALIZATION", state=None,
                         evidence=EVIDENCE, audit_error=None, chain=None):
        current = recovery() if state is None else state
        checkpoint_chain = contexts() if chain is None else chain
        with patch(
            "scripts.tools.incremental_completed_snapshot_audit.audit_completed_snapshot"
        ) as collector:
            if audit_error is not None:
                collector.side_effect = audit_error
            else:
                collector.return_value = evidence
            result = verify_read_only(
                stage=stage, repo_id=REPO, target_revision=REV,
                root=Path("."),
                checkpoint_reader=lambda root, rev: checkpoint_chain,
                recovery_reader=lambda **kw: current,
            )
            return result, collector.call_count

    def test_completed_with_independent_audit_verified(self):
        verdict, calls = self.run_verification()
        self.assertEqual((verdict.stage, verdict.status), ("FINALIZATION", "VERIFIED"))
        self.assertEqual(verdict.evidence, (EVIDENCE,))
        self.assertEqual(verdict.blockers, ())
        self.assertIs(verdict.write_authorized, False)
        self.assertEqual(calls, 1)

    def test_completed_auditor_exception_blocked(self):
        verdict, calls = self.run_verification(audit_error=RuntimeError("archive corrupt"))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertIn("archive corrupt", " ".join(verdict.blockers))
        self.assertEqual(calls, 1)

    def test_completed_missing_archive_blocked(self):
        verdict, calls = self.run_verification(audit_error=FileNotFoundError("archive missing"))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 1)

    def test_completed_database_error_blocked(self):
        verdict, calls = self.run_verification(audit_error=ValueError("record count mismatch"))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 1)

    def test_completed_mismatched_target_id_incomplete(self):
        verdict, calls = self.run_verification(state=recovery(target_snapshot_id=999))
        self.assertEqual(verdict.status, "INCOMPLETE")
        self.assertEqual(calls, 0)

    def test_completed_boolean_target_id_rejected(self):
        verdict, calls = self.run_verification(state=recovery(target_snapshot_id=True))
        self.assertEqual(verdict.status, "INCOMPLETE")
        self.assertEqual(calls, 0)

    def test_completed_write_authorization_rejected(self):
        verdict, calls = self.run_verification(state=recovery(write_authorized=True))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 0)

    def test_completed_upstream_blocker_rejected(self):
        verdict, calls = self.run_verification(state=recovery(blockers=("checkpoint inconsistent",)))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 0)

    def test_completed_malformed_blockers_rejected(self):
        verdict, calls = self.run_verification(state=recovery(blockers=["not tuple"]))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 0)

    def test_completed_missing_evidence_blocked(self):
        verdict, calls = self.run_verification(evidence=None)
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 1)

    def test_completed_wrong_snapshot_in_evidence_blocked(self):
        verdict, calls = self.run_verification(
            evidence="Completed snapshot #365; observations=286")
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 1)

    def test_completed_wrong_return_type_blocked(self):
        verdict, calls = self.run_verification(evidence={"snapshot": SNAPSHOT})
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 1)

    def test_unfinalized_state_never_runs_completed_auditor(self):
        verdict, calls = self.run_verification(state=recovery(recovery_state="MANIFEST_REGISTERED"))
        self.assertEqual(verdict.status, "INCOMPLETE")
        self.assertEqual(calls, 0)

    def test_other_stage_never_runs_completed_auditor(self):
        verdict, calls = self.run_verification(stage="MANIFEST_REGISTRATION")
        self.assertEqual(verdict.status, "INCOMPLETE")
        self.assertEqual(calls, 0)

    def test_no_registered_checkpoint_remains_incomplete(self):
        plan, prep, reg = contexts()
        verdict, calls = self.run_verification(chain=(plan, prep, None))
        self.assertEqual(verdict.status, "INCOMPLETE")
        self.assertEqual(calls, 0)

    def test_checkpoint_parent_drift_blocked(self):
        plan, prep, reg = contexts()
        drift = SimpleNamespace(repo_id="wrong", target_revision=REV)
        reg.prepared = SimpleNamespace(planned=drift)
        verdict, calls = self.run_verification(chain=(plan, prep, reg))
        self.assertEqual(verdict.status, "BLOCKED")
        self.assertEqual(calls, 0)

    def test_no_planned_checkpoint_remains_incomplete(self):
        verdict, calls = self.run_verification(chain=(None, None, None))
        self.assertEqual(verdict.status, "INCOMPLETE")
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
