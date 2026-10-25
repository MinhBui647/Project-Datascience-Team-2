import unittest
from types import SimpleNamespace

from scripts.orchestration.controller import plan_once


REVISION = "a" * 40
REPOSITORY = "sample/epl-dataset"


def sample_plan(**overrides):
    values = {
        "repo_id": REPOSITORY,
        "target_revision": REVISION,
        "decision": "PLAN",
        "recovery_state": "NONE",
        "next_review_stage": "PREPARE",
        "blockers": (),
        "stages": ("PREPARE",),
        "read_only_previews": (("python", "--plan"),),
        "planned_context_sha256": "b" * 64,
        "write_authorized": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ReadOnlyControllerTests(unittest.TestCase):

    def call_controller(self, result=None, **kwargs):
        calls = []

        def planner(**arguments):
            calls.append(arguments)
            return result if result is not None else sample_plan()

        outcome = plan_once(
            repo_id=kwargs.get("repo_id", REPOSITORY),
            target_revision=kwargs.get("target_revision", REVISION),
            baseline_snapshot=kwargs.get("baseline_snapshot"),
            planner=planner,
        )
        return outcome, calls

    def test_review_stage_calls_planner_once(self):
        outcome, calls = self.call_controller(baseline_snapshot=4)
        self.assertEqual(outcome.status, "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(outcome.next_review_stage, "PREPARE")
        self.assertEqual(outcome.stage_count, 1)
        self.assertEqual(outcome.read_only_preview_count, 1)
        self.assertFalse(outcome.m5_write_executed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["baseline_snapshot"], 4)

    def test_blockers_are_preserved(self):
        outcome, _ = self.call_controller(
            sample_plan(blockers=("missing evidence",))
        )
        self.assertEqual(outcome.status, "BLOCKED")
        self.assertEqual(outcome.blockers, ("missing evidence",))

    def test_no_next_stage_is_not_claimed_completed(self):
        outcome, _ = self.call_controller(
            sample_plan(next_review_stage=None, stages=())
        )
        self.assertEqual(outcome.status, "NO_STAGE_REPORTED")

    def test_target_revision_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            self.call_controller(
                sample_plan(target_revision="c" * 40)
            )

    def test_invalid_sha_fails_before_planning(self):
        for revision in ("main", "A" * 40, "abc"):
            with self.subTest(revision=revision):
                with self.assertRaises(ValueError):
                    self.call_controller(target_revision=revision)

    def test_invalid_baseline_is_rejected(self):
        for baseline in (0, -1, True):
            with self.subTest(baseline=baseline):
                with self.assertRaises(ValueError):
                    self.call_controller(baseline_snapshot=baseline)

    def test_missing_contract_field_fails_closed(self):
        incomplete = sample_plan()
        del incomplete.blockers
        with self.assertRaises(ValueError):
            self.call_controller(incomplete)

    def test_empty_repository_is_rejected(self):
        with self.assertRaises(ValueError):
            self.call_controller(repo_id="")

    def test_m4_write_authorization_never_triggers_m5_write(self):
        outcome, _ = self.call_controller(
            sample_plan(write_authorized=True)
        )
        self.assertTrue(outcome.m4_write_authorized)
        self.assertFalse(outcome.m5_write_executed)


if __name__ == "__main__":
    unittest.main()
