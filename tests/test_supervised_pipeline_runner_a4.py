"""A4 offline contracts: no HF, PostgreSQL or real writer execution."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.orchestration.supervised_pipeline_runner import (
    Request, SupervisedBlocked, STAGES, _argv, operate, _parse_result,
)

R = "a" * 40
C = "b" * 64
I = "c" * 64
M = "d" * 64
REPO = "Toibinguz/epl-premier-league-dataset"


class FakeLauncher:
    def __init__(self, stage, kind, *, state="READY_FOR_EXPLICIT_APPROVAL", succeed=True,
                 failure=None):
        self.calls = []
        self.stage = stage
        self.kind = kind
        self.state = state
        self.succeed = succeed
        self.failure = failure

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert kwargs["shell"] is False and kwargs["check"] is False
        assert kwargs["cwd"]
        assert kwargs["timeout"] <= 900
        mode = "--plan" in argv
        if self.failure and (not mode or self.failure == "preview"):
            return SimpleNamespace(returncode=2, stdout=json.dumps({"stage": self.stage, "state":"BLOCKED"}))
        if mode:
            report = {"stage": self.stage, "state": self.state,
                      "repo_id": REPO, "target_revision": R,
                      "context_sha256": C, "intent_sha256": I,
                      "write_authorized": False, "stage_advanced": False}
            return SimpleNamespace(returncode=0 if self.state in ("READY_FOR_EXPLICIT_APPROVAL", "REVIEW_ONLY", "SKIPPED") else 2,
                                   stdout=json.dumps(report))
        if self.kind == "checkpoint":
            report = {"stage": self.stage, "state":"CREATED_NEW", "checkpoint_published":True,
                      "repo_id": REPO, "target_revision": R}
        elif self.kind in ("scoped", "preparation"):
            report = {"stage": self.stage, "state":"VERIFIED_AFTER_WRITE", "artifact_write_verified":True,
                      "db_write_executed":False, "stage_advanced":False,
                      "repo_id": REPO, "target_revision": R}
        else:
            report = {"stage": self.stage, "state":"VERIFIED_AFTER_WRITE", "write_verified":True,
                      "next_stage_authorized":False,"repo_id": REPO, "target_revision": R}
        return SimpleNamespace(returncode=0, stdout=json.dumps(report))


class A4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts" / "orchestration").mkdir(parents=True)
        (self.root / "scripts" / "orchestration" / "end_to_end_readonly.py").write_text("# fixture")

    def req(self, stage, **kwargs):
        return Request(stage=stage, repo_id=REPO, target_revision=R, baseline_snapshot=367, **kwargs)

    def test_all_stages_registered(self):
        self.assertEqual(len(STAGES), 11)

    def test_bad_revision(self):
        with self.assertRaises(SupervisedBlocked):
            Request(stage="PLANNED", repo_id=REPO, target_revision="main", baseline_snapshot=367)

    def test_unknown_stage(self):
        with self.assertRaises(SupervisedBlocked):
            self.req("ROUTE")

    def test_incorrect_repo(self):
        with self.assertRaises(SupervisedBlocked):
            Request(stage="PLANNED", repo_id="not-repo", target_revision=R, baseline_snapshot=367)

    def test_malformed_sha(self):
        with self.assertRaises(SupervisedBlocked):
            self.req("MANIFEST_PREPARATION", context_sha256="abc")

    def test_scoped_only_cache_archive(self):
        with self.assertRaises(SupervisedBlocked):
            self.req("MANIFEST_PREPARATION", scoped_preparation=True)

    def test_non_candidate_cache_path(self):
        with self.assertRaises(SupervisedBlocked):
            self.req("FINALIZATION", candidate_cache="cache")

    def test_checkpoint_rejects_intent(self):
        with self.assertRaises(SupervisedBlocked):
            self.req("PLANNED", intent_sha256=I)

    def test_no_approval_accepted_with_preview(self):
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("PLANNED"), root=self.root, approve_stage="PLANNED")

    def test_no_execution_without_confirmation(self):
        fake=FakeLauncher("PLANNED", "checkpoint", state="REVIEW_ONLY")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("PLANNED", context_sha256=C), root=self.root,
                    execute=True,approve_stage="PLANNED",launcher=fake)
        self.assertEqual(len(fake.calls),0)

    def test_no_execution_without_intent(self):
        fake=FakeLauncher("FINALIZATION", "database")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("FINALIZATION", context_sha256=C), root=self.root,
                    execute=True,approve_stage="FINALIZATION",confirm=True,launcher=fake)
        self.assertEqual(len(fake.calls),0)

    def test_prepared_requires_manifest_to_execute(self):
        fake=FakeLauncher("PREPARED", "checkpoint", state="REVIEW_ONLY")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("PREPARED", context_sha256=C), root=self.root,
                    execute=True,approve_stage="PREPARED",confirm=True,launcher=fake)
        self.assertEqual(len(fake.calls),0)

    def test_plan_is_no_write_and_readonly_database(self):
        fake=FakeLauncher("FINALIZATION", "database")
        result, code=operate(request=self.req("FINALIZATION", context_sha256=C),root=self.root,launcher=fake)
        self.assertEqual(code,0)
        self.assertEqual(result["state"],"READY_FOR_MANUAL_APPROVAL")
        self.assertFalse(result["write_authorized"])
        self.assertEqual(len(fake.calls),1)
        self.assertIn("default_transaction_read_only=on", fake.calls[0][1]["env"]["PGOPTIONS"])
        self.assertNotIn("--execute-one-stage",fake.calls[0][0])

    def test_skip_does_not_authorize(self):
        fake=FakeLauncher("MANIFEST_PREPARATION","preparation",state="SKIPPED")
        result,code=operate(request=self.req("MANIFEST_PREPARATION"),root=self.root,launcher=fake)
        self.assertEqual((result["state"],code), ("REVIEW_ONLY_OR_BLOCKED",0))

    def test_error_state_stays_blocked(self):
        fake=FakeLauncher("FINALIZATION", "database", state="BLOCKED")
        result,code=operate(request=self.req("FINALIZATION"),root=self.root,launcher=fake)
        self.assertEqual(code,2)
        self.assertEqual(len(fake.calls),1)

    def test_checkpoint_exec_exactly_one_stage(self):
        fake=FakeLauncher("PLANNED","checkpoint",state="REVIEW_ONLY")
        report,code=operate(request=self.req("PLANNED",context_sha256=C),root=self.root,
                  execute=True,approve_stage="PLANNED",confirm=True,launcher=fake)
        self.assertEqual(code,0)
        self.assertEqual(report["state"],"ONE_STAGE_VERIFIED")
        self.assertEqual(len(fake.calls),2)
        self.assertIn("--publish",fake.calls[1][0])
        self.assertNotIn("--expected-intent-sha256",fake.calls[1][0])

    def test_preparation_exec_exact_one_stage(self):
        fake=FakeLauncher("MANIFEST_PREPARATION","preparation")
        report,code=operate(request=self.req("MANIFEST_PREPARATION",context_sha256=C,intent_sha256=I,manifest_sha256=M),root=self.root,
                  execute=True,approve_stage="MANIFEST_PREPARATION",confirm=True,launcher=fake)
        self.assertEqual(code,0)
        self.assertEqual(len(fake.calls),2)
        self.assertIn("--confirm-artifact-write",fake.calls[1][0])
        self.assertIn("--expected-manifest-sha256",fake.calls[1][0])

    def test_scoped_cache_exec(self):
        fake=FakeLauncher("CACHE_DOWNLOAD","scoped")
        report,code=operate(request=self.req("CACHE_DOWNLOAD",context_sha256=C,intent_sha256=I,scoped_preparation=True),root=self.root,
                  execute=True,approve_stage="CACHE_DOWNLOAD",confirm=True,launcher=fake)
        self.assertEqual(code,0)
        self.assertIn("scripts.orchestration.safe_stage_transition",fake.calls[1][0])

    def test_db_exec(self):
        fake=FakeLauncher("SNAPSHOT_REGISTRATION","database")
        report,code=operate(request=self.req("SNAPSHOT_REGISTRATION",context_sha256=C,intent_sha256=I),root=self.root,
                  execute=True,approve_stage="SNAPSHOT_REGISTRATION",confirm=True,launcher=fake)
        self.assertEqual(code,0)
        self.assertIn("--confirm-single-stage-write",fake.calls[1][0])
        self.assertNotIn("default_transaction_read_only=on",fake.calls[1][1]["env"].get("PGOPTIONS",""))

    def test_wrong_preview_context_prevents_writer(self):
        fake=FakeLauncher("SNAPSHOT_REGISTRATION","database")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("SNAPSHOT_REGISTRATION",context_sha256="e"*64,intent_sha256=I),root=self.root,
                  execute=True,approve_stage="SNAPSHOT_REGISTRATION",confirm=True,launcher=fake)
        self.assertEqual(len(fake.calls),1)

    def test_wrong_preview_intent_prevents_writer(self):
        fake=FakeLauncher("CACHE_DOWNLOAD","preparation")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("CACHE_DOWNLOAD",context_sha256=C,intent_sha256="e"*64),root=self.root,
                  execute=True,approve_stage="CACHE_DOWNLOAD",confirm=True,launcher=fake)
        self.assertEqual(len(fake.calls),1)

    def test_preview_writer_claim_is_blocked(self):
        fake=FakeLauncher("FINALIZATION","database",state="BLOCKED")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("FINALIZATION",context_sha256=C,intent_sha256=I),root=self.root,
                  execute=True,approve_stage="FINALIZATION",confirm=True,launcher=fake)
        self.assertEqual(len(fake.calls),1)

    def test_backend_execution_failure_not_retried(self):
        fake=FakeLauncher("CACHE_DOWNLOAD","preparation",failure="writer")
        with self.assertRaises(SupervisedBlocked) as cm:
            operate(request=self.req("CACHE_DOWNLOAD",context_sha256=C,intent_sha256=I),root=self.root,
                    execute=True,approve_stage="CACHE_DOWNLOAD",confirm=True,launcher=fake)
        self.assertTrue(cm.exception.may_have_attempted)
        self.assertEqual(len(fake.calls),2)

    def test_bogus_writer_output_ambiguous(self):
        class Bogus(FakeLauncher):
            def __call__(self,argv,**kw):
                r=super().__call__(argv,**kw)
                if "--execute-one-stage" in argv:
                    r.stdout="not-json"
                return r
        fake=Bogus("CACHE_DOWNLOAD","preparation")
        with self.assertRaises(SupervisedBlocked) as cm:
            operate(request=self.req("CACHE_DOWNLOAD",context_sha256=C,intent_sha256=I),root=self.root,
                    execute=True,approve_stage="CACHE_DOWNLOAD",confirm=True,launcher=fake)
        self.assertTrue(cm.exception.may_have_attempted)

    def test_preview_true_write_flag_blocks(self):
        class Liar(FakeLauncher):
            def __call__(self,argv,**kw):
                r=super().__call__(argv,**kw)
                obj=json.loads(r.stdout)
                obj["write_executed"]=True
                r.stdout=json.dumps(obj)
                return r
        fake=Liar("FINALIZATION","database")
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("FINALIZATION"),root=self.root,launcher=fake)

    def test_preview_artifact_side_effect_flag_blocks(self):
        class Liar(FakeLauncher):
            def __call__(self,argv,**kw):
                r=super().__call__(argv,**kw)
                obj=json.loads(r.stdout)
                obj["artifact_write_executed"]=True
                r.stdout=json.dumps(obj)
                return r
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("MANIFEST_PREPARATION"),root=self.root,
                    launcher=Liar("MANIFEST_PREPARATION","preparation"))

    def test_preview_missing_identity_blocks(self):
        class Liar(FakeLauncher):
            def __call__(self,argv,**kw):
                r=super().__call__(argv,**kw)
                obj=json.loads(r.stdout)
                obj.pop("repo_id")
                r.stdout=json.dumps(obj)
                return r
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("FINALIZATION"),root=self.root,
                    launcher=Liar("FINALIZATION","database"))

    def test_plan_missing_root(self):
        with self.assertRaises(SupervisedBlocked):
            operate(request=self.req("PLANNED"),root=self.root / "missing")


if __name__ == "__main__":
    unittest.main()
