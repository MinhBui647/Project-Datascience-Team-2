"""Offline M4.6.4c.7 tests: no HF network / real PostgreSQL / real archive writes."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest import TestCase, mock
import hashlib
import os
import unittest

from scripts.tools import promote_incremental_archive as mod
from scripts.tools.incremental_archive_promotion import ArchiveCandidate

REV = "a" * 40
FP = "b" * 64
S1 = "c" * 64
S2 = "d" * 64
P1 = "2026_2027_new/a.json"
P2 = "raw_data/fotmob/b.json"


def candidate(path=P1, sha=S1, nbytes=4, records=1, action="READY_NEW"):
    return ArchiveCandidate(path, sha, nbytes, records,
                            f"raw-archive-v2/objects/{sha[:2]}/{sha}", action)


class FakePlan:
    fingerprint = FP
    target_raw_count = 3
    def __init__(self, **kwargs):
        self.categories = {"UNCHANGED": 1, "NEW": 1, "CHANGED": 1, "REMOVED": 0, "REVIEW": 0}
        self.categories.update(kwargs)
    def count(self, category):
        return self.categories.get(category, 0)


def action(path, cat):
    return NS(path=path, category=cat, expected_bytes=4,
              kind="UPSERT_VERSION_AFTER_REHASH", file_format="json")


def context():
    return NS(repo_id="company/dataset", baseline_snapshot=365,
              baseline_revision="9" * 40, target_revision=REV,
              plan=FakePlan(), excluded=2,
              actions=(action(P1, "NEW"), action(P2, "CHANGED")),
              target={P1: object(), P2: object()})


def examples():
    return (candidate(P1, S1), candidate(P2, S2))


def cli_args(*extra):
    return ["--baseline-snapshot", "365", "--target-revision", REV,
            "--expected-fingerprint", FP, "--expected-files", "2",
            "--expected-bytes", "8", "--expected-records", "2", *extra]


class TestCLI(TestCase):
    def assert_bad(self, args):
        with self.assertRaises(SystemExit) as cm:
            mod.parse_args(args)
        self.assertEqual(cm.exception.code, 2)

    def test_plan_valid(self):
        a = mod.parse_args(cli_args("--plan"))
        self.assertTrue(a.plan)
        self.assertFalse(a.promote)

    def test_promote_requires_pair(self):
        a = mod.parse_args(cli_args("--promote", "--confirm-archive-only"))
        self.assertTrue(a.promote)
        self.assertTrue(a.confirm_archive_only)

    def test_no_mode(self):
        self.assert_bad(cli_args())

    def test_both_modes(self):
        self.assert_bad(cli_args("--plan", "--promote", "--confirm-archive-only"))

    def test_missing_write_confirmation(self):
        self.assert_bad(cli_args("--promote"))

    def test_confirmation_disallowed_on_plan(self):
        self.assert_bad(cli_args("--plan", "--confirm-archive-only"))

    def test_zero_baseline(self):
        a = cli_args("--plan")
        a[1] = "0"
        self.assert_bad(a)

    def test_negative_baseline(self):
        a = cli_args("--plan")
        a[1] = "-3"
        self.assert_bad(a)

    def test_nonsha_revision(self):
        a = cli_args("--plan")
        a[3] = "main"
        self.assert_bad(a)

    def test_uppercase_revision(self):
        a = cli_args("--plan")
        a[3] = "A" * 40
        self.assert_bad(a)

    def test_invalid_fingerprint(self):
        a = cli_args("--plan")
        a[5] = "x" * 64
        self.assert_bad(a)

    def test_nonpositive_files(self):
        a = cli_args("--plan")
        a[7] = "0"
        self.assert_bad(a)

    def test_nonpositive_bytes(self):
        a = cli_args("--plan")
        a[9] = "0"
        self.assert_bad(a)

    def test_nonpositive_records(self):
        a = cli_args("--plan")
        a[11] = "0"
        self.assert_bad(a)

    def test_repo_blank(self):
        self.assert_bad(cli_args("--repo-id", "  ", "--plan"))

    def test_default_cache_is_downloaders(self):
        from scripts.tools.download_incremental_candidates import DEFAULT_CACHE
        self.assertEqual(mod.parse_args(cli_args("--plan")).cache_dir, DEFAULT_CACHE)


class TestContract(TestCase):
    def check(self, cs=None, plan=None, actions=None, **expected):
        values = {"expected_fingerprint": FP, "expected_files": 2,
                  "expected_bytes": 8, "expected_records": 2,
                  "actions": context().actions}
        values.update(expected)
        if actions is not None:
            values["actions"] = actions
        return mod.check_promotion_contract(plan or FakePlan(),
                                            examples() if cs is None else cs, **values)

    def bad(self, cs=None, plan=None, **expected):
        with self.assertRaises(ValueError):
            self.check(cs, plan=plan, **expected)

    def test_valid(self): self.check()
    def test_legacy_api(self):
        mod.check_promotion_contract(FakePlan(), examples(),
            expected_fingerprint=FP, expected_files=2,
            expected_bytes=8, expected_records=2)
    def test_fingerprint(self): self.bad(expected_fingerprint="a" * 64)
    def test_removed(self): self.bad(plan=FakePlan(REMOVED=1))
    def test_review(self): self.bad(plan=FakePlan(REVIEW=1))
    def test_incomplete_raw_coverage(self): self.bad(plan=FakePlan(UNCHANGED=2))
    def test_file_count(self): self.bad(expected_files=3)
    def test_byte_count(self): self.bad(expected_bytes=9)
    def test_record_count(self): self.bad(expected_records=3)
    def test_duplicate_paths(self): self.bad((candidate(), candidate(sha=S2)))
    def test_bad_sha(self): self.bad((candidate(sha="0"), candidate(P2, S2)))
    def test_bad_archive_path(self): self.bad((replace(candidate(), archive_relative_path="bad"), examples()[1]))
    def test_bad_status(self): self.bad((replace(candidate(), action="UNVERIFIED"), examples()[1]))
    def test_empty_candidate_records(self): self.bad((candidate(records=0), candidate(P2, S2, records=2)))
    def test_wrong_actions(self): self.bad(actions=(action("wrong.json", "NEW"), action(P2, "CHANGED")))
    def test_wrong_category(self): self.bad(actions=(action(P1, "REUSE"), action(P2, "CHANGED")))
    def test_duplicate_actions(self): self.bad(actions=(action(P1, "NEW"), action(P1, "CHANGED")))


class TestCache(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.file1 = self.cache / "a.json"
        self.file2 = self.cache / "b.json"
        self.file1.write_text("{}", encoding="utf-8")
        self.file2.write_text("[]", encoding="utf-8")

    def fetcher(self, **kwargs):
        self.assertTrue(kwargs["local_files_only"])
        self.assertEqual(kwargs["revision"], REV)
        self.assertEqual(kwargs["repo_id"], "company/dataset")
        self.assertEqual(kwargs["cache_dir"], str(self.cache.resolve()))
        return self.file1 if kwargs["filename"] == P1 else self.file2

    def test_resolve_both_pinned(self):
        result = mod.resolve_cached_candidates(context(), self.cache, fetcher=self.fetcher)
        self.assertEqual(set(result), {P1, P2})

    def test_missing_cached_object(self):
        self.file1.unlink()
        with self.assertRaises(FileNotFoundError):
            mod.resolve_cached_candidates(context(), self.cache, fetcher=self.fetcher)

    def test_external_path_blocked(self):
        with TemporaryDirectory() as other:
            outside = Path(other) / "x.json"
            outside.write_text("{}")
            with self.assertRaises(RuntimeError):
                mod.resolve_cached_candidates(context(), self.cache, fetcher=lambda **_: outside)

    def test_directory_instead_of_file_blocked(self):
        with self.assertRaises(RuntimeError):
            mod.resolve_cached_candidates(context(), self.cache, fetcher=lambda **_: self.cache)

    def test_symlink_outside_blocked_if_supported(self):
        with TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir) / "x.json"
            outside.write_text("{}")
            symlink = self.cache / "outside_link.json"
            try:
                symlink.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink unavailable on this machine")
            with self.assertRaises(RuntimeError):
                mod.resolve_cached_candidates(context(), self.cache, fetcher=lambda **_: symlink)

    def test_duplicate_action_path(self):
        ctx = context()
        ctx.actions = (action(P1, "NEW"), action(P1, "CHANGED"))
        with self.assertRaises(RuntimeError):
            mod.resolve_cached_candidates(ctx, self.cache, fetcher=self.fetcher)

    def test_no_cache_directory(self):
        with self.assertRaises(FileNotFoundError):
            mod.resolve_cached_candidates(context(), self.cache / "gone", fetcher=self.fetcher)

    def test_prepare_bundle_valid(self):
        def audit(plan, target, files, root, expected_records):
            self.assertEqual(set(files), {P1, P2})
            self.assertEqual(expected_records, 2)
            return examples()
        bundle = mod.prepare_bundle(context(), self.cache,
                expected_fingerprint=FP, expected_files=2,
                expected_bytes=8, expected_records=2,
                fetcher=self.fetcher, auditor=audit)
        self.assertEqual(bundle.candidates, examples())

    def test_prepare_bundle_rejects_extra_path(self):
        with self.assertRaises(ValueError):
            mod.prepare_bundle(context(), self.cache,
                expected_fingerprint=FP, expected_files=2,
                expected_bytes=8, expected_records=2,
                fetcher=self.fetcher,
                auditor=lambda *_args, **_kwargs: (candidate(P1, S1), candidate("alien.json", S2)))

    def test_prepare_bundle_propagates_audit_failure(self):
        def fail(*_args, **_kwargs): raise RuntimeError("bad archive")
        with self.assertRaisesRegex(RuntimeError, "bad archive"):
            mod.prepare_bundle(context(), self.cache,
                expected_fingerprint=FP, expected_files=2,
                expected_bytes=8, expected_records=2,
                fetcher=self.fetcher, auditor=fail)


class TestPublication(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = {P1: self.root / "one", P2: self.root / "two"}
        for p in self.files.values(): p.write_bytes(b"test")
        self.items = examples()
        self.target = {P1: object(), P2: object()}
        self.states = {}

    def publish(self, root, src, identity, candidate):
        self.states[candidate.sha256] = "ALREADY_ARCHIVED"
        return "ARCHIVED_NEW"

    def inspector(self, root, sha, nbytes):
        return self.root / sha, "archive-path", self.states.get(sha, "READY_NEW")

    def test_publish_batch(self):
        self.assertEqual(mod.promote_batch(self.root, self.items, self.files, self.target,
                                          publisher=self.publish, inspector=self.inspector), (2, 0))

    def test_already_archived(self):
        self.states = {S1: "ALREADY_ARCHIVED", S2: "ALREADY_ARCHIVED"}
        self.assertEqual(mod.promote_batch(self.root, self.items, self.files, self.target,
                         publisher=lambda *args: "ALREADY_ARCHIVED", inspector=self.inspector), (0, 2))

    def test_unknown_publisher_result(self):
        with self.assertRaises(RuntimeError):
            mod.promote_batch(self.root, self.items, self.files, self.target,
                              publisher=lambda *args: "INVALID", inspector=self.inspector)

    def test_post_write_inspection_failure(self):
        with self.assertRaises(RuntimeError):
            mod.promote_batch(self.root, self.items, self.files, self.target,
                              publisher=lambda *args: "ARCHIVED_NEW", inspector=self.inspector)

    def test_duplicate_candidate_paths_blocked(self):
        with self.assertRaises(ValueError):
            mod.promote_batch(self.root, (candidate(), candidate(sha=S2)),
                              self.files, self.target, publisher=self.publish, inspector=self.inspector)

    def test_missing_cached_file_blocked(self):
        with self.assertRaises(ValueError):
            mod.promote_batch(self.root, self.items, {P1: self.files[P1]},
                              self.target, publisher=self.publish, inspector=self.inspector)

    def test_missing_identity_blocked(self):
        with self.assertRaises(ValueError):
            mod.promote_batch(self.root, self.items, self.files, {P1: object()},
                              publisher=self.publish, inspector=self.inspector)

    def test_partial_failure_retry(self):
        calls = [0]
        def flaky(root, src, identity, cand):
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("interrupted")
            self.states[cand.sha256] = "ALREADY_ARCHIVED"
            return "ARCHIVED_NEW"
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            mod.promote_batch(self.root, self.items, self.files, self.target,
                              publisher=flaky, inspector=self.inspector)
        self.assertEqual(self.states.get(S1), "ALREADY_ARCHIVED")
        def retry(root, src, identity, cand):
            if cand.sha256 in self.states: return "ALREADY_ARCHIVED"
            return self.publish(root, src, identity, cand)
        self.assertEqual(mod.promote_batch(self.root, self.items, self.files, self.target,
                                          publisher=retry, inspector=self.inspector), (1, 1))

    def test_post_promotion_reaudit_success(self):
        b = mod.PromotionBundle(context(), self.files, self.items)
        mod.verify_post_promotion(b, root=self.root, expected_records=2,
            auditor=lambda *_args, **_kwargs: tuple(replace(c, action="ALREADY_ARCHIVED") for c in self.items))

    def test_post_promotion_reaudit_detects_drift(self):
        b = mod.PromotionBundle(context(), self.files, self.items)
        with self.assertRaises(RuntimeError):
            mod.verify_post_promotion(b, root=self.root, expected_records=2,
                auditor=lambda *_args, **_kwargs: (replace(self.items[0], action="ALREADY_ARCHIVED"),))


class TestMainModes(TestCase):
    def run_main(self, mode="--plan", *, bad_preflight=False):
        ctx = context()
        args = cli_args(mode, *(["--confirm-archive-only"] if mode == "--promote" else []))
        bundle = mod.PromotionBundle(ctx, {P1: Path("a"), P2: Path("b")}, examples())
        with mock.patch.object(mod, "load_context", return_value=ctx) as load, \
             mock.patch.object(mod, "validated_cache_path", return_value=Path("cache")) as cp, \
             mock.patch.object(mod, "prepare_bundle", side_effect=RuntimeError("bad preflight") if bad_preflight else None,
                               return_value=bundle) as prepare, \
             mock.patch.object(mod, "promote_batch", return_value=(2, 0)) as promote, \
             mock.patch.object(mod, "verify_post_promotion") as verify:
            if bad_preflight:
                with self.assertRaisesRegex(RuntimeError, "bad preflight"):
                    mod.main(args)
            else:
                self.assertEqual(mod.main(args), 0)
            return load, cp, prepare, promote, verify

    def test_plan_never_calls_publisher(self):
        load, cp, prepare, promote, verify = self.run_main()
        promote.assert_not_called()
        verify.assert_not_called()
        load.assert_called_once()
        cp.assert_called_once()

    def test_promote_explicit_calls_and_reaudits(self):
        load, cp, prepare, promote, verify = self.run_main("--promote")
        promote.assert_called_once()
        verify.assert_called_once()

    def test_bad_preflight_blocks_promotion(self):
        load, cp, prepare, promote, verify = self.run_main("--promote", bad_preflight=True)
        promote.assert_not_called()
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
