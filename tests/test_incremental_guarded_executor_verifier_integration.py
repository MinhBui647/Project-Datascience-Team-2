"""M4.6.4d.6c.2b.2b.3: read-only Guarded Executor x verifier integration.

Run from project root (PowerShell):
  .\\.venv_v2\\Scripts\\python.exe -m unittest discover -s tests \
      -p 'test_incremental_guarded_executor_verifier_integration.py' -v

No DB/HF requests, no subprocess writes: mocked at the CLI boundary.
"""
from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
from unittest import TestCase, mock

from scripts.tools import incremental_guarded_stage_executor as executor
from scripts.tools.incremental_post_execution_verifier import VerificationResult

REV = "a" * 40
REPO = "Toibinguz/epl-premier-league-dataset"
STAGE = "REUSE_LINKING"
HASH = "b" * 64


def v(status="VERIFIED", stage=STAGE):
    return VerificationResult(stage, status, evidence=("AUDIT: matched",) if status == "VERIFIED" else (),
                              blockers=("Not confirmed",) if status != "VERIFIED" else ())


class EvidenceReviewTests(TestCase):
    def test_verified_evidence_valid(self):
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=lambda **kw: v())
        self.assertEqual(out.status, "VERIFIED")
        self.assertFalse(out.write_authorized)

    def test_incomplete_retained_not_upgraded(self):
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=lambda **kw: v("INCOMPLETE"))
        self.assertEqual(out.status, "INCOMPLETE")

    def test_blocked_retained(self):
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=lambda **kw: v("BLOCKED"))
        self.assertEqual(out.status, "BLOCKED")

    def test_mismatched_stage_fails_closed(self):
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=lambda **kw: v(stage="PLANNED"))
        self.assertEqual(out.status, "BLOCKED")

    def test_non_typed_result_fails_closed(self):
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=lambda **kw: {"status": "VERIFIED"})
        self.assertEqual(out.status, "BLOCKED")

    def test_verifier_exception_fails_closed(self):
        def broken(**_):
            raise RuntimeError("audit unavailable")
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=broken)
        self.assertEqual(out.status, "BLOCKED")
        self.assertIn("audit unavailable", out.blockers[0])

    def test_write_approval_attempt_fails_closed(self):
        def dangerous(**_):
            return VerificationResult(STAGE, "VERIFIED", write_authorized=True)
        out = executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage=STAGE,
                                             root=Path.cwd(), verifier=dangerous)
        self.assertEqual(out.status, "BLOCKED")

    def test_bad_revision_rejected_without_reader_call(self):
        cb = mock.Mock()
        with self.assertRaises(ValueError):
            executor.verify_stage_evidence(repo_id=REPO, target_revision="main", stage=STAGE,
                                           root=Path.cwd(), verifier=cb)
        cb.assert_not_called()

    def test_route_rejected(self):
        with self.assertRaises(ValueError):
            executor.verify_stage_evidence(repo_id=REPO, target_revision=REV, stage="ROUTE",
                                           root=Path.cwd(), verifier=lambda **kw: v())

    def test_render_writes_never_authorized(self):
        text = executor.format_evidence_inspection(v(), preceding_plan_children=2)
        self.assertIn("Write authorization: NO", text)
        self.assertIn("Stage advancement: NOT AUTHORIZED", text)
        self.assertIn("NOT evidence of a write", text)

    def test_exit_codes_distinct(self):
        self.assertEqual([executor.evidence_exit_code(s) for s in
                          ("VERIFIED", "INCOMPLETE", "BLOCKED")], [0, 1, 2])


