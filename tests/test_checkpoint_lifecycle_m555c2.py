"""Offline contract tests. M4 evidence and publish are injected only for tests."""
import hashlib
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from scripts.orchestration.checkpoint_lifecycle import Services, checkpoint_once

REPO = 'Toibinguz/epl-premier-league-dataset'
REV = 'a' * 40
MANIFEST = 'b' * 64


@dataclass(frozen=True)
class Planned:
    repo_id: str = REPO
    target_revision: str = REV


@dataclass(frozen=True)
class Prepared:
    planned: Planned


@dataclass(frozen=True)
class Registered:
    prepared: Prepared


class Fixture:
    def __init__(self):
        self.chain = [None, None, None]
        self.publications = 0
        self.new_plans = 0
        self.drift = False
        self.fail_new_plan = False
        self.prepared_calls = 0
        self.registered_calls = 0
        self.stage_digest_override = None

    def digest(self, obj):
        return hashlib.sha256(repr(obj).encode()).hexdigest()

    def read_chain(self, root, rev):
        assert rev == REV
        return tuple(self.chain)

    def new_plan(self, repo, rev, baseline):
        self.new_plans += 1
        if self.fail_new_plan:
            raise RuntimeError('M4 revision already processed')
        planned = Planned(target_revision='c' * 40) if self.drift and self.new_plans == 2 else Planned()
        summary = SimpleNamespace(repo_id=repo, target_revision=rev,
                                  next_review_stage='PLANNED', decision='PLAN_NEW_REVISION',
                                  write_authorized=False, blockers=(),
                                  planned_context_sha256=self.digest(planned))
        return summary, planned

    def prepared(self, planned, evidence, root, manifest):
        self.prepared_calls += 1
        assert evidence == 'CACHE_OK' and manifest == MANIFEST
        return Prepared(planned)

    def registered(self, prepared):
        self.registered_calls += 1
        return Registered(prepared)

    def publish(self, root, context):
        self.publications += 1
        if isinstance(context, Registered):
            i, stage = 2, 'registered'
        elif isinstance(context, Prepared):
            i, stage = 1, 'prepared'
        else:
            i, stage = 0, 'planned'
        if self.chain[i] is not None and self.chain[i] != context:
            raise RuntimeError('collision')
        self.chain[i] = context
        return f'reports/m4_6_runs/{REV}/{stage}.json', 'CREATED_NEW'

    def services(self):
        return Services(
            read_chain=self.read_chain,
            new_plan=self.new_plan,
            cache_evidence=lambda planned, root: 'CACHE_OK',
            prepared=self.prepared,
            registered=self.registered,
            digest=self.digest,
            publish=self.publish,
        )


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.fixture = Fixture()
        self.svc = self.fixture.services()

    def call(self, stage='PLANNED', **kwargs):
        defaults = dict(root=self.root, stage=stage, repo_id=REPO,
                        target_revision=REV, services=self.svc,
                        baseline_snapshot=367 if stage == 'PLANNED' else None,
                        expected_manifest_sha256=MANIFEST if stage == 'PREPARED' else None)
        defaults.update(kwargs)
        return checkpoint_once(**defaults)

    def approve(self, stage, digest):
        return self.call(stage, publish=True, approve_stage=stage,
                         expected_context_sha256=digest)

    def test_new_planned_preview_no_publication(self):
        result = self.call()
        self.assertEqual(result['state'], 'REVIEW_ONLY')
        self.assertEqual(self.fixture.publications, 0)
        self.assertFalse(result['write_authorized'])

    def test_new_planned_requires_explicit_stage_approval(self):
        sha = self.call()['context_sha256']
        with self.assertRaises(ValueError):
            self.call(publish=True, expected_context_sha256=sha)
        self.assertEqual(self.fixture.publications, 0)

    def test_planned_refuses_wrong_digest(self):
        with self.assertRaisesRegex(RuntimeError, 'digest'):
            self.approve('PLANNED', 'f' * 64)
        self.assertEqual(self.fixture.publications, 0)

    def test_planned_publishes_only_after_two_reviews(self):
        sha = self.call()['context_sha256']
        result = self.approve('PLANNED', sha)
        self.assertEqual(result['state'], 'CREATED_NEW')
        self.assertEqual(self.fixture.publications, 1)
        self.assertIsInstance(self.fixture.chain[0], Planned)
        self.assertGreaterEqual(self.fixture.new_plans, 3)

    def test_planned_review_drift_blocks_publication(self):
        sha = self.call()['context_sha256']
        self.fixture.new_plans = 0
        self.fixture.drift = True
        with self.assertRaises(RuntimeError):
            self.approve('PLANNED', sha)
        self.assertEqual(self.fixture.publications, 0)

    def test_existing_planned_not_overwritten(self):
        self.fixture.chain[0] = Planned()
        with self.assertRaisesRegex(RuntimeError, 'Already checkpointed'):
            self.call()
        self.assertEqual(self.fixture.new_plans, 0)

    def test_completed_revision_cannot_be_backfilled_as_planned(self):
        self.fixture.fail_new_plan = True
        with self.assertRaisesRegex(RuntimeError, 'already processed'):
            self.call()
        self.assertEqual(self.fixture.publications, 0)

    def test_prepared_requires_planned(self):
        with self.assertRaisesRegex(RuntimeError, 'requires planned'):
            self.call('PREPARED')

    def test_prepared_requires_pinned_manifest(self):
        self.fixture.chain[0] = Planned()
        with self.assertRaisesRegex(ValueError, 'manifest SHA256'):
            self.call('PREPARED', expected_manifest_sha256=None)
        self.assertEqual(self.fixture.prepared_calls, 0)

    def test_prepared_preview_and_publish(self):
        self.fixture.chain[0] = Planned()
        sha = self.call('PREPARED')['context_sha256']
        self.assertEqual(self.fixture.publications, 0)
        self.assertEqual(self.approve('PREPARED', sha)['state'], 'CREATED_NEW')
        self.assertEqual(self.fixture.chain[1], Prepared(Planned()))

    def test_prepared_refuses_existing(self):
        self.fixture.chain[:2] = [Planned(), Prepared(Planned())]
        with self.assertRaisesRegex(RuntimeError, 'requires planned only'):
            self.call('PREPARED')

    def test_registered_requires_prepared(self):
        self.fixture.chain[0] = Planned()
        with self.assertRaisesRegex(RuntimeError, 'requires exact planned'):
            self.call('REGISTERED')

    def test_registered_preview_and_publish(self):
        self.fixture.chain[:2] = [Planned(), Prepared(Planned())]
        sha = self.call('REGISTERED')['context_sha256']
        self.assertEqual(self.fixture.publications, 0)
        self.assertEqual(self.approve('REGISTERED', sha)['state'], 'CREATED_NEW')
        self.assertEqual(self.fixture.chain[2], Registered(Prepared(Planned())))

    def test_reject_unknown_stage(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.call('INVALID')

    def test_reject_inconsistent_chain_shape(self):
        self.svc = Services(
            read_chain=lambda *args: (None, None),
            new_plan=self.fixture.new_plan, cache_evidence=lambda *args: None,
            prepared=self.fixture.prepared, registered=self.fixture.registered,
            digest=self.fixture.digest, publish=self.fixture.publish)
        with self.assertRaisesRegex(RuntimeError, 'Malformed'):
            self.call()

    def test_preview_rejects_approval_fields(self):
        with self.assertRaisesRegex(ValueError, 'Preview'):
            self.call(approve_stage='PLANNED', expected_context_sha256='a' * 64)

    def test_no_database_write_or_retry_flags(self):
        output = self.call()
        self.assertEqual(output['db_write_executed'], False)
        self.assertEqual(output['hf_body_downloaded'], False)
        self.assertNotIn('retry_authorized', output)


if __name__ == '__main__':
    unittest.main()
