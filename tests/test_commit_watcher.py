import contextlib
import io
import subprocess
import sys
import unittest
from types import SimpleNamespace

from scripts.orchestration.commit_watcher import (
    watch_once, report_dict,
)
from scripts.orchestration.watch_cli import parse_args


SHA = "a" * 40
REPO = "sample/epl-dataset"


def sample_plan(**overrides):
    values = {
        "repo_id": REPO,
        "target_revision": SHA,
        "decision": "PLAN",
        "recovery_state": "NONE",
        "next_review_stage": "PREPARE",
        "blockers": (),
        "stages": ("PREPARE",),
        "read_only_previews": (),
        "planned_context_sha256": "b" * 64,
        "write_authorized": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class CommitWatcherTests(unittest.TestCase):

    def observe(self, *, ref="main", plan=None,
                resolver=None, repo_id=REPO, baseline=None):
        if resolver is None:
            resolver = lambda repo, reference: SHA
        if plan is None:
            plan = sample_plan()

        return watch_once(
            repo_id=repo_id,
            revision_ref=ref,
            baseline_snapshot=baseline,
            resolver=resolver,
            planner=lambda **kwargs: plan,
        )

    def test_pinned_sha_bypasses_resolver(self):
        def forbidden(*args):
            self.fail("Resolver must not run")

        result = self.observe(ref=SHA, resolver=forbidden)

        self.assertFalse(result.resolution_performed)
        self.assertEqual(result.pinned_revision, SHA)

    def test_branch_resolution_once(self):
        calls = []

        def resolver(repo, ref):
            calls.append((repo, ref))
            return SHA

        result = self.observe(resolver=resolver)
        self.assertTrue(result.resolution_performed)
        self.assertEqual(calls, [(REPO, "main")])

    def test_pr_ref_resolution(self):
        refs = []

        def resolver(repo, ref):
            refs.append(ref)
            return SHA

        self.observe(ref="refs/pr/12", resolver=resolver)
        self.assertEqual(refs, ["refs/pr/12"])

    def test_skip_requires_explicit_m4_decision(self):
        result = self.observe(
            plan=sample_plan(
                decision="SKIPPED",
                next_review_stage=None,
                stages=(),
            )
        )
        self.assertEqual(result.state, "SKIPPED_BY_M4")

    def test_blockers_fail_closed(self):
        result = self.observe(
            plan=sample_plan(blockers=("Evidence missing",))
        )
        self.assertEqual(result.state, "BLOCKED")

    def test_recovery_blocked_cannot_be_skipped(self):
        result = self.observe(
            plan=sample_plan(
                decision="SKIPPED",
                recovery_state="BLOCKED",
                next_review_stage=None,
                stages=(),
            )
        )
        self.assertEqual(result.state, "BLOCKED")

    def test_unknown_decision_not_called_completed(self):
        result = self.observe(
            plan=sample_plan(
                decision="UNKNOWN",
                next_review_stage=None,
                stages=(),
            )
        )
        self.assertEqual(result.state, "UNRESOLVED")

    def test_normal_stage_requires_review(self):
        result = self.observe()
        self.assertEqual(result.state, "REVIEW_REQUIRED")
        self.assertFalse(result.controller.m5_write_executed)

    def test_invalid_ref_rejected(self):
        with self.assertRaises(ValueError):
            self.observe(ref="main;unsafe")

    def test_invalid_repo_rejected(self):
        with self.assertRaises(ValueError):
            self.observe(repo_id="bad repo")

    def test_invalid_baseline_rejected(self):
        with self.assertRaises(ValueError):
            self.observe(baseline=True)

    def test_invalid_resolved_sha_rejected(self):
        with self.assertRaises(ValueError):
            self.observe(resolver=lambda repo, ref: "main")

    def test_m4_target_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.observe(
                plan=sample_plan(target_revision="c" * 40)
            )

    def test_m4_write_flag_does_not_authorize_m5(self):
        result = self.observe(
            plan=sample_plan(
                decision="SKIPPED",
                write_authorized=True,
                next_review_stage=None,
                stages=(),
            )
        )
        self.assertEqual(result.state, "UNRESOLVED")
        self.assertFalse(result.controller.m5_write_executed)
        self.assertFalse(report_dict(result)["controller"]["m5_write_executed"])

    def test_cli_help(self):
        result = subprocess.run(
            [
                sys.executable, "-B", "-m",
                "scripts.orchestration.watch_cli",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--revision-ref", result.stdout)
        self.assertIn("--check", result.stdout)

    def test_cli_requires_explicit_check(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                parse_args(["--repo-id", REPO])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
