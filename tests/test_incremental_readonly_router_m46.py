#!/usr/bin/env python3
"""M4.6.4d.5a offline routing tests: mocked DB/HF; absolutely no writes."""
from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import ast
import importlib.util
import unittest
from unittest import mock

from scripts.tools.incremental_recovery_audit import RecoveryAssessment, audit_recovery
from scripts.tools.incremental_readonly_router import (
    RoutingOutcome, format_routing_report, main, parse_args, route_read_only,
)
from tests.test_plan_incremental_orchestrator_m46 import (
    REPO, TARGET, BASE_REV, fake_readers,
)


def assessment(state="NO_REGISTERED_SNAPSHOT", sid=None, *, blockers=(),
               expected_reuse=0, verified_reuse=0, expected_candidates=0,
               verified_candidates=0):
    return RecoveryAssessment(state, sid, expected_reuse, verified_reuse,
                              expected_candidates, verified_candidates,
                              "MANUAL_REVIEW", False, blockers)


def auditor(result):
    return mock.Mock(return_value=result)


def lightweight_readers(*, latest=365, state=None):
    return SimpleNamespace(
        select_baseline=mock.Mock(return_value=SimpleNamespace(snapshot_id=latest)),
        read_target_state=mock.Mock(return_value=state),
        load_target=mock.Mock(side_effect=AssertionError("HF MUST NOT BE CALLED")),
    )


class ValidationTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.stderr", new=StringIO()); p.start(); self.addCleanup(p.stop)

    def test_missing_plan(self):
        with self.assertRaises(SystemExit): parse_args(["--target-revision", TARGET])
    def test_write_flags_forbidden(self):
        for flag in ("--commit", "--apply", "--download", "--publish", "--register", "--resume"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit):
                parse_args(["--target-revision",TARGET,"--plan",flag])
    def test_non_sha40(self):
        for bad in ("main", "ABC", "A"*40, "f"*41, ""):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                parse_args(["--target-revision",bad,"--plan"])
    def test_negative_baseline(self):
        with self.assertRaises(SystemExit):
            parse_args(["--target-revision",TARGET,"--plan","--baseline-snapshot","-1"])
    def test_empty_repo(self):
        with self.assertRaises(SystemExit):
            parse_args(["--target-revision",TARGET,"--plan","--repo-id"," "])
    def test_accept_valid(self):
        a = parse_args(["--target-revision",TARGET,"--plan"])
        self.assertEqual(a.target_revision,TARGET)
        self.assertIsNone(a.baseline_snapshot)
    def test_explicit_baseline_flag(self):
        a = parse_args(["--target-revision",TARGET,"--plan","--baseline-snapshot","365"])
        self.assertEqual(a.baseline_snapshot,365)
    def test_help_is_lightweight(self):
        with mock.patch("scripts.tools.incremental_readonly_router.default_readers",side_effect=AssertionError):
            with mock.patch("sys.stdout",new=StringIO()), self.assertRaises(SystemExit) as cm:
                parse_args(["--help"])
            self.assertEqual(cm.exception.code,0)
    def test_invalid_repo_before_audit(self):
        a=auditor(assessment())
        with self.assertRaises(ValueError): route_read_only(repo_id="",target_revision=TARGET,
                                                            readers=lightweight_readers(),recovery_auditor=a)
        a.assert_not_called()
    def test_invalid_revision_before_audit(self):
        a=auditor(assessment())
        with self.assertRaises(ValueError): route_read_only(repo_id=REPO,target_revision="main",
                                                            readers=lightweight_readers(),recovery_auditor=a)
        a.assert_not_called()
    def test_invalid_baseline_before_audit(self):
        a=auditor(assessment())
        for bad in (0,-3,True,"365"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                route_read_only(repo_id=REPO,target_revision=TARGET,
                                baseline_snapshot=bad,readers=lightweight_readers(),recovery_auditor=a)
        a.assert_not_called()
    def test_malformed_audit_output_rejected(self):
        with self.assertRaises(RuntimeError):
            route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(),
                            recovery_auditor=lambda **kw: None)
    def test_audit_cannot_authorize_write(self):
        with self.assertRaises(ValueError): replace(assessment(), write_authorized=True)
    def test_router_cannot_authorize_write(self):
        with self.assertRaises(ValueError):
            RoutingOutcome("BLOCKED_RECOVERY_REVIEW",TARGET,assessment(),write_authorized=True)
    def test_invalid_outcome_exposes_planner(self):
        r=fake_readers(); ar=auditor(assessment())
        good=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,recovery_auditor=ar)
        with self.assertRaises(ValueError): replace(good,decision="BLOCKED_RECOVERY_REVIEW")
        with self.assertRaises(ValueError): replace(good,recovery=assessment("PREPARATION_PARTIAL"))
    def test_report_type(self):
        with self.assertRaises(TypeError): format_routing_report(None)


