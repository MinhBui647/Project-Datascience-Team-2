"""M4.6.4c.6 offline tests: no Hugging Face network and no PostgreSQL connections."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest import TestCase, main as unittest_main
from unittest.mock import patch

from scripts.tools import download_incremental_candidates as subject

REV = "a" * 40
BASE = "b" * 40
FP = "c" * 64
REPO = "Toibinguz/epl-premier-league-dataset"
PATH1 = "2026_2027_new/example_1.json"
PATH2 = "raw_data/foo/example_2.json"


class FakePlan:
    def __init__(self, *, actions=None, reused=100, fingerprint=FP, raw_count=4,
                 candidate_bytes=10, counts=None):
        self.actions = actions or ()
        self.reused_records = reused
        self.fingerprint = fingerprint
        self.target_raw_count = raw_count
        self.candidate_bytes = candidate_bytes
        self.counts = counts or {"UNCHANGED": 2, "NEW": 1, "CHANGED": 1, "REMOVED": 0, "REVIEW": 0}

    def count(self, category):
        return self.counts.get(category, 0)


def action(path=PATH1, category="NEW", size=4):
    return SimpleNamespace(path=path, category=category,
                           kind="UPSERT_VERSION_AFTER_REHASH", file_format="json", expected_bytes=size)


def standard():
    a = (action(), action(PATH2, "CHANGED", 6))
    target = {PATH1: SimpleNamespace(size=4), PATH2: SimpleNamespace(size=6),
              "raw_data/foo/unchanged1.json": SimpleNamespace(size=3),
              "raw_data/foo/unchanged2.json": SimpleNamespace(size=3)}
    plan = FakePlan(actions=a)
    ctx = subject.DownloadContext(REPO, 365, BASE, REV, plan, target, a, 18)
    return ctx


def argv(*extras):
    return ["--baseline-snapshot", "365", "--target-revision", REV,
            "--expected-fingerprint", FP, "--expected-files", "2",
            "--expected-bytes", "10", *extras]


def check_for(a):
    return SimpleNamespace(path=a.path, category=a.category, bytes=a.expected_bytes,
                           sha256="f" * 64, records=2)


class CliTests(TestCase):
    def parse_error(self, arguments):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as cm:
            subject.parse_args(arguments)
        self.assertEqual(cm.exception.code, 2)

    def test_plan(self):
        opts = subject.parse_args(argv("--plan"))
        self.assertTrue(opts.plan)
        self.assertFalse(opts.download)

    def test_download_confirmed(self):
        opts = subject.parse_args(argv("--download", "--confirm-cache-only"))
        self.assertTrue(opts.download)

    def test_download_without_confirm_blocked(self):
        self.parse_error(argv("--download"))

    def test_plan_with_confirm_blocked(self):
        self.parse_error(argv("--plan", "--confirm-cache-only"))

    def test_no_mode_blocked(self):
        self.parse_error(argv())

    def test_both_modes_blocked(self):
        self.parse_error(argv("--plan", "--download"))

    def test_floating_revision_blocked(self):
        args = argv("--plan")
        args[3] = "main"
        self.parse_error(args)

    def test_uppercase_revision_blocked(self):
        self.parse_error([*argv("--plan")[:3], REV.upper(), *argv("--plan")[4:]])

    def test_invalid_fingerprint_blocked(self):
        v = argv("--plan")
        v[5] = "bad"
        self.parse_error(v)

    def test_empty_files_blocked(self):
        v = argv("--plan")
        v[7] = "0"
        self.parse_error(v)

    def test_bad_optional_records_blocked(self):
        self.parse_error(argv("--plan", "--expected-records", "0"))

    def test_optional_records_allowed(self):
        self.assertEqual(subject.parse_args(argv("--plan", "--expected-records", "4")).expected_records, 4)


class PathTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "reports").mkdir()

    def test_safe_raw_paths(self):
        self.assertTrue(subject.candidate_path_ok(PATH1))
        self.assertTrue(subject.candidate_path_ok(PATH2))

    def test_unsafe_raw_paths(self):
        for p in ["../raw_data/x.json", "clean/x.json", "raw_data/../evil.json",
                  "raw_data//x.json", "raw_data/x.csv", "raw_data\\bad.json",
                  "raw_data/./x.json", "raw_data/a\nb.json", None]:
            with self.subTest(path=p):
                self.assertFalse(subject.candidate_path_ok(p))

    def test_plan_does_not_create_cache_directory(self):
        actual = subject.validated_cache_path("reports/new_cache", root=self.root, create=False)
        self.assertFalse(actual.exists())

    def test_download_creates_only_inside_reports(self):
        actual = subject.validated_cache_path("reports/new_cache", root=self.root, create=True)
        self.assertTrue(actual.is_dir())
        self.assertTrue(actual.is_relative_to(self.root / "reports"))

    def test_blocks_cache_root(self):
        with self.assertRaises(ValueError):
            subject.validated_cache_path("reports", root=self.root)

    def test_blocks_cache_traversal(self):
        for p in ("../bad", "reports/../../bad", "reports/../reports/inside"):
            with self.subTest(p=p), self.assertRaises(ValueError):
                subject.validated_cache_path(p, root=self.root)

    def test_blocks_cache_outside_reports(self):
        with self.assertRaises(ValueError):
            subject.validated_cache_path(str(self.root / "outside"), root=self.root)

    def test_blocks_file_in_place_of_dir(self):
        (self.root / "reports" / "bad").write_text("file", encoding="utf8")
        with self.assertRaises(ValueError):
            subject.validated_cache_path("reports/bad/sub", root=self.root, create=True)

    def test_blocks_symlink_path_if_supported(self):
        real = self.root / "reports" / "real"
        real.mkdir()
        link = self.root / "reports" / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"Symlink privilege unavailable: {e}")
        with self.assertRaises(ValueError):
            subject.validated_cache_path("reports/link/data", root=self.root)


class ContractTests(TestCase):
    def test_valid_plan_contract(self):
        ctx = standard()
        subject.verify_plan_contract(ctx.plan, ctx.actions, ctx.target,
                                     fingerprint=FP, files=2, total_bytes=10)

    def assert_bad(self, ctx, *, fp=FP, files=2, total_bytes=10):
        with self.assertRaises(RuntimeError):
            subject.verify_plan_contract(ctx.plan, ctx.actions, ctx.target,
                                         fingerprint=fp, files=files, total_bytes=total_bytes)

    def test_fingerprint_mismatch(self):
        self.assert_bad(standard(), fp="d" * 64)

    def test_candidate_count_mismatch(self):
        self.assert_bad(standard(), files=1)

    def test_byte_count_mismatch(self):
        self.assert_bad(standard(), total_bytes=9)

    def test_removed_path_blocked(self):
        ctx = standard()
        ctx.plan.counts["REMOVED"] = 1
        self.assert_bad(ctx)

    def test_review_path_blocked(self):
        ctx = standard()
        ctx.plan.counts["REVIEW"] = 1
        self.assert_bad(ctx)

    def test_unapproved_format_blocked(self):
        ctx = standard()
        ctx = replace(ctx, actions=(SimpleNamespace(**{**vars(ctx.actions[0]), "file_format": "csv"}), ctx.actions[1]))
        self.assert_bad(ctx)

    def test_unsafe_action_path_blocked(self):
        ctx = standard()
        ctx = replace(ctx, actions=(action("raw_data/../escape.json"), ctx.actions[1]))
        self.assert_bad(ctx)

    def test_duplicate_action_path_blocked(self):
        ctx = standard()
        ctx = replace(ctx, actions=(ctx.actions[0], ctx.actions[0]))
        self.assert_bad(ctx)

    def test_changed_metadata_blocked(self):
        ctx = standard()
        ctx.target[PATH1].size = 999
        self.assert_bad(ctx)


class DownloadTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name).resolve()
        self.f1 = self.cache / "first.json"
        self.f2 = self.cache / "second.json"
        self.f1.write_text("{}", encoding="utf-8")
        self.f2.write_text("{}", encoding="utf-8")
        self.ctx = standard()
        self.calls = []

    def downloader(self, **kwargs):
        self.calls.append(kwargs)
        return str(self.f1 if kwargs["filename"] == PATH1 else self.f2)

    @staticmethod
    def validator(a, identity, path):
        return check_for(a)

    @staticmethod
    def assessor(plan, checks, expected_record_count=None):
        n = sum(x.records for x in checks)
        if expected_record_count is not None and n != expected_record_count:
            raise ValueError("Expected record mismatch")
        return plan.reused_records + n

    def run_download(self, **kwargs):
        with redirect_stdout(StringIO()):
            return subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                               validator=self.validator, assessor=self.assessor,
                                               **kwargs)

    def test_downloads_pinned_paths_and_verifies(self):
        checks, refs = self.run_download(expected_records=4)
        self.assertEqual(len(checks), 2)
        self.assertEqual(refs, 104)
        self.assertEqual([x["filename"] for x in self.calls], [PATH1, PATH2])
        self.assertTrue(all(x["revision"] == REV and x["repo_id"] == REPO for x in self.calls))
        self.assertTrue(all(x["local_files_only"] is False for x in self.calls))
        self.assertTrue(all(x["cache_dir"] == str(self.cache) for x in self.calls))

    def test_retry_after_partial_failure(self):
        def flaky(**kwargs):
            if kwargs["filename"] == PATH2 and not getattr(self, "retry", False):
                raise OSError("Simulated download interruption")
            return self.downloader(**kwargs)
        with self.assertRaises(OSError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=flaky,
                                        validator=self.validator, assessor=self.assessor)
        self.retry = True
        with redirect_stdout(StringIO()):
            checks, refs = subject.download_candidates(self.ctx, self.cache, downloader=flaky,
                                                       validator=self.validator, assessor=self.assessor)
        self.assertEqual(refs, 104)
        self.assertEqual(len(checks), 2)

    def test_wrong_records_rejected(self):
        with self.assertRaises(ValueError), redirect_stdout(StringIO()):
            self.run_download(expected_records=5)

    def test_wrong_sha_rejected(self):
        def invalid(a, i, p):
            return SimpleNamespace(**{**vars(check_for(a)), "sha256": "bad"})
        with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                        validator=invalid, assessor=self.assessor)

    def test_empty_json_records_rejected(self):
        def empty(a, i, p):
            return SimpleNamespace(**{**vars(check_for(a)), "records": 0})
        with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                        validator=empty, assessor=self.assessor)

    def test_wrong_byte_length_rejected(self):
        def incorrect(a, i, p):
            return SimpleNamespace(**{**vars(check_for(a)), "bytes": 3})
        with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                        validator=incorrect, assessor=self.assessor)

    def test_wrong_action_category_rejected(self):
        def incorrect(a, i, p):
            return SimpleNamespace(**{**vars(check_for(a)), "category": "REUSE"})
        with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                        validator=incorrect, assessor=self.assessor)

    def test_validator_error_propagates(self):
        def reject(a, i, p):
            raise ValueError("Git blob mismatch")
        with self.assertRaisesRegex(ValueError, "Git blob mismatch"), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                        validator=reject, assessor=self.assessor)

    def test_output_path_outside_cache_rejected(self):
        outside = self.cache.parent / (self.cache.name + "_outside.json")
        outside.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache,
                                        downloader=lambda **kw: str(outside),
                                        validator=self.validator, assessor=self.assessor)

    def test_missing_download_file_rejected(self):
        with self.assertRaises(FileNotFoundError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache,
                                        downloader=lambda **kw: str(self.cache / "not-found.json"),
                                        validator=self.validator, assessor=self.assessor)

    def test_inconsistent_assessor_rejected(self):
        with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
            subject.download_candidates(self.ctx, self.cache, downloader=self.downloader,
                                        validator=self.validator, assessor=lambda p, c, **k: 0)

    def test_empty_cache_dir_rejected(self):
        with self.assertRaises(FileNotFoundError):
            subject.download_candidates(self.ctx, self.cache / "does_not_exist",
                                        downloader=self.downloader, validator=self.validator,
                                        assessor=self.assessor)


class ReadOnlyContextTests(TestCase):
    """Fake the entire DB/HF metadata boundary; real network/DB are never called."""

    def fake_modules(self, *, drift=None):
        ctx = standard()
        modules = {}
        def mk(name, **functions):
            m = ModuleType(name)
            m.__dict__.update(functions)
            modules[name] = m
        mk("scripts.tools.check_raw_updates",
           load_baseline=lambda sid, repo: (BASE if drift != "db" else "e" * 40, {"old": 1}),
           load_target=lambda repo, rev: (REV if drift != "hf" else "d" * 40, ctx.target, 18))
        mk("scripts.tools.incremental_sync",
           assemble_plan=lambda baseline, target, existing, sources: ctx.plan)
        mk("scripts.tools.raw_sync_write_plan",
           read_db_state=lambda sid, repo, revision: (BASE, {}))
        mk("scripts.tools.raw_sync_preflight",
           read_existing_source_codes=lambda: {"foo"})
        mk("scripts.tools.incremental_cache_gate",
           candidate_actions=lambda plan, target: ctx.actions)
        return modules

    def test_build_context_matches_pin_and_counts(self):
        options = subject.parse_args(argv("--plan"))
        with patch.dict(sys.modules, self.fake_modules()):
            context = subject.load_context(options)
        self.assertEqual(context.baseline_snapshot, 365)
        self.assertEqual(context.target_revision, REV)
        self.assertEqual(len(context.actions), 2)
        self.assertEqual(context.plan.fingerprint, FP)

    def test_reject_hf_revision_mismatch(self):
        options = subject.parse_args(argv("--plan"))
        with patch.dict(sys.modules, self.fake_modules(drift="hf")), self.assertRaises(RuntimeError):
            subject.load_context(options)

    def test_reject_baseline_db_revision_mismatch(self):
        options = subject.parse_args(argv("--plan"))
        with patch.dict(sys.modules, self.fake_modules(drift="db")), self.assertRaises(RuntimeError):
            subject.load_context(options)

    def test_reject_wrong_expected_file_count(self):
        args = argv("--plan")
        args[7] = "9"
        options = subject.parse_args(args)
        with patch.dict(sys.modules, self.fake_modules()), self.assertRaises(RuntimeError):
            subject.load_context(options)


class MainTests(TestCase):
    def test_plan_never_calls_downloader(self):
        with patch.object(subject, "load_context", return_value=standard()), \
             patch.object(subject, "validated_cache_path", return_value=Path("/virtual/reports/cache")), \
             patch.object(subject, "download_candidates", side_effect=AssertionError("unexpected download")), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(subject.main(argv("--plan")), 0)
        self.assertIn("File-body downloads: NONE", output.getvalue())

    def test_confirmed_mode_calls_download(self):
        with patch.object(subject, "load_context", return_value=standard()), \
             patch.object(subject, "validated_cache_path", return_value=Path("/virtual/reports/cache")) as paths, \
             patch.object(subject, "download_candidates", return_value=((check_for(action()), check_for(action(PATH2, "CHANGED", 6))), 104)) as dl, \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(subject.main(argv("--download", "--confirm-cache-only")), 0)
        self.assertEqual(paths.call_count, 2)
        self.assertEqual(dl.call_count, 1)
        self.assertIn("Downstream --expected-records: 4", output.getvalue())

    def test_failed_plan_is_fail_closed(self):
        with patch.object(subject, "load_context", side_effect=RuntimeError("fingerprint mismatch")), \
             patch.object(subject, "download_candidates") as dl:
            with self.assertRaises(RuntimeError), redirect_stdout(StringIO()):
                subject.main(argv("--download", "--confirm-cache-only"))
        dl.assert_not_called()


if __name__ == "__main__":
    unittest_main()
