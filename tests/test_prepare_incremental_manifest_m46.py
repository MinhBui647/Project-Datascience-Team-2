"""M4.6.4c.8a offline tests. No live HF, PostgreSQL, or project-root writes."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest import TestCase, mock
import hashlib
import json
import os

from scripts.tools import prepare_incremental_manifest as m

REV = "a" * 40
FP = "b" * 64
BASE = "c" * 40
RAW_A = "2026_2027_new/matches/a.json"
RAW_B = "raw_data/fotmob/matches.csv"
NONRAW = "README.md"


def hfrow(path, size, git=None, lfs=None):
    return NS(path=path, size=size, blob_id=git, lfs={"sha256": lfs} if lfs else {})


class FakePlan:
    fingerprint = FP
    target_raw_count = 2
    def count(self, name):
        return {"REMOVED": 0, "REVIEW": 0, "UNCHANGED": 1, "NEW": 1, "CHANGED": 0}.get(name, 0)


def context():
    return NS(repo_id="Toibinguz/epl-premier-league-dataset", target_revision=REV,
              baseline_snapshot=365, baseline_revision=BASE, plan=FakePlan(),
              target={RAW_A: NS(size=6, git_blob_id="d" * 40, lfs_sha256=None),
                      RAW_B: NS(size=10, git_blob_id=None, lfs_sha256="e" * 64)},
              excluded=1)


def rows():
    return [hfrow(RAW_B, 10, lfs="e" * 64), hfrow(NONRAW, 2, git="f" * 40),
            hfrow(RAW_A, 6, git="d" * 40)]


def argv(*extra):
    return ["--baseline-snapshot", "365", "--target-revision", REV,
            "--expected-fingerprint", FP, "--expected-full-files", "3",
            "--expected-raw-files", "2", "--expected-files", "1",
            "--expected-bytes", "6", *extra]


def sample_payload():
    return m.canonical_bytes(m.build_manifest(context(), rows(), expected_full_files=3,
                                               expected_raw_files=2))


class CLITests(TestCase):
    def fails(self, args):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm:
            m.parse_args(args)
        self.assertEqual(cm.exception.code, 2)

    def test_plan(self):
        a = m.parse_args(argv("--plan"))
        self.assertTrue(a.plan)
        self.assertFalse(a.publish)

    def test_publish(self):
        a = m.parse_args(argv("--publish", "--confirm-manifest-archive-only",
                              "--expected-manifest-sha256", "e" * 64))
        self.assertTrue(a.publish)

    def test_missing_mode(self): self.fails(argv())
    def test_both_modes(self): self.fails(argv("--plan", "--publish"))
    def test_publish_no_confirmation(self): self.fails(argv("--publish", "--expected-manifest-sha256", "e" * 64))
    def test_confirmation_on_plan(self): self.fails(argv("--plan", "--confirm-manifest-archive-only"))
    def test_publish_no_sha(self): self.fails(argv("--publish", "--confirm-manifest-archive-only"))
    def test_publish_bad_sha(self): self.fails(argv("--publish", "--confirm-manifest-archive-only", "--expected-manifest-sha256", "bad"))
    def test_plan_with_sha(self): self.fails(argv("--plan", "--expected-manifest-sha256", "e" * 64))
    def test_floating_revision(self): self.fails([*argv()[:3], "main", *argv()[4:], "--plan"])
    def test_uppercase_revision(self): self.fails([*argv()[:3], REV.upper(), *argv()[4:], "--plan"])
    def test_bad_fingerprint(self):
        a = argv("--plan"); a[5] = "z" * 64; self.fails(a)
    def test_zero_files(self):
        a = argv("--plan"); a[a.index("--expected-files")+1] = "0"; self.fails(a)
    def test_negative_bytes(self):
        a = argv("--plan"); a[a.index("--expected-bytes")+1] = "-1"; self.fails(a)
    def test_raw_more_than_full(self):
        a = argv("--plan"); a[a.index("--expected-raw-files")+1] = "4"; self.fails(a)
    def test_zero_baseline(self):
        a = argv("--plan"); a[1] = "0"; self.fails(a)


class NormalizationTests(TestCase):
    def test_row_format_source(self):
        got = m.normalized_row(rows()[-1], REV)
        self.assertEqual(got["format"], "json")
        self.assertEqual(got["source_hint"], "unclassified_2026_2027")
        self.assertEqual(got["hf_revision"], REV)

    def test_lfs_only(self):
        got = m.normalized_row(rows()[0], REV)
        self.assertEqual(got["lfs_sha256"], "e" * 64)
        self.assertEqual(got["git_blob_id"], "")

    def test_uppercase_identity_normalized(self):
        row = m.normalized_row(hfrow(RAW_B, 5, git="A" * 40), REV)
        self.assertEqual(row["git_blob_id"], "a" * 40)

    def test_unsafe_paths(self):
        for path in ("../evil.json", "/tmp/a.json", "raw_data/../a.json", "raw_data//a.json", "raw_data/evil\\name.json", "raw_data/\x00.json"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                m.normalized_row(hfrow(path, 6, git="a" * 40), REV)

    def test_invalid_size(self):
        for size in (None, True, -1, 5.4):
            with self.subTest(size=size), self.assertRaises(RuntimeError):
                m.normalized_row(hfrow(RAW_A, size, git="d" * 40), REV)

    def test_missing_identity(self):
        with self.assertRaises(RuntimeError): m.normalized_row(hfrow(RAW_A, 2), REV)

    def test_unknown_out_of_scope_path(self):
        with self.assertRaises(RuntimeError): m.normalized_row(hfrow("other/a.json", 2, git="a" * 40), REV)

    def test_unsupported_raw_format(self):
        with self.assertRaises(RuntimeError): m.normalized_row(hfrow("raw_data/a.zip", 2, git="a" * 40), REV)

    def test_malformed_lfs(self):
        with self.assertRaises(RuntimeError): m.normalized_row(hfrow(RAW_B, 2, lfs="no"), REV)

    def test_malformed_git(self):
        with self.assertRaises(RuntimeError): m.normalized_row(hfrow(RAW_B, 2, git="bad"), REV)


class ManifestTests(TestCase):
    def valid(self, entries=None, **counts):
        return m.build_manifest(context(), rows() if entries is None else entries,
                                expected_full_files=counts.get("full", 3),
                                expected_raw_files=counts.get("raw", 2))

    def test_full_manifest_schema(self):
        manifest = self.valid()
        self.assertEqual(manifest["file_count"], 3)
        self.assertEqual(manifest["repo_type"], "dataset")
        self.assertEqual(manifest["resolved_commit"], REV)
        self.assertIn("source_hint_counts", manifest)
        self.assertEqual([r["relative_path"] for r in manifest["files"]], sorted([RAW_A, RAW_B, NONRAW]))

    def test_downstream_manifest_registration_schema(self):
        """Real M4.6 registration validator accepts the generated manifest."""
        from scripts.tools.register_incremental_manifest import check_manifest
        request = NS(validate=lambda: None,
                     reuse=NS(registration=NS(repo_id=context().repo_id,
                                              target_revision=REV, manifest_files=3,
                                              raw_files=2)))
        raw = check_manifest(self.valid(), request)
        self.assertEqual(set(raw), {RAW_A, RAW_B})

    def test_deterministic_order(self):
        self.assertEqual(m.canonical_bytes(self.valid()), m.canonical_bytes(self.valid(list(reversed(rows())))))

    def test_no_timestamp(self): self.assertNotIn("generated_at_utc", self.valid())
    def test_no_candidates_in_manifest_suppression(self): self.assertEqual(len(self.valid()["files"]), 3)

    def test_duplicate_paths(self):
        with self.assertRaises(RuntimeError): self.valid([rows()[0], rows()[1], rows()[0]])

    def test_wrong_full_count(self):
        with self.assertRaises(RuntimeError): self.valid(full=4)

    def test_wrong_raw_count(self):
        with self.assertRaises(RuntimeError): self.valid(raw=1)

    def test_missing_target_path(self):
        with self.assertRaises(RuntimeError): self.valid([rows()[0], rows()[1], hfrow("raw_data/other/x.json", 3, git="d" * 40)])

    def test_wrong_size(self):
        altered = rows(); altered[-1] = hfrow(RAW_A, 7, git="d" * 40)
        with self.assertRaises(RuntimeError): self.valid(altered)

    def test_wrong_git(self):
        altered = rows(); altered[-1] = hfrow(RAW_A, 6, git="a" * 40)
        with self.assertRaises(RuntimeError): self.valid(altered)

    def test_wrong_lfs(self):
        altered = rows(); altered[0] = hfrow(RAW_B, 10, lfs="a" * 64)
        with self.assertRaises(RuntimeError): self.valid(altered)

    def test_blocked_plan(self):
        ctx = context()
        with mock.patch.object(ctx.plan, "count", side_effect=lambda name: 1 if name == "REMOVED" else 0):
            with self.assertRaises(RuntimeError): m.build_manifest(ctx, rows(), expected_full_files=3, expected_raw_files=2)

    def test_canonical_bytes(self):
        data = sample_payload()
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(json.loads(data)["file_count"], 3)
        self.assertEqual(hashlib.sha256(data).hexdigest(), hashlib.sha256(sample_payload()).hexdigest())

    def test_oversized_budget(self):
        with mock.patch.object(m, "MAX_MANIFEST_BYTES", 1):
            with self.assertRaises(RuntimeError): sample_payload()


class FetchTests(TestCase):
    def test_metadata_only_pinned(self):
        calls = []
        class FakeApi:
            def repo_info(self, **kw):
                calls.append(("info", kw)); return NS(sha=REV)
            def list_repo_tree(self, **kw):
                calls.append(("tree", kw)); return rows() + ["folder"]
        got = m.fetch_full_tree(context(), api=FakeApi(), file_type=NS)
        self.assertEqual(len(got), 3)
        self.assertEqual(calls[0][1]["revision"], REV)
        self.assertEqual(calls[1][1]["revision"], REV)
        self.assertTrue(calls[1][1]["recursive"])

    def test_wrong_commit(self):
        fake = NS(repo_info=lambda **kw: NS(sha="b" * 40))
        with self.assertRaises(RuntimeError): m.fetch_full_tree(context(), api=fake, file_type=NS)


class PublisherTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.payload = sample_payload()
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.report, self.archive = m.manifest_paths(REV, self.digest)

    def test_plan_does_not_create_dirs(self):
        report, archive, status = m.inspect_manifest_outputs(self.root, REV, self.digest, self.payload)
        self.assertEqual(status, ("READY_NEW", "READY_NEW"))
        self.assertFalse((self.root / "reports").exists())
        self.assertFalse((self.root / "raw-archive-v2").exists())

    def test_publish_both(self):
        a, b, outcome1, outcome2 = m.publish_manifest(self.root, REV, self.digest, self.payload)
        self.assertEqual((a, b), (self.report, self.archive))
        self.assertEqual((outcome1, outcome2), ("CREATED_NEW", "CREATED_NEW"))
        self.assertEqual((self.root / a).read_bytes(), (self.root / b).read_bytes())

    def test_publish_idempotent(self):
        m.publish_manifest(self.root, REV, self.digest, self.payload)
        result = m.publish_manifest(self.root, REV, self.digest, self.payload)
        self.assertEqual(result[2:], ("ALREADY_VERIFIED", "ALREADY_VERIFIED"))

    def test_report_corrupt_no_overwrite(self):
        m.publish_manifest(self.root, REV, self.digest, self.payload)
        (self.root / self.report).write_text("corrupt")
        with self.assertRaises(RuntimeError): m.publish_manifest(self.root, REV, self.digest, self.payload)
        self.assertEqual((self.root / self.report).read_text(), "corrupt")

    def test_archive_corrupt_no_overwrite(self):
        m.publish_manifest(self.root, REV, self.digest, self.payload)
        (self.root / self.archive).write_text("corrupt")
        with self.assertRaises(RuntimeError): m.publish_manifest(self.root, REV, self.digest, self.payload)
        self.assertEqual((self.root / self.archive).read_text(), "corrupt")

    def test_partial_publish_resumable(self):
        m.publish_create_only(self.root, self.report, self.payload)
        result = m.publish_manifest(self.root, REV, self.digest, self.payload)
        self.assertEqual(result[2:], ("ALREADY_VERIFIED", "CREATED_NEW"))

    def test_wrong_digest_refuses(self):
        with self.assertRaises(RuntimeError): m.inspect_manifest_outputs(self.root, REV, "a" * 64, self.payload)

    def test_no_direct_replace(self):
        with mock.patch.object(m.os, "replace", side_effect=AssertionError("replace forbidden")):
            m.publish_manifest(self.root, REV, self.digest, self.payload)

    def test_publish_failure_is_partial_and_retryable(self):
        orig = m.publish_create_only
        calls = []
        def injected(root, path, payload):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("simulated disk failure")
            return orig(root, path, payload)
        with mock.patch.object(m, "publish_create_only", side_effect=injected):
            with self.assertRaises(OSError): m.publish_manifest(self.root, REV, self.digest, self.payload)
        self.assertTrue((self.root / self.report).is_file())
        self.assertFalse((self.root / self.archive).exists())
        m.publish_manifest(self.root, REV, self.digest, self.payload)

    def test_traversal_refused(self):
        for p in ("reports/../escape.json", "../evil.json", "/root/a.json"):
            with self.subTest(path=p), self.assertRaises(RuntimeError):
                m.guarded_destination(self.root, p, create_parents=True)

    def test_symlink_destination_refused(self):
        dest = self.root / self.report
        dest.parent.mkdir(parents=True)
        outside = self.root / "outside.json"
        outside.write_bytes(self.payload)
        try:
            dest.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not allowed on this Windows account")
        with self.assertRaises(RuntimeError):
            m.publish_manifest(self.root, REV, self.digest, self.payload)

    def test_symlink_ancestor_refused(self):
        outside = self.root / "other"
        outside.mkdir()
        try:
            (self.root / "reports").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not allowed on this Windows account")
        with self.assertRaises(RuntimeError): m.publish_manifest(self.root, REV, self.digest, self.payload)

    def test_filename_validation(self):
        with self.assertRaises(RuntimeError): m.manifest_paths("main", self.digest)


class MainTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = context()

    def mocks(self):
        return (mock.patch.object(m, "ROOT", self.root),
                mock.patch.object(m, "load_context", return_value=self.ctx),
                mock.patch.object(m, "fetch_full_tree", return_value=rows()))

    def test_plan_no_writes(self):
        a, b, c = self.mocks()
        with a, b, c, redirect_stdout(StringIO()) as out:
            rc = m.main(argv("--plan"))
        self.assertEqual(rc, 0)
        self.assertIn("MANIFEST PLAN: PASS", out.getvalue())
        self.assertFalse((self.root / "reports").exists())

    def test_publish_without_expected_digest_refused(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            m.main(argv("--publish", "--confirm-manifest-archive-only"))

    def test_publish_incorrect_expected_digest_refused_without_writes(self):
        a, b, c = self.mocks()
        with a, b, c, redirect_stdout(StringIO()):
            with self.assertRaises(RuntimeError):
                m.main(argv("--publish", "--confirm-manifest-archive-only",
                            "--expected-manifest-sha256", "a" * 64))
        self.assertFalse((self.root / "reports").exists())

    def test_publish_correct_digest(self):
        a, b, c = self.mocks()
        digest = hashlib.sha256(sample_payload()).hexdigest()
        with a, b, c, redirect_stdout(StringIO()) as out:
            rc = m.main(argv("--publish", "--confirm-manifest-archive-only",
                             "--expected-manifest-sha256", digest))
        self.assertEqual(rc, 0)
        self.assertIn("MANIFEST PUBLICATION: PASS", out.getvalue())
        report, archive = m.manifest_paths(REV, digest)
        self.assertEqual((self.root / report).read_bytes(), (self.root / archive).read_bytes())

    def test_full_count_drift_refused(self):
        a, b, c = self.mocks()
        self.ctx.excluded = 2
        with a, b, c, redirect_stdout(StringIO()), self.assertRaises(RuntimeError):
            m.main(argv("--plan"))


if __name__ == "__main__":
    import unittest
    unittest.main()
