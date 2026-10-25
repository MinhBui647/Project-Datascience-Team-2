#!/usr/bin/env python3
"""M3.4E – bounded, resumable coordinator for the already-tested M3.4D runner.

Place at: raw_storage/scripts/tools/window_orchestrator.py

Offline plan:
    .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\window_orchestrator.py --plan --start-offset 14 --windows 2 --window-size 20

Commit ONLY after reviewing that plan:
    .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\window_orchestrator.py --commit --confirm-run --start-offset 14 --windows 2 --window-size 20

If interrupted or a window fails, resume the same checkpoint (after fixing the error):
    .\\.venv_v2\\Scripts\\python.exe scripts\\tools\\window_orchestrator.py --resume reports\\m3_4e_runs\\run_<id>.json --confirm-run

Important: This is NOT full-batch authorization. JSON/CSV only, max 5 MiB per source,
maximum 3 windows of 20 catalog paths per run. Defer large / compressed files.
Snapshot remains discovered. Idempotency is enforced by the existing M3.4D/DB logic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "scripts" / "tools"
RUNNER = TOOLS / "chunked_batch.py"
CATALOG = ROOT / "reports" / "raw_scope_catalog_v2.csv"
REPORTS = ROOT / "reports" / "m3_4e_runs"
LOCK_FILE = REPORTS / ".m3_4e_writer.lock"
REPO = "Toibinguz/epl-premier-league-dataset"
REV = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
MAX_WINDOWS = 3
MAX_WINDOW_SIZE = 20
MAX_FILE_MIB = 5
REPORT_RE = re.compile(r"^Report: (reports/m3_4_batch_[0-9a-f-]+\.json)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic_json(path: Path, content: dict) -> None:
    """Atomic JSON replacement; never touches V1 files or raw archives."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp",
                                     prefix=".m3_4e_", dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        json.dump(content, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_verified_catalog():
    sys.path.insert(0, str(TOOLS))
    import chunked_batch as d  # local, tested runner; no network imports
    if (d.ROOT.resolve() != ROOT.resolve() or d.REV != REV
            or d.REPO != REPO or d.CATALOG.resolve() != CATALOG.resolve()):
        raise RuntimeError("Unexpected M3.4D runner, catalog or pinned revision")
    if not RUNNER.is_file():
        raise RuntimeError(f"Missing prior tested runner at {RUNNER}")
    return d, d.load_catalog()


def make_windows(rows, runner, start: int, count: int, size: int) -> list[dict]:
    if start < 0 or start >= len(rows):
        raise ValueError(f"start-offset must be in 0..{len(rows)-1}")
    if not 1 <= count <= MAX_WINDOWS:
        raise ValueError(f"windows must be 1..{MAX_WINDOWS}")
    if not 1 <= size <= MAX_WINDOW_SIZE:
        raise ValueError(f"window-size must be 1..{MAX_WINDOW_SIZE}")
    windows = []
    for offset in range(start, min(len(rows), start + count * size), size):
        selected, eligible, deferred = runner.plan_window(rows, offset, size, MAX_FILE_MIB * 1024 * 1024)
        if not selected:
            break
        windows.append({
            "offset": offset, "limit": len(selected), "end": offset + len(selected),
            "eligible_paths": [r["relative_path"] for _, r in eligible],
            "deferred": [{"path": r["relative_path"], "reason": reason} for _, r, reason in deferred],
            "status": "PENDING", "report": None,
        })
    return windows


def show_windows(windows):
    print("=== M3.4E | BOUNDED MULTI-WINDOW INGESTION ===")
    print("Repo:", REPO)
    print("Pinned revision:", REV)
    print("Formats: CSV / JSON; defer CSV.GZ, TAR.GZ and files > 5 MiB")
    print("Window count:", len(windows))
    for w in windows:
        print(f"WINDOW [{w['offset']}, {w['end']}) | process={len(w['eligible_paths'])} "
              f"| defer={len(w['deferred'])} | {w['status']}")
        if w['deferred']:
            for r in w['deferred']:
                print(f"  DEFER {r['reason']} | {r['path']}")
    print("Eligible files in run:", sum(len(w['eligible_paths']) for w in windows))
    print("Deferred entries in run:", sum(len(w['deferred']) for w in windows))


def load_checkpoint(path_arg: str) -> tuple[Path, dict]:
    path = Path(path_arg)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path.parent != REPORTS.resolve() or not re.fullmatch(r"run_[0-9a-f-]{36}\.json", path.name):
        raise RuntimeError("--resume must point to a previously generated M3.4E run checkpoint")
    with path.open("r", encoding="utf-8") as fp:
        state = json.load(fp)
    return path, state


def verify_checkpoint(state: dict, rows, runner) -> None:
    if state.get("repo") != REPO or state.get("revision") != REV:
        raise RuntimeError("Checkpoint is not for the pinned repository/revision")
    if state.get("catalog_sha256") != sha256_file(CATALOG):
        raise RuntimeError("Catalog bytes changed since this checkpoint; refusing to resume")
    if state.get("runner_sha256") != sha256_file(RUNNER):
        raise RuntimeError("M3.4D code changed since this checkpoint; inspect before resuming")
    recreated = make_windows(rows, runner, state["start_offset"], state["window_count"], state["window_size"])
    existing = state.get("windows")
    if len(recreated) != len(existing):
        raise RuntimeError("Checkpoint window count changed")
    for i, ref in enumerate(recreated):
        saved = existing[i]
        for key in ("offset", "limit", "end", "eligible_paths", "deferred"):
            if ref[key] != saved.get(key):
                raise RuntimeError(f"Checkpoint plan mismatch at window {i} ({key})")
        if saved.get("status") not in ("PENDING", "FAILED", "PASS", "DEFERRED_ONLY"):
            raise RuntimeError(f"Unexpected checkpoint status at window {i}")
        if saved["status"] == "PASS":
            validate_batch_report(saved, state)


def validate_batch_report(window: dict, state: dict) -> dict:
    report_rel = window.get("report")
    if not report_rel or not re.fullmatch(r"reports/m3_4_batch_[0-9a-f-]+\.json", report_rel):
        raise RuntimeError("Missing/invalid M3.4D report path")
    p = (ROOT / report_rel).resolve()
    if p.parent != (ROOT / "reports").resolve():
        raise RuntimeError("Unsafe batch report path")
    with p.open("r", encoding="utf-8") as fp:
        report = json.load(fp)
    if (report.get("repo_id") != REPO or report.get("revision") != REV
            or report.get("offset") != window["offset"] or report.get("limit") != window["limit"]
            or report.get("failed_files") != 0):
        raise RuntimeError("M3.4D report identity/count does not match requested window")
    results = report.get("results", [])
    if len(results) != len(window["eligible_paths"]):
        raise RuntimeError("M3.4D report does not include every eligible path")
    if [r.get("path") for r in results] != window["eligible_paths"]:
        raise RuntimeError("M3.4D report path/order mismatch")
    if any(r.get("status") not in ("INSERTED_NEW", "SKIPPED_UNCHANGED") for r in results):
        raise RuntimeError("Unexpected result status in M3.4D report")
    inserted = sum(r.get("status") == "INSERTED_NEW" for r in results)
    skipped = sum(r.get("status") == "SKIPPED_UNCHANGED" for r in results)
    if (report.get("inserted_versions") != inserted or
            report.get("skipped_unchanged") != skipped or
            report.get("selected_count") != len(results)):
        raise RuntimeError("M3.4D report summary disagrees with per-file statuses")
    rows_inserted = [r.get("rows_inserted") for r in results]
    if (any(type(x) is not int or x < 0 for x in rows_inserted) or
            report.get("rows_inserted") != sum(rows_inserted)):
        raise RuntimeError("M3.4D report record totals disagree with per-file results")
    deferred = report.get("deferred") or []
    if [(r.get("path"), r.get("reason")) for r in deferred] != [
        (r["path"], r["reason"]) for r in window["deferred"]
    ]:
        raise RuntimeError("M3.4D deferred list mismatch")
    return report


def run_one(window: dict, state: dict) -> str:
    """Delegate DB writes to unchanged, already-validated M3.4D executable."""
    cmd = [sys.executable, str(RUNNER), "--commit", "--confirm-small-batch",
           "--offset", str(window["offset"]), "--limit", str(window["limit"]),
           "--max-file-mib", str(MAX_FILE_MIB)]
    print("Executing existing M3.4D window", window["offset"], "to", window["end"], flush=True)
    report_rel = None
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8:replace"
    with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace", env=env, bufsize=1) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            match = REPORT_RE.fullmatch(line.strip())
            if match:
                report_rel = match.group(1)
        code = process.wait()
    if code != 0:
        raise RuntimeError(f"M3.4D returned exit code {code}; inspect its report and resume safely")
    if report_rel is None:
        raise RuntimeError("M3.4D passed without emitting a report path")
    window["report"] = report_rel
    validate_batch_report(window, state)
    return report_rel


def process(state: dict, path: Path) -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError("M3.4E lock exists. Verify no other batch is running before recovering it")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"run_id={state['run_id']}\n")
        for window in state["windows"]:
            if window["status"] in ("PASS", "DEFERRED_ONLY"):
                print(f"SKIP completed window [{window['offset']}, {window['end']})")
                continue
            if not window["eligible_paths"]:
                window["status"] = "DEFERRED_ONLY"
                window["updated_utc"] = utc_now()
                state["updated_utc"] = utc_now()
                write_atomic_json(path, state)
                continue
            # A failure may occur *after* DB commit but before checkpoint save.
            # On resume, the M3.4D runner re-checks DB idempotently.
            try:
                run_one(window, state)
            except Exception as exc:
                window["status"] = "FAILED"
                state["status"] = "STOPPED_ON_ERROR"
                window["error"] = f"{type(exc).__name__}: {exc}"[:900]
                window["updated_utc"] = utc_now()
                state["updated_utc"] = utc_now()
                write_atomic_json(path, state)
                print(f"M3.4E STOPPED: {window['error']}", file=sys.stderr)
                print("Resume after fixing error with --resume and --confirm-run")
                return 1
            window["status"] = "PASS"
            window.pop("error", None)
            window["updated_utc"] = utc_now()
            state["updated_utc"] = utc_now()
            write_atomic_json(path, state)
        state["status"] = "WINDOWS_FINISHED_WITH_DEFERRED" if any(w["deferred"] for w in state["windows"]) else "WINDOWS_FINISHED"
        state["updated_utc"] = utc_now()
        write_atomic_json(path, state)
        print("=== M3.4E | SUMMARY ===")
        print("Run checkpoint:", path.relative_to(ROOT).as_posix())
        print("Windows:", len(state["windows"]))
        print("Eligible paths attempted:", sum(len(w["eligible_paths"]) for w in state["windows"]))
        print("Deferred paths:", sum(len(w["deferred"]) for w in state["windows"]))
        print("All requested windows processed. Snapshot NOT marked complete.")
        print("M3.4E RESULT: PASS")
        return 0
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="No network, PostgreSQL or disk writes")
    mode.add_argument("--commit", action="store_true", help="Commit bounded windows via existing M3.4D")
    mode.add_argument("--resume", metavar="CHECKPOINT", help="Resume failed/interrupted run from checkpoint")
    p.add_argument("--confirm-run", action="store_true", help="Required for --commit/--resume")
    p.add_argument("--start-offset", type=int, default=14, help="Zero-based sorted catalog offset")
    p.add_argument("--windows", type=int, default=2, help="Maximum 3 windows per run")
    p.add_argument("--window-size", type=int, default=20, help="Maximum 20 catalog paths per window")
    args = p.parse_args()
    if args.plan and args.confirm_run:
        p.error("--confirm-run is only valid for --commit / --resume")
    if (args.commit or args.resume) and not args.confirm_run:
        p.error("--commit and --resume require --confirm-run after reviewing --plan")
    runner, rows = read_verified_catalog()
    if args.resume:
        if (args.start_offset, args.windows, args.window_size) != (14, 2, 20):
            p.error("Do not supply window planning flags with --resume")
        cp_path, state = load_checkpoint(args.resume)
        verify_checkpoint(state, rows, runner)
        show_windows(state["windows"])
        print("Mode: RESUME using confirmed checkpoint (already PASS windows not repeated)")
        return process(state, cp_path)
    windows = make_windows(rows, runner, args.start_offset, args.windows, args.window_size)
    show_windows(windows)
    if args.plan:
        print("Mode: PLAN (local catalog only; no DB/network/files written)")
        print("M3.4E PLAN: PASS")
        return 0
    state = {
        "run_id": str(uuid.uuid4()), "repo": REPO, "revision": REV,
        "catalog_sha256": sha256_file(CATALOG), "runner_sha256": sha256_file(RUNNER),
        "created_utc": utc_now(), "updated_utc": utc_now(),
        "start_offset": args.start_offset, "window_count": args.windows,
        "window_size": args.window_size, "file_size_cap_mib": MAX_FILE_MIB,
        "status": "RUNNING", "windows": windows,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    cp_path = REPORTS / f"run_{state['run_id']}.json"
    if cp_path.exists():
        raise RuntimeError("Unexpected checkpoint collision")
    write_atomic_json(cp_path, state)
    print("Mode: COMMIT; checkpoint:", cp_path.relative_to(ROOT).as_posix())
    return process(state, cp_path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"M3.4E RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
