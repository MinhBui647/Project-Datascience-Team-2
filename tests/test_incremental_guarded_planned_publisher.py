"""M4.6.4d.6c.2b.2b.4c.3: offline, DB/HF-free guarded publication tests."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.tools.incremental_execution_context import (
    CandidateSpec, PlannedContext, checkpoint_digest, read_checkpoint,
    _checkpoint_rel, _guard_path,
)
from scripts.tools.incremental_guarded_planned_publisher import (
    run_guarded, parse_args, format_result,
)

REV = 'd3648ed988914f238fa60cd68feda26118d4cbb9'
BASE_REV = '0216641d6081234e9e35a3de65c138830c77963a'
REPO = 'Toibinguz/epl-premier-league-dataset'
FP = 'e039e88a9f06a76a6d25cbe846e4843141e4359300359f0d64db5f95b339a329'


def ctx():
    return PlannedContext(
        repo_id=REPO, baseline_snapshot_id=365, baseline_revision=BASE_REV,
        target_revision=REV, plan_fingerprint=FP,
        expected_baseline_files=286, expected_full_files=314,
        expected_raw_files=296, expected_reuse=282,
        expected_files=14, expected_bytes=140,
        expected_baseline_records=1000, expected_reused_records=950,
        candidates=tuple(CandidateSpec(
            f'2026_2027_new/matches/fotmob/{i:02d}.json',
            'NEW' if i < 10 else 'CHANGED', 10, 'fotmob', f'{i+1:040x}', None)
            for i in range(14))
    )


def outcome(p):
    return SimpleNamespace(
        decision='PLAN_NEW_REVISION', target_snapshot_id=None,
        target_revision=p.target_revision, baseline_snapshot_id=p.baseline_snapshot_id,
        baseline_revision=p.baseline_revision, planned=p
    )


class GuardedCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = ctx()
        self.kw = dict(repo_id=REPO, target_revision=REV,
                       expected_baseline_snapshot=365, expected_plan_fingerprint=FP,
                       expected_context_sha256=checkpoint_digest(self.p), root=self.root)
        self.calls = []

    def reader(self, **kwargs):
        self.calls.append(kwargs)
        return outcome(self.p)

    def evaluate(self, **overrides):
        return run_guarded(**(self.kw | {'plan_reader': self.reader} | overrides))

    def test_preview_is_read_only_and_does_not_create_directories(self):
        res = self.evaluate()
        self.assertEqual(res.status, 'PREVIEW_READY')
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root/'reports').exists())
        self.assertFalse(res.write_authorized)

    def test_preview_expected_summary(self):
        res = self.evaluate()
        self.assertEqual((res.reused_files,res.expected_files), (282,14))
        self.assertEqual(res.digest, self.kw['expected_context_sha256'])

    def test_publisher_requires_confirmation(self):
        with self.assertRaisesRegex(RuntimeError,'confirm-planned-only'):
            self.evaluate(publish=True)
        self.assertEqual(len(self.calls),0)
        self.assertFalse((self.root/'reports').exists())

    def test_plan_rejects_confirmation(self):
        with self.assertRaisesRegex(RuntimeError,'forbidden'):
            self.evaluate(confirmed=True)

    def test_confirmed_publishes_exact_checkpoint_and_reads_back(self):
        res = self.evaluate(publish=True, confirmed=True)
        self.assertEqual(res.status,'CREATED_NEW')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(read_checkpoint(self.root,REV,'planned'),self.p)
        self.assertTrue((self.root/res.relative_path).is_file())
        self.assertFalse(res.write_authorized)

    def test_replay_is_idempotent_same_bytes(self):
        self.evaluate(publish=True, confirmed=True)
        data = (self.root/_checkpoint_rel(REV,'planned')).read_bytes()
        res = self.evaluate(publish=True, confirmed=True)
        self.assertEqual(res.status,'ALREADY_VERIFIED')
        self.assertEqual((self.root/_checkpoint_rel(REV,'planned')).read_bytes(),data)

    def test_reject_corrupted_existing(self):
        self.evaluate(publish=True, confirmed=True)
        (self.root/_checkpoint_rel(REV,'planned')).write_bytes(b'fake')
        with self.assertRaisesRegex(RuntimeError,'Invalid checkpoint'):
            self.evaluate(publish=True, confirmed=True)

    def test_reject_mismatched_existing_even_if_well_formed(self):
        from scripts.tools.incremental_execution_context import publish_checkpoint
        alternate = replace(self.p, expected_baseline_records=1001)
        publish_checkpoint(self.root, alternate)
        with self.assertRaisesRegex(RuntimeError,'Existing planned checkpoint differs'):
            self.evaluate(publish=True, confirmed=True)

    def test_reject_plan_digest_mismatch_without_write(self):
        with self.assertRaisesRegex(RuntimeError,'SHA256 differs'):
            self.evaluate(expected_context_sha256='a'*64,publish=True,confirmed=True)
        self.assertFalse((self.root/'reports').exists())

    def test_reject_plan_fingerprint_mismatch(self):
        with self.assertRaisesRegex(RuntimeError,'fingerprint'):
            self.evaluate(expected_plan_fingerprint='a'*64,publish=True,confirmed=True)

    def test_reject_stale_baseline(self):
        with self.assertRaisesRegex(RuntimeError,'baseline changed'):
            self.evaluate(expected_baseline_snapshot=364,publish=True,confirmed=True)
        self.assertFalse((self.root/'reports').exists())

    def test_planner_must_choose_latest_baseline(self):
        self.evaluate()
        self.assertIsNone(self.calls[0]['baseline_snapshot'])

    def test_target_snapshot_exists_blocked(self):
        def reader(**kw):
            o=outcome(self.p)
            o.decision='RECOVERY_REQUIRED';o.target_snapshot_id=800
            return o
        with self.assertRaisesRegex(RuntimeError,'Not a new target'):
            self.evaluate(plan_reader=reader,publish=True,confirmed=True)

    def test_skip_no_changes_blocked(self):
        def reader(**kw):
            o=outcome(self.p);o.decision='SKIP_NO_RAW_CHANGES';o.planned=None
            return o
        with self.assertRaises(RuntimeError):
            self.evaluate(plan_reader=reader,publish=True,confirmed=True)

    def test_second_planner_drift_blocked(self):
        original=self.p
        def drifting(**kw):
            self.calls.append(kw)
            p=original if len(self.calls)==1 else replace(original,expected_baseline_records=1001)
            return outcome(p)
        with self.assertRaisesRegex(RuntimeError,'SHA256 differs'):
            self.evaluate(plan_reader=drifting,publish=True,confirmed=True)
        self.assertFalse((self.root/'reports').exists())

    def test_second_planner_drift_even_if_context_pinned(self):
        original=self.p
        def fake(**kw):
            self.calls.append(kw)
            return outcome(original) if len(self.calls)==1 else SimpleNamespace(
                **(vars(outcome(original)) | {'target_snapshot_id': 5}))
        with self.assertRaises(RuntimeError):
            self.evaluate(plan_reader=fake,publish=True,confirmed=True)

    def test_reject_cross_repository(self):
        with self.assertRaises(RuntimeError):
            self.evaluate(repo_id='other/repo',publish=True,confirmed=True)

    def test_reject_non_sha_revision(self):
        with self.assertRaises(ValueError):
            self.evaluate(target_revision='main',publish=True,confirmed=True)

    def test_reject_symlinked_checkpoint_parent(self):
        try:
            (self.root/'reports').symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation not permitted by this Windows account')
        with self.assertRaisesRegex(RuntimeError,'symlink'):
            self.evaluate(publish=True,confirmed=True)

    def test_reject_existing_checkpoint_symlink(self):
        (self.root/'reports'/'m4_6_runs'/REV).mkdir(parents=True)
        target=self.root/'reports'/'m4_6_runs'/REV/'planned.json'
        try:
            target.symlink_to(self.root/'not_a_checkpoint')
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation not permitted by this Windows account')
        with self.assertRaisesRegex(RuntimeError,'symlink'):
            self.evaluate(publish=True,confirmed=True)

    def test_bad_writer_result_blocks(self):
        def bad(root,context):return ('bad/location','CREATED_NEW')
        with self.assertRaisesRegex(RuntimeError,'unexpected identity'):
            self.evaluate(publish=True,confirmed=True,checkpoint_writer=bad)
        self.assertFalse((self.root/'reports').exists())

    def test_writer_must_have_readback(self):
        def liar(root,context):return (_checkpoint_rel(REV,'planned'),'CREATED_NEW')
        with self.assertRaises(FileNotFoundError):
            self.evaluate(publish=True,confirmed=True,checkpoint_writer=liar)

    def test_no_write_during_preview_with_writer_injected(self):
        def forbidden(*args):raise AssertionError('writer invoked')
        self.evaluate(checkpoint_writer=forbidden)

    def test_expected_digest_is_canonical_sha256_not_payload_hash(self):
        self.assertEqual(len(checkpoint_digest(self.p)),64)
        self.assertNotEqual(checkpoint_digest(self.p),FP)

    def test_only_planned_created(self):
        self.evaluate(publish=True,confirmed=True)
        folders=list((self.root/'reports'/'m4_6_runs'/REV).iterdir())
        self.assertEqual([p.name for p in folders],['planned.json'])

    def test_print_report_no_write_approval(self):
        s=format_result(self.evaluate())
        self.assertIn('Stage advancement: NOT AUTHORIZED',s)
        self.assertIn('Further write authorization: NO',s)

    def test_invalid_baseline_types_fail(self):
        for v in (-1,0,True,'365'):
            with self.subTest(v=v), self.assertRaises(ValueError):
                self.evaluate(expected_baseline_snapshot=v)

    def test_parser_plan(self):
        a=parse_args(['--plan','--target-revision',REV,
                     '--expected-baseline-snapshot','365',
                     '--expected-plan-fingerprint',FP,
                     '--expected-context-sha256',checkpoint_digest(self.p)])
        self.assertFalse(a.publish)

    def test_parser_publish_without_confirmation_rejected(self):
        with patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                parse_args(['--publish','--target-revision',REV,
                           '--expected-baseline-snapshot','365',
                           '--expected-plan-fingerprint',FP,
                           '--expected-context-sha256',checkpoint_digest(self.p)])

    def test_parser_main_revision_rejected(self):
        with patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                parse_args(['--plan','--target-revision','main',
                           '--expected-baseline-snapshot','365',
                           '--expected-plan-fingerprint',FP,
                           '--expected-context-sha256',checkpoint_digest(self.p)])


if __name__=='__main__':
    unittest.main()
