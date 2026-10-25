"""Read-only CSV orchestration: explicit opt-in and default safety invariants."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.incremental import plan_incremental_orchestrator as mod

REPO = "Toibinguz/epl-premier-league-dataset"
BASE = "a" * 40
TARGET = "b" * 40
CSV = "2026_2027_new/football_data_raw_2627.csv"

class Readers:
    def __init__(self):
        self.calls = []
        self.decision = "PLAN_NEW_REVISION"
        self.raw_decision = "PLAN_RAW_CHANGES"
        self.removed = False
        self.bad_revision = False
    def select_baseline(self, repo):
        self.calls.append(("select_baseline", repo))
        return SimpleNamespace(snapshot_id=7, hf_revision=BASE, raw_scope_file_count=1)
    def load_baseline(self, snapshot, repo):
        return BASE, {"raw_data/base.json": object()}
    def read_target_state(self, repo, rev):
        return None
    def classify_target(self, *args):
        return self.decision
    def load_target(self, repo, rev, *, already_resolved):
        self.calls.append(("load_target", rev, already_resolved))
        if self.removed:
            return TARGET, {CSV: object()}, 0
        return (BASE if self.bad_revision else TARGET), {"raw_data/base.json": object(), CSV: object()}, 0
    def decide_raw_action(self, base, target):
        return self.raw_decision
    def read_db_state(self, snapshot, repo, rev):
        self.calls.append(("read_db_state", snapshot))
        return BASE, {}
    def source_codes(self):
        return {"football_data"}
    def assemble_plan(self, *args, **kw):
        self.calls.append(("assemble_plan", kw))
        return SimpleNamespace(count=lambda name: 0)
    def candidate_actions(self, *args, **kw):
        self.calls.append(("candidate_actions", kw))
        return (SimpleNamespace(path=CSV),)
    def download_context_type(self, *args):
        self.calls.append(("download_context_type", len(args), args[-1]))
        return SimpleNamespace(candidates=(SimpleNamespace(path=CSV),))

class CsvReadOnlyOrchestratorTests(unittest.TestCase):
    def execute(self, reader, *, csv=False):
        with patch.object(mod, "build_planned", return_value=object()) as build:
            outcome = mod.plan_read_only(repo_id=REPO, target_revision=TARGET,
                readers=reader, allow_csv_planning=csv)
        return outcome, build

    def test_default_json_only_calls_keep_old_signatures(self):
        reader = Readers()
        outcome, build = self.execute(reader)
        self.assertEqual(outcome.decision, "PLAN_NEW_REVISION")
        self.assertEqual(next(x[1] for x in reader.calls if x[0] == "assemble_plan"), {})
        self.assertEqual(next(x[1] for x in reader.calls if x[0] == "candidate_actions"), {})
        self.assertEqual(next(x[1] for x in reader.calls if x[0] == "download_context_type"), 8)
        self.assertNotIn("allow_csv_context", build.call_args.kwargs)

    def test_explicit_opt_in_flows_across_all_three_gates(self):
        reader = Readers()
        outcome, build = self.execute(reader, csv=True)
        self.assertEqual(outcome.decision, "PLAN_NEW_REVISION")
        self.assertEqual(next(x[1] for x in reader.calls if x[0] == "assemble_plan"),
                         {"allow_csv_planning": True})
        self.assertEqual(next(x[1] for x in reader.calls if x[0] == "candidate_actions"),
                         {"allow_csv": True})
        self.assertEqual(next(x[1:] for x in reader.calls if x[0] == "download_context_type"),
                         (9, True))
        self.assertIs(build.call_args.kwargs["allow_csv_context"], True)

    def test_reject_non_boolean_opt_in(self):
        with self.assertRaisesRegex(ValueError, "opt-in"):
            mod.plan_read_only(repo_id=REPO, target_revision=TARGET,
                               readers=Readers(), allow_csv_planning=1)

    def test_existing_target_skips_before_hf(self):
        r=Readers(); r.decision="SKIP_ALREADY_PROCESSED"
        result=mod.plan_read_only(repo_id=REPO, target_revision=TARGET,
                                   readers=r,allow_csv_planning=True)
        self.assertEqual(result.decision,"SKIP_ALREADY_PROCESSED")
        self.assertFalse(any(x[0]=="load_target" for x in r.calls))

    def test_no_raw_changes_does_not_plan(self):
        r=Readers(); r.raw_decision="SKIP_NO_RAW_CHANGES"
        result=mod.plan_read_only(repo_id=REPO, target_revision=TARGET,
                                  readers=r,allow_csv_planning=True)
        self.assertEqual(result.decision,"SKIP_NO_RAW_CHANGES")
        self.assertFalse(any(x[0]=="assemble_plan" for x in r.calls))

    def test_removed_path_blocks_before_db(self):
        r=Readers(); r.removed=True
        result=mod.plan_read_only(repo_id=REPO, target_revision=TARGET,
                                  readers=r,allow_csv_planning=True)
        self.assertEqual(result.decision,"BLOCKED_REMOVED_PATHS")
        self.assertFalse(any(x[0]=="read_db_state" for x in r.calls))

    def test_pinned_revision_drift_still_blocked(self):
        r=Readers(); r.bad_revision=True
        with self.assertRaisesRegex(RuntimeError,"Target SHA40 changed"):
            self.execute(r,csv=True)

    def test_cli_opt_in_parses(self):
        args=mod.parse_args(["--target-revision",TARGET,"--plan","--allow-csv-plan-only"])
        self.assertTrue(args.allow_csv_plan_only)

    def test_cli_default_remains_off(self):
        args=mod.parse_args(["--target-revision",TARGET,"--plan"])
        self.assertFalse(args.allow_csv_plan_only)

    def test_cli_main_forwarding_only_when_requested(self):
        for with_flag in (False,True):
            with self.subTest(with_flag=with_flag):
                argv=["--target-revision",TARGET,"--plan"]
                if with_flag: argv.append("--allow-csv-plan-only")
                skipped=mod.ReadOnlyOutcome("SKIP_ALREADY_PROCESSED",7,BASE,TARGET,None,None)
                with patch.object(mod,"plan_read_only",return_value=skipped) as caller:
                    self.assertEqual(mod.main(argv,output=lambda line:None),0)
                self.assertEqual(caller.call_args.kwargs.get("allow_csv_planning",False),with_flag)

if __name__ == "__main__": unittest.main()
