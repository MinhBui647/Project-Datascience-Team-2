#!/usr/bin/env python3
r"""M3.4H.4 regression; read-only. Run from raw_storage root.

.\.venv_v2\Scripts\python.exe tests\m3_4h_trailing_empty_regression_v2.py
"""
from __future__ import annotations

import csv
import gzip
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from raw_parser import ParseError, iter_parse_events  # noqa: E402


def make_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def require_fail(path: Path, fmt: str, expected: str) -> None:
    try:
        list(iter_parse_events(path, fmt))
    except ParseError as exc:
        assert expected in str(exc), (expected, str(exc))
    else:
        raise AssertionError(f"Expected ParseError: {expected}")


def main() -> None:
    print("=== M3.4H.4 | TRAILING EMPTY CSV REGRESSION ===")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.csv"
        header = [f"col_{i}" for i in range(57)]
        base = [f"v_{i}" for i in range(57)]
        rows = [base, base + [""] * 5, base + [""] * 15]
        make_csv(path, header, rows)
        result = list(iter_parse_events(path, "csv"))
        assert len(result) == 3
        assert [v.record_locator for v in result] == ["row:1", "row:2", "row:3"]
        assert all(len(v.raw_data) == 57 for v in result)
        assert all(v.raw_data == dict(zip(header, base)) for v in result)
        print("PASS | 57/62/72 cells: empty trailing cells accepted; 57 values kept")

        rows = [base + ["", " \t", ""]]
        make_csv(path, header, rows)
        assert list(iter_parse_events(path, "csv"))[0].raw_data == dict(zip(header, base))
        print("PASS | whitespace-only trailing cells accepted")

        rows = [base + ["", "unexpected", ""]]
        make_csv(path, header, rows)
        require_fail(path, "csv", "non-empty cells beyond header")
        print("PASS | non-empty extra values rejected (no silent data loss)")

        rows = [base[:-1]]
        make_csv(path, header, rows)
        require_fail(path, "csv", "cells !=")
        print("PASS | short rows rejected")

        make_csv(path, ["name", "", ""], [["Alice", "x", "y", "", ""]])
        parsed = list(iter_parse_events(path, "csv"))
        assert parsed[0].raw_data == {
            "name": "Alice", "__unnamed_csv_col_001": "x", "__unnamed_csv_col_002": "y"
        }
        print("PASS | unnamed-header mapping and trailing empty policy coexist")

        zipped = Path(folder) / "sample.csv.gz"
        with gzip.open(zipped, "wb") as fh:
            fh.write(path.read_bytes())
        assert list(iter_parse_events(zipped, "csv.gz")) == parsed
        print("PASS | CSV.GZ shares identical row handling")

        make_csv(path, ["a", "a"], [["1", "2"]])
        require_fail(path, "csv", "duplicate header")
        print("PASS | duplicate named headers still rejected")

        make_csv(path, ["a", "b"], [["1", "2"]])
        assert list(iter_parse_events(path, "csv"))[0].raw_data == {"a": "1", "b": "2"}
        print("PASS | normal CSV unchanged")

    try:
        from huggingface_hub import hf_hub_download
        real = Path(hf_hub_download(
            repo_id="Toibinguz/epl-premier-league-dataset",
            repo_type="dataset",
            filename="raw_data/football_data_co_uk/football_data_raw_0304.csv",
            revision="4c6bec3a2d89f36496f40c854ba8de374a7b2b36",
            local_files_only=True,
        ))
    except Exception as exc:
        print(f"FAIL | actual 0304 CSV unavailable in local HF cache: {type(exc).__name__}: {exc}")
        raise AssertionError("Real 2003-2004 CSV is required for this checkpoint") from exc

    widths: Counter[int] = Counter()
    with real.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, strict=True)
        header = next(reader)
        assert len(header) == 57, f"Unexpected source header width: {len(header)}"
        for index, row in enumerate(reader, start=1):
            widths[len(row)] += 1
            assert len(row) >= len(header), (index, len(row))
            assert all(not cell.strip() for cell in row[len(header):]), index
    assert widths == Counter({57: 335, 62: 13, 72: 32}), widths
    n = 0
    with real.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, strict=True)
        next(reader)
        for index, (row, event) in enumerate(zip(reader, iter_parse_events(real, "csv"), strict=True), start=1):
            assert event.record_locator == f"row:{index}"
            assert len(event.raw_data) == 57
            assert list(event.raw_data.values()) == row[:57], index
            n += 1
    assert n == 380, n
    print("PASS | actual pinned 0304 CSV: 380 rows, 45 trailing-empty rows, all 57 values preserved")
    print("M3.4H.4 TRAILING EMPTY REGRESSION: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"M3.4H.4 TRAILING EMPTY REGRESSION: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