class RouteGateTests(unittest.TestCase):
    def test_completed_current_skips_without_hf(self):
        r=lightweight_readers(latest=365)
        a=auditor(assessment("COMPLETED",365))
        out=route_read_only(repo_id=REPO,target_revision=BASE_REV,readers=r,recovery_auditor=a)
        self.assertEqual(out.decision,"SKIP_ALREADY_CURRENT")
        self.assertIsNone(out.planner)
        r.load_target.assert_not_called()
        a.assert_called_once()
        self.assertIs(a.call_args.kwargs["state_reader"],r.read_target_state)
    def test_completed_historical_skips_without_hf(self):
        r=lightweight_readers(latest=401)
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment("COMPLETED",365)))
        self.assertEqual(out.decision,"SKIP_ALREADY_PROCESSED")
    def test_completed_missing_id_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,"snapshot ID"):
            route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(),
                            recovery_auditor=auditor(assessment("COMPLETED")))
    def test_completed_invalid_latest_id_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,"completed snapshot"):
            route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(latest=-1),
                            recovery_auditor=auditor(assessment("COMPLETED",365)))
    def test_in_progress_recovery_without_hf(self):
        for state in ("REGISTERED_PENDING_REUSE", "REUSE_COMPLETE", "CANDIDATES_PARTIAL",
                      "CANDIDATES_COMPLETE", "MANIFEST_REGISTERED"):
            with self.subTest(state=state):
                r=lightweight_readers()
                result=assessment(state,366,expected_reuse=272,verified_reuse=272,
                                  expected_candidates=14,verified_candidates=5)
                out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                                    recovery_auditor=auditor(result))
                self.assertEqual(out.decision,"RECOVERY_REVIEW_REQUIRED")
                self.assertIsNone(out.planner)
                r.load_target.assert_not_called()
                r.select_baseline.assert_not_called()
    def test_incomplete_prepare_prevents_new_plan(self):
        for state in ("PREPARATION_PARTIAL", "PREPARED_READY"):
            with self.subTest(state=state):
                r=fake_readers()
                out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                                    recovery_auditor=auditor(assessment(state)))
                self.assertEqual(out.decision,"RECOVERY_REVIEW_REQUIRED")
                r.load_target.assert_not_called()
                r.read_target_state.assert_not_called()  # audit owns this read
    def test_inprogress_missing_checkpoint_blocked(self):
        r=lightweight_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment("BLOCKED_REVIEW",366,blockers=("checkpoint missing",))))
        self.assertEqual(out.decision,"BLOCKED_RECOVERY_REVIEW")
        self.assertIn("checkpoint missing",format_routing_report(out))
        r.load_target.assert_not_called()
    def test_untrusted_state_blocked(self):
        r=lightweight_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment("STRANGE_STATE",366)))
        self.assertEqual(out.decision,"BLOCKED_UNKNOWN_RECOVERY_STATE")
    def test_explicit_baseline_not_used_during_recovery(self):
        r=lightweight_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,baseline_snapshot=365,
                            readers=r,recovery_auditor=auditor(assessment("CANDIDATES_PARTIAL",366)))
        self.assertEqual(out.decision,"BLOCKED_EXPLICIT_BASELINE_RECOVERY")
        r.select_baseline.assert_not_called()
    def test_explicit_baseline_during_local_preparation_blocked(self):
        r=lightweight_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,baseline_snapshot=365,
                            readers=r,recovery_auditor=auditor(assessment("PREPARED_READY")))
        self.assertTrue(out.decision.startswith("BLOCKED_"))
    def test_absent_checkpoints_blocked(self):
        r=fake_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment("BLOCKED_REVIEW",None,blockers=("invalid planned checkpoint",))))
        self.assertEqual(out.decision,"BLOCKED_RECOVERY_REVIEW")
        r.load_target.assert_not_called()
    def test_absent_with_unexpected_sid_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"unexpected target ID"):
            route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(),
                            recovery_auditor=auditor(assessment("NO_REGISTERED_SNAPSHOT",99)))
    def test_absent_with_blockers_blocked(self):
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(),
                            recovery_auditor=auditor(assessment(blockers=("odd",))))
        self.assertEqual(out.decision,"BLOCKED_ABSENCE_WITH_BLOCKERS")
    def test_absent_new_revision(self):
        r=fake_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"PLAN_NEW_REVISION")
        self.assertEqual(out.planner.planned.expected_files,2)
        self.assertEqual(r.read_target_state.call_count,2)  # planner gate + postcheck
        self.assertEqual(r.load_target.call_count,1)
        self.assertIn("--plan",format_routing_report(out))
    def test_absent_no_raw_changes(self):
        r=fake_readers(raw="SKIP_NO_RAW_CHANGES")
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"SKIP_NO_RAW_CHANGES")
        r.read_db_state.assert_not_called()
    def test_absent_new_plan_explicit_baseline(self):
        r=fake_readers()
        out=route_read_only(repo_id=REPO,target_revision=TARGET,baseline_snapshot=365,
                            readers=r,recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"PLAN_NEW_REVISION")
        r.select_baseline.assert_not_called()
    def test_target_registers_before_new_planner_gate(self):
        r=fake_readers(decision="RECOVERY_REQUIRED",target_state=SimpleNamespace(snapshot_id=366,status="in_progress"))
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"BLOCKED_PLANNING_GATE")
        r.load_target.assert_not_called()
    def test_target_registers_while_planning(self):
        r=fake_readers()
        r.read_target_state.side_effect=[None,SimpleNamespace(snapshot_id=366,status="in_progress")]
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"BLOCKED_TARGET_STATE_CHANGED")
        self.assertIsNone(out.planner)
        self.assertIn("rerun audit",out.explanation)
    def test_target_completed_before_new_planner_gate(self):
        r=fake_readers(decision="SKIP_ALREADY_PROCESSED")
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"SKIP_ALREADY_PROCESSED")
        r.load_target.assert_not_called()
    def test_planner_blocked_removal_not_exposed(self):
        r=fake_readers(raw="BLOCKED_METADATA_REVIEW")
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"BLOCKED_PLANNING_GATE")
        self.assertIsNone(out.planner)
    def test_planner_unknown_gate_not_exposed(self):
        r=fake_readers(raw="UNRECOGNIZED")
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                            recovery_auditor=auditor(assessment()))
        self.assertEqual(out.decision,"BLOCKED_PLANNING_GATE")
    def test_audit_exception_prevents_hf(self):
        r=fake_readers()
        bad=mock.Mock(side_effect=RuntimeError("database not reachable"))
        with self.assertRaisesRegex(RuntimeError,"not reachable"):
            route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,recovery_auditor=bad)
        r.load_target.assert_not_called()
    def test_read_only_report_for_recovery(self):
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(),
                            recovery_auditor=auditor(assessment("CANDIDATES_PARTIAL",366,
                                 expected_candidates=14,verified_candidates=5)))
        report=format_routing_report(out)
        for part in ("CANDIDATES_PARTIAL","5/14","PostgreSQL writes: NONE", "Write authorized: NO"):
            self.assertIn(part,report)
        self.assertNotIn("--commit",report)
    def test_read_only_report_for_current(self):
        out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=lightweight_readers(),
                            recovery_auditor=auditor(assessment("COMPLETED",365)))
        report=format_routing_report(out)
        self.assertIn("SKIP_ALREADY_CURRENT",report)
        self.assertIn("not re-audited",report)
    def test_no_child_subprocess_interfaces(self):
        tree=ast.parse(Path(importlib.util.find_spec("scripts.tools.incremental_readonly_router").origin).read_text(encoding="utf-8"))
        imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        self.assertNotIn("subprocess",imports)
        names=[alias.name for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names]
        self.assertNotIn("subprocess",names)
    def test_absent_with_real_audit_enters_planner(self):
        # Real audit sees neither a target nor checkpoint; planner rechecks.
        r=fake_readers()
        with mock.patch("scripts.tools.incremental_recovery_audit.read_checkpoints",
                        return_value=(None,None,None)):
            out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                                recovery_auditor=audit_recovery)
        self.assertEqual(out.decision,"PLAN_NEW_REVISION")
        self.assertEqual(r.read_target_state.call_count,3)  # audit + planner + postcheck

    def test_inprogress_with_real_audit_and_no_checkpoint_is_blocked(self):
        state=SimpleNamespace(snapshot_id=366,status="in_progress")
        r=lightweight_readers(state=state)
        with mock.patch("scripts.tools.incremental_recovery_audit.read_checkpoints",
                        return_value=(None,None,None)):
            out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                                recovery_auditor=audit_recovery)
        self.assertEqual(out.decision,"BLOCKED_RECOVERY_REVIEW")
        self.assertIn("registered checkpoint",format_routing_report(out))
        r.load_target.assert_not_called()

    def test_preparation_partial_with_real_audit_never_calls_hf(self):
        source=fake_readers()
        pinned=route_read_only(repo_id=REPO,target_revision=TARGET,readers=source,
                               recovery_auditor=auditor(assessment())).planner.planned
        r=lightweight_readers(state=None)
        with mock.patch("scripts.tools.incremental_recovery_audit.read_checkpoints",
                        return_value=(pinned,None,None)):
            out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,
                                recovery_auditor=audit_recovery)
        self.assertEqual(out.recovery.recovery_state,"PREPARATION_PARTIAL")
        self.assertEqual(out.decision,"RECOVERY_REVIEW_REQUIRED")
        r.load_target.assert_not_called()

    def test_completed_with_real_audit_skips_db_evidence(self):
        # Exercises both real module interfaces; all DB/HF functions mocked.
        r=lightweight_readers(latest=365,state=SimpleNamespace(snapshot_id=365,status="completed"))
        with mock.patch("scripts.tools.incremental_recovery_audit.read_db_evidence",side_effect=AssertionError):
            out=route_read_only(repo_id=REPO,target_revision=TARGET,readers=r,recovery_auditor=audit_recovery)
        self.assertEqual(out.decision,"SKIP_ALREADY_CURRENT")
        r.read_target_state.assert_called_once_with(REPO,TARGET)


