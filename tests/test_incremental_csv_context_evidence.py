"""CSV plan checkpoint/manifest/recovery proofs; no production DB writes."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from scripts.incremental.incremental_execution_context import (
    CandidateSpec, CandidateReceipt, PlannedContext, PreparedContext, RegisteredContext,
    build_planned, build_prepared, checkpoint_digest, candidate_format,
    publish_checkpoint, read_checkpoint,
)
from scripts.incremental.incremental_recovery_audit import (
    verified_manifest, classify_registered, SnapshotEvidence, ObservationEvidence,
    PhysicalEvidence, DatabaseEvidence,
)
from scripts.incremental.register_incremental_manifest import validate_observations

A, B = 'a'*40, 'b'*40
SHA = 'c'*64
P = '2026_2027_new/football_data_raw_2627.csv'
P_JSON = 'raw_data/fotmob/match.json'


def planned_for(path=P):
    spec = CandidateSpec(path,'NEW',12,'fotmob',None,SHA)
    return PlannedContext(
        'sample/epl',3,A,B,'d'*64,1,2,1,0,1,12,1,0,(spec,)
    )


def manifest_for(planned, fmt=None):
    p = planned.candidates[0]
    return {
        'repo_id':planned.repo_id,'repo_type':'dataset',
        'resolved_commit':planned.target_revision, 'file_count':planned.expected_full_files,
        'files': [
            {'relative_path': p.path, 'hf_revision':planned.target_revision,
             'size_bytes':p.expected_bytes, 'git_blob_id':None,
             'lfs_sha256':p.lfs_sha256,'format':fmt or candidate_format(p.path)},
            {'relative_path':'README.md', 'hf_revision':planned.target_revision,
             'size_bytes':100, 'git_blob_id':'f'*40, 'lfs_sha256':None, 'format':'md'},
        ]
    }


def manifest_bytes(planned, fmt=None):
    return json.dumps(manifest_for(planned,fmt),sort_keys=True).encode()


def prepared_for(path=P):
    plan=planned_for(path)
    receipt=CandidateReceipt(path,12,SHA,2)
    return build_prepared(plan,(receipt,),manifest_bytes=manifest_bytes(plan))


def download_for(path=P, fmt=None):
    identity=SimpleNamespace(size=12,git_blob_id=None,lfs_sha256=SHA)
    action=SimpleNamespace(path=path,kind='UPSERT_VERSION_AFTER_REHASH',category='NEW',
      file_format=fmt or candidate_format(path),source_code='fotmob',expected_bytes=12,
      git_blob_id=None,lfs_sha256=SHA)
    plan=SimpleNamespace(actions=(action,),target_raw_count=1,
        baseline_records=1,reused_records=0,superseded_records=1,candidate_bytes=12,
        fingerprint='d'*64,count=lambda cat: 0 if cat in ('REMOVED','REVIEW','UNCHANGED') else 1 if cat=='NEW' else 0)
    return SimpleNamespace(plan=plan,actions=(action,),target={path: identity},
        repo_id='sample/epl',baseline_snapshot=3,baseline_revision=A,
        target_revision=B,excluded=1)


class CsvContextEvidenceTests(unittest.TestCase):
    def test_csv_spec_receipt_allowed(self):
        spec=planned_for().candidates[0]
        self.assertEqual(candidate_format(spec.path),'csv')
        self.assertEqual(CandidateReceipt(P,12,SHA,2).records,2)

    def test_tar_path_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'candidate'):
            CandidateSpec('raw_data/a.tar.gz','NEW',12,'fotmob',None,SHA)

    def test_unsafe_csv_path_rejected(self):
        with self.assertRaises(ValueError):
            CandidateSpec('raw_data/../outside.csv','NEW',12,'fotmob',None,SHA)

    def test_csv_context_requires_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            build_planned(download_for(),expected_baseline_files=1,expected_full_files=2)

    def test_csv_context_opt_in(self):
        result=build_planned(download_for(),expected_baseline_files=1,
                             expected_full_files=2,allow_csv_context=True)
        self.assertEqual(result.candidates[0].path,P)
        self.assertEqual(result.expected_files,1)

    def test_json_context_default_unchanged(self):
        result=build_planned(download_for(P_JSON),expected_baseline_files=1,
                             expected_full_files=2)
        self.assertEqual(result.candidates[0].path,P_JSON)

    def test_reject_format_spoof(self):
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            build_planned(download_for(P,fmt='json'),expected_baseline_files=1,
                           expected_full_files=2,allow_csv_context=True)

    def test_bad_optin_type(self):
        with self.assertRaisesRegex(ValueError, 'opt-in'):
            build_planned(download_for(),expected_baseline_files=1,
                expected_full_files=2,allow_csv_context=1)

    def test_csv_manifest_preparation_pass(self):
        self.assertEqual(prepared_for().expected_records,2)

    def test_csv_manifest_format_spoof_rejected(self):
        plan=planned_for()
        with self.assertRaisesRegex(ValueError,'Manifest candidate identity'):
            build_prepared(plan,(CandidateReceipt(P,12,SHA,2),),
                manifest_bytes=manifest_bytes(plan,'json'))

    def test_legacy_json_checkpoint_payload_digest_unchanged(self):
        context=planned_for(P_JSON)
        # Golden value captures pre-CSV dataclass v1 serialization contract.
        self.assertEqual(checkpoint_digest(context), JSON_GOLDEN)

    def test_csv_checkpoint_roundtrip(self):
        plan=planned_for()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            rel, status=publish_checkpoint(root,plan)
            self.assertEqual(status,'CREATED_NEW')
            self.assertEqual(read_checkpoint(root,B,'planned'),plan)
            self.assertEqual(publish_checkpoint(root,plan)[1],'ALREADY_VERIFIED')

    def test_recovery_manifest_csv(self):
        prepared=prepared_for()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for rel in (prepared.manifest_report,prepared.manifest_archive):
                path=root/rel
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(manifest_bytes(prepared.planned))
            raw=verified_manifest(root,prepared)
            self.assertEqual(raw[P]['format'],'csv')

    def test_recovery_manifest_rejects_json_spoof(self):
        prepared=prepared_for()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for rel in (prepared.manifest_report,prepared.manifest_archive):
                path=root/rel
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(manifest_bytes(prepared.planned,'json'))
            # Manifest pinned sha no longer matches forged bytes -> fail closed.
            with self.assertRaisesRegex(RuntimeError,'SHA256|metadata drift'):
                verified_manifest(root,prepared)

    def test_recovery_csv_candidate_physical(self):
        prepared=prepared_for()
        registered=RegisteredContext(prepared,9)
        snapshot=lambda ident,rev,status: SnapshotEvidence(ident,'sample/epl',rev,status,2,1,None,None)
        obs=ObservationEvidence(P,'sample/epl','linked',12,None,SHA,101,'sample/epl',P,
          'csv',12,SHA,'raw-archive-v2/objects/'+SHA[:2]+'/'+SHA,'verified','success',2,
          'fotmob',None,None,None,None)
        evidence=DatabaseEvidence(3,A,snapshot(3,A,'completed'),snapshot(9,B,'in_progress'),
          (obs,),(PhysicalEvidence(101,2,2,0),),1)
        result=classify_registered(registered,evidence, {P:manifest_for(prepared.planned)['files'][0]})
        self.assertEqual(result.recovery_state,'CANDIDATES_COMPLETE')
        self.assertFalse(result.write_authorized)

    def test_recovery_wrong_csv_db_format_fails(self):
        prepared=prepared_for()
        registered=RegisteredContext(prepared,9)
        snapshot=lambda ident,rev,status: SnapshotEvidence(ident,'sample/epl',rev,status,2,1,None,None)
        obs=ObservationEvidence(P,'sample/epl','linked',12,None,SHA,101,'sample/epl',P,
          'json',12,SHA,'raw-archive-v2/objects/'+SHA[:2]+'/'+SHA,'verified','success',2,
          'fotmob',None,None,None,None)
        evidence=DatabaseEvidence(3,A,snapshot(3,A,'completed'),snapshot(9,B,'in_progress'),
          (obs,),(PhysicalEvidence(101,2,2,0),),1)
        with self.assertRaisesRegex(RuntimeError,'Candidate version/receipt mismatch'):
            classify_registered(registered,evidence,{P:manifest_for(prepared.planned)['files'][0]})

    def test_manifest_registration_csv_candidate(self):
        action=SimpleNamespace(path=P, kind='UPSERT_VERSION_AFTER_REHASH',
          category='NEW',file_format='csv',source_code='fotmob',
          expected_bytes=12,git_blob_id=None,lfs_sha256=SHA)
        plan=SimpleNamespace(actions=(action,))
        reg=SimpleNamespace(repo_id='sample/epl', raw_files=1,reuse_files=0,
          candidate_files=1,candidate_records=2)
        req=SimpleNamespace(reuse=SimpleNamespace(registration=reg,expected_reused_records=0))
        row=(P,'sample/epl','linked',12,None,SHA,101,'sample/epl',P,12,SHA,
          'raw-archive-v2/objects/'+SHA[:2]+'/'+SHA,'verified','success',2,12,'fotmob','csv')
        counts,summary=validate_observations((row,),{P:manifest_for(planned_for())['files'][0]},
           plan,(),req)
        self.assertEqual(counts,{101:2})
        self.assertEqual(summary.candidates,1)

    def test_manifest_registration_csv_path_spoof_rejected(self):
        action=SimpleNamespace(path=P, kind='UPSERT_VERSION_AFTER_REHASH',
          category='NEW',file_format='json',source_code='fotmob',
          expected_bytes=12,git_blob_id=None,lfs_sha256=SHA)
        plan=SimpleNamespace(actions=(action,))
        reg=SimpleNamespace(repo_id='sample/epl', raw_files=1,reuse_files=0,
          candidate_files=1,candidate_records=2)
        req=SimpleNamespace(reuse=SimpleNamespace(registration=reg,expected_reused_records=0))
        row=(P,'sample/epl','linked',12,None,SHA,101,'sample/epl',P,12,SHA,
          'raw-archive-v2/objects/'+SHA[:2]+'/'+SHA,'verified','success',2,12,'fotmob','csv')
        with self.assertRaisesRegex(RuntimeError,'Candidate path/source'):
            validate_observations((row,),{P:manifest_for(planned_for())['files'][0]},
                plan,(),req)

# Updated by patch packaging script with a golden from the original source.
JSON_GOLDEN = 'd730dfddf2346f1bf8794fe8b412c36fb563f7ec9b2110ce813becdc61657edc'

if __name__ == '__main__':
    unittest.main()
