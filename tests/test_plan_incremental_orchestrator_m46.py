#!/usr/bin/env python3
"""M4.6.4d.3 offline orchestrator tests. No DB/HF, subprocess or filesystem writes."""
from __future__ import annotations

from dataclasses import replace
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.tools.plan_incremental_orchestrator import (
    ReadOnlyOutcome, plan_read_only, preview_only_commands, format_report,
    parse_args, main,
)
from scripts.tools.incremental_execution_context import PlannedContext

REPO = "Toibinguz/epl-premier-league-dataset"
BASE_REV = "1" * 40
TARGET = "2" * 40
FINGERPRINT = "f" * 64
A = "2026_2027_new/fpl_fixtures_latest.json"
B = "raw_data/fotmob/fotmob_test.json"
C = "raw_data/understat/old.csv"


def a_action():
    return SimpleNamespace(path=A, category="CHANGED", kind="UPSERT_VERSION_AFTER_REHASH",
                           file_format="json", expected_bytes=10, source_code="fpl",
                           git_blob_id="a" * 40, lfs_sha256=None)


def b_action():
    return SimpleNamespace(path=B, category="NEW", kind="UPSERT_VERSION_AFTER_REHASH",
                           file_format="json", expected_bytes=20, source_code="fotmob",
                           git_blob_id=None, lfs_sha256="b" * 64)


def c_action():
    return SimpleNamespace(path=C, category="UNCHANGED", kind="REUSE_FILE_ID",
                           file_format=None, expected_bytes=200, source_code=None,
                           git_blob_id="a" * 40, lfs_sha256=None)


def fake_plan():
    acts = (a_action(), b_action(), c_action())
    return SimpleNamespace(actions=acts, target_raw_count=3, candidate_bytes=30,
                           baseline_records=100, reused_records=80,
                           superseded_records=20, fingerprint=FINGERPRINT,
                           count=lambda name: sum(x.category == name for x in acts))


def fake_readers(*, decision="PLAN_NEW_REVISION", raw="PLAN_RAW_CHANGES", target_state=None):
    base = {A: SimpleNamespace(size=9), C: SimpleNamespace(size=200)}
    target = {A: SimpleNamespace(size=10, git_blob_id="a" * 40, lfs_sha256=None),
              B: SimpleNamespace(size=20, git_blob_id=None, lfs_sha256="b" * 64),
              C: SimpleNamespace(size=200, git_blob_id="a" * 40, lfs_sha256=None)}
    state = target_state
    if state is None and decision in ("SKIP_ALREADY_CURRENT", "SKIP_ALREADY_PROCESSED", "RECOVERY_REQUIRED"):
        state = SimpleNamespace(snapshot_id=365 if decision == "SKIP_ALREADY_CURRENT" else 366,
                                status="completed" if decision.startswith("SKIP_") else "in_progress")
    r = SimpleNamespace(
        base=base, target=target, selection=SimpleNamespace(snapshot_id=365, hf_revision=BASE_REV,
                                                           raw_scope_file_count=2),
        select_baseline=mock.Mock(side_effect=lambda repo: r.selection),
        load_baseline=mock.Mock(side_effect=lambda sid,repo: (BASE_REV,r.base)),
        read_target_state=mock.Mock(return_value=state),
        classify_target=mock.Mock(return_value=decision),
        load_target=mock.Mock(side_effect=lambda repo,rev,**kw: (rev,r.target,1)),
        decide_raw_action=mock.Mock(return_value=raw),
        read_db_state=mock.Mock(return_value=(BASE_REV, {})),
        source_codes=mock.Mock(return_value={"fpl","fotmob"}),
        assemble_plan=mock.Mock(return_value=fake_plan()),
        candidate_actions=mock.Mock(side_effect=lambda plan,target: tuple(x for x in plan.actions if x.category in ("NEW","CHANGED"))),
        download_context_type=lambda *args: SimpleNamespace(
            repo_id=args[0], baseline_snapshot=args[1], baseline_revision=args[2],
            target_revision=args[3], plan=args[4], target=args[5], actions=args[6], excluded=args[7]),
    )
    return r

def readers(**kwargs):
    return fake_readers(**kwargs)


