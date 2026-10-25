#!/usr/bin/env python3
"""M4.6.4d.6c.1 offline contract, cache, manifest and DB-evidence tests."""
from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from scripts.tools.incremental_execution_context import (
    CandidateReceipt, PlannedContext, PreparedContext, RegisteredContext,
)
from scripts.tools.incremental_archive_promotion import ArchiveCandidate
from scripts.tools.incremental_runner_contracts import (
    CacheEvidence, RegistrationRow, PLAN_MODULES,
    _cache_relative, _check_evidence, receipts_from_archive_audit,
    audit_cache_evidence, plan_command, build_prepared_from_archives,
    build_registered_from_db,
)
from tests.test_incremental_execution_context_m46 import (
    planned, prepared, registered, receipts, fake_download_context, manifest,
    payload, spec_a, spec_b,
)

ROOT = Path(__file__).resolve().parents[1]


def archive_rows():
    return (
        ArchiveCandidate(spec_a().path, receipts()[0].content_sha256, 10, 2,
                         'raw-archive-v2/objects/cc/' + 'c'*64, 'READY_NEW'),
        ArchiveCandidate(spec_b().path, receipts()[1].content_sha256, 20, 3,
                         'raw-archive-v2/objects/bb/' + 'b'*64, 'ALREADY_ARCHIVED'),
    )


def evidence():
    return receipts_from_archive_audit(planned(), archive_rows())


class PureCacheConversionTests(unittest.TestCase):
    def test_valid_audited_rows(self):
        ev = evidence()
        self.assertEqual(ev.expected_records, 5)
        self.assertEqual((ev.ready_new, ev.already_archived), (1, 1))
        self.assertEqual(ev.receipts, receipts())

    def test_sorted_input_not_required(self):
        self.assertEqual(receipts_from_archive_audit(planned(), archive_rows()[::-1]), evidence())

    def test_empty_candidates(self):
        with self.assertRaises(ValueError): receipts_from_archive_audit(planned(), ())

    def test_duplicate_paths(self):
        with self.assertRaises(ValueError): receipts_from_archive_audit(planned(), (archive_rows()[0],)*2)

    def test_missing_candidate(self):
        with self.assertRaises(RuntimeError): receipts_from_archive_audit(planned(), archive_rows()[:1])

    def test_wrong_candidate_path(self):
        bad = replace(archive_rows()[0], relative_path='raw_data/other.json')
        with self.assertRaises(RuntimeError): receipts_from_archive_audit(planned(), (bad, archive_rows()[1]))

    def test_wrong_size(self):
        bad = replace(archive_rows()[0], byte_count=11)
        with self.assertRaises(RuntimeError): receipts_from_archive_audit(planned(), (bad, archive_rows()[1]))

    def test_wrong_records(self):
        bad = replace(archive_rows()[0], record_count=0)
        with self.assertRaises(ValueError): receipts_from_archive_audit(planned(), (bad, archive_rows()[1]))

    def test_wrong_lfs_sha(self):
        newsha = 'd'*64
        bad = replace(archive_rows()[1], sha256=newsha,
                      archive_relative_path=f'raw-archive-v2/objects/{newsha[:2]}/{newsha}')
        with self.assertRaises(RuntimeError): receipts_from_archive_audit(planned(), (archive_rows()[0], bad))

    def test_bad_sha(self):
        with self.assertRaises(ValueError): receipts_from_archive_audit(
            planned(), (replace(archive_rows()[0], sha256='bad'), archive_rows()[1]))

    def test_bad_archive_object_path(self):
        with self.assertRaises(ValueError): receipts_from_archive_audit(
            planned(), (replace(archive_rows()[0], archive_relative_path='raw-archive-v2/else'), archive_rows()[1]))

    def test_bad_archive_action(self):
        with self.assertRaises(ValueError): receipts_from_archive_audit(
            planned(), (replace(archive_rows()[0], action='CORRUPT'), archive_rows()[1]))

    def test_changed_planned_context(self):
        with self.assertRaises(RuntimeError): _check_evidence(
            replace(planned(), expected_baseline_records=101), evidence())

    def test_wrong_total(self):
        with self.assertRaises(ValueError): replace(evidence(), expected_records=4)

    def test_wrong_state_count(self):
        with self.assertRaises(ValueError): replace(evidence(), ready_new=2)

    def test_false_boolean_state_count(self):
        with self.assertRaises(ValueError): replace(evidence(), ready_new=True)

    def test_sha_of_context_is_pinned(self):
        with self.assertRaises(RuntimeError): _check_evidence(
            planned(), replace(evidence(), planned_sha256='e'*64))


class FreshCacheAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []

    def loader(self, args):
        self.calls.append(('loader', args.target_revision, args.expected_fingerprint))
        return fake_download_context()

    def validator(self, path, **kw):
        self.calls.append(('validator', path, kw.get('create')))
        return self.root

    def resolver(self, ctx, cache):
        self.calls.append(('resolver', ctx.target_revision))
        return {x.path: self.root / str(i) for i, x in enumerate(ctx.actions)}

    def auditor(self, plan, target, local, root):
        self.calls.append(('auditor', tuple(sorted(local))))
        return archive_rows()

    def run_audit(self, **kw):
        return audit_cache_evidence(planned(), root=self.root,
            context_loader=kw.get('context_loader', self.loader),
            cache_validator=kw.get('cache_validator', self.validator),
            resolver=kw.get('resolver', self.resolver),
            auditor=kw.get('auditor', self.auditor))

    def test_valid_fresh_audit(self):
        self.assertEqual(self.run_audit(), evidence())
        self.assertEqual([row[0] for row in self.calls],
                         ['loader','validator','resolver','auditor'])

    def test_plan_drift_fails_before_cache_read(self):
        wrong = fake_download_context()
        wrong.target_revision = '8'*40
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_audit(context_loader=lambda args: wrong)
        self.assertEqual(self.calls, [])

    def test_source_revision_drift(self):
        wrong = fake_download_context()
        wrong.baseline_revision = '8'*40
        with self.assertRaises(RuntimeError): self.run_audit(context_loader=lambda args: wrong)

    def test_candidate_audit_missing(self):
        with self.assertRaises(RuntimeError):
            self.run_audit(auditor=lambda *args: archive_rows()[:1])

    def test_archive_audit_failure_propagates(self):
        def broken(*_): raise RuntimeError('cache object corrupted')
        with self.assertRaisesRegex(RuntimeError, 'corrupted'):
            self.run_audit(auditor=broken)

    def test_invalid_cache_dir_before_load(self):
        with self.assertRaises(ValueError):
            audit_cache_evidence(planned(), cache_dir='../../etc', root=self.root,
                context_loader=self.loader)
        self.assertFalse(self.calls)

    def test_type_of_context(self):
        with self.assertRaises(TypeError): audit_cache_evidence('incorrect')


