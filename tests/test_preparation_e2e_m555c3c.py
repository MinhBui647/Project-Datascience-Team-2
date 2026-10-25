"""M5.5.5c.3c | OFFLINE preparation-flow integration simulation.

Exercises REAL M5 checkpoint_once, plan_preparation and execute_preparation
but injects entirely SYNTHETIC M4 route/readers, source digests, artifact
verification, and subprocess launcher. The three simulated M4 children do NOT
run, no real HF/DB calls occur, and only a TemporaryDirectory receives the M5
attempt journals. This does NOT establish live new-revision readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as Obj
import unittest

from scripts.orchestration.checkpoint_lifecycle import (
    Services as CheckpointServices, checkpoint_once,
)
from scripts.orchestration.preparation_stage_coordinator import (
    PreparationReview, REPO_ID, Services as PreparationServices, preview_preparation,
)
from scripts.orchestration.guarded_preparation_executor import (
    plan_preparation, execute_preparation, PreparationGateError,
)

REV = '9' * 40  # synthetic revision, never pointed at Hugging Face
BASE = 367
MANIFEST = 'e' * 64  # synthetic independently pinned manifest digest
STAGES = ('MANIFEST_PREPARATION', 'CACHE_DOWNLOAD', 'ARCHIVE_PROMOTION')
MODULES = {
    'MANIFEST_PREPARATION': 'prepare_incremental_manifest',
    'CACHE_DOWNLOAD': 'download_incremental_candidates',
    'ARCHIVE_PROMOTION': 'promote_incremental_archive',
}
MODES = {
    'MANIFEST_PREPARATION': ('--publish','--confirm-manifest-archive-only'),
    'CACHE_DOWNLOAD': ('--download','--confirm-cache-only'),
    'ARCHIVE_PROMOTION': ('--promote','--confirm-archive-only'),
}


@dataclass(frozen=True)
class SimPlanned:
    repo_id: str = REPO_ID
    target_revision: str = REV


@dataclass(frozen=True)
class SimPrepared:
    planned: SimPlanned
    manifest_sha256: str = MANIFEST


class SimulatedM4:
    """Injected M4 fixtures; production modules are NEVER invoked."""
    def __init__(self, root: Path):
        self.root = root
        self.chain = [None, None, None]
        self.next_index = 0
        self.completed: set[str] = set()
        self.launched: list[str] = []
        self.publications: list[str] = []
        self.block_stage: str | None = None
        self.fail_stage: str | None = None
        self.stage_drift: str | None = None
        self.forced_m4_stage: str | None = None
        self.review_calls: dict[str,int] = {}
        self.external_writes = 0

    @staticmethod
    def digest(obj) -> str:
        return hashlib.sha256(repr(obj).encode('utf-8')).hexdigest()

    def read_chain(self, root, rev):
        assert root == self.root and rev == REV
        return tuple(self.chain)

    def new_plan(self, repo, rev, baseline):
        if (self.chain[0] is not None or self.next_index != 0
                or repo != REPO_ID or rev != REV or baseline != BASE):
            raise RuntimeError('Synthetic M4 new-revision planning refused')
        planned = SimPlanned()
        meta = Obj(repo_id=repo,target_revision=rev,next_review_stage='PLANNED',
                   decision='PLAN_NEW_REVISION',write_authorized=False,
                   blockers=(),planned_context_sha256=self.digest(planned))
        return meta,planned

    def cache_evidence(self, planned, root):
        if (planned != SimPlanned() or root != self.root
                or set(STAGES) != self.completed):
            raise RuntimeError('Synthetic prepared evidence incomplete')
        return 'SIMULATED_ARCHIVE_RECEIPTS'

    def build_prepared(self, planned, evidence, root, sha):
        if (planned != SimPlanned() or evidence != 'SIMULATED_ARCHIVE_RECEIPTS'
                or sha != MANIFEST or root != self.root):
            raise RuntimeError('Synthetic manifest/archives not verified')
        return SimPrepared(planned)

    def publish(self, root, context):
        if root != self.root:
            raise RuntimeError('Wrong temporary root')
        if isinstance(context, SimPrepared):
            if self.chain[0] != context.planned or self.chain[1] is not None:
                raise RuntimeError('Invalid prepared lineage')
            index, stage = 1, 'prepared'
        elif isinstance(context, SimPlanned):
            if any(self.chain):
                raise RuntimeError('Cannot overwrite planned')
            index, stage = 0, 'planned'
        else:
            raise RuntimeError('Unexpected checkpoint publication')
        self.chain[index] = context
        self.publications.append(stage)
        # Return M4-like result without creating checkpoint files.
        return f'reports/m4_6_runs/{REV}/{stage}.json', 'CREATED_NEW'

    def checkpoint_services(self):
        return CheckpointServices(
            read_chain=self.read_chain,
            new_plan=self.new_plan,
            cache_evidence=self.cache_evidence,
            prepared=self.build_prepared,
            registered=lambda _: (_ for _ in ()).throw(RuntimeError('DB disabled')),
            digest=self.digest,
            publish=self.publish,
        )

    def checkpoint(self, stage, *, publish=False, digest=None):
        return checkpoint_once(root=self.root,stage=stage,repo_id=REPO_ID,
            target_revision=REV,baseline_snapshot=BASE if stage == 'PLANNED' else None,
            expected_manifest_sha256=MANIFEST if stage == 'PREPARED' else None,
            publish=publish,approve_stage=stage if publish else None,
            expected_context_sha256=digest if publish else None,
            services=self.checkpoint_services())

    def _m4_review(self, **args):
        """Synthetic M4 ROUTE + StageReview, not a production router."""
        stage = self._requested_stage
        assert args['repo_id'] == REPO_ID and args['target_revision'] == REV
        assert args['baseline_snapshot'] == BASE and args['root'] == self.root
        self.review_calls[stage] = self.review_calls.get(stage,0) + 1
        current = STAGES[self.next_index] if self.next_index < len(STAGES) else None
        if self.forced_m4_stage is not None:
            current = self.forced_m4_stage
        if stage == self.block_stage:
            decision, next_stage, disposition, blockers = ('BLOCKED_SYNTHETIC',stage,'BLOCKED',('synthetic blocker',))
        elif self.chain[0] == SimPlanned() and self.chain[1] is None and current == stage:
            decision, next_stage, disposition, blockers = ('PLAN_NEW_REVISION',stage,'REVIEW_READ_ONLY_PLAN',())
        else:
            decision, next_stage, disposition, blockers = ('PLAN_NEW_REVISION',current,'REVIEW_READ_ONLY_PLAN',())
        return Obj(
            plan=Obj(repo_id=REPO_ID,target_revision=REV,decision=decision,
                     next_review_stage=next_stage,write_authorized=False,blockers=blockers),
            contract=Obj(write_authorized=False,disposition=disposition,
                         review_stage=next_stage,context_sha256=self.digest(self.chain[0])
                         if self.chain[0] else '0'*64,blockers=()),
        )

    def _m4_validated_commands(self, review):
        stage = self._requested_stage
        argv = [sys.executable,'-m','scripts.tools.' + MODULES[stage],
                '--repo-id',REPO_ID,'--target-revision',REV,
                '--baseline-snapshot',str(BASE),
                '--expected-fingerprint', 'a' * 64,
                '--expected-files','3','--expected-bytes','1500']
        if stage == 'MANIFEST_PREPARATION':
            argv += ['--expected-full-files','314','--expected-raw-files','296',
                     '--allow-csv-manifest-plan-only']
        elif stage == 'CACHE_DOWNLOAD':
            argv += ['--allow-csv-cache-only']
        else:
            argv += ['--expected-records','72']
        if self.stage_drift == stage and self.review_calls[stage] >= 3:
            argv[argv.index('--expected-fingerprint') + 1] = 'b'*64
        argv.append('--plan')
        return (tuple(argv),)

    def prepare_review(self, **args):
        """Exercise the REAL M5 read-only Coordinator through synthetic M4 services."""
        self._requested_stage = args['stage']
        services = PreparationServices(
            review_stage=self._m4_review,
            validated_commands=self._m4_validated_commands,
            read_chain=self.read_chain,
        )
        return preview_preparation(**args,services=services)

    def source_checker(self, root: Path, stage: str) -> tuple[str,str]:
        if root != self.root or stage not in STAGES:
            raise RuntimeError('Bad synthetic M4 source check')
        return ('1'*64,'2'*64)  # SYNTHETIC: not actual audited M4 source hashes

    def verifier(self, **args):
        stage = args['stage']
        if args['root'] != self.root or args['target_revision'] != REV:
            raise RuntimeError('Synthetic evidence identity changed')
        status = ('BLOCKED' if stage == self.block_stage else
                  'VERIFIED' if stage in self.completed else 'INCOMPLETE')
        return Obj(stage=stage,write_authorized=False,status=status)

    def launcher(self, argv, **kwargs):
        # Never launches any actual subprocess. Mimics M4 after strict argv checks.
        if kwargs.get('cwd') != str(self.root) or kwargs.get('shell') is not False:
            raise RuntimeError('Unsafe launch contract')
        if kwargs.get('check') is not False or kwargs.get('capture_output') is not True:
            raise RuntimeError('Unexpected launch arguments')
        stage = STAGES[self.next_index]
        if tuple(argv[1:3]) != ('-m','scripts.tools.'+MODULES[stage]):
            raise RuntimeError('Wrong synthetic M4 child module')
        for mode in MODES[stage]:
            if mode not in argv:
                raise RuntimeError('Missing expected writer mode')
        if stage == 'MANIFEST_PREPARATION':
            if '--expected-manifest-sha256' not in argv or argv[-1] != '--allow-csv-manifest-publish':
                raise RuntimeError('Manifest pinning or CSV opt-in missing')
            if argv[argv.index('--expected-manifest-sha256')+1] != MANIFEST:
                raise RuntimeError('Wrong synthetic manifest digest')
        if stage == 'CACHE_DOWNLOAD' and '--allow-csv-cache-only' not in argv:
            raise RuntimeError('CSV cache opt-in missing')
        self.launched.append(stage)
        if stage == self.fail_stage:
            return Obj(returncode=8)
        self.completed.add(stage)
        return Obj(returncode=0)

    def preview_stage(self, stage, *, manifest_override=None, context_override=None):
        return plan_preparation(root=self.root,repo_id=REPO_ID,target_revision=REV,
            stage=stage,baseline_snapshot=BASE,
            expected_manifest_sha256=(MANIFEST if stage == 'MANIFEST_PREPARATION' else None)
                if manifest_override is None else manifest_override,
            expected_context_sha256=context_override,
            reviewer=self.prepare_review,source_checker=self.source_checker)

    def execute_stage(self, stage, *, approved_digest=None, approved_intent=None):
        preview = self.preview_stage(stage)
        if preview.state != 'READY_FOR_EXPLICIT_APPROVAL':
            raise RuntimeError('Synthetic stage not ready: '+preview.state)
        return execute_preparation(root=self.root,repo_id=REPO_ID,target_revision=REV,
            stage=stage,baseline_snapshot=BASE,approved_stage=stage,
            expected_context_sha256=approved_digest or preview.context_sha256,
            expected_intent_sha256=approved_intent or preview.intent_sha256,
            expected_manifest_sha256=MANIFEST if stage == 'MANIFEST_PREPARATION' else None,
            reviewer=self.prepare_review,source_checker=self.source_checker,
            verifier=self.verifier,launcher=self.launcher)

    def advance_after_independent_review(self, stage):
        """Manual test-fixture advancement, NEVER an M5 executor write/advance."""
        if (self.next_index >= 3 or STAGES[self.next_index] != stage
                or stage not in self.completed or self.verifier(
                    repo_id=REPO_ID,target_revision=REV,stage=stage,root=self.root
                ).status != 'VERIFIED'):
            raise RuntimeError('Cannot simulate advancement without VERIFIED evidence')
        self.next_index += 1

    def journal_files(self, suffix):
        return tuple(self.root.rglob('*.'+suffix+'.json'))


class PreparationE2ESimulation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='m555c3c_offline_')
        self.addCleanup(temp.cleanup)
        self.fixture = SimulatedM4(Path(temp.name))

    def plan(self):
        preview = self.fixture.checkpoint('PLANNED')
        self.assertEqual(preview['state'],'REVIEW_ONLY')
        self.assertFalse(preview['write_authorized'])
        publish = self.fixture.checkpoint('PLANNED',publish=True,digest=preview['context_sha256'])
        self.assertEqual(publish['state'],'CREATED_NEW')

    def test_full_planned_manifest_cache_archive_prepared_flow(self):
        self.plan()
        for stage in STAGES:
            p = self.fixture.preview_stage(stage)
            self.assertEqual(p.state,'READY_FOR_EXPLICIT_APPROVAL')
            self.assertFalse(p.write_authorized)
            self.assertFalse(p.artifact_write_executed)
            result = self.fixture.execute_stage(stage)
            self.assertEqual(result['state'],'VERIFIED_AFTER_WRITE')
            self.assertFalse(result['stage_advanced'])
            self.assertFalse(result['db_write_executed'])
            self.fixture.advance_after_independent_review(stage)
        p = self.fixture.checkpoint('PREPARED')
        self.assertEqual(p['state'],'REVIEW_ONLY')
        pub = self.fixture.checkpoint('PREPARED',publish=True,digest=p['context_sha256'])
        self.assertEqual(pub['state'],'CREATED_NEW')
        self.assertEqual(self.fixture.publications,['planned','prepared'])
        self.assertEqual(self.fixture.launched,list(STAGES))
        self.assertEqual(len(self.fixture.journal_files('claim')),3)
        self.assertEqual(len(self.fixture.journal_files('result')),3)
        self.assertIsNone(self.fixture.chain[2])
        self.assertEqual(self.fixture.external_writes,0)

    def test_archive_cannot_be_launched_before_manifest_and_cache(self):
        self.plan()
        p = self.fixture.preview_stage('ARCHIVE_PROMOTION')
        self.assertEqual(p.state,'NOT_CURRENT_STAGE')
        self.assertEqual(self.fixture.launched,[])
        self.assertEqual(len(self.fixture.journal_files('claim')),0)

    def test_m4_sticky_manifest_recovery_does_not_authorize_cache(self):
        """The REAL M4 recovery router may retain MANIFEST_PREPARATION as entrypoint.

        This test does not assume it can resolve CACHE readiness. It proves M5
        fails closed rather than bypassing the M4 stage decision.
        """
        self.plan()
        self.fixture.execute_stage('MANIFEST_PREPARATION')
        self.fixture.advance_after_independent_review('MANIFEST_PREPARATION')
        self.fixture.forced_m4_stage='MANIFEST_PREPARATION'
        p = self.fixture.preview_stage('CACHE_DOWNLOAD')
        self.assertEqual(p.state,'NOT_CURRENT_STAGE')
        self.assertEqual(self.fixture.launched,['MANIFEST_PREPARATION'])
        self.assertEqual(len(self.fixture.journal_files('claim')),1)

    def test_prepared_checkpoint_refused_before_candidate_archive_evidence(self):
        self.plan()
        with self.assertRaisesRegex(RuntimeError,'incomplete'):
            self.fixture.checkpoint('PREPARED')
        self.assertEqual(self.fixture.publications,['planned'])

    def test_manifest_requires_independent_pin_before_claim(self):
        self.plan()
        with self.assertRaisesRegex(PreparationGateError,'independently pinned'):
            self.fixture.preview_stage('MANIFEST_PREPARATION',manifest_override='')
        self.assertEqual(self.fixture.launched,[])
        self.assertEqual(len(self.fixture.journal_files('claim')),0)

    def test_attempt_journal_prevents_second_launch(self):
        self.plan()
        result = self.fixture.execute_stage('MANIFEST_PREPARATION')
        self.assertEqual(result['state'],'VERIFIED_AFTER_WRITE')
        preview = self.fixture.preview_stage('MANIFEST_PREPARATION')
        self.assertEqual(preview.state,'BLOCKED_ATTEMPT_REVIEW')
        self.assertEqual(self.fixture.launched,['MANIFEST_PREPARATION'])
        self.assertEqual(len(self.fixture.journal_files('claim')),1)

    def test_failed_cache_does_not_advance_or_publish_prepared(self):
        self.plan()
        self.fixture.execute_stage('MANIFEST_PREPARATION')
        self.fixture.advance_after_independent_review('MANIFEST_PREPARATION')
        self.fixture.fail_stage='CACHE_DOWNLOAD'
        with self.assertRaisesRegex(PreparationGateError,'manual reconciliation'):
            self.fixture.execute_stage('CACHE_DOWNLOAD')
        self.assertEqual(self.fixture.next_index,1)
        self.assertNotIn('CACHE_DOWNLOAD',self.fixture.completed)
        self.assertEqual(self.fixture.preview_stage('CACHE_DOWNLOAD').state,'BLOCKED_ATTEMPT_REVIEW')
        with self.assertRaisesRegex(RuntimeError,'incomplete'):
            self.fixture.checkpoint('PREPARED')
        self.assertEqual(self.fixture.publications,['planned'])
        self.assertEqual(self.fixture.launched,['MANIFEST_PREPARATION','CACHE_DOWNLOAD'])

    def test_m4_evidence_block_forbids_launch(self):
        self.plan()
        self.fixture.block_stage='MANIFEST_PREPARATION'
        self.assertEqual(self.fixture.preview_stage('MANIFEST_PREPARATION').state,'BLOCKED')
        self.assertEqual(self.fixture.launched,[])
        self.assertEqual(len(self.fixture.journal_files('claim')),0)

    def test_changed_fresh_review_forbids_launch(self):
        self.plan()
        self.fixture.stage_drift='MANIFEST_PREPARATION'
        with self.assertRaisesRegex(PreparationGateError,'drift'):
            self.fixture.execute_stage('MANIFEST_PREPARATION')
        self.assertEqual(self.fixture.launched,[])
        self.assertEqual(len(self.fixture.journal_files('claim')),0)

    def test_wrong_approval_hash_does_not_create_journal(self):
        self.plan()
        with self.assertRaisesRegex(PreparationGateError,'Intent SHA256'):
            self.fixture.execute_stage('MANIFEST_PREPARATION',approved_intent='f'*64)
        self.assertEqual(self.fixture.launched,[])
        self.assertEqual(len(self.fixture.journal_files('claim')),0)

if __name__ == '__main__':
    unittest.main()