class CLITests(TestCase):
    def run_cli(self, args, verifier=None):
        lines=[]
        code=executor.main(args, output=lines.append, verifier=verifier)
        return code, "\n".join(lines)

    def test_standalone_verification_no_launcher(self):
        with mock.patch.object(executor, "execute_read_only_plan", side_effect=AssertionError("invoked")), \
             mock.patch.object(executor, "review_one_stage", side_effect=AssertionError("invoked")):
            code, log = self.run_cli(["--verify-stage-evidence", "--stage", STAGE,
                                      "--target-revision", REV], lambda **kw: v())
        self.assertEqual(code, 0)
        self.assertIn("Evidence status: VERIFIED", log)
        self.assertIn("Preceding --plan children: 0", log)

    def test_standalone_incomplete_code(self):
        code, log = self.run_cli(["--verify-stage-evidence", "--stage", STAGE,
                                  "--target-revision", REV], lambda **kw: v("INCOMPLETE"))
        self.assertEqual(code, 1)
        self.assertIn("Evidence status: INCOMPLETE", log)

    def test_standalone_bad_evidence_blocks(self):
        code, log = self.run_cli(["--verify-stage-evidence", "--stage", STAGE,
                                  "--target-revision", REV], lambda **kw: v(stage="PLANNED"))
        self.assertEqual(code, 2)
        self.assertIn("Evidence status: BLOCKED", log)

    def test_plan_mode_cannot_opt_in_to_after_check(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as ctx:
            executor.parse_args(["--plan", "--stage", STAGE, "--target-revision", REV,
                                 "--verify-after-read-only-plan"])
        self.assertEqual(ctx.exception.code, 2)

    def test_evidence_mode_rejects_context_approval(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as ctx:
            executor.parse_args(["--verify-stage-evidence", "--stage", STAGE,
                                 "--target-revision", REV,
                                 "--expected-context-sha256", HASH])
        self.assertEqual(ctx.exception.code, 2)

    def test_evidence_mode_rejects_mutation_flags(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as ctx:
            executor.parse_args(["--verify-stage-evidence", "--stage", STAGE,
                                 "--target-revision", REV, "--commit"])
        self.assertEqual(ctx.exception.code, 2)

    def test_evidence_mode_requires_explicit_stage(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as ctx:
            executor.parse_args(["--verify-stage-evidence", "--target-revision", REV])
        self.assertEqual(ctx.exception.code, 2)

    def test_after_plan_checks_independently_and_no_advancement(self):
        ran=executor.StageResult("READ_ONLY_PLANS_RAN", STAGE, "plan-only", 2, HASH)
        with mock.patch.object(executor, "execute_read_only_plan", return_value=ran) as run:
            code, log=self.run_cli(["--execute-read-only-plan", "--stage", STAGE,
                                    "--approve-stage", STAGE, "--expected-context-sha256", HASH,
                                    "--target-revision", REV, "--verify-after-read-only-plan"],
                                    lambda **kw: v("INCOMPLETE"))
        self.assertEqual(code, 1)
        self.assertIn("Read-only child commands run: 2", log)
        self.assertIn("Evidence status: INCOMPLETE", log)
        self.assertIn("Stage advancement: NOT AUTHORIZED", log)
        run.assert_called_once()

    def test_after_plan_verifier_not_called_if_child_raises(self):
        cb=mock.Mock(return_value=v())
        with mock.patch.object(executor, "execute_read_only_plan", side_effect=RuntimeError("child failed")):
            code, log=self.run_cli(["--execute-read-only-plan", "--stage", STAGE,
                                    "--approve-stage", STAGE, "--expected-context-sha256", HASH,
                                    "--target-revision", REV, "--verify-after-read-only-plan"], cb)
        self.assertEqual(code, 2)
        cb.assert_not_called()
        self.assertIn("BLOCKED", log)

    def test_old_read_only_execution_stays_unchanged(self):
        ran=executor.StageResult("READ_ONLY_PLANS_RAN", STAGE, "plan-only", 1, HASH)
        cb=mock.Mock(return_value=v())
        with mock.patch.object(executor, "execute_read_only_plan", return_value=ran):
            code, log=self.run_cli(["--execute-read-only-plan", "--stage", STAGE,
                                    "--approve-stage", STAGE, "--expected-context-sha256", HASH,
                                    "--target-revision", REV], cb)
        self.assertEqual(code, 0)
        cb.assert_not_called()
        self.assertNotIn("Evidence status:", log)

    def test_after_plan_verified_not_claimed_as_writer(self):
        ran=executor.StageResult("READ_ONLY_PLANS_RAN", STAGE, "plan-only", 1, HASH)
        with mock.patch.object(executor, "execute_read_only_plan", return_value=ran):
            code, log=self.run_cli(["--execute-read-only-plan", "--stage", STAGE,
                                    "--approve-stage", STAGE, "--expected-context-sha256", HASH,
                                    "--target-revision", REV, "--verify-after-read-only-plan"],
                                    lambda **kw: v())
        self.assertEqual(code, 0)
        self.assertIn("Evidence status: VERIFIED", log)
        self.assertIn("NOT evidence of a write", log)

    def test_real_read_only_core_then_verifier_opt_in(self):
        # Exercise the existing core function, not a replacement StageResult stub.
        commands=(("python", "-m", "scripts.tools.safe_plan", "--plan"),)
        fake_review=SimpleNamespace(plan=SimpleNamespace(next_review_stage=STAGE))
        reviewer=mock.Mock(return_value=fake_review)
        launcher=mock.Mock(return_value=SimpleNamespace(returncode=0))
        audit=mock.Mock(return_value=v("INCOMPLETE"))
        output=[]
        with mock.patch.object(executor, "_check_approval") as approval, \
             mock.patch.object(executor, "validate_read_only_commands", return_value=commands):
            code=executor.main(["--execute-read-only-plan", "--stage", STAGE,
                                "--approve-stage", STAGE, "--expected-context-sha256", HASH,
                                "--target-revision", REV, "--verify-after-read-only-plan"],
                               reviewer=reviewer, launcher=launcher, output=output.append,
                               verifier=audit)
        self.assertEqual(code, 1)
        self.assertEqual(reviewer.call_count, 2)  # double fresh re-audit
        self.assertEqual(approval.call_count, 2)
        launcher.assert_called_once()
        self.assertEqual(launcher.call_args.kwargs["shell"], False)
        self.assertEqual(launcher.call_args.kwargs["check"], False)
        audit.assert_called_once()
        self.assertIn("Evidence status: INCOMPLETE", "\n".join(output))

    def test_core_child_nonzero_never_invokes_verifier(self):
        commands=(("python", "-m", "scripts.tools.safe_plan", "--plan"),)
        fake_review=SimpleNamespace(plan=SimpleNamespace(next_review_stage=STAGE))
        reviewer=mock.Mock(return_value=fake_review)
        launcher=mock.Mock(return_value=SimpleNamespace(returncode=9))
        audit=mock.Mock(return_value=v())
        with mock.patch.object(executor, "_check_approval"), \
             mock.patch.object(executor, "validate_read_only_commands", return_value=commands):
            code,log=self.run_cli_with_launch(["--execute-read-only-plan", "--stage", STAGE,
                            "--approve-stage", STAGE, "--expected-context-sha256", HASH,
                            "--target-revision", REV, "--verify-after-read-only-plan"],
                            reviewer, launcher, audit)
        self.assertEqual(code, 2)
        audit.assert_not_called()
        self.assertIn("BLOCKED", log)

    def run_cli_with_launch(self, args, reviewer, launcher, verifier):
        lines=[]
        code=executor.main(args, output=lines.append, reviewer=reviewer,
                           launcher=launcher, verifier=verifier)
        return code, "\n".join(lines)

    def test_verify_mode_does_not_need_approval(self):
        args=executor.parse_args(["--verify-stage-evidence", "--target-revision", REV,
                                  "--stage", STAGE])
        self.assertIsNone(args.approve_stage)
        self.assertFalse(args.execute_read_only_plan)


if __name__ == '__main__':
    import unittest
    unittest.main()
