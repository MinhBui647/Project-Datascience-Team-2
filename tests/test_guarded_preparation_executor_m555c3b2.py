"""Offline preparation Executor tests. Never call HF/DB or a real M4 child."""
from pathlib import Path
from types import SimpleNamespace as S
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import Mock

from scripts.orchestration.preparation_stage_coordinator import PreparationReview, REPO_ID
from scripts.orchestration.guarded_preparation_executor import (
    plan_preparation, execute_preparation, _pinned_sources, PreparationGateError,
    _parse_preview, _intent, _evidence, M4_PINNED_SHA256,
)

REV = 'a' * 40
CTX = 'b' * 64
MANIFEST = 'c' * 64
PREVIEW = 'd' * 64

def make_review(stage='MANIFEST_PREPARATION', *, csv=False, state='REVIEW_PREPARATION',
                fingerprint='e'*64, badoption=False):
    module = {'MANIFEST_PREPARATION':'prepare_incremental_manifest',
              'CACHE_DOWNLOAD':'download_incremental_candidates',
              'ARCHIVE_PROMOTION':'promote_incremental_archive'}[stage]
    args = [sys.executable,'-m','scripts.tools.'+module,
            '--repo-id',REPO_ID,'--target-revision',REV,
            '--baseline-snapshot','367','--expected-fingerprint',fingerprint,
            '--expected-files','3','--expected-bytes','1500']
    if stage == 'MANIFEST_PREPARATION':
        args += ['--expected-full-files','314','--expected-raw-files','296']
    elif stage == 'ARCHIVE_PROMOTION':
        args += ['--expected-records','72']
    if csv and stage == 'MANIFEST_PREPARATION': args += ['--allow-csv-manifest-plan-only']
    if csv and stage == 'CACHE_DOWNLOAD': args += ['--allow-csv-cache-only']
    if badoption: args += ['--mystery','1']
    args.append('--plan')
    return PreparationReview(
        state=state,stage=stage,repo_id=REPO_ID,target_revision=REV,
        context_sha256=CTX,preview_sha256=PREVIEW,cli_module='scripts.tools.'+module,
        read_only_argv=tuple(args) if state=='REVIEW_PREPARATION' else (),
        planned_checkpoint='PRESENT',reason='review')

class PreparationGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.review=Mock(return_value=make_review())
        self.sources=Mock(return_value=('1'*64,'2'*64))
        self.verifier=Mock(side_effect=[S(stage='MANIFEST_PREPARATION',write_authorized=False,status='INCOMPLETE'),
                                        S(stage='MANIFEST_PREPARATION',write_authorized=False,status='VERIFIED')])
        self.child=Mock(return_value=S(returncode=0))
        self.base=dict(root=self.root,repo_id=REPO_ID,target_revision=REV,
                       stage='MANIFEST_PREPARATION',baseline_snapshot=367,
                       expected_manifest_sha256=MANIFEST,
                       reviewer=self.review,source_checker=self.sources)
    def preview(self, **kw):
        return plan_preparation(**{**self.base, **kw})
    def execute(self, **kw):
        preview=self.preview()
        vals={**self.base,'approved_stage':self.base['stage'],
              'expected_context_sha256':CTX,
              'expected_intent_sha256':preview.intent_sha256,
              'verifier':self.verifier,'launcher':self.child}
        vals.update(kw)
        return execute_preparation(**vals)
    def claims(self):
        return list(self.root.rglob('*.claim.json'))
    def test_plan_never_launches_or_reserves(self):
        out=self.preview()
        self.assertEqual(out.state,'READY_FOR_EXPLICIT_APPROVAL')
        self.assertFalse(out.write_authorized)
        self.assertIn('--publish',out.writer_argv)
        self.assertIn('--expected-manifest-sha256',out.writer_argv)
        self.assertNotIn('--plan',out.writer_argv)
        self.assertEqual(self.claims(),[])
        self.child.assert_not_called()
    def test_csv_manifest_safe_pinned_publish(self):
        self.review.return_value=make_review(csv=True)
        out=self.preview()
        self.assertIn('--allow-csv-manifest-publish',out.writer_argv)
        self.assertNotIn('--allow-csv-manifest-plan-only',out.writer_argv)
    def test_cache_preview_and_execution_command(self):
        self.review.return_value=make_review(stage='CACHE_DOWNLOAD',csv=True)
        out=self.preview(stage='CACHE_DOWNLOAD',expected_manifest_sha256=None)
        self.assertIn('--download',out.writer_argv)
        self.assertIn('--confirm-cache-only',out.writer_argv)
        self.assertIn('--allow-csv-cache-only',out.writer_argv)
    def test_archive_preview_and_execution_command(self):
        self.review.return_value=make_review(stage='ARCHIVE_PROMOTION')
        out=self.preview(stage='ARCHIVE_PROMOTION',expected_manifest_sha256=None)
        self.assertIn('--promote',out.writer_argv)
        self.assertIn('--expected-records',out.writer_argv)
    def test_no_manifest_sha_fails_before_claim(self):
        with self.assertRaisesRegex(PreparationGateError,'Manifest publication'):
            self.preview(expected_manifest_sha256=None)
        self.assertFalse(self.claims())
    def test_bad_source_drift_fails_before_claim(self):
        self.sources.side_effect=PreparationGateError('M4 source drift')
        with self.assertRaisesRegex(PreparationGateError,'drift'): self.preview()
        self.assertFalse(self.claims())
    def test_unknown_option_rejected(self):
        self.review.return_value=make_review(badoption=True)
        with self.assertRaisesRegex(PreparationGateError,'Unknown'):
            self.preview()
    def test_no_change_skips_before_source_inspection(self):
        self.review.return_value=make_review(state='SKIPPED')
        out=self.preview(expected_manifest_sha256=None)
        self.assertEqual(out.state,'SKIPPED')
        self.sources.assert_not_called()
        self.child.assert_not_called()
    def test_malformed_context_rejected(self):
        with self.assertRaisesRegex(PreparationGateError,'context'):
            self.execute(expected_context_sha256='0'*64)
        self.child.assert_not_called()
    def test_wrong_digest_blocks_before_claim(self):
        with self.assertRaisesRegex(PreparationGateError,'Intent SHA256'):
            self.execute(expected_intent_sha256='9'*64)
        self.child.assert_not_called()
        self.assertFalse(self.claims())
    def test_changed_second_review_blocks(self):
        self.review.side_effect=[make_review(), make_review(),
                                 make_review(fingerprint='f'*64)]
        preview=self.preview()
        with self.assertRaisesRegex(PreparationGateError,'drift'):
            execute_preparation(**{**self.base,'approved_stage':self.base['stage'],
                 'expected_context_sha256':CTX,'expected_intent_sha256':preview.intent_sha256,
                 'verifier':self.verifier,'launcher':self.child})
        self.assertFalse(self.claims())
    def test_pre_verification_must_be_incomplete(self):
        self.verifier=Mock(return_value=S(stage='MANIFEST_PREPARATION',write_authorized=False,status='VERIFIED'))
        with self.assertRaisesRegex(PreparationGateError,'Pre-write evidence'):
            self.execute()
        self.assertFalse(self.claims())
    def test_one_launch_records_permanent_verified_claim(self):
        out=self.execute()
        self.assertEqual(out['state'],'VERIFIED_AFTER_WRITE')
        self.assertEqual(len(self.claims()),1)
        self.assertEqual(len(list(self.root.rglob('*.result.json'))),1)
        self.child.assert_called_once()
        _,kwargs=self.child.call_args
        self.assertFalse(kwargs['shell'])
        self.assertFalse(out['stage_advanced'])
        self.verifier=Mock(return_value=S(stage='MANIFEST_PREPARATION',write_authorized=False,status='INCOMPLETE'))
        blocked=self.preview()
        self.assertEqual(blocked.state,'BLOCKED_ATTEMPT_REVIEW')
        with self.assertRaisesRegex(PreparationGateError,'not approved'):
            execute_preparation(**{**self.base,'approved_stage':'MANIFEST_PREPARATION',
                'expected_context_sha256':CTX,'expected_intent_sha256':out['intent_sha256'],
                'verifier':self.verifier,'launcher':self.child})
        self.child.assert_called_once()
    def test_orphan_result_also_blocks_preview(self):
        repo_hash=hashlib.sha256(REPO_ID.encode()).hexdigest()
        path=self.root/'reports'/'m5_write_attempts'/'v1'/repo_hash/REV
        path.mkdir(parents=True)
        (path/'MANIFEST_PREPARATION.result.json').write_text('untrusted')
        self.assertEqual(self.preview().state,'BLOCKED_ATTEMPT_REVIEW')
        self.child.assert_not_called()
    def test_child_failure_unverified_journal_remains(self):
        self.child.return_value=S(returncode=4)
        with self.assertRaises(PreparationGateError) as caught:self.execute()
        self.assertTrue(caught.exception.attempted)
        self.assertEqual(len(self.claims()),1)
        self.assertEqual(len(list(self.root.rglob('*.result.json'))),1)
    def test_timeout_unverified_journal_remains(self):
        import subprocess
        self.child.side_effect=subprocess.TimeoutExpired(cmd='M4',timeout=3)
        with self.assertRaises(PreparationGateError) as caught:self.execute()
        self.assertTrue(caught.exception.attempted)
        self.assertEqual(len(self.claims()),1)
    def test_post_evidence_incomplete_is_ambiguous(self):
        self.verifier=Mock(side_effect=[S(stage='MANIFEST_PREPARATION',write_authorized=False,status='INCOMPLETE'),
                                        S(stage='MANIFEST_PREPARATION',write_authorized=False,status='INCOMPLETE')])
        with self.assertRaisesRegex(PreparationGateError,'manual reconciliation'):
            self.execute()
        self.assertEqual(len(self.claims()),1)
    def test_missing_stage_approval_no_launch(self):
        with self.assertRaisesRegex(PreparationGateError,'approval'):
            self.execute(approved_stage='ARCHIVE_PROMOTION')
        self.child.assert_not_called()
    def test_source_checker_validates_actual_file_hash(self):
        for path,digest in M4_PINNED_SHA256['CACHE_DOWNLOAD']:
            p=self.root.joinpath(*path.split('/'))
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text('not pinned')
        with self.assertRaisesRegex(PreparationGateError,'source drift'):
            _pinned_sources(self.root,'CACHE_DOWNLOAD')

if __name__=='__main__':unittest.main()
