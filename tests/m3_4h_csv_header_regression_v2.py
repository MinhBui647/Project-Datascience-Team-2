#!/usr/bin/env python3
r"""M3.4H: read-only regression of CSV empty headers, no DB, no HF downloads.

Run from raw_storage project root:
    .\.venv_v2\Scripts\python.exe tests\m3_4h_csv_header_regression_v2.py
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from raw_parser import ParseError, iter_parse_events, _csv_jsonb_headers


def must_fail(path: Path, fmt: str, expected: str):
    try:
        list(iter_parse_events(path, fmt))
    except ParseError as exc:
        assert expected in str(exc), (expected, str(exc))
    else:
        raise AssertionError(f"Expected ParseError ({expected})")


def main():
    print("=== M3.4H | CSV HEADER REGRESSION ===")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = folder / "sample.csv"
        headers = [f"field_{i}" for i in range(48)] + [""] * 5
        record = [f"value_{i}" for i in range(53)]
        # Preserve nonempty values even in originally unnamed columns.
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerow(record)
        parsed = list(iter_parse_events(path, "csv"))
        assert len(parsed) == 1
        assert parsed[0].record_locator == "row:1"
        assert len(parsed[0].raw_data) == 53
        for i in range(48):
            assert parsed[0].raw_data[f"field_{i}"] == f"value_{i}"
        for i in range(48, 53):
            assert parsed[0].raw_data[f"__unnamed_csv_col_{i:03d}"] == f"value_{i}"
        print("PASS | 53 columns, five unnamed trailing columns, no values lost")

        zipped = folder / "sample.csv.gz"
        with gzip.open(zipped, "wb") as f:
            f.write(path.read_bytes())
        assert list(iter_parse_events(zipped, "csv.gz")) == parsed
        print("PASS | CSV.GZ follows identical header policy")

        path.write_text("team,team\nA,B\n", encoding="utf8")
        must_fail(path, "csv", "duplicate header")
        print("PASS | duplicate named headers still rejected")

        path.write_text("team,score\nA,2,unexpected\n", encoding="utf8")
        must_fail(path, "csv", "cells !=")
        print("PASS | row/header width mismatch still rejected")

        path.write_text("a,b\nA,B\n", encoding="utf8")
        result = list(iter_parse_events(path, "csv"))
        assert result[0].raw_data == {"a": "A", "b": "B"}
        print("PASS | normal CSV unchanged")

        names = _csv_jsonb_headers(["name", "__unnamed_csv_col_001", "", ""])
        assert len(names) == len(set(names)) == 4
        print("PASS | generated names cannot collide with real headers")

        path.write_text(",,\n1,2,3\n", encoding="utf8")
        must_fail(path, "csv", "no usable header")
        print("PASS | totally unnamed CSV rejected")

    # Real user fixture was already diagnosed at 53 columns, indexes 48..52;
    # test it from local HF cache if present (NO downloading).
    try:
        from huggingface_hub import hf_hub_download
        real = hf_hub_download(
            repo_id="Toibinguz/epl-premier-league-dataset",
            repo_type="dataset",
            filename="raw_data/football_data_co_uk/football_data_raw_0203.csv",
            revision="4c6bec3a2d89f36496f40c854ba8de374a7b2b36",
            local_files_only=True,
        )
    except Exception as exc:
        print(f"SKIP | actual 0203 CSV not in local HF cache ({type(exc).__name__})")
    else:
        with open(real, "r", encoding="utf-8-sig", newline="") as f:
            original = next(csv.reader(f))
        assert len(original) == 53 and [i for i, col in enumerate(original) if not col.strip()] == list(range(48, 53))
        count = 0
        for ev in iter_parse_events(real, "csv"):
            assert len(ev.raw_data) == 53
            count += 1
        assert count > 0
        print(f"PASS | actual pinned 0203 CSV fully parsed: {count} rows; 53 keys/row")
    print("M3.4H CSV REGRESSION: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"M3.4H CSV REGRESSION: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