class ArgumentTests(unittest.TestCase):
    def setUp(self):
        stderr_patch = mock.patch("sys.stderr", new=StringIO())
        stderr_patch.start()
        self.addCleanup(stderr_patch.stop)

    def test_reject_missing_plan(self):
        with self.assertRaises(SystemExit): parse_args(["--target-revision",TARGET])
    def test_reject_short_revision(self):
        with self.assertRaises(SystemExit): parse_args(["--target-revision","main","--plan"])
    def test_reject_uppercase_revision(self):
        with self.assertRaises(SystemExit): parse_args(["--target-revision",TARGET.upper().replace("2","A"),"--plan"])
    def test_reject_zero_baseline(self):
        with self.assertRaises(SystemExit): parse_args(["--baseline-snapshot","0","--target-revision",TARGET,"--plan"])
    def test_reject_negative_baseline(self):
        with self.assertRaises(SystemExit): parse_args(["--baseline-snapshot","-9","--target-revision",TARGET,"--plan"])
    def test_reject_blank_repo(self):
        with self.assertRaises(SystemExit): parse_args(["--repo-id"," ","--target-revision",TARGET,"--plan"])
    def test_reject_write_flags(self):
        for flag in ("--commit","--apply","--download","--publish","--register"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit):
                parse_args(["--target-revision",TARGET,"--plan",flag])
    def test_accept_auto_baseline(self):
        args = parse_args(["--target-revision",TARGET,"--plan"])
        self.assertIsNone(args.baseline_snapshot)
    def test_accept_explicit_baseline(self):
        args = parse_args(["--target-revision",TARGET,"--baseline-snapshot","365","--plan"])
        self.assertEqual(args.baseline_snapshot,365)
    def test_help_does_not_initialize_readers(self):
        with mock.patch("scripts.tools.plan_incremental_orchestrator.default_readers", side_effect=AssertionError):
            with self.assertRaises(SystemExit) as cm, mock.patch("sys.stdout",new=StringIO()):
                parse_args(["--help"])
            self.assertEqual(cm.exception.code,0)


