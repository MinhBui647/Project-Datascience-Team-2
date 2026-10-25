"""Offline contract tests for M4.5.4a write-plan generation."""
import unittest
from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.raw_sync_write_plan import ExistingFile, build_plan


def ident(n, sha=None):
    return FileIdentity(n, None, sha or "a" * 64)


def prior(fid, size=5, count=2, sha=None, **kw):
    return ExistingFile(fid, sha or "a" * 64, size, count, **kw)


class WriteReadinessTest(unittest.TestCase):
    def test_unchanged_reuses_exact_file_id(self):
        p = build_plan({"raw_data/a.json": ident(5)}, {"raw_data/a.json": ident(5)}, {"raw_data/a.json": prior(17)})
        self.assertEqual(p.reuse, ("raw_data/a.json",))
        self.assertEqual(p.reused_file_ids, (17,))
        self.assertEqual(p.reused_record_count, 2)

    def test_new_file_requires_ingest(self):
        p = build_plan({}, {"raw_data/new.json": ident(5)}, {})
        self.assertEqual(p.new, ("raw_data/new.json",))
        self.assertEqual(p.reused_record_count, 0)

    def test_changed_file_preserves_old_records(self):
        p = build_plan({"raw_data/a.json": ident(5)}, {"raw_data/a.json": ident(6, "b" * 64)}, {"raw_data/a.json": prior(17)})
        self.assertEqual(p.changed, ("raw_data/a.json",))
        self.assertEqual(p.superseded_record_count, 2)
        self.assertFalse(p.reuse)

    def test_removed_file_is_not_a_delete(self):
        p = build_plan({"raw_data/a.json": ident(5)}, {}, {"raw_data/a.json": prior(17)})
        self.assertEqual(p.removed, ("raw_data/a.json",))
        self.assertEqual(p.superseded_record_count, 2)

    def test_fails_when_metadata_requires_review(self):
        with self.assertRaisesRegex(RuntimeError, "REVIEW"):
            build_plan({"raw_data/a.json": FileIdentity(5, None, None)}, {"raw_data/a.json": FileIdentity(5, None, None)}, {"raw_data/a.json": prior(17)})

    def test_incomplete_baseline_refused(self):
        with self.assertRaisesRegex(RuntimeError, "not completed"):
            build_plan({"raw_data/a.json": ident(5)}, {"raw_data/a.json": ident(5)}, {"raw_data/a.json": prior(17, parse_state="partial")})

    def test_duplicate_file_ids_refused(self):
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            build_plan({"raw_data/a.json": ident(5), "raw_data/b.json": ident(5)}, {"raw_data/a.json": ident(5), "raw_data/b.json": ident(5)}, {"raw_data/a.json": prior(17), "raw_data/b.json": prior(17)})

    def test_baseline_file_count_mismatch_refused(self):
        with self.assertRaisesRegex(RuntimeError, "disagree"):
            build_plan({"raw_data/a.json": ident(5)}, {"raw_data/a.json": ident(5)}, {})

    def test_lfs_sha_mismatch_refused(self):
        with self.assertRaisesRegex(RuntimeError, "sha256 mismatch"):
            build_plan({"raw_data/a.json": ident(5)}, {"raw_data/a.json": ident(5)}, {"raw_data/a.json": prior(17, sha="b" * 64)})

    def test_size_mismatch_refused(self):
        with self.assertRaisesRegex(RuntimeError, "size mismatch"):
            build_plan({"raw_data/a.json": ident(5)}, {"raw_data/a.json": ident(5)}, {"raw_data/a.json": prior(17, size=6)})

    def test_real_update_shape_272_reuse_10_new_4_changed(self):
        old = {f"raw_data/old_{i}.json": ident(5) for i in range(276)}
        new = dict(old)
        for i in range(4):
            new[f"raw_data/old_{i}.json"] = ident(6, "b" * 64)
        for i in range(10):
            new[f"raw_data/new_{i}.json"] = ident(5)
        prior_files = {path: prior(i + 1) for i, path in enumerate(old)}
        plan = build_plan(old, new, prior_files)
        self.assertEqual((len(plan.reuse), len(plan.new), len(plan.changed), len(plan.removed)), (272, 10, 4, 0))
        self.assertEqual(plan.baseline_linked_record_count, 552)
        self.assertEqual(plan.reused_record_count, 544)

    def test_deterministic_sorted_reuse(self):
        old = {"raw_data/z.json": ident(5), "raw_data/a.json": ident(5)}
        p = build_plan(old, dict(old), {"raw_data/z.json": prior(5), "raw_data/a.json": prior(6)})
        self.assertEqual(p.reuse, ("raw_data/a.json", "raw_data/z.json"))
        self.assertEqual(p.reused_file_ids, (6, 5))


if __name__ == "__main__":
    unittest.main()
