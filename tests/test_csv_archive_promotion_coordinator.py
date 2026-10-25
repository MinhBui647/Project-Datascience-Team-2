"""M4.CSV.3b.4i | No production HF/DB/archive access in test suite."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from scripts.incremental import promote_csv_candidate_archives as m


class ReceiptTests(unittest.TestCase):
    @staticmethod
    def candidate(path='2026_2027_new/football_data_raw_2627.csv', body=b'a,b\n1,2\n', action='READY_NEW'):
        sha = hashlib.sha256(body).hexdigest()
        return NS(relative_path=path, sha256=sha, byte_count=len(body), record_count=1,
                  archive_relative_path=f'raw-archive-v2/objects/{sha[:2]}/{sha}', action=action)

    def test_receipt_deterministic(self):
        x = self.candidate()
        self.assertEqual(m.receipt_hash((x,)), m.receipt_hash((x,)))

    def test_receipt_action_independent(self):
        x = self.candidate()
        self.assertEqual(m.receipt_hash((x,)), m.receipt_hash((replace_obj(x, action='ALREADY_ARCHIVED'),)))

    def test_receipt_order_independent(self):
        a = self.candidate()
        b = self.candidate('raw_data/a.json', b'{"a":1}')
        self.assertEqual(m.receipt_hash((a,b)), m.receipt_hash((b,a)))

    def test_duplicate_blocked(self):
        x = self.candidate()
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            m.receipt_hash((x,x))

    def test_tar_blocked(self):
        with self.assertRaises(ValueError):
            m.receipt_hash((self.candidate('2026_2027_new/a.tar.gz'),))

    def test_traversal_blocked(self):
        with self.assertRaises(ValueError):
            m.receipt_hash((self.candidate('raw_data/../a.csv'),))

    def test_noncanonical_archive_blocked(self):
        with self.assertRaises(ValueError):
            m.receipt_hash((replace_obj(self.candidate(), archive_relative_path='bad/path'),))

    def test_invalid_record_count_blocked(self):
        with self.assertRaises(ValueError):
            m.receipt_hash((replace_obj(self.candidate(), record_count=0),))

    def test_no_candidates_blocked(self):
        with self.assertRaises(ValueError):
            m.receipt_hash(())


class CommandTests(unittest.TestCase):
    @staticmethod
    def flags(*flags):
        return ['--baseline-snapshot','4','--target-revision','a'*40,
                '--expected-fingerprint','b'*64,'--expected-files','1',
                '--expected-bytes','7','--expected-records','1',*flags]

    def test_plan_default(self):
        self.assertTrue(m.parse_args(self.flags('--plan')).plan)

    def test_promote_requires_both_controls(self):
        for extras in [(), ('--confirm-csv-archive-only',),
                       ('--expected-receipt-sha256','a'*64)]:
            with self.subTest(extras=extras):
                with self.assertRaises(SystemExit):
                    m.parse_args(self.flags('--promote',*extras))

    def test_promote_both_controls(self):
        x=m.parse_args(self.flags('--promote','--confirm-csv-archive-only',
                                    '--expected-receipt-sha256','f'*64))
        self.assertTrue(x.promote)

    def test_plan_forbids_receipt(self):
        with self.assertRaises(SystemExit):
            m.parse_args(self.flags('--plan','--expected-receipt-sha256','f'*64))

    def test_plan_forbids_confirmation(self):
        with self.assertRaises(SystemExit):
            m.parse_args(self.flags('--plan','--confirm-csv-archive-only'))

    def test_unknown_repo_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_args(self.flags('--plan','--repo-id','wrong/repo'))

    def test_cache_traversal_rejected(self):
        with self.assertRaises(SystemExit):
            m.parse_args(self.flags('--plan','--cache-dir','reports/../outside'))


class CoordinatorTests(unittest.TestCase):
    def test_plan_never_calls_writer(self):
        candidate=ReceiptTests.candidate()
        r=m.ArchivePromotionPlan(m.REPO,4,'a'*40,'b'*64,(candidate,),
                                  {candidate.relative_path:Path('cache')}, {}, m.receipt_hash((candidate,)))
        with patch.object(m,'construct_plan',return_value=r), patch.object(m,'promote') as pub:
            self.assertEqual(m.main(CommandTests.flags('--plan')),0)
            pub.assert_not_called()

    def test_promote_rejects_wrong_receipt_before_writer(self):
        candidate=ReceiptTests.candidate()
        r=m.ArchivePromotionPlan(m.REPO,4,'a'*40,'b'*64,(candidate,),
                                  {candidate.relative_path:Path('cache')}, {}, m.receipt_hash((candidate,)))
        args=CommandTests.flags('--promote','--confirm-csv-archive-only',
                                '--expected-receipt-sha256','f'*64)
        with patch.object(m,'construct_plan',return_value=r), patch.object(m,'promote') as pub:
            with self.assertRaisesRegex(RuntimeError, 'receipt changed'):
                m.main(args)
            pub.assert_not_called()

    def test_promote_is_idempotent_on_temp_archive(self):
        from scripts.tools.incremental_archive_promotion import inspect_cached
        body=b'A,B\nX,Y\n'
        candidate=ReceiptTests.candidate(body=body)
        with TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'raw-archive-v2'/'objects').mkdir(parents=True)
            cached=root/'cache.csv';cached.write_bytes(body)
            # The real adapter requires a valid HF Git blob identity.
            git=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
            identity=NS(size=len(body),git_blob_id=git,lfs_sha256=None)
            orig=inspect_cached(root,candidate.relative_path,cached,identity,allow_csv=True)
            plan=m.ArchivePromotionPlan(m.REPO,4,'a'*40,'b'*64,(orig,),
                                        {orig.relative_path:cached}, {orig.relative_path:identity},
                                        m.receipt_hash((orig,)))
            self.assertEqual(m.promote(plan,root=root),(1,0))
            self.assertEqual(m.promote(plan,root=root),(0,1))
            self.assertEqual((root/orig.archive_relative_path).read_bytes(),body)

    def test_promote_catches_modified_cache(self):
        from scripts.tools.incremental_archive_promotion import inspect_cached
        body=b'A,B\nX,Y\n'
        candidate=ReceiptTests.candidate(body=body)
        with TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'raw-archive-v2'/'objects').mkdir(parents=True)
            cached=root/'cache.csv';cached.write_bytes(body)
            git=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
            identity=NS(size=len(body),git_blob_id=git,lfs_sha256=None)
            orig=inspect_cached(root,candidate.relative_path,cached,identity,allow_csv=True)
            plan=m.ArchivePromotionPlan(m.REPO,4,'a'*40,'b'*64,(orig,),
                                        {orig.relative_path:cached}, {orig.relative_path:identity},
                                        m.receipt_hash((orig,)))
            cached.write_bytes(b'A,B\nQ,Z\n')
            with self.assertRaises(Exception):
                m.promote(plan,root=root)
            self.assertFalse((root/orig.archive_relative_path).exists())


class CachedCsvSymlinkTests(unittest.TestCase):
    """Exercise all CSV cache consumers with the real hash/parser/archive gates.

    Only the OS symlink resolution is simulated, since Windows test workers may
    lack symlink privileges. Resolving the HF snapshot pointer must produce a
    regular blob inside the selected cache; the CSV adapter still rejects the
    unresolved pointer itself.
    """

    def setUp(self):
        from scripts.incremental.check_raw_updates import FileIdentity
        from scripts.tools.raw_sync_write_plan import ExistingFile
        from scripts.tools.incremental_sync import assemble_plan
        from scripts.incremental import (
            incremental_snapshot_prepare, incremental_archive_promotion,
            plan_incremental_candidates, ingest_incremental_candidates,
        )
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.cache = self.root / 'reports/cache'
        self.cache.mkdir(parents=True)
        self.blob = self.cache / 'blobs/content'
        self.blob.parent.mkdir()
        self.body = b'A,B\nX,Y\n'
        self.blob.write_bytes(self.body)
        self.pointer = self.cache / 'snapshots/revision/file.csv'
        self.path = '2026_2027_new/football_data_raw_2627.csv'
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.identity = FileIdentity(len(self.body), None, self.sha)
        self.base = 'c' * 40
        self.rev = 'd' * 40
        self.baseline = {self.path: FileIdentity(len(self.body), None, 'a' * 64)}
        self.target = {self.path: self.identity}
        self.existing = {self.path: ExistingFile(4, 'a' * 64, len(self.body), 1)}
        self.plan = assemble_plan(self.baseline, self.target, self.existing,
                                  {'football_data'}, allow_csv_planning=True)
        archive = self.root / f'raw-archive-v2/objects/{self.sha[:2]}/{self.sha}'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(self.body)
        self.modules = {
            'snapshot_preparation': incremental_snapshot_prepare,
            'archive_plan': incremental_archive_promotion,
            'candidate_plan': plan_incremental_candidates,
            'candidate_bundle': ingest_incremental_candidates,
            'csv_promotion': m,
        }

    @contextmanager
    def environment(self, consumer, target):
        from scripts.incremental import check_raw_updates, incremental_cache_gate
        from scripts.tools import raw_sync_write_plan, raw_sync_preflight
        from scripts.incremental.register_incremental_snapshot import Registration
        from scripts.incremental.populate_incremental_reuse import ReuseRequest, TargetState
        module = self.modules[consumer]
        real_resolve, real_is_symlink = Path.resolve, Path.is_symlink

        def resolve(path, *args, **kwargs):
            if path == self.pointer:
                return real_resolve(target, *args, **kwargs)
            return real_resolve(path, *args, **kwargs)

        def is_symlink(path):
            return path == self.pointer or real_is_symlink(path)

        request = ReuseRequest(Registration(m.REPO, 4, self.rev, self.plan.fingerprint,
                                           1, 1, 1, 0, 1, len(self.body), 1), 5, 1, 0)
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, 'resolve', resolve))
            stack.enter_context(patch.object(Path, 'is_symlink', is_symlink))
            stack.enter_context(patch('huggingface_hub.hf_hub_download', return_value=str(self.pointer)))
            for owner, name, value in (
                (check_raw_updates, 'load_baseline', (self.base, self.baseline)),
                (check_raw_updates, 'load_target', (self.rev, self.target, 0)),
                (raw_sync_write_plan, 'read_db_state', (self.base, self.existing)),
                (raw_sync_preflight, 'read_existing_source_codes', {'football_data'}),
                (incremental_cache_gate, 'cache_directory', self.cache),
            ):
                stack.enter_context(patch.object(owner, name, return_value=value))
                # Archive-plan CLI imports these functions at module load time.
                if consumer == 'archive_plan' and hasattr(module, name):
                    stack.enter_context(patch.object(module, name, return_value=value))
            stack.enter_context(patch.object(module, 'ROOT', self.root))
            if consumer == 'snapshot_preparation':
                stack.enter_context(patch.object(module, 'read_target_db_state',
                    return_value={(self.path, self.sha): None}))
            if consumer in ('candidate_plan', 'candidate_bundle'):
                state = TargetState(m.REPO, self.rev, 'in_progress', 1, 1, None, None, ())
                stack.enter_context(patch.object(module, 'inspect_db',
                    return_value=(self.base, self.existing, state)))
                stack.enter_context(patch.object(module, 'read_only_args', return_value={}))
                status = self.modules['candidate_plan'].CandidateStatus(
                    self.path, None, 'READY_INSERT_NEW_VERSION', 1, len(self.body))
                stack.enter_context(patch.object(module, 'audit_candidate_db', return_value=(status,)))
                connection = NS(cursor=lambda: nullcontext(object()))
                stack.enter_context(patch('psycopg.connect', side_effect=lambda **kw: nullcontext(connection)))
            stack.enter_context(redirect_stdout(io.StringIO()))
            yield request

    def invoke(self, consumer, request):
        module = self.modules[consumer]
        if consumer == 'csv_promotion':
            result = module.construct_plan(NS(repo_id=m.REPO, baseline_snapshot=4,
                target_revision=self.rev, expected_fingerprint=self.plan.fingerprint,
                expected_files=1, expected_bytes=len(self.body), expected_records=1,
                cache_dir='reports/cache'), root=self.root)
            self.assertEqual(result.local_files[self.path], self.blob)
            self.assertEqual(result.candidates[0].record_count, 1)
            return
        if consumer == 'candidate_bundle':
            result = module.build_verified_bundle(request, 'reports/cache', allow_csv=True)
            self.assertEqual(result.prepared[self.path].records[0][2], {'A': 'X', 'B': 'Y'})
            return
        argv = ['gate', '--baseline-snapshot', '4', '--target-revision', self.rev,
                '--expected-fingerprint', self.plan.fingerprint, '--expected-records', '1', '--plan']
        if consumer != 'archive_plan':
            argv += ['--expected-files', '1', '--expected-bytes', str(len(self.body)), '--expected-reuse', '0']
        if consumer == 'snapshot_preparation':
            argv += ['--allow-csv-preflight-only']
        elif consumer == 'archive_plan':
            argv += ['--allow-csv-archive-plan-only']
        else:
            argv += ['--target-snapshot-id', '5', '--expected-baseline-files', '1',
                     '--expected-full-files', '1', '--expected-raw-files', '1',
                     '--expected-baseline-records', '1', '--expected-reused-records', '0',
                     '--allow-csv-candidate-plan-only']
        with patch.object(sys, 'argv', argv):
            self.assertEqual(module.main(), 0)

    def test_trusted_snapshot_symlink_uses_verified_blob_in_every_csv_gate(self):
        from scripts.incremental.incremental_csv_adapter import inspect_csv_archive
        for consumer in self.modules:
            with self.subTest(consumer=consumer), self.environment(consumer, self.blob) as request:
                with self.assertRaisesRegex(ValueError, 'Symbolic links'):
                    inspect_csv_archive(self.pointer, expected_size=len(self.body),
                                        expected_lfs_sha256=self.sha)
                self.invoke(consumer, request)

    def test_snapshot_symlink_escaping_selected_cache_is_rejected_in_every_gate(self):
        outside = self.root / 'outside.csv'
        outside.write_bytes(self.body)
        for consumer in self.modules:
            with self.subTest(consumer=consumer), self.environment(consumer, outside) as request:
                with self.assertRaisesRegex(RuntimeError, 'cache|Cached|directory'):
                    self.invoke(consumer, request)

    def test_directory_cache_target_is_rejected_in_every_gate(self):
        for consumer in self.modules:
            with self.subTest(consumer=consumer), self.environment(consumer, self.cache) as request:
                with self.assertRaisesRegex(RuntimeError, 'cache|Cached|directory'):
                    self.invoke(consumer, request)

    def test_csv_blob_still_requires_the_pinned_content_hash_in_every_gate(self):
        self.blob.write_bytes(b'A,B\nQ,Z\n')
        for consumer in self.modules:
            with self.subTest(consumer=consumer), self.environment(consumer, self.blob) as request:
                with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                    self.invoke(consumer, request)


def replace_obj(obj, **kwargs):
    return NS(**{**vars(obj), **kwargs})


if __name__=='__main__':
    unittest.main()
