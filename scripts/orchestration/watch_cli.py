"""Explicit read-only Hugging Face revision check."""

import argparse
import importlib
import json
from pathlib import Path
import sys

from .commit_watcher import watch_once, report_dict

ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="M5 HF commit watcher; M4 remains authoritative"
    )

    parser.add_argument("--check", action="store_true")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision-ref", default="main")
    parser.add_argument("--baseline-snapshot", type=int)

    args = parser.parse_args(argv)

    if not args.check:
        parser.error("Explicit --check is required")

    return args


def load_m4_services():
    """Late imports: --help does not access HF or PostgreSQL."""
    directory = ROOT / "scripts" / "incremental"

    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

    updates = importlib.import_module("check_raw_updates")
    stages = importlib.import_module(
        "incremental_guarded_stage_planner"
    )

    return (
        updates.resolve_target_revision,
        stages.plan_stages_read_only,
    )


def main(argv=None):
    args = parse_args(argv)

    try:
        resolver, planner = load_m4_services()

        outcome = watch_once(
            repo_id=args.repo_id,
            revision_ref=args.revision_ref,
            baseline_snapshot=args.baseline_snapshot,
            resolver=resolver,
            planner=planner,
        )

    except Exception as exc:
        print(
            json.dumps({
                "state": "FAILED",
                "error_type": type(exc).__name__,
                "m5_write_executed": False,
            }),
            file=sys.stderr,
        )
        return 3

    print(json.dumps(
        report_dict(outcome),
        ensure_ascii=False,
        indent=2,
    ))

    if outcome.state == "BLOCKED":
        return 2

    if outcome.state == "UNRESOLVED":
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
