"""Offline fail-closed preparation bridge tests. No M4/Pg/HF invocations."""
import hashlib
from pathlib import Path
from types import SimpleNamespace as S
import sys
import tempfile
import unittest
from unittest.mock import Mock

from scripts.orchestration.preparation_stage_coordinator import (
    Services, PREPARATION, REPO_ID, preview_preparation, _check_argv,
)

REV='a'*40
CTX='b'*64


def cmd(stage='MANIFEST_PREPARATION', *, mod=None, switches=(), rev=REV):
    module='scripts.tools.'+(mod or PREPARATION[stage])
    return (sys.executable, '-m', module, '--repo-id', REPO_ID,
            '--target-revision', rev, '--baseline-snapshot','367',
            *switches,'--plan')


def report(stage='MANIFEST_PREPARATION', *, decision='RECOVERY_REVIEW_REQUIRED',
           disposition='REVIEW_READ_ONLY_PLAN', blockers=(), write=False,
           next_stage=None, digest=CTX):
    plan=S(repo_id=REPO_ID,target_revision=REV,decision=decision,
           write_authorized=write,blockers=blockers,
           next_review_stage=stage if next_stage is None else next_stage)
    contract=S(review_stage=stage,disposition=disposition,
               write_authorized=False,blockers=(),context_sha256=digest)
    return S(plan=plan,contract=contract)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.reviewer=Mock(return_value=report())
        self.validator=Mock(return_value=(cmd(),))
        self.reader=Mock(return_value=(S(repo_id=REPO_ID,target_revision=REV),None,None))
        self.sv=Services(self.reviewer,self.validator,self.reader)
        self.common=dict(root=self.root,repo_id=REPO_ID,target_revision=REV,
                         stage='MANIFEST_PREPARATION',baseline_snapshot=367,
                         services=self.sv)
    def run_review(self,**overrides):
        return preview_preparation(**{**self.common,**overrides})
    def test_manifest_preview_is_only_review(self):
        out=self.run_review()
        self.assertEqual(out.state,'REVIEW_PREPARATION')
        self.assertFalse(out.write_authorized)
        self.assertFalse(out.artifact_write_executed)
        self.assertFalse(out.hf_body_downloaded)
        self.assertFalse(out.stage_advanced)
        self.assertEqual(out.context_sha256,CTX)
        self.assertEqual(out.cli_module,'scripts.tools.prepare_incremental_manifest')
        self.assertEqual(len(out.preview_sha256),64)
    def test_cache_preview(self):
        self.reviewer.return_value=report('CACHE_DOWNLOAD')
        self.validator.return_value=(cmd('CACHE_DOWNLOAD'),)
        self.assertEqual(self.run_review(stage='CACHE_DOWNLOAD').state,'REVIEW_PREPARATION')
    def test_cache_csv_readonly_preview_allowed(self):
        self.reviewer.return_value=report('CACHE_DOWNLOAD')
        self.validator.return_value=(cmd('CACHE_DOWNLOAD',switches=('--allow-csv-cache-only',)),)
        self.assertEqual(self.run_review(stage='CACHE_DOWNLOAD').state,'REVIEW_PREPARATION')
    def test_cache_csv_flag_rejected_for_manifest(self):
        self.validator.return_value=(cmd(switches=('--allow-csv-cache-only',)),)
        with self.assertRaises(RuntimeError):self.run_review()
    def test_archive_preview(self):
        self.reviewer.return_value=report('ARCHIVE_PROMOTION')
        self.validator.return_value=(cmd('ARCHIVE_PROMOTION'),)
        self.assertEqual(self.run_review(stage='ARCHIVE_PROMOTION').state,'REVIEW_PREPARATION')
    def test_manifest_csv_plan_only_flag(self):
        self.validator.return_value=(cmd(switches=('--allow-csv-manifest-plan-only',)),)
        self.assertEqual(self.run_review().state,'REVIEW_PREPARATION')
    def test_repeat_hash_deterministic(self):
        self.assertEqual(self.run_review().preview_sha256,self.run_review().preview_sha256)
    def test_skip_completed_has_no_checkpoint_reader(self):
        self.reviewer.return_value=report(decision='SKIP_ALREADY_CURRENT',disposition='SKIPPED',next_stage='MANIFEST_PREPARATION')
        # Completed M4 plan must have next_review_stage=None.
        self.reviewer.return_value.plan.next_review_stage=None
        self.assertEqual(self.run_review().state,'SKIPPED')
        self.reader.assert_not_called(); self.validator.assert_not_called()
    def test_skip_with_next_stage_refused(self):
        self.reviewer.return_value=report(decision='SKIP_ALREADY_CURRENT',disposition='SKIPPED')
        with self.assertRaisesRegex(RuntimeError,'skip stage'): self.run_review()
    def test_m4_blocked(self):
        self.reviewer.return_value=report(decision='BLOCKED_EVIDENCE',disposition='BLOCKED')
        self.assertEqual(self.run_review().state,'BLOCKED')
    def test_m4_write_authorization_rejected(self):
        self.reviewer.return_value=report(write=True)
        with self.assertRaisesRegex(RuntimeError,'authorization'): self.run_review()
    def test_m4_next_stage_is_different(self):
        self.reviewer.return_value.plan.next_review_stage='CACHE_DOWNLOAD'
        self.assertEqual(self.run_review().state,'NOT_CURRENT_STAGE')
    def test_missing_planned_checkpoint_blocks(self):
        self.reader.return_value=(None,None,None)
        self.assertEqual(self.run_review().state,'BLOCKED')
    def test_existing_prepared_checkpoint_blocks(self):
        self.reader.return_value=(S(repo_id=REPO_ID,target_revision=REV),S(),None)
        self.assertEqual(self.run_review().state,'BLOCKED')
    def test_malformed_chain_rejected(self):
        self.reader.return_value=()
        with self.assertRaisesRegex(RuntimeError,'checkpoint chain'):self.run_review()
    def test_wrong_checkpoint_identity_rejected(self):
        self.reader.return_value=(S(repo_id='rogue/repo',target_revision=REV),None,None)
        with self.assertRaisesRegex(RuntimeError,'identity'):self.run_review()
    def test_expected_context_drift_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'mismatches'):
            self.run_review(expected_context_sha256='c'*64)
    def test_wrong_module_rejected(self):
        self.validator.return_value=(cmd(mod='finalize_incremental_snapshot'),)
        with self.assertRaisesRegex(RuntimeError,'module'):self.run_review()
    def test_side_effect_flag_rejected(self):
        self.validator.return_value=(cmd(switches=('--confirm-manifest-archive-only','true')),)
        with self.assertRaisesRegex(RuntimeError,'side-effect'):self.run_review()
    def test_csv_manifest_publish_rejected(self):
        self.validator.return_value=(cmd(switches=('--allow-csv-manifest-publish',)),)
        with self.assertRaisesRegex(RuntimeError,'side-effect'):self.run_review()
    def test_nonplan_rejected(self):
        self.validator.return_value=(cmd()[:-1]+('--download',),)
        with self.assertRaises(RuntimeError):self.run_review()
    def test_two_commands_rejected(self):
        self.validator.return_value=(cmd(),cmd())
        with self.assertRaisesRegex(RuntimeError,'Exactly one'):self.run_review()
    def test_wrong_target_in_argv_rejected(self):
        self.validator.return_value=(cmd(rev='c'*40),)
        with self.assertRaisesRegex(RuntimeError,'mismatched'):self.run_review()
    def test_invalid_inputs_never_use_services(self):
        with self.assertRaises(ValueError):self.run_review(target_revision='main')
        self.reviewer.assert_not_called()
    def test_no_persistence_on_review(self):
        self.run_review()
        self.assertEqual(list(self.root.rglob('*')),[])

if __name__ == '__main__':unittest.main()