class CommandPreviewTests(unittest.TestCase):
    def test_allowlist_contains_all_ten_steps(self):
        self.assertEqual(len(PLAN_MODULES), 10)

    def test_planned_commands(self):
        for step in ('download','manifest_preparation'):
            cmd = plan_command(step, planned())
            self.assertEqual(cmd[:2], (sys.executable, '-m'))
            self.assertEqual(cmd[-1], '--plan')
            self.assertEqual(cmd[2], 'scripts.tools.'+PLAN_MODULES[step])

    def test_prepared_commands(self):
        for step in ('snapshot_preparation', 'snapshot_registration'):
            self.assertIn('--expected-records', plan_command(step, prepared()))

    def test_registered_commands(self):
        for step in ('reuse','candidate_planner','candidate_ingestion',
                     'manifest_registration','finalization'):
            cmd = plan_command(step, registered())
            self.assertIn('--target-snapshot-id', cmd)
            self.assertEqual(cmd[-1], '--plan')

    def test_promote_before_prepared(self):
        cmd = plan_command('archive_promotion', planned(), cache_evidence=evidence())
        self.assertIn('--expected-records', cmd)
        self.assertEqual(cmd[cmd.index('--expected-records')+1], '5')
        self.assertEqual(cmd[-1], '--plan')

    def test_promote_rejects_prepared_context(self):
        with self.assertRaises(ValueError): plan_command('archive_promotion', prepared(), cache_evidence=evidence())

    def test_promote_requires_audit_evidence(self):
        with self.assertRaises(TypeError): plan_command('archive_promotion', planned())

    def test_promote_rejects_evidence_drift(self):
        with self.assertRaises(RuntimeError): plan_command('archive_promotion',
            replace(planned(), expected_baseline_records=101), cache_evidence=evidence())

    def test_other_stages_reject_receipts(self):
        with self.assertRaises(ValueError): plan_command('download', planned(), cache_evidence=evidence())

    def test_invalid_stage(self):
        with self.assertRaises(ValueError): plan_command('commit-all', registered())

    def test_wrong_context_stage(self):
        with self.assertRaises(ValueError): plan_command('finalization', planned())

    def test_every_preview_has_no_write_flags(self):
        commands = [plan_command('manifest_preparation', planned()),
                    plan_command('download', planned()),
                    plan_command('archive_promotion', planned(), cache_evidence=evidence()),
                    plan_command('snapshot_preparation', prepared()),
                    plan_command('snapshot_registration', prepared())]
        commands += [plan_command(step, registered()) for step in (
            'reuse','candidate_planner','candidate_ingestion','manifest_registration','finalization')]
        write_flags = {'--apply','--commit','--promote','--publish','--register',
                       '--download','--link','--confirm-cache-only',
                       '--confirm-archive-only','--confirm-manifest-only',
                       '--confirm-snapshot-only','--confirm-candidate-write',
                       '--confirm-reuse-only','--confirm-complete'}
        for cmd in commands:
            self.assertFalse(write_flags.intersection(cmd), cmd)
            self.assertEqual(cmd.count('--plan'), 1)
            self.assertEqual(cmd[1], '-m')

    def test_safe_cache_dir(self):
        self.assertEqual(_cache_relative('reports/cache-a'), 'reports/cache-a')

    def test_unsafe_cache_dir(self):
        for value in ('../reports/cache', '/tmp/cache','cache','reports/../else',
                      'reports//cache','reports/\\cache','reports/x\nthing', '', 'reports/'):
            with self.subTest(value=value), self.assertRaises(ValueError): _cache_relative(value)

    def test_download_command_is_accepted_by_real_parser(self):
        from scripts.tools.download_incremental_candidates import parse_args
        cmd = plan_command('download', planned())
        parsed = parse_args(cmd[3:])
        self.assertTrue(parsed.plan)
        self.assertEqual(parsed.expected_fingerprint, planned().plan_fingerprint)
        self.assertFalse(parsed.download)

    def test_manifest_command_is_accepted_by_real_parser(self):
        from scripts.tools.prepare_incremental_manifest import parse_args
        parsed = parse_args(plan_command('manifest_preparation', planned())[3:])
        self.assertTrue(parsed.plan)
        self.assertFalse(parsed.publish)
        self.assertEqual(parsed.expected_full_files, planned().expected_full_files)

    def test_promotion_command_is_accepted_by_real_parser(self):
        from scripts.tools.promote_incremental_archive import parse_args
        parsed = parse_args(plan_command('archive_promotion', planned(),
                                         cache_evidence=evidence())[3:])
        self.assertTrue(parsed.plan)
        self.assertEqual(parsed.expected_records, 5)
        self.assertFalse(parsed.promote)

    def test_ingestion_command_is_accepted_by_real_parser(self):
        from scripts.tools.ingest_incremental_candidates import make_parser, check_mode
        parser = make_parser()
        parsed = parser.parse_args(plan_command('candidate_ingestion', registered())[3:])
        check_mode(parsed, parser)
        self.assertTrue(parsed.plan)
        self.assertFalse(parsed.apply)

    def test_finalization_command_is_accepted_by_real_parser(self):
        from scripts.tools.finalize_incremental_snapshot import make_parser, check_mode
        parser = make_parser()
        parsed = parser.parse_args(plan_command('finalization', registered())[3:])
        check_mode(parsed, parser)
        self.assertTrue(parsed.plan)
        self.assertFalse(parsed.commit)

    def test_module_flags_match_actual_parsers_from_ast(self):
        """Cheap offline API drift test: do not call CLI main() / DB / HF."""
        options = {'download': planned(), 'manifest_preparation': planned(),
                   'archive_promotion': planned(),
                   'snapshot_preparation': prepared(), 'snapshot_registration': prepared()}
        options.update({step: registered() for step in (
            'reuse','candidate_planner','candidate_ingestion','manifest_registration','finalization')})
        for step, context in options.items():
            cmd = (plan_command(step, context, cache_evidence=evidence())
                   if step=='archive_promotion' else plan_command(step, context))
            module_name = PLAN_MODULES[step]
            module_path = ROOT/'scripts'/'tools'/(module_name+'.py')
            canonical_path = ROOT/'scripts'/'incremental'/(module_name+'.py')
            if canonical_path.is_file():
                wrapper_source = module_path.read_text(encoding='utf-8')
                expected_alias = '_CANONICAL = "scripts.incremental.' + module_name + '"'
                self.assertIn(expected_alias, wrapper_source)
                module_path = canonical_path
            tree = ast.parse(module_path.read_text(encoding='utf-8'))
            supported = {arg.value for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and node.func.attr == 'add_argument'
                         for arg in node.args if isinstance(arg, ast.Constant)
                         and isinstance(arg.value, str) and arg.value.startswith('--')}
            flags = {arg for arg in cmd[3:] if arg.startswith('--')}
            self.assertFalse(flags - supported, (step, flags-supported))


class PreparedEvidenceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.planned = planned()
        a = b'x'*10
        b = b'y'*20
        self.a_digest, self.b_digest = hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()
        candidate_b = replace(spec_b(), lfs_sha256=self.b_digest)
        self.planned = replace(planned(), candidates=(spec_a(), candidate_b))
        self.receipts = (
            CandidateReceipt(spec_a().path, len(a), self.a_digest, 2),
            CandidateReceipt(spec_b().path, len(b), self.b_digest, 3),
        )
        self.evidence = CacheEvidence(
            __import__('scripts.tools.incremental_execution_context', fromlist=['checkpoint_digest']).checkpoint_digest(self.planned),
            self.receipts, 5, 1, 1)
        m = manifest()
        for r in m['files']:
            if r['relative_path'] == spec_b().path:
                r['lfs_sha256'] = self.b_digest
        self.payload = (json.dumps(m, sort_keys=True)+'\n').encode('utf-8')
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.report = f'reports/m4_6_manifests/{self.planned.target_revision}/hf_manifest_v2.json'
        self.manifest_archive = f'raw-archive-v2/manifests/{self.digest[:2]}/{self.digest}.json'
        for relative in (self.report, self.manifest_archive):
            dest = self.root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.payload)
        for data, receipt in zip((a,b), self.receipts):
            archive = self.root / f'raw-archive-v2/objects/{receipt.content_sha256[:2]}/{receipt.content_sha256}'
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(data)

    def call(self):
        return build_prepared_from_archives(self.planned, self.evidence,
                 root=self.root, expected_manifest_sha256=self.digest)

    def test_prepared_from_actual_files(self):
        ctx = self.call()
        self.assertIsInstance(ctx, PreparedContext)
        self.assertEqual(ctx.expected_records, 5)
        self.assertEqual(ctx.expected_record_references, 85)
        self.assertEqual(ctx.manifest_sha256, self.digest)

    def test_wrong_manifest_pinned_hash(self):
        with self.assertRaises(RuntimeError): build_prepared_from_archives(
            self.planned, self.evidence, root=self.root, expected_manifest_sha256='f'*64)

    def test_invalid_manifest_sha(self):
        with self.assertRaises(ValueError): build_prepared_from_archives(
            self.planned, self.evidence, root=self.root, expected_manifest_sha256='short')

    def test_manifest_byte_corruption(self):
        (self.root/self.report).write_bytes(b'invalid')
        with self.assertRaises(RuntimeError): self.call()

    def test_manifest_archive_missing(self):
        (self.root/self.manifest_archive).unlink()
        with self.assertRaises(RuntimeError): self.call()

    def test_archive_content_corruption(self):
        r = self.receipts[0]
        (self.root/f'raw-archive-v2/objects/{r.content_sha256[:2]}/{r.content_sha256}').write_bytes(b'z'*10)
        with self.assertRaises(RuntimeError): self.call()

    def test_archive_missing(self):
        r = self.receipts[1]
        (self.root/f'raw-archive-v2/objects/{r.content_sha256[:2]}/{r.content_sha256}').unlink()
        with self.assertRaises(RuntimeError): self.call()

    def test_manifest_candidate_identity_drift(self):
        m = json.loads(self.payload)
        for r in m['files']:
            if r['relative_path'] == spec_a().path: r['size_bytes'] = 11
        updated = (json.dumps(m, sort_keys=True)+'\n').encode()
        new_digest = hashlib.sha256(updated).hexdigest()
        (self.root/self.report).write_bytes(updated)
        (self.root/self.manifest_archive).unlink()
        new_archive = self.root/f'raw-archive-v2/manifests/{new_digest[:2]}/{new_digest}.json'
        new_archive.parent.mkdir(parents=True, exist_ok=True)
        new_archive.write_bytes(updated)
        with self.assertRaises(ValueError): build_prepared_from_archives(self.planned,
            self.evidence, root=self.root, expected_manifest_sha256=new_digest)

    def test_context_drift(self):
        with self.assertRaises(RuntimeError): build_prepared_from_archives(
            replace(self.planned, expected_baseline_records=101), self.evidence,
            root=self.root, expected_manifest_sha256=self.digest)

    @unittest.skipUnless(hasattr(Path, 'symlink_to'), 'No symlink support')
    def test_manifest_symlink_blocked(self):
        r = self.root/self.report
        r.unlink()
        outside = self.root/'external.json'
        outside.write_bytes(self.payload)
        try: r.symlink_to(outside)
        except (OSError, NotImplementedError): self.skipTest('Symlink unavailable')
        with self.assertRaises(RuntimeError): self.call()