class GateTests(unittest.TestCase):
    def test_skip_current_no_hf(self):
        r=readers(decision="SKIP_ALREADY_CURRENT")
        result=plan_read_only(repo_id=REPO,target_revision=BASE_REV,readers=r)
        self.assertEqual(result.decision,"SKIP_ALREADY_CURRENT")
        r.load_target.assert_not_called()
        r.read_db_state.assert_not_called()
    def test_skip_historical_no_hf(self):
        r=readers(decision="SKIP_ALREADY_PROCESSED")
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"SKIP_ALREADY_PROCESSED")
        r.load_target.assert_not_called()
    def test_recovery_no_hf(self):
        r=readers(decision="RECOVERY_REQUIRED")
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"RECOVERY_REQUIRED")
        r.load_target.assert_not_called()
    def test_blocked_target_no_hf(self):
        r=readers(decision="BLOCKED_EXISTING_TARGET_STATE")
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"BLOCKED_EXISTING_TARGET_STATE")
        r.load_target.assert_not_called()
    def test_no_raw_changes_skips_db_sync_plan(self):
        r=readers(raw="SKIP_NO_RAW_CHANGES")
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"SKIP_NO_RAW_CHANGES")
        r.read_db_state.assert_not_called()
        r.assemble_plan.assert_not_called()
    def test_review_skips_db_sync_plan(self):
        r=readers(raw="BLOCKED_METADATA_REVIEW")
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"BLOCKED_METADATA_REVIEW")
        r.read_db_state.assert_not_called()
    def test_removed_path_blocked_before_sync(self):
        r=readers()
        r.base["raw_data/removed.json"]=SimpleNamespace(size=1)
        r.selection.raw_scope_file_count=3
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"BLOCKED_REMOVED_PATHS")
        r.read_db_state.assert_not_called()
    def test_invalid_preflight_rejected(self):
        r=readers(raw="UNKNOWN")
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertEqual(result.decision,"UNKNOWN")
    def test_hf_revision_drift(self):
        r=readers()
        r.load_target=mock.Mock(return_value=("3"*40,r.target,1))
        with self.assertRaisesRegex(RuntimeError,"SHA40"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
    def test_wrong_baseline_db_revision(self):
        r=readers()
        r.read_db_state=mock.Mock(return_value=("3"*40,{}))
        with self.assertRaisesRegex(RuntimeError,"Baseline revision drifted"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
    def test_selected_baseline_revision_drift(self):
        r=readers()
        r.selection.hf_revision="4"*40
        with self.assertRaisesRegex(RuntimeError,"Baseline selection"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        r.read_target_state.assert_not_called()
    def test_selected_baseline_file_drift(self):
        r=readers()
        r.selection.raw_scope_file_count=99
        with self.assertRaisesRegex(RuntimeError,"Baseline selection"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
    def test_explicit_baseline_does_not_autoselect(self):
        r=readers()
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,baseline_snapshot=42,readers=r)
        self.assertEqual(result.baseline_snapshot_id,42)
        r.select_baseline.assert_not_called()
    def test_target_new_state_inconsistent(self):
        r=readers(target_state=SimpleNamespace(snapshot_id=366,status="in_progress"))
        with self.assertRaisesRegex(RuntimeError,"state gate"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        r.load_target.assert_not_called()
    def test_invalid_excluded(self):
        r=readers()
        r.load_target=mock.Mock(return_value=(TARGET,r.target,-1))
        with self.assertRaisesRegex(RuntimeError,"inventory"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
    def test_baseline_empty(self):
        r=readers();r.base.clear()
        with self.assertRaisesRegex(RuntimeError,"no Raw"):
            plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
    def test_invalid_requested_sha(self):
        r=readers()
        with self.assertRaises(ValueError):
            plan_read_only(repo_id=REPO,target_revision="main",readers=r)
        r.select_baseline.assert_not_called()
    def test_invalid_baseline_zero(self):
        r=readers()
        with self.assertRaises(ValueError):
            plan_read_only(repo_id=REPO,target_revision=TARGET,baseline_snapshot=0,readers=r)
    def test_empty_repo(self):
        with self.assertRaises(ValueError):
            plan_read_only(repo_id="",target_revision=TARGET,readers=readers())


class ContextTests(unittest.TestCase):
    def test_build_planned_context(self):
        r=readers()
        outcome=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=r)
        self.assertIsInstance(outcome.planned,PlannedContext)
        p=outcome.planned
        self.assertEqual((p.expected_full_files,p.expected_raw_files,p.expected_baseline_files),(4,3,2))
        self.assertEqual((p.expected_reuse,p.expected_files,p.expected_bytes),(1,2,30))
        self.assertEqual((p.expected_baseline_records,p.expected_reused_records),(100,80))
        self.assertEqual([c.path for c in p.candidates],[A,B])
    def test_no_write_interfaces_created(self):
        p=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=readers()).planned
        cmds=preview_only_commands(p)
        self.assertEqual(len(cmds),2)
        for argv in cmds:
            self.assertEqual(argv[-1],"--plan")
            self.assertNotIn("--commit",argv)
            self.assertNotIn("--publish",argv)
            self.assertNotIn("--download",argv)
            self.assertNotIn("--apply",argv)
    def test_preview_flags_match_context(self):
        p=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=readers()).planned
        a,b=preview_only_commands(p)
        self.assertIn("--expected-full-files",a)
        self.assertIn("--expected-raw-files",a)
        self.assertIn("--expected-fingerprint",b)
        self.assertIn(FINGERPRINT,b)
    def test_report_contains_relevant_counts(self):
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=readers())
        out=format_report(result)
        self.assertIn("REUSE: 1 | candidates: 2 | candidate bytes: 30",out)
        self.assertIn("Checkpoints written: NONE",out)
        self.assertIn("NOT WRITE APPROVAL",out)
        self.assertNotIn("--confirm-cache-only",out)
    def test_recovery_reporting(self):
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=readers(decision="RECOVERY_REQUIRED"))
        out=format_report(result)
        self.assertIn("separate guarded recovery",out)
        self.assertIn("BLOCKED",out)
    def test_skip_reporting(self):
        result=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=readers(decision="SKIP_ALREADY_PROCESSED"))
        self.assertIn("NO INGESTION REQUIRED",format_report(result))
    def test_bad_outcome_conflicts(self):
        planned=plan_read_only(repo_id=REPO,target_revision=TARGET,readers=readers()).planned
        with self.assertRaises(ValueError):
            ReadOnlyOutcome("SKIP_ALREADY_CURRENT",365,BASE_REV,TARGET,365,planned)
    def test_missing_planned_for_new(self):
        with self.assertRaises(ValueError):
            ReadOnlyOutcome("PLAN_NEW_REVISION",365,BASE_REV,TARGET,None,None)
    def test_preview_requires_planned(self):
        with self.assertRaises(TypeError): preview_only_commands(None)
    def test_format_requires_outcome(self):
        with self.assertRaises(TypeError): format_report(None)


class MainTests(unittest.TestCase):
    def test_skip_main_returns_zero(self):
        lines=[]
        r=readers(decision="SKIP_ALREADY_PROCESSED")
        status=main(["--target-revision",TARGET,"--plan"],readers=r,output=lines.append)
        self.assertEqual(status,0)
        self.assertIn("SKIP_ALREADY_PROCESSED",lines[0])
    def test_new_main_returns_zero(self):
        lines=[]
        status=main(["--target-revision",TARGET,"--plan"],readers=readers(),output=lines.append)
        self.assertEqual(status,0)
        self.assertIn("PLAN_NEW_REVISION",lines[0])
    def test_recovery_main_returns_two(self):
        lines=[]
        status=main(["--target-revision",TARGET,"--plan"],readers=readers(decision="RECOVERY_REQUIRED"),output=lines.append)
        self.assertEqual(status,2)
    def test_blocked_main_returns_two(self):
        lines=[]
        status=main(["--target-revision",TARGET,"--plan"],readers=readers(raw="BLOCKED_METADATA_REVIEW"),output=lines.append)
        self.assertEqual(status,2)
    def test_explicit_snapshot_main(self):
        r=readers()
        main(["--target-revision",TARGET,"--baseline-snapshot","365","--plan"],readers=r,output=lambda _: None)
        r.select_baseline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
