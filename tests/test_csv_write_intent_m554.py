"""M5.5.4b isolated command mapping tests, no DB/HF/subprocess execution."""
import sys
import unittest
from scripts.orchestration.csv_write_intent import (
    STAGE_WRITERS, WriterIntent, build_writer_intent,
)

COMMON = {
    "--repo-id": "Toibinguz/epl-premier-league-dataset",
    "--baseline-snapshot": "366",
    "--target-revision": "a" * 40,
    "--expected-fingerprint": "b" * 64,
    "--expected-baseline-files": "296",
    "--expected-full-files": "314",
    "--expected-raw-files": "296",
    "--expected-reuse": "293",
    "--expected-files": "3",
    "--expected-bytes": "2916818",
    "--expected-records": "72",
    "--target-snapshot-id": "367",
    "--expected-baseline-records": "8502604",
    "--expected-reused-records": "8502542",
    "--cache-dir": "reports/m4_6_content_cache",
    "--manifest-report": "reports/m4_6_manifests/" + "a" * 40 + "/hf_manifest_v2.json",
    "--manifest-sha256": "c" * 64,
}


def plan(stage, overrides=None):
    module = STAGE_WRITERS[stage][0]
    opts = {**COMMON, **(overrides or {})}
    fields = tuple(part for pair in opts.items() for part in pair)
    return (sys.executable, "-m", "scripts.tools." + module, *fields, "--plan")


class CsvWriteIntentTests(unittest.TestCase):
    def test_five_known_m4_write_mappings(self):
        for stage, (_, mode, confirm, csv) in STAGE_WRITERS.items():
            with self.subTest(stage=stage):
                intent = build_writer_intent(stage, plan(stage))
                self.assertIsInstance(intent, WriterIntent)
                self.assertEqual(intent.argv[-3:], (mode, confirm, csv))
                self.assertNotIn("--plan", intent.argv)
                self.assertFalse(intent.write_authorized)

    def test_refuse_unknown_stage(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            build_writer_intent("ARCHIVE_PROMOTION", plan("FINALIZATION"))

    def test_refuse_module_substitution(self):
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            build_writer_intent("REUSE_LINKING", plan("FINALIZATION"))

    def test_refuse_invalid_sha(self):
        with self.assertRaisesRegex(ValueError, "SHA40"):
            build_writer_intent("REUSE_LINKING", plan("REUSE_LINKING", {"--target-revision": "main"}))

    def test_refuse_duplicate_flag(self):
        p = plan("REUSE_LINKING")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_writer_intent("REUSE_LINKING", p[:-1] + ("--repo-id", "other/repo", "--plan"))

    def test_refuse_unsafe_original_write_flag(self):
        p = plan("REUSE_LINKING")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            build_writer_intent("REUSE_LINKING", p[:-1] + ("--apply", "true", "--plan"))

    def test_refuse_unpinned_candidate_cache(self):
        p = tuple(v for pair in COMMON.items() if pair[0] != "--cache-dir" for v in pair)
        src = (sys.executable, "-m", "scripts.tools.ingest_incremental_candidates", *p, "--plan")
        with self.assertRaisesRegex(ValueError, "cache-dir"):
            build_writer_intent("CANDIDATE_INGESTION", src)

    def test_refuse_unpinned_manifest(self):
        with self.assertRaisesRegex(ValueError, "Manifest SHA256"):
            build_writer_intent("FINALIZATION", plan("FINALIZATION", {"--manifest-sha256": "bad"}))

    def test_refuse_positive_authorization(self):
        i = build_writer_intent("FINALIZATION", plan("FINALIZATION"))
        with self.assertRaisesRegex(ValueError, "never grant"):
            WriterIntent(i.stage, i.module, i.argv, i.csv_flag, True)


if __name__ == "__main__":
    unittest.main()
