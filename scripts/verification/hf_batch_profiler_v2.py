r"""EPL Raw Storage V2: scoped, revision-pinned HF batch profiling.

Usage from repository root (PowerShell):
    .\.venv_v2\Scripts\python.exe scripts\tools\hf_batch_profiler_v2.py

Inputs:
    reports/hf_manifest_v2.json (created with hf_manifest_v2.py)

Outputs:
    reports/raw_scope_catalog_v2.csv        ALL raw-scope files, metadata + optional sample notes
    reports/raw_profile_v2.json              summary, samples and exceptions

Safety:
    - Read-only on the dataset repository and PostgreSQL.
    - Download only a small representative subset to the huggingface_hub cache.
    - Pinned to the exact commit from the manifest, never moving 'main'.
    - Never extracts tar files, and never touches raw-archive/raw-data.
    - Sample-based schema inference is not proof that every file has the same structure.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import tarfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)

RAW_PREFIXES = ("raw_data/", "2026_2027_new/")
FIELDS = [
    "relative_path", "group", "source_hint", "format", "season_hint", "size_bytes",
    "hf_revision", "lfs_sha256", "git_blob_id", "inspection_status", "structure_kind",
    "field_count", "sample_keys", "sample_rows", "inspection_note",
]


def is_raw_file(p: str) -> bool:
    return isinstance(p, str) and p.startswith(RAW_PREFIXES)


def source_key(rec: dict) -> str:
    source = (rec.get("source_hint") or "unknown").strip()
    if source.startswith("unclassified"):
        return "unknown"
    return source


def fmt_from_name(name: str) -> str:
    v = name.lower()
    for ext in (".jsonl.gz", ".csv.gz", ".tar.gz", ".jsonl", ".ndjson", ".json", ".csv"):
        if v.endswith(ext):
            return ext[1:]
    return Path(v).suffix.lstrip(".") or "other"


def pick_representatives(rows: list[dict], per_family: int, max_samples: int) -> list[str]:
    groups = defaultdict(list)
    for rec in rows:
        key = (rec["group"], source_key(rec), rec.get("format") or fmt_from_name(rec["relative_path"]))
        groups[key].append(rec)
    selected = []
    # Cover both historical and new data, and every format/source combination.
    for key in sorted(groups):
        candidates = sorted(groups[key], key=lambda r: (r.get("size_bytes") is None, r.get("size_bytes") or 0, r["relative_path"]))
        selected.extend(x["relative_path"] for x in candidates[:per_family])
    # Prefer at least one archive where possible (will still respect size cap).
    selected = sorted(set(selected), key=lambda p: (not p.endswith(".tar.gz"), p))
    return selected[:max_samples]


def short_keys(record: object, limit: int = 80) -> list[str]:
    return [str(k) for k in list(record.keys())[:limit]] if isinstance(record, dict) else []


def csv_profile(path: Path, compressed: bool = False) -> dict:
    def get_reader(enc: str):
        if compressed:
            return gzip.open(path, "rt", encoding=enc, errors="strict", newline="")
        return path.open("r", encoding=enc, errors="strict", newline="")
    last_error = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with get_reader(encoding) as f:
                reader = csv.reader(f)
                header = next(reader, [])
                sample = []
                for _ in range(3):
                    try:
                        sample.append(next(reader))
                    except StopIteration:
                        break
            return {
                "structure_kind": "csv_table", "field_count": len(header),
                "sample_keys": header[:80], "sample_rows": len(sample),
                "sample_record_preview": [r[:12] for r in sample],
                "encoding": encoding, "inspection_note": "Rows sampled only; full row count not computed",
            }
        except (UnicodeError, csv.Error) as exc:
            last_error = exc
    raise ValueError(f"Cannot read CSV: {last_error}")


def json_profile(path: Path, jsonl: bool = False, compressed: bool = False) -> dict:
    opener = gzip.open if compressed else open
    with opener(path, "rt", encoding="utf-8-sig") as f:
        if jsonl:
            sample = []
            for line in f:
                if line.strip():
                    sample.append(json.loads(line))
                if len(sample) == 3:
                    break
            first = sample[0] if sample else None
            return {
                "structure_kind": "jsonl_records", "field_count": len(first) if isinstance(first, dict) else None,
                "sample_keys": short_keys(first), "sample_rows": len(sample),
                "sample_record_types": [type(v).__name__ for v in sample],
                "inspection_note": "JSONL prefix sampled; total rows not counted",
            }
        obj = json.load(f)
    if isinstance(obj, dict):
        nested = {}
        for k, v in list(obj.items())[:60]:
            nested[k] = {"kind": type(v).__name__, "length": len(v) if isinstance(v, (list, dict)) else None}
        return {"structure_kind": "json_object", "field_count": len(obj),
                "sample_keys": short_keys(obj), "sample_rows": 1, "nested_shapes": nested,
                "inspection_note": "Full JSON object parsed; nested values only summarized"}
    if isinstance(obj, list):
        first = obj[0] if obj else None
        return {"structure_kind": "json_array", "field_count": len(first) if isinstance(first, dict) else None,
                "sample_keys": short_keys(first), "sample_rows": min(len(obj), 3), "array_length": len(obj),
                "inspection_note": "JSON array parsed; first item's keys sampled"}
    return {"structure_kind": f"json_scalar_{type(obj).__name__}", "field_count": None,
            "sample_keys": [], "sample_rows": 1, "inspection_note": "JSON scalar, needs object wrapper in V1 schema"}


def tar_profile(path: Path) -> dict:
    # Avoid extractall/extract: inventory names only.
    count = 0
    suffixes = Counter()
    member_preview = []
    total_bytes = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            count += 1
            total_bytes += max(member.size, 0)
            suffixes[fmt_from_name(member.name)] += 1
            if len(member_preview) < 12:
                member_preview.append({"path": member.name, "bytes": member.size})
    return {"structure_kind": "tar_gz_archive", "field_count": None,
            "sample_keys": [], "sample_rows": count, "archive_member_count": count,
            "uncompressed_member_bytes": total_bytes,
            "archive_member_formats": dict(suffixes), "archive_members_preview": member_preview,
            "inspection_note": "Archive members listed only; no extraction and no per-member schema inspection"}


def inspect_file(path: Path, fmt: str) -> dict:
    if fmt in ("csv", "csv.gz"):
        return csv_profile(path, compressed=fmt.endswith(".gz"))
    if fmt in ("jsonl", "ndjson", "jsonl.gz"):
        return json_profile(path, jsonl=True, compressed=fmt.endswith(".gz"))
    if fmt == "json":
        return json_profile(path)
    if fmt == "tar.gz":
        return tar_profile(path)
    return {"structure_kind": "unsupported_format", "field_count": None,
            "sample_keys": [], "sample_rows": 0,
            "inspection_note": "Metadata only; no reader for this format"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="reports/hf_manifest_v2.json")
    ap.add_argument("--output-dir", default="reports")
    ap.add_argument("--per-family", type=int, default=2)
    ap.add_argument("--max-samples", type=int, default=36)
    ap.add_argument("--max-file-mb", type=float, default=25.0)
    ap.add_argument("--metadata-only", action="store_true")
    args = ap.parse_args()
    if args.per_family < 1 or args.max_samples < 1 or args.max_file_mb <= 0:
        ap.error("Per-family, max-samples, and max-file-mb must be positive")

    mpath = Path(args.manifest)
    if not mpath.is_file():
        ap.error(f"Manifest not found: {mpath}. Run hf_manifest_v2.py first")
    manifest = json.loads(mpath.read_text(encoding="utf-8-sig"))
    records = manifest.get("files")
    commit = manifest.get("resolved_commit")
    repo_id = manifest.get("repo_id")
    if not isinstance(records, list) or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        ap.error("Invalid manifest structure or unpinned commit")
    if not isinstance(repo_id, str) or not repo_id:
        ap.error("Manifest repo_id missing")
    if any(r.get("hf_revision") != commit for r in records):
        ap.error("Manifest contains inconsistent file revisions")

    raw = [dict(r) for r in records if is_raw_file(r.get("relative_path", ""))]
    excluded = [r for r in records if not is_raw_file(r.get("relative_path", ""))]
    # The manifest from M1.1 includes /clean and repository root files; they are intentionally excluded.
    selected = set() if args.metadata_only else set(pick_representatives(raw, args.per_family, args.max_samples))
    by_path = {r["relative_path"]: r for r in raw}
    attempted = 0
    status_counts = Counter()
    sample_results = {}
    errors = []

    if selected:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            ap.error('huggingface_hub missing; install with .\\.venv_v2\\Scripts\\python.exe -m pip install huggingface_hub')

    for path in sorted(selected):
        r = by_path[path]
        nbytes = r.get("size_bytes")
        if isinstance(nbytes, int) and nbytes > args.max_file_mb * 1024 * 1024:
            sample_results[path] = {"inspection_status": "skipped_size", "inspection_note": f"File metadata > {args.max_file_mb:g} MB"}
            status_counts["skipped_size"] += 1
            continue
        attempted += 1
        try:
            # Remote request locked to one exact commit, never to moving HEAD.
            local = Path(hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=path,
                revision=commit,
                token=os.getenv("HF_TOKEN") or None,
            ))
            p = inspect_file(local, r.get("format") or fmt_from_name(path))
            p["inspection_status"] = "sampled"
            sample_results[path] = p
            status_counts["sampled"] += 1
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc)[:240]}"
            sample_results[path] = {"inspection_status": "error", "inspection_note": detail}
            errors.append({"relative_path": path, "message": detail})
            status_counts["error"] += 1

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    catalog_path = outdir / "raw_scope_catalog_v2.csv"
    report_path = outdir / "raw_profile_v2.json"
    rows = []
    for r in raw:
        p = sample_results.get(r["relative_path"], {})
        rows.append({
            "relative_path": r["relative_path"],
            "group": r.get("group") or r["relative_path"].split("/")[0],
            "source_hint": r.get("source_hint", ""),
            "format": r.get("format", ""),
            "season_hint": r.get("season_hint", ""),
            "size_bytes": r.get("size_bytes", ""),
            "hf_revision": commit,
            "lfs_sha256": r.get("lfs_sha256", ""),
            "git_blob_id": r.get("git_blob_id", ""),
            "inspection_status": p.get("inspection_status", "metadata_only"),
            "structure_kind": p.get("structure_kind", ""),
            "field_count": p.get("field_count", ""),
            "sample_keys": json.dumps(p.get("sample_keys", []), ensure_ascii=False),
            "sample_rows": p.get("sample_rows", ""),
            "inspection_note": p.get("inspection_note", ""),
        })
    summary = {
        "repo_id": repo_id,
        "pinned_commit": commit,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_total": len(records),
        "raw_scope_files": len(raw),
        "excluded_files": len(excluded),
        "excluded_by_group": dict(Counter((r.get("group") or r["relative_path"].split("/")[0]) for r in excluded)),
        "raw_by_group": dict(Counter(r["relative_path"].split("/")[0] for r in raw)),
        "raw_by_format": dict(Counter(r.get("format", "other") for r in raw)),
        "raw_by_source_hint": dict(Counter(source_key(r) for r in raw)),
        "raw_total_size_metadata_bytes": sum(r.get("size_bytes") or 0 for r in raw),
        "selected_sample_count": len(selected),
        "attempted_download_count": attempted,
        "sample_status_counts": dict(status_counts),
        "sample_profiles": sample_results,
        "errors": errors,
        "notes": [
            "clean/ and repository root files are excluded from raw ingestion, but retained in the full manifest.",
            "All raw_scope_files are cataloged; only selected representatives are inspected for content structure.",
            "A sampled schema is a hint; not proof every file in its source/format family matches.",
            "No PostgreSQL writes, no raw-data/raw-archive modifications, no TAR extraction.",
        ],
    }
    csv_tmp = catalog_path.with_suffix(".csv.tmp")
    json_tmp = report_path.with_suffix(".json.tmp")
    with csv_tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    json_tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(csv_tmp, catalog_path)
    os.replace(json_tmp, report_path)

    print("=== RAW SCOPE & BATCH PROFILE V2 ===")
    for k in ("pinned_commit", "manifest_total", "raw_scope_files", "excluded_files", "excluded_by_group", "raw_by_group", "raw_by_format", "raw_by_source_hint", "selected_sample_count", "attempted_download_count", "sample_status_counts"):
        print(f"{k}: {summary[k]}")
    print("Catalog:", catalog_path)
    print("Report:", report_path)
    print("Read-only on database and project raw storage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