class MainTests(unittest.TestCase):
    def test_completed_returns_zero(self):
        lines=[]
        rc=main(["--target-revision",TARGET,"--plan"],readers=lightweight_readers(),
                recovery_auditor=auditor(assessment("COMPLETED",365)),output=lines.append)
        self.assertEqual(rc,0);self.assertIn("SKIP_ALREADY_CURRENT",lines[0])
    def test_recovery_returns_zero_as_review_not_write(self):
        lines=[]
        rc=main(["--target-revision",TARGET,"--plan"],readers=lightweight_readers(),
                recovery_auditor=auditor(assessment("REUSE_COMPLETE",366)),output=lines.append)
        self.assertEqual(rc,0);self.assertIn("MANUAL REVIEW",lines[0])
    def test_blocked_returns_two(self):
        lines=[]
        rc=main(["--target-revision",TARGET,"--plan"],readers=lightweight_readers(),
                recovery_auditor=auditor(assessment("BLOCKED_REVIEW",366,blockers=("bad file",))),
                output=lines.append)
        self.assertEqual(rc,2);self.assertIn("BLOCKED (NO WRITES)",lines[0])
    def test_new_returns_zero(self):
        lines=[]
        rc=main(["--target-revision",TARGET,"--plan"],readers=fake_readers(),
                recovery_auditor=auditor(assessment()),output=lines.append)
        self.assertEqual(rc,0);self.assertIn("PLAN_NEW_REVISION",lines[0])
    def test_race_returns_two(self):
        lines=[]
        r=fake_readers();r.read_target_state.side_effect=[None,SimpleNamespace(snapshot_id=366,status="in_progress")]
        rc=main(["--target-revision",TARGET,"--plan"],readers=r,
                recovery_auditor=auditor(assessment()),output=lines.append)
        self.assertEqual(rc,2)
    def test_unknown_state_returns_two(self):
        rc=main(["--target-revision",TARGET,"--plan"],readers=lightweight_readers(),
                recovery_auditor=auditor(assessment("BOGUS")),output=lambda _:None)
        self.assertEqual(rc,2)


if __name__ == "__main__":
    unittest.main()
