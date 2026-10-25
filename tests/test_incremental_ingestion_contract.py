"""Offline contract tests for M4.5.4b. No DB, HF, or archive operations."""
import unittest

from scripts.tools.incremental_ingestion_contract import (
    ContractViolation, MemoryDB, StoredVersion, VerifiedCandidate,
)

REPO = "Toibinguz/epl-premier-league-dataset"
REV = "0216641d6081234e9e35a3de65c138830c77963a"
OLD_SHA = "a" * 64
NEW_SHA = "b" * 64
PATH = "2026_2027_new/fpl_bootstrap_latest.json"
UNCHANGED = "2026_2027_new/matches/fotmob/5795475.json"


def candidate(path=PATH, category="CHANGED", sha=NEW_SHA, count=2, size=100):
    return VerifiedCandidate(REPO, path, REV, category, sha, size, count, "fpl")


def setup(existing=True, paths=(PATH, UNCHANGED)):
    db = MemoryDB()
    if existing:
        db.register_existing(StoredVersion(9, REPO, UNCHANGED, OLD_SHA, 80, 1))
        db.register_existing(StoredVersion(10, REPO, PATH, OLD_SHA, 80, 1))
    db.start_snapshot(REPO, REV, set(paths))
    return db


class ContractTest(unittest.TestCase):
    def test_reuse_only_links_observation_without_records(self):
        db = setup()
        old = dict(db.raw_record_counts)
        self.assertEqual(db.link_unchanged(REPO, REV, UNCHANGED, 9), (9, "REUSED"))
        self.assertEqual(db.raw_record_counts, old)

    def test_reused_file_id_must_match_path(self):
        db = setup()
        with self.assertRaises(ContractViolation):
            db.link_unchanged(REPO, REV, UNCHANGED, 10)

    def test_reuse_idempotency(self):
        db = setup()
        db.link_unchanged(REPO, REV, UNCHANGED, 9)
        self.assertEqual(db.link_unchanged(REPO, REV, UNCHANGED, 9),
                         (9, "ALREADY_LINKED"))
        self.assertEqual(len(db.snapshot.observations), 1)

    def test_json_new_version_and_old_version_preserved(self):
        db = setup()
        fid, action = db.ingest_verified_json(candidate(),
                verified_sha256=NEW_SHA, verified_byte_size=100,
                parsed_record_count=2)
        self.assertEqual(action, "INSERTED_NEW_VERSION")
        self.assertNotEqual(fid, 10)
        self.assertIn((REPO, PATH, OLD_SHA), db.versions)
        self.assertEqual(db.raw_record_counts[10], 1)
        self.assertEqual(db.raw_record_counts[fid], 2)

    def test_retry_same_version_no_duplicate_file_or_records(self):
        db = setup()
        args = dict(verified_sha256=NEW_SHA, verified_byte_size=100, parsed_record_count=2)
        first = db.ingest_verified_json(candidate(), **args)
        second = db.ingest_verified_json(candidate(), **args)
        self.assertEqual(first[0], second[0])
        self.assertEqual(second[1], "ALREADY_LINKED")
        self.assertEqual(len(db.versions), 3)
        self.assertEqual(len(db.raw_record_counts), 3)

    def test_already_committed_version_can_be_linked_in_retry(self):
        db = setup()
        db.register_existing(StoredVersion(25, REPO, PATH, NEW_SHA, 100, 2))
        fid, action = db.ingest_verified_json(candidate(),
                        verified_sha256=NEW_SHA, verified_byte_size=100,
                        parsed_record_count=2)
        self.assertEqual((fid, action), (25, "REUSED_EXISTING_VERSION"))

    def test_conflicting_same_sha_metadata_blocks(self):
        db = setup()
        db.register_existing(StoredVersion(25, REPO, PATH, NEW_SHA, 150, 2))
        before = db.snapshot.observations.copy()
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                    verified_byte_size=100, parsed_record_count=2)
        self.assertEqual(db.snapshot.observations, before)

    def test_incomplete_existing_file_blocks(self):
        db = setup()
        db.register_existing(StoredVersion(25, REPO, PATH, NEW_SHA, 100, 2,
                                          parse_state="partial"))
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                    verified_byte_size=100, parsed_record_count=2)

    def test_corrupt_physical_record_count_blocks_reuse(self):
        db = setup()
        db.raw_record_counts[9] = 3
        with self.assertRaises(ContractViolation):
            db.link_unchanged(REPO, REV, UNCHANGED, 9)

    def test_wrong_sha_refused_before_any_write(self):
        db = setup()
        old = (db.versions.copy(), db.raw_record_counts.copy(), db.snapshot.observations.copy())
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256="c"*64,
                                    verified_byte_size=100, parsed_record_count=2)
        self.assertEqual(old, (db.versions, db.raw_record_counts, db.snapshot.observations))

    def test_wrong_byte_size_refused(self):
        db = setup()
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                    verified_byte_size=101, parsed_record_count=2)

    def test_parser_record_count_drift_refused(self):
        db = setup()
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                    verified_byte_size=100, parsed_record_count=3)

    def test_three_interruption_points_roll_back_entire_file(self):
        for fault in ("after_version_insert", "after_record_insert", "after_observation"):
            with self.subTest(fault=fault):
                db = setup()
                baseline = (db.versions.copy(), db.raw_record_counts.copy(),
                            db.snapshot.observations.copy(), db.next_file_id)
                with self.assertRaises(ContractViolation):
                    db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                            verified_byte_size=100,
                                            parsed_record_count=2, fail_at=fault)
                self.assertEqual(baseline, (db.versions, db.raw_record_counts,
                                             db.snapshot.observations, db.next_file_id))

    def test_reuse_interruption_rolls_back_observation(self):
        db = setup()
        with self.assertRaises(ContractViolation):
            db.link_unchanged(REPO, REV, UNCHANGED, 9, fail_at="after_observation")
        self.assertFalse(db.snapshot.observations)

    def test_conflicting_observation_rejected(self):
        db = setup()
        db.snapshot.observations[PATH] = 10
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                    verified_byte_size=100, parsed_record_count=2)
        self.assertEqual(db.snapshot.observations[PATH], 10)

    def test_wrong_target_revision_rejected(self):
        db = setup()
        other_rev = "c"*40
        wrong = VerifiedCandidate(REPO, PATH, other_rev, "CHANGED", NEW_SHA, 100, 2, "fpl")
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(wrong, verified_sha256=NEW_SHA,
                                    verified_byte_size=100, parsed_record_count=2)

    def test_out_of_scope_path_and_wrong_category_rejected(self):
        with self.assertRaises(ContractViolation):
            candidate(path="clean/file.json")
        with self.assertRaises(ContractViolation):
            candidate(category="UNCHANGED")
        with self.assertRaises(ContractViolation):
            candidate(path="2026_2027_new/../x.json")

    def test_incomplete_snapshot_cannot_finalize(self):
        db = setup()
        db.link_unchanged(REPO, REV, UNCHANGED, 9)
        with self.assertRaises(ContractViolation):
            db.finalize(manifest_verified=True, manifest_sha256="d"*64)
        self.assertEqual(db.snapshot.status, "in_progress")

    def test_manifest_proof_required_even_when_all_files_linked(self):
        db = setup()
        db.link_unchanged(REPO, REV, UNCHANGED, 9)
        db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                verified_byte_size=100, parsed_record_count=2)
        with self.assertRaises(ContractViolation):
            db.finalize(manifest_verified=False, manifest_sha256="d"*64)
        self.assertEqual(db.snapshot.status, "in_progress")

    def test_finalize_only_after_explicit_manifest_verification(self):
        db = setup()
        db.link_unchanged(REPO, REV, UNCHANGED, 9)
        db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                verified_byte_size=100, parsed_record_count=2)
        self.assertEqual(db.snapshot.status, "in_progress")
        db.finalize(manifest_verified=True, manifest_sha256="d"*64)
        self.assertEqual(db.snapshot.status, "completed")
        with self.assertRaises(ContractViolation):
            db.ingest_verified_json(candidate(), verified_sha256=NEW_SHA,
                                    verified_byte_size=100, parsed_record_count=2)

    def test_cannot_start_same_or_second_snapshot_implicitly(self):
        db = setup()
        with self.assertRaises(ContractViolation):
            db.start_snapshot(REPO, REV, {PATH})


if __name__ == "__main__":
    unittest.main()
