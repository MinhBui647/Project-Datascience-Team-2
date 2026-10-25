"""A2 isolated tests: no HF requests, no PostgreSQL connections, no writer invocation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "scripts/orchestration/unified_incremental_runner.py"
spec = importlib.util.spec_from_file_location("unified_incremental_runner_a2", MODULE)
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
SHA = "a" * 40


def result(state, *, stage=None, action="Review stage", **extra):
    val = dict(state=state, repo_id=mod.DEFAULT_REPO, requested_ref="main",
               target_revision=SHA, next_review_stage=stage, next_action=action,
               write_authorized=False, write_executed=False,
               retry_authorized=False, stage_advanced=False)
    val.update(extra)
    return json.dumps(val)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = self.root / "scripts/orchestration/end_to_end_readonly.py"
        p.parent.mkdir(parents=True)
        p.write_text("# stub not executed\n")

    def run_check(self, state="SKIPPED_CURRENT", stage=None, code=0, **extra):
        def launcher(argv, **kwargs):
            self.assertEqual(argv[1:5], ["-B", "-m", "scripts.orchestration.end_to_end_readonly", "--check"])
            self.assertFalse(kwargs["shell"])
            self.assertFalse(kwargs["check"])
            self.assertEqual(kwargs["cwd"], str(self.root))
            self.assertIn("default_transaction_read_only=on", kwargs["env"]["PGOPTIONS"])
            self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            return subprocess.CompletedProcess(argv, code, result(state, stage=stage, **extra), "")
        return mod.run_once(root=self.root, launcher=launcher)

    def test_nochange(self):
        r, code = self.run_check()
        self.assertEqual((r.state, code), ("SKIPPED_CURRENT", 0))
        self.assertIsNone(r.next_review_stage)
        self.assertFalse(r.write_executed)

    def test_checkpoint_review(self):
        r, code = self.run_check("REVIEW_CHECKPOINT", stage="PLANNED")
        self.assertEqual((r.state, r.next_review_stage, code), ("REVIEW_CHECKPOINT", "PLANNED", 0))

    def test_preparation_review(self):
        r, code = self.run_check("REVIEW_PREPARATION", stage="MANIFEST_PREPARATION")
        self.assertEqual((r.state, code), ("REVIEW_PREPARATION", 0))

    def test_db_write_is_never_executed(self):
        r, code = self.run_check("REVIEW_DB_WRITE", stage="CANDIDATE_INGESTION")
        self.assertEqual((r.state, code), ("REVIEW_DB_WRITE", 0))
        self.assertFalse(r.write_authorized)
        self.assertFalse(r.write_executed)

    def test_known_blocked_returns_2(self):
        r, code = self.run_check("ROUTER_ALIGNMENT_REQUIRED", code=2)
        self.assertEqual((r.state, code), ("BLOCKED_REVIEW", 2))

    def test_unknown_state_success_exit_blocks(self):
        r, code = self.run_check("SOMETHING_NEW", code=0)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_identity_drift_blocks(self):
        r, code = self.run_check(repo_id="other/repo")
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_write_claim_blocks(self):
        r, code = self.run_check(write_executed=True)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_missing_flag_blocks(self):
        payload = json.loads(result("SKIPPED_CURRENT")); del payload["stage_advanced"]
        launcher = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        r, code = mod.run_once(root=self.root, launcher=launcher)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_bogus_json_blocks(self):
        launcher = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "not json", "")
        r, code = mod.run_once(root=self.root, launcher=launcher)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_stdout_over_budget_blocks(self):
        launcher = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "z" * 600000, "")
        r, code = mod.run_once(root=self.root, launcher=launcher)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_timeout_blocks(self):
        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)
        r, code = mod.run_once(root=self.root, launcher=timeout)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_launch_exception_blocks(self):
        def oops(argv, **kw):
            raise OSError("subprocess failed")
        r, code = mod.run_once(root=self.root, launcher=oops)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_receipt_persists(self):
        r, code = self.run_check()
        path = self.root / "reports/m5_scheduler/runs" / (r.run_id + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((data["state"], data["target_revision"]), ("SKIPPED_CURRENT", SHA))
        self.assertFalse(data["write_authorized"])

    def test_lock_does_not_get_removed(self):
        self.run_check()
        self.assertTrue((self.root / "reports/m5_scheduler/single_instance.lock").is_file())

    def test_second_instance_blocks(self):
        path = self.root / "reports/m5_scheduler/single_instance.lock"
        with mod.local_process_lock(path):
            with self.assertRaisesRegex(RuntimeError, "Another scheduler instance"):
                with mod.local_process_lock(path):
                    pass
        # An OS-held lock is released even when lock file remains.
        with mod.local_process_lock(path):
            pass

    def test_concurrent_check_fails_closed(self):
        path = self.root / "reports/m5_scheduler/single_instance.lock"
        with mod.local_process_lock(path):
            r, code = mod.run_once(root=self.root, launcher=lambda *a, **kw: self.fail("launcher called"))
        self.assertEqual((r.state, code), ("BLOCKED", 2))
        self.assertIn("active", r.detail)

    def test_input_root_validation_prevents_subprocess(self):
        with self.assertRaisesRegex(ValueError, "lacks"):
            mod.run_once(root=self.root / "scripts", launcher=lambda *a, **kw: self.fail("launcher"))

    def test_reject_invalid_baseline(self):
        with self.assertRaises(ValueError):
            mod.run_once(root=self.root, baseline_snapshot=0, launcher=lambda *a, **kw: self.fail("launcher"))

    def test_symlink_reports_refused(self):
        target = self.root / "external"
        target.mkdir()
        try:
            (self.root / "reports").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            mod.run_once(root=self.root, launcher=lambda *a, **kw: self.fail("launcher"))

    def test_no_baseline_on_schedule(self):
        self.assertNotIn("--baseline-snapshot", mod._child_argv(mod.DEFAULT_REPO, "main", None))

    def test_explicit_baseline(self):
        argv = mod._child_argv(mod.DEFAULT_REPO, "main", 367)
        self.assertEqual(argv[-2:], ["--baseline-snapshot", "367"])

    def test_allowlist_no_writer_mode(self):
        argv = mod._child_argv(mod.DEFAULT_REPO, "main", None)
        for flag in ("--apply", "--commit", "--download", "--promote", "--publish", "--execute-one-stage"):
            self.assertNotIn(flag, argv)

    def test_invalid_review_stage_blocks(self):
        r, code = self.run_check("REVIEW_PREPARATION", stage=None)
        self.assertEqual((r.state, code), ("BLOCKED", 2))

    def test_nochange_with_stage_blocks(self):
        r, code = self.run_check("SKIPPED_CURRENT", stage="PLANNED")
        self.assertEqual((r.state, code), ("BLOCKED", 2))


if __name__ == "__main__":
    unittest.main()
