"""M4.6.4d.6c.2b.2b.2 — pure fail-closed and injected read-only tests.

From the project root:
.\\.venv_v2\\Scripts\\python.exe -m unittest discover -s tests -p 'test_incremental_post_execution_verifier*.py' -v
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase, mock
from contextlib import redirect_stderr
from io import StringIO

from scripts.tools.incremental_post_execution_verifier import (
    classify_db_stage, verify_read_only,
)

SHA = "a" * 40
REPO = "Toibinguz/epl-premier-league-dataset"


def audit(state="MANIFEST_REGISTERED", *, er=2, vr=2, ec=3, vc=3,
          sid=42, blockers=(), write=False):
    return SimpleNamespace(
        recovery_state=state, expected_reuse=er, verified_reuse=vr,
        expected_candidates=ec, verified_candidates=vc,
        target_snapshot_id=sid, blockers=blockers, write_authorized=write,
    )


def contexts(*, revision=SHA, repo=REPO, with_prepared=True,
             with_registered=True):
    planned = SimpleNamespace(repo_id=repo, target_revision=revision)
    prepared = SimpleNamespace(planned=planned) if with_prepared else None
    registered = (SimpleNamespace(prepared=prepared, target_snapshot_id=42)
                  if with_registered and prepared is not None else None)
    return planned, prepared, registered


class AuditConsistencyTests(TestCase):
    def test_negative_counts_are_blocked_even_for_registration(self):
        self.assertEqual(classify_db_stage("SNAPSHOT_REGISTRATION", audit(er=-1), target_id=42).status,
                         "BLOCKED")

    def test_boolean_counts_are_blocked(self):
        self.assertEqual(classify_db_stage("REUSE_LINKING", audit(vr=True), target_id=42).status,
                         "BLOCKED")

    def test_more_seen_than_expected_blocked(self):
        self.assertEqual(classify_db_stage("CANDIDATE_INGESTION", audit(vc=4), target_id=42).status,
                         "BLOCKED")

    def test_false_pending_reuse_state_blocked(self):
        self.assertEqual(classify_db_stage("REUSE_LINKING",
            audit("REGISTERED_PENDING_REUSE", vr=2, vc=0), target_id=42).status, "BLOCKED")

    def test_partial_candidates_with_full_count_blocked(self):
        self.assertEqual(classify_db_stage("CANDIDATE_INGESTION",
            audit("CANDIDATES_PARTIAL", vc=3), target_id=42).status, "BLOCKED")

    def test_candidates_before_reuse_blocked(self):
        self.assertEqual(classify_db_stage("CANDIDATE_INGESTION",
            audit("CANDIDATES_PARTIAL", vr=1, vc=1), target_id=42).status, "BLOCKED")

    def test_manifest_registered_missing_candidates_blocked(self):
        self.assertEqual(classify_db_stage("MANIFEST_REGISTRATION",
            audit("MANIFEST_REGISTERED", vc=1), target_id=42).status, "BLOCKED")

    def test_malformed_recovery_evidence_blocked(self):
        self.assertEqual(classify_db_stage("SNAPSHOT_REGISTRATION",
            SimpleNamespace(recovery_state="REUSE_COMPLETE", blockers=(), write_authorized=False,
                            target_snapshot_id=42), target_id=42).status, "BLOCKED")

    def test_completed_does_not_verify_finalization(self):
        self.assertEqual(classify_db_stage("FINALIZATION", audit("COMPLETED"), target_id=42).status,
                         "INCOMPLETE")

    def test_blocked_assessment_cannot_verify(self):
        self.assertEqual(classify_db_stage("REUSE_LINKING",
            audit("BLOCKED_REVIEW", blockers=("missing",)), target_id=42).status, "BLOCKED")

    def test_zero_candidate_reuse_complete_may_prove_reuse(self):
        self.assertEqual(classify_db_stage("REUSE_LINKING",
            audit("REUSE_COMPLETE", ec=0, vc=0), target_id=42).status, "VERIFIED")


class ReadOnlyIntegrationTests(TestCase):
    """Mocks only filesystem/DB readers; calls the actual verifier control flow."""

    def test_no_planned_checkpoint_is_incomplete(self):
        result = verify_read_only(stage="PLANNED", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: (None, None, None))
        self.assertEqual(result.status, "INCOMPLETE")
        self.assertFalse(result.write_authorized)

    def test_pinned_revision_drift_blocked(self):
        result = verify_read_only(stage="PLANNED", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: contexts(revision="b"*40))
        self.assertEqual(result.status, "BLOCKED")

    def test_planned_checkpoint_can_be_verified_read_only(self):
        result = verify_read_only(stage="PLANNED", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: contexts())
        self.assertEqual(result.status, "VERIFIED")

    def test_wrong_prepared_parent_blocked(self):
        p, prepared, reg = contexts()
        prepared.planned = SimpleNamespace(repo_id=REPO, target_revision="b"*40)
        result = verify_read_only(stage="PLANNED", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: (p, prepared, reg))
        self.assertEqual(result.status, "BLOCKED")

    def test_missing_registered_is_incomplete(self):
        result = verify_read_only(stage="REUSE_LINKING", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: contexts(with_registered=False))
        self.assertEqual(result.status, "INCOMPLETE")

    def test_valid_recovery_audit_reused(self):
        rr = mock.Mock(return_value=audit("REUSE_COMPLETE", ec=0, vc=0))
        result = verify_read_only(stage="REUSE_LINKING", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: contexts(), recovery_reader=rr)
        self.assertEqual(result.status, "VERIFIED")
        self.assertFalse(result.write_authorized)
        self.assertEqual(rr.call_count, 1)

    def test_recovery_audit_error_blocks(self):
        def bad_reader(**_):
            raise RuntimeError("DB mismatch")
        result = verify_read_only(stage="REUSE_LINKING", repo_id=REPO, target_revision=SHA,
                                  checkpoint_reader=lambda *_: contexts(), recovery_reader=bad_reader)
        self.assertEqual(result.status, "BLOCKED")

    def test_no_writer_flags_available(self):
        # CLI parser must refuse unknown writer flags even before examining target.
        from scripts.tools.incremental_post_execution_verifier import main
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["--target-revision", SHA, "--stage", "ROUTE", "--plan", "--commit"])
        self.assertEqual(cm.exception.code, 2)

    def test_invalid_revision_fails_closed(self):
        with self.assertRaises(ValueError):
            verify_read_only(stage="PLANNED", repo_id=REPO, target_revision="short",
                             checkpoint_reader=lambda *_: contexts())

    def test_manifest_requires_prepared_evidence(self):
        result = verify_read_only(stage="MANIFEST_PREPARATION", repo_id=REPO,
                                  target_revision=SHA,
                                  checkpoint_reader=lambda *_: contexts(with_prepared=False))
        self.assertEqual(result.status, "INCOMPLETE")

    def test_cache_evidence_with_bad_pinning_blocks(self):
        # The independent _check_evidence may reject stale receipts.
        p, prep, reg = contexts()
        with mock.patch("scripts.tools.incremental_runner_contracts._check_evidence",
                        side_effect=RuntimeError("receipt drift")) as chk:
            result = verify_read_only(stage="CACHE_DOWNLOAD", repo_id=REPO,
                    target_revision=SHA, checkpoint_reader=lambda *_: (p,prep,reg),
                    cache_reader=lambda *a, **k: SimpleNamespace(receipts=("x",)))
        self.assertEqual(result.status, "BLOCKED")
        chk.assert_called_once()


if __name__ == "__main__":
    from unittest import main
    main()
