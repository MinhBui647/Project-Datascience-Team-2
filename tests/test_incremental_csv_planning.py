
import unittest

from scripts.tools.check_raw_updates import FileIdentity
from scripts.tools.raw_sync_write_plan import ExistingFile
from scripts.tools.incremental_sync import assemble_plan


def ident(size=5, sha="a" * 64):
    return FileIdentity(
        size=size, git_blob_id=None, lfs_sha256=sha
    )


def previous():
    return ExistingFile(
        file_id=11,
        sha256="a" * 64,
        size=5,
        record_count=2,
    )


class IncrementalCsvPlanningTests(unittest.TestCase):

    def test_new_csv_opt_in(self):
        path = "raw_data/fotmob/new.csv"
        plan = assemble_plan(
            {}, {path: ident()}, {}, {"fotmob"},
            allow_csv_planning=True,
        )
        self.assertEqual(plan.count("NEW"), 1)
        self.assertEqual(plan.actions[0].file_format, "csv")
        self.assertEqual(
            plan.actions[0].kind,
            "UPSERT_VERSION_AFTER_REHASH",
        )

    def test_changed_csv_opt_in(self):
        path = "raw_data/fotmob/match.csv"
        plan = assemble_plan(
            {path: ident()},
            {path: ident(6, "b" * 64)},
            {path: previous()},
            {"fotmob"},
            allow_csv_planning=True,
        )
        self.assertEqual(plan.count("CHANGED"), 1)
        self.assertEqual(plan.actions[0].baseline_file_id, 11)

    def test_default_csv_still_blocked(self):
        with self.assertRaisesRegex(
            RuntimeError, "JSON files only"
        ):
            assemble_plan(
                {},
                {"raw_data/fotmob/new.csv": ident()},
                {},
                {"fotmob"},
            )

    def test_tar_still_blocked(self):
        with self.assertRaisesRegex(
            RuntimeError, "JSON and CSV only"
        ):
            assemble_plan(
                {},
                {"raw_data/fotmob/a.tar.gz": ident()},
                {},
                {"fotmob"},
                allow_csv_planning=True,
            )

    def test_csv_size_budget(self):
        with self.assertRaisesRegex(RuntimeError, "byte budget"):
            assemble_plan(
                {},
                {"raw_data/fotmob/large.csv": ident(100)},
                {},
                {"fotmob"},
                allow_csv_planning=True,
                max_csv_bytes=50,
            )

    def test_missing_source_still_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "absent"):
            assemble_plan(
                {},
                {"raw_data/fotmob/new.csv": ident()},
                {},
                set(),
                allow_csv_planning=True,
            )

    def test_deterministic_fingerprint(self):
        a = "raw_data/fotmob/a.csv"
        b = "raw_data/fotmob/b.csv"

        first = assemble_plan(
            {}, {a: ident(), b: ident()}, {},
            {"fotmob"}, allow_csv_planning=True,
        )
        second = assemble_plan(
            {}, {b: ident(), a: ident()}, {},
            {"fotmob"}, allow_csv_planning=True,
        )
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_invalid_switch(self):
        with self.assertRaisesRegex(ValueError, "planning switch"):
            assemble_plan(
                {}, {}, {}, set(), allow_csv_planning=1
            )

    def test_invalid_budget(self):
        with self.assertRaisesRegex(ValueError, "CSV budget"):
            assemble_plan(
                {}, {}, {}, set(),
                allow_csv_planning=True,
                max_csv_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
