"""Immutable recovery protocol: process evidence cannot be skipped or altered."""
from pathlib import Path
import json
import tempfile
import unittest

from scripts.orchestration.auto_recovery_journal import StageJournal, RecoveryJournalError


class RecoveryJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parameters = dict(root=self.root, repo_id="Toibinguz/epl-premier-league-dataset",
                               revision="a" * 40, stage="CANDIDATE_INGESTION",
                               original_claim_sha256="b" * 64, context_sha256="c" * 64,
                               argv=("python", "-m", "scripts.tools.ingest_incremental_candidates", "--apply"))
        self.journal = StageJournal(**self.parameters)

    def alter(self, name, edit):
        path = self.journal.directory / name
        value = json.loads(path.read_text())
        edit(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_transient_chain_and_verification_are_bound_and_append_only(self):
        first = self.journal.begin()
        self.journal.finish(first, outcome="TRANSIENT_FAILURE", category="database", exit_code=2)
        original = {p.name: p.read_bytes() for p in self.journal.directory.iterdir()}
        second = self.journal.begin()
        self.journal.finish(second, outcome="CHILD_SUCCEEDED", exit_code=0)
        self.journal.mark_verified()
        history = self.journal.history()
        self.assertEqual([x.outcome for x in history], ["TRANSIENT_FAILURE", "CHILD_SUCCEEDED"])
        self.assertTrue(history[-1].verified)
        for name, data in original.items():
            self.assertEqual((self.journal.directory / name).read_bytes(), data)
        with self.assertRaises(RecoveryJournalError):
            self.journal.begin()

    def test_missing_or_unknown_process_result_fences_another_attempt(self):
        first = self.journal.begin()
        with self.assertRaises(RecoveryJournalError):
            self.journal.begin()
        self.journal.finish(first, outcome="OUTCOME_UNKNOWN")
        with self.assertRaises(RecoveryJournalError):
            self.journal.begin()
        with self.assertRaises(RecoveryJournalError):
            self.journal.mark_verified()

    def test_child_success_allows_verification_only(self):
        first = self.journal.begin()
        self.journal.finish(first, outcome="CHILD_SUCCEEDED", exit_code=0)
        with self.assertRaises(RecoveryJournalError):
            self.journal.begin()
        self.journal.mark_verified()
        marker = self.journal.directory / "0001.verified.json"
        original = marker.read_bytes()
        self.journal.mark_verified()
        self.assertEqual(marker.read_bytes(), original)

    def test_original_claim_context_and_argv_cannot_change(self):
        self.journal.begin()
        variants = ({"original_claim_sha256": "d" * 64}, {"context_sha256": "e" * 64},
                    {"argv": self.parameters["argv"] + ("--different",)})
        for change in variants:
            with self.subTest(change=change), self.assertRaises(RecoveryJournalError):
                StageJournal(**{**self.parameters, **change}).history()

    def test_tampered_claim_is_not_accepted_by_its_result(self):
        first = self.journal.begin()
        self.journal.finish(first, outcome="CHILD_SUCCEEDED", exit_code=0)
        self.alter("0001.claim.json", lambda value: value.update(created_at_utc="2026-01-01T00:00:00+00:00"))
        with self.assertRaises(RecoveryJournalError):
            self.journal.history()

    def test_result_tampering_is_rejected_by_verified_marker(self):
        first = self.journal.begin()
        self.journal.finish(first, outcome="CHILD_SUCCEEDED", exit_code=0)
        self.journal.mark_verified()
        self.alter("0001.result.json", lambda value: value.update(category="database"))
        with self.assertRaises(RecoveryJournalError):
            self.journal.history()

    def test_orphan_result_and_attempt_gap_are_rejected(self):
        first = self.journal.begin()
        self.journal.finish(first, outcome="TRANSIENT_FAILURE", category="database", exit_code=2)
        (self.journal.directory / "0001.claim.json").unlink()
        with self.assertRaises(RecoveryJournalError):
            self.journal.history()

    def test_utc_timestamp_is_required_on_claim(self):
        self.journal.begin()
        for stamp in (None, "bad", "2026-01-01T00:00:00"):
            with self.subTest(stamp=stamp):
                self.alter("0001.claim.json", lambda value: value.update(created_at_utc=stamp))
                with self.assertRaises(RecoveryJournalError):
                    self.journal.history()

    def test_invalid_result_is_rejected_before_file_is_created(self):
        attempt = self.journal.begin()
        for invalid in (dict(outcome="OTHER"), dict(outcome="CHILD_SUCCEEDED", exit_code=2),
                        dict(outcome="TRANSIENT_FAILURE", category="database", exit_code=0),
                        dict(outcome="TRANSIENT_FAILURE", category=None, exit_code=2)):
            with self.subTest(invalid=invalid), self.assertRaises(RecoveryJournalError):
                self.journal.finish(attempt, **invalid)
            self.assertFalse((self.journal.directory / "0001.result.json").exists())

    def test_result_cannot_be_rewritten(self):
        attempt = self.journal.begin()
        self.journal.finish(attempt, outcome="CHILD_SUCCEEDED", exit_code=0)
        data = (self.journal.directory / "0001.result.json").read_bytes()
        with self.assertRaises(RecoveryJournalError):
            self.journal.finish(attempt, outcome="OUTCOME_UNKNOWN")
        self.assertEqual((self.journal.directory / "0001.result.json").read_bytes(), data)

    def test_global_attempt_limit_cannot_be_reset_with_another_instance(self):
        for _ in range(5):
            attempt = self.journal.begin()
            self.journal.finish(attempt, outcome="TRANSIENT_FAILURE", category="database", exit_code=2)
        with self.assertRaises(RecoveryJournalError):
            StageJournal(**self.parameters).begin()

    def test_constructor_requires_pinned_valid_identity(self):
        for changes in ({"repo_id": ""}, {"repo_id": "bad\nrepo"}, {"revision": "main"},
                        {"argv": ()}, {"argv": ["python"]}, {"argv": ("",)}, {"stage": "OTHER"}):
            with self.subTest(changes=changes), self.assertRaises(RecoveryJournalError):
                StageJournal(**{**self.parameters, **changes})


if __name__ == "__main__":
    unittest.main()
