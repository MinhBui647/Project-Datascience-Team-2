"""M5 plan-only CLI. Does not enable M4 write operations."""

import argparse
import importlib
import json
from pathlib import Path
import sys

from .controller import plan_once, report_dict, validate_inputs


ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="M5 read-only controller using existing M4 planner"
    )
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--baseline-snapshot", type=int)

    args = parser.parse_args(argv)

    if not args.plan:
        parser.error("Explicit --plan is required")

    return args


def load_m4_planner():
    """Late import: --help stays independent from DB/HF access."""
    canonical_dir = ROOT / "scripts" / "incremental"

    if str(canonical_dir) not in sys.path:
        sys.path.insert(0, str(canonical_dir))

    module = importlib.import_module(
        "incremental_guarded_stage_planner"
    )

    return module.plan_stages_read_only


def main(argv=None):
    args = parse_args(argv)

    try:
        validate_inputs(
            args.repo_id,
            args.target_revision,
            args.baseline_snapshot,
        )

        summary = plan_once(
            repo_id=args.repo_id,
            target_revision=args.target_revision,
            baseline_snapshot=args.baseline_snapshot,
            planner=load_m4_planner(),
        )

    except Exception as exc:
        print(
            json.dumps({
                "status": "FAILED",
                "error_type": type(exc).__name__,
                "m5_write_executed": False,
            }),
            file=sys.stderr,
        )
        return 3

    print(json.dumps(
        report_dict(summary),
        indent=2,
        ensure_ascii=False,
    ))

    return 2 if summary.status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
