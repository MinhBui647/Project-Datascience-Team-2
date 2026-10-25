"""Offline read-only deterministic incremental sync planner regression."""
import unittest

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.raw_sync_write_plan import ExistingFile
from scripts.tools.incremental_sync import assemble_plan


def ident(n=5, sha="a" * 64):
    return FileIdentity(size=n, git_blob_id=None, lfs_sha256=sha)


def prev(fid=1, n=5, count=2, sha="a" * 64, **kw):
    return ExistingFile(file_id=fid, sha256=sha, size=n, record_count=count, **kw)


class IncrementalSyncPlanTest(unittest.TestCase):
    def test_unchanged_reuses_baseline_file_id(self):
        old = {"raw_data/a.json": ident()}
        plan = assemble_plan(old, old, {"raw_data/a.json": prev(15)}, set())
        self.assertEqual(plan.count("UNCHANGED"), 1)
        self.assertEqual(plan.actions[0].baseline_file_id, 15)
        self.assertEqual(plan.actions[0].kind, "REUSE_FILE_ID")

    def test_new_json_requires_sha_lookup_not_blind_insert(self):
        plan = assemble_plan({}, {"raw_data/fotmob/new.json": ident()}, {}, {"fotmob"})
        self.assertEqual(plan.count("NEW"), 1)
        self.assertEqual(plan.actions[0].kind, "UPSERT_VERSION_AFTER_REHASH")
        self.assertIsNone(plan.actions[0].baseline_file_id)

    def test_changed_json_retains_old_file_id_for_traceability(self):
        path = "raw_data/fotmob/a.json"
        plan = assemble_plan({path: ident()}, {path: ident(6, "b" * 64)},
                             {path: prev(11)}, {"fotmob"})
        self.assertEqual(plan.count("CHANGED"), 1)
        self.assertEqual(plan.actions[0].baseline_file_id, 11)
        self.assertEqual(plan.superseded_records, 2)

    def test_removed_is_notify_only(self):
        path = "raw_data/a.json"
        plan = assemble_plan({path: ident()}, {}, {path: prev(8)}, set())
        self.assertEqual(plan.count("REMOVED"), 1)
        self.assertEqual(plan.actions[0].kind, "NOTIFY_ONLY_NO_DELETE")

    def test_clean_only_commit_yields_reuse_no_ingest(self):
        path = "raw_data/a.json"
        plan = assemble_plan({path: ident()}, {path: ident()}, {path: prev(5)}, set())
        self.assertEqual(plan.count("NEW") + plan.count("CHANGED"), 0)

    def test_missing_source_code_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "absent"):
            assemble_plan({}, {"raw_data/fotmob/a.json": ident()}, {}, set())

    def test_unsupported_format_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "JSON files only"):
            assemble_plan({}, {"raw_data/fotmob/a.csv": ident()}, {}, {"fotmob"})

    def test_oversized_json_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "byte budget"):
            assemble_plan({}, {"raw_data/fotmob/a.json": ident(16)}, {},
                          {"fotmob"}, max_json_bytes=15)

    def test_total_batch_budget_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "batch size"):
            assemble_plan({}, {"raw_data/fotmob/a.json": ident(10),
                               "raw_data/fotmob/b.json": ident(10)}, {},
                          {"fotmob"}, max_batch_bytes=19)

    def test_incomplete_baseline_fails_closed(self):
        old = {"raw_data/a.json": ident()}
        with self.assertRaisesRegex(RuntimeError, "not completed"):
            assemble_plan(old, old, {"raw_data/a.json": prev(parse_state="partial")}, set())

    def test_metadata_review_fails_closed(self):
        old = {"raw_data/a.json": FileIdentity(5, None, None)}
        with self.assertRaisesRegex(RuntimeError, "REVIEW"):
            assemble_plan(old, old, {"raw_data/a.json": prev()}, set())

    def test_out_of_scope_paths_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Non-Raw"):
            assemble_plan({}, {"clean/a.json": ident()}, {}, set())

    def test_deterministic_fingerprint(self):
        a = {"raw_data/fotmob/z.json": ident(), "raw_data/fotmob/a.json": ident()}
        b = {"raw_data/fotmob/a.json": ident(), "raw_data/fotmob/z.json": ident()}
        x = assemble_plan({}, a, {}, {"fotmob"})
        y = assemble_plan({}, b, {}, {"fotmob"})
        self.assertEqual(x.fingerprint, y.fingerprint)
        self.assertEqual([z.path for z in x.actions], sorted(a))

    def test_fingerprint_changes_for_altered_content(self):
        x = assemble_plan({}, {"raw_data/fotmob/a.json": ident()}, {}, {"fotmob"})
        y = assemble_plan({}, {"raw_data/fotmob/a.json": ident(5, "b" * 64)}, {}, {"fotmob"})
        self.assertNotEqual(x.fingerprint, y.fingerprint)

    def test_accounting_real_shape(self):
        old = {f"raw_data/fotmob/a_{i}.json": ident() for i in range(276)}
        new = dict(old)
        for i in range(4):
            new[f"raw_data/fotmob/a_{i}.json"] = ident(6, "b" * 64)
        for i in range(10):
            new[f"raw_data/fotmob/new_{i}.json"] = ident()
        prior = {path: prev(fid=i + 1) for i, path in enumerate(old)}
        plan = assemble_plan(old, new, prior, {"fotmob"})
        self.assertEqual((plan.count("UNCHANGED"), plan.count("NEW"),
                          plan.count("CHANGED"), plan.count("REMOVED")),
                         (272, 10, 4, 0))
        self.assertEqual(plan.target_raw_count, 286)
        self.assertEqual(plan.reused_records, 544)


if __name__ == "__main__":
    unittest.main()
