import hashlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.incremental_sync import SyncAction, SyncPlan
from scripts.tools.incremental_cache_gate import (
    candidate_actions, check_cached_candidate, assess_results,
)
from scripts.tools.download_incremental_candidates import (
    DownloadContext, candidate_path_ok, verify_plan_contract,
)


class CsvCacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = b"HomeTeam,AwayTeam\nArsenal,Chelsea\nChelsea,Arsenal\n"
        self.fp = Path(self.tmp.name) / "sha-addressed-object"
        self.fp.write_bytes(self.body)
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.git = hashlib.sha1(
            b"blob " + str(len(self.body)).encode() + b"\0" + self.body
        ).hexdigest()
        self.path = "2026_2027_new/football_data_raw_2627.csv"
        self.identity = FileIdentity(
            size=len(self.body), git_blob_id=None, lfs_sha256=self.sha
        )
        self.action = SyncAction(
            kind="UPSERT_VERSION_AFTER_REHASH", path=self.path,
            category="NEW", baseline_file_id=None, source_code="football_data",
            file_format="csv", expected_bytes=len(self.body),
            lfs_sha256=self.sha, git_blob_id=None,
        )
        self.plan = SyncPlan(
            actions=(self.action,), baseline_records=0, reused_records=0,
            superseded_records=0, target_raw_count=1,
            candidate_bytes=len(self.body), fingerprint="a" * 64,
        )
        self.target = {self.path: self.identity}

    def test_opt_in_csv_candidate_selection(self):
        picked = candidate_actions(self.plan, self.target, allow_csv=True)
        self.assertEqual(picked, (self.action,))

    def test_default_csv_rejected(self):
        with self.assertRaises(ValueError):
            candidate_actions(self.plan, self.target)

    def test_csv_archive_verification(self):
        checked = check_cached_candidate(
            self.action, self.identity, self.fp, allow_csv=True
        )
        self.assertEqual(checked.records, 2)
        self.assertEqual(checked.sha256, self.sha)
        self.assertEqual(checked.bytes, len(self.body))

    def test_default_cache_validator_rejects_csv(self):
        with self.assertRaises(ValueError):
            check_cached_candidate(self.action, self.identity, self.fp)

    def test_assessment_requires_explicit_csv(self):
        checked = check_cached_candidate(
            self.action, self.identity, self.fp, allow_csv=True
        )
        with self.assertRaises(ValueError):
            assess_results(self.plan, (checked,))
        self.assertEqual(
            assess_results(self.plan, (checked,), allow_csv=True), 2
        )

    def test_cache_contract_default_rejects_csv(self):
        with self.assertRaises(RuntimeError):
            verify_plan_contract(
                self.plan, (self.action,), self.target,
                fingerprint=self.plan.fingerprint, files=1,
                total_bytes=len(self.body),
            )

    def test_cache_contract_csv_opt_in(self):
        verify_plan_contract(
            self.plan, (self.action,), self.target,
            fingerprint=self.plan.fingerprint, files=1,
            total_bytes=len(self.body), allow_csv=True,
        )

    def test_path_allowlist_is_explicit(self):
        self.assertFalse(candidate_path_ok(self.path))
        self.assertTrue(candidate_path_ok(self.path, allow_csv=True))
        self.assertFalse(candidate_path_ok(
            "2026_2027_new/matches/fotmob.tar.gz", allow_csv=True
        ))
        self.assertFalse(candidate_path_ok(
            "2026_2027_new/../unsafe.csv", allow_csv=True
        ))

    def test_wrong_lfs_bytes_are_blocked(self):
        bad = FileIdentity(
            size=len(self.body), git_blob_id=None, lfs_sha256="f" * 64
        )
        changed = SyncAction(
            kind=self.action.kind, path=self.path, category="NEW",
            baseline_file_id=None, source_code=self.action.source_code,
            file_format="csv", expected_bytes=len(self.body),
            lfs_sha256="f" * 64, git_blob_id=None,
        )
        with self.assertRaises(ValueError):
            check_cached_candidate(changed, bad, self.fp, allow_csv=True)

    def test_valid_git_blob_without_lfs(self):
        git_identity = FileIdentity(
            size=len(self.body), git_blob_id=self.git, lfs_sha256=None
        )
        action = SyncAction(
            kind=self.action.kind, path=self.path, category="NEW",
            baseline_file_id=None, source_code=self.action.source_code,
            file_format="csv", expected_bytes=len(self.body),
            lfs_sha256=None, git_blob_id=self.git,
        )
        result = check_cached_candidate(
            action, git_identity, self.fp, allow_csv=True
        )
        self.assertEqual(result.records, 2)

    def test_download_context_flag_defaults_off(self):
        context = DownloadContext(
            "sample/epl", 1, "a" * 40, "b" * 40,
            self.plan, self.target, (self.action,), 0,
        )
        self.assertFalse(context.allow_csv)


if __name__ == "__main__":
    unittest.main()