class RegistrationEvidenceTests(unittest.TestCase):
    def test_valid_independent_read(self):
        p = prepared().planned
        r = RegistrationRow(366,p.repo_id,p.target_revision,'in_progress',
                            p.expected_full_files,p.expected_raw_files,None,None,
                            p.baseline_snapshot_id)
        self.assertEqual(build_registered_from_db(prepared(), reader=lambda pre:r).target_snapshot_id, 366)

    def test_missing_row(self):
        with self.assertRaises(TypeError): build_registered_from_db(prepared(), reader=lambda pre:None)

    def test_row_wrong_type(self):
        with self.assertRaises(TypeError): build_registered_from_db(prepared(), reader=lambda pre:object())

    def test_latest_baseline_drift(self):
        p = prepared().planned
        row = RegistrationRow(366,p.repo_id,p.target_revision,'in_progress',4,3,None,None,999)
        with self.assertRaises(RuntimeError): build_registered_from_db(prepared(), reader=lambda pre:row)

    def test_row_duplicate_snapshot_id(self):
        p = prepared().planned
        row = RegistrationRow(365,p.repo_id,p.target_revision,'in_progress',4,3,None,None,365)
        with self.assertRaises(RuntimeError): build_registered_from_db(prepared(), reader=lambda pre:row)

    def test_wrong_target_revision(self):
        p = prepared().planned
        row = RegistrationRow(366,p.repo_id,'9'*40,'in_progress',4,3,None,None,365)
        with self.assertRaises(ValueError): build_registered_from_db(prepared(), reader=lambda pre:row)

    def test_manifest_registered_early(self):
        p = prepared().planned
        row = RegistrationRow(366,p.repo_id,p.target_revision,'in_progress',4,3,'a'*64,None,365)
        with self.assertRaises(ValueError): build_registered_from_db(prepared(), reader=lambda pre:row)

    def test_completed_target_not_registered_checkpoint(self):
        p = prepared().planned
        row = RegistrationRow(366,p.repo_id,p.target_revision,'completed',4,3,None,None,365)
        with self.assertRaises(ValueError): build_registered_from_db(prepared(), reader=lambda pre:row)

    def test_invalid_record_counts(self):
        p = prepared().planned
        row = RegistrationRow(366,p.repo_id,p.target_revision,'in_progress',True,3,None,None,365)
        with self.assertRaises(RuntimeError): build_registered_from_db(prepared(), reader=lambda pre:row)


if __name__ == '__main__': unittest.main()
