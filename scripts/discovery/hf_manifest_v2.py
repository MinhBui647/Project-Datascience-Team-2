r"""Read-only HF repository inventory for EPL Raw Storage V2.

Run from project root on Windows:
  .\.venv_v2\Scripts\python.exe scripts\tools\hf_manifest_v2.py

Does not download file bodies or write to PostgreSQL.
Writes reports/hf_manifest_v2.json and reports/hf_manifest_v2.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ID = "Toibinguz/epl-premier-league-dataset"
FORMAT_SUFFIXES = (
    (".tar.gz", "tar.gz"),
    (".jsonl.gz", "jsonl.gz"),
    (".csv.gz", "csv.gz"),
    (".jsonl", "jsonl"),
    (".ndjson", "jsonl"),
    (".json", "json"),
    (".csv", "csv"),
    (".parquet", "parquet"),
    (".xlsx", "xlsx"),
    (".zip", "zip"),
    (".md", "md"),
    (".txt", "txt"),
    (".gz", "gz"),
)
FIELDS = [
    "relative_path", "format", "size_bytes", "source_hint", "season_hint",
    "league_hint", "group", "lfs_sha256", "git_blob_id", "hf_revision",
]


def detect_format(path: str) -> str:
    name = path.lower()
    return next((label for suffix, label in FORMAT_SUFFIXES if name.endswith(suffix)), "other")


def season_from_path(path: str) -> str | None:
    # Only a metadata hint; does not decide a record's actual season.
    match = re.search(r"(?<!\d)(20\d{2})[_-](20\d{2}|\d{2})(?!\d)", path)
    if match:
        start = int(match.group(1))
        finish = int(match.group(2))
        if finish < 100:
            finish = (start // 100) * 100 + finish
            if finish < start:
                finish += 100
        if finish == start + 1:
            return f"{start}-{finish}"
    match = re.search(r"(?<!\d)(\d{2})(\d{2})(?!\d)", Path(path).stem)
    if match and path.lower().startswith("2026_2027_new/") and (match.group(1), match.group(2)) == ("26", "27"):
        return "2026-2027"
    if path.lower().startswith("2026_2027_new/"):
        return "2026-2027"
    return None


def source_from_path(path: str) -> str:
    parts = path.lower().split("/")
    if len(parts) > 1 and parts[0] == "raw_data":
        return parts[1]
    if parts[0] == "2026_2027_new":
        if len(parts) > 2 and parts[1] == "matches" and parts[2] in ("fotmob", "understat"):
            return parts[2]
        filename = parts[-1]
        if filename.startswith("fpl_"):
            return "fpl"
        if "football_data" in filename:
            return "football_data"
        if "weather" in filename:
            return "weather"
        if "fotmob" in filename:
            return "fotmob"
        if "understat" in filename:
            return "understat"
        return "unclassified_2026_2027"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create version-pinned Hugging Face metadata manifest, without downloading files")
    parser.add_argument("--repo-id", default=REPO_ID)
    parser.add_argument("--revision", default="main", help="Branch/tag/commit; metadata listing is pinned to resolved commit")
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()

    try:
        from huggingface_hub import HfApi
        from huggingface_hub.hf_api import RepoFile
    except ImportError:
        print('Missing package. Run: .\\.venv_v2\\Scripts\\python.exe -m pip install huggingface_hub', file=sys.stderr)
        return 2

    # A private repository can be accessed with HF_TOKEN in the environment;
    # credentials are never written to the report.
    api = HfApi(token=os.getenv("HF_TOKEN") or None)
    print(f"Connecting to Hugging Face dataset: {args.repo_id}", flush=True)
    try:
        info = api.repo_info(repo_id=args.repo_id, repo_type="dataset", revision=args.revision)
        commit = str(info.sha)
        print(f"Pinned commit: {commit}", flush=True)
        tree = api.list_repo_tree(repo_id=args.repo_id, repo_type="dataset", revision=commit, recursive=True)
        records = []
        for item in tree:
            if not isinstance(item, RepoFile):
                continue
            path = item.path
            group = path.split("/", 1)[0] if "/" in path else "<root>"
            lfs = getattr(item, "lfs", None) or {}
            if not isinstance(lfs, dict):
                lfs = {}
            records.append({
                "relative_path": path,
                "format": detect_format(path),
                "size_bytes": getattr(item, "size", None),
                "source_hint": source_from_path(path),
                "season_hint": season_from_path(path) or "",
                "league_hint": "EPL (repository scope; not row-verified)",
                "group": group,
                "lfs_sha256": lfs.get("sha256", ""),
                "git_blob_id": getattr(item, "blob_id", "") or "",
                "hf_revision": commit,
            })
    except Exception as exc:
        print(f"HF inventory failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        print("No existing report was overwritten. Check network, repo ID, revision, or permissions.", file=sys.stderr)
        return 1

    records.sort(key=lambda record: record["relative_path"])
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "repo_id": args.repo_id,
        "repo_type": "dataset",
        "requested_revision": args.revision,
        "resolved_commit": commit,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(records),
        "total_size_bytes_metadata": sum(r["size_bytes"] or 0 for r in records),
        "group_counts": dict(sorted(Counter(r["group"] for r in records).items())),
        "format_counts": dict(sorted(Counter(r["format"] for r in records).items())),
        "source_hint_counts": dict(sorted(Counter(r["source_hint"] for r in records).items())),
        "unclassified_paths": [r["relative_path"] for r in records if r["source_hint"].startswith(("unknown", "unclassified"))],
        "notes": [
            "Manifest only: file bodies are NOT downloaded and SHA-256 is NOT computed locally.",
            "LFS SHA-256 is supplied by HF only when available; Git blob IDs are not SHA-256 of local file bytes.",
            "Source/season/league are hints inferred from paths or project scope; verify before storing as authoritative metadata.",
        ],
        "files": records,
    }
    json_path = output_dir / "hf_manifest_v2.json"
    csv_path = output_dir / "hf_manifest_v2.csv"
    json_tmp = json_path.with_suffix(".json.tmp")
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    json_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    os.replace(json_tmp, json_path)
    os.replace(csv_tmp, csv_path)

    print("\n=== HF MANIFEST V2 SUMMARY ===")
    print("Repo:", args.repo_id)
    print("Commit:", commit)
    print("Total files:", len(records))
    print("Groups:", manifest["group_counts"])
    print("Formats:", manifest["format_counts"])
    print("Sources (path hints):", manifest["source_hint_counts"])
    print("Unclassified paths:", len(manifest["unclassified_paths"]))
    for path in manifest["unclassified_paths"][:12]:
        print("  -", path)
    print("JSON manifest:", json_path)
    print("CSV catalog:", csv_path)
    print("No source content downloaded; no PostgreSQL changes made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
