#!/usr/bin/env python3
r"""M3.4H.6: verify strict UTF8 -> CP1252 CSV parsing; optional all-27 source audit.

Run from raw_storage root:
  .\.venv_v2\Scripts\python.exe tests\m3_4h_encoding_audit_v2.py --audit-football-data

No PostgreSQL connections/writes. Audit uses pinned HF downloads and saves a JSON report.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from raw_parser import ParseError, detect_csv_encoding, iter_parse_events  # noqa: E402

REVISION = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
REPO = "Toibinguz/epl-premier-league-dataset"
PINNED_0405 = (ROOT / "raw-archive-v2" / "objects" / "85" /
    "85241d09c572dfdf0f03817ceebd9a07d113fd45b7dead1ab6e5a329fd920b2c")


def fail(path: Path, fmt: str, needle: str) -> None:
    try:
        list(iter_parse_events(path, fmt))
    except ParseError as exc:
        assert needle in str(exc), (needle, str(exc))
    else:
        raise AssertionError(f"Expected ParseError: {needle}")


def regression() -> None:
    print("=== M3.4H.6 | ENCODING REGRESSION ===")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sample.csv"
        p.write_bytes(b"id,name\n1,Caf\xc3\xa9\n")
        assert detect_csv_encoding(p, "csv") == "utf-8-sig"
        assert list(iter_parse_events(p, "csv"))[0].raw_data == {"id": "1", "name": "Café"}
        print("PASS | strict UTF-8 remains preferred")

        p.write_bytes(b"id,name\n1,Alice\n2,Fran\xe7ois\n")
        assert detect_csv_encoding(p, "csv") == "cp1252"
        assert [x.raw_data["name"] for x in iter_parse_events(p, "csv")] == ["Alice", "François"]
        print("PASS | strict CP1252 fallback retains non-UTF8 characters")

        p.write_bytes(b"\xef\xbb\xbfA,,C\n1,2,3,,\n")
        assert detect_csv_encoding(p, "csv") == "utf-8-sig"
        assert list(iter_parse_events(p, "csv"))[0].raw_data == {
            "A": "1", "__unnamed_csv_col_001": "2", "C": "3"
        }
        print("PASS | UTF-8 BOM, empty header and empty trailing cells coexist")

        p.write_bytes(b"a,b\n1,2\n3,4\n5,\x81\n")
        fail(p, "csv", "invalid under UTF-8 and CP1252")
        print("PASS | undefined CP1252 bytes rejected (no lossy replacement)")

        p.write_bytes(b"a,b\n1,2\n3,\xe9,unexpected\n")
        fail(p, "csv", "non-empty cells beyond header")
        print("PASS | non-empty extra cells still rejected after fallback")

        p.write_bytes(b"a,b\n1,2\n3,\xe9,,\n")
        gz = Path(tmp) / "sample.csv.gz"
        with gzip.open(gz, "wb") as handle:
            handle.write(p.read_bytes())
        assert detect_csv_encoding(gz, "csv.gz") == "cp1252"
        assert list(iter_parse_events(gz, "csv.gz")) == list(iter_parse_events(p, "csv"))
        print("PASS | CSV.GZ supports identical strict fallback")

        p.write_bytes(b"a,b\n1,2\n3,4\n5,\xe9\n")
        it = iter_parse_events(p, "csv")
        assert next(it).raw_data == {"a": "1", "b": "2"}
        assert [v.raw_data for v in it] == [
            {"a": "3", "b": "4"}, {"a": "5", "b": "é"}
        ]
        print("PASS | late non-UTF8 bytes selected before first emitted record")

    assert PINNED_0405.is_file(), (
        "Missing pinned immutable 0405 archive (expected from failed ingestion): " + str(PINNED_0405)
    )
    content = PINNED_0405.read_bytes()
    assert hashlib.sha256(content).hexdigest() == PINNED_0405.name, "Immutable archive SHA256 mismatch"
    assert len(content) == 85894, len(content)
    assert detect_csv_encoding(PINNED_0405, "csv") == "cp1252"
    with io.StringIO(content.decode("cp1252"), newline="") as stream:
        reader = csv.reader(stream, strict=True)
        header = next(reader)
        assert len(header) == 57
        original = list(reader)
    assert len(original) == 380
    assert sum(len(r) == 57 for r in original) == 335
    assert sum(len(r) == 62 for r in original) == 45
    parsed = list(iter_parse_events(PINNED_0405, "csv"))
    assert len(parsed) == 380
    for index, (row, evt) in enumerate(zip(original, parsed, strict=True), start=1):
        assert evt.record_locator == f"row:{index}"
        assert list(evt.raw_data.values()) == row[:57], index
        assert all(not v.strip() for v in row[57:]), index
    print("PASS | pinned immutable 0405 CSV: SHA256 valid; 380 rows, 45 empty-tail rows, all 57 fields retained")
    print("M3.4H.6 ENCODING REGRESSION: PASS")


def audit_all() -> None:
    from huggingface_hub import hf_hub_download
    catalog_path = ROOT / "reports" / "raw_scope_catalog_v2.csv"
    with catalog_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    selection = sorted(
        (r for r in rows if r["relative_path"].startswith("raw_data/football_data_co_uk/")
         and r["format"] == "csv"),
        key=lambda r: r["relative_path"]
    )
    assert len(selection) == 27, f"Expected exactly 27 pinned Football-Data CSVs: {len(selection)}"
    assert all(r["hf_revision"] == REVISION for r in selection)
    print("=== M3.4H.6 | PINNED FOOTBALL-DATA AUDIT (27 FILES) ===", flush=True)
    report = []
    for i, entry in enumerate(selection, start=1):
        name = entry["relative_path"]
        try:
            local = Path(hf_hub_download(
                repo_id=REPO, repo_type="dataset", revision=REVISION,
                filename=name,
            ))
            byte_size = local.stat().st_size
            if byte_size != int(entry["size_bytes"]):
                raise ValueError(f"size mismatch: {byte_size} vs catalog {entry['size_bytes']}")
            git_blob_sha = entry.get("git_blob_id", "")
            if git_blob_sha:
                raw_bytes = local.read_bytes()  # source files are ~100 KB; safe
                check = hashlib.sha1(f"blob {len(raw_bytes)}\0".encode() + raw_bytes).hexdigest()
                if check != git_blob_sha:
                    raise ValueError("source Git blob SHA1 mismatch")
            encoding = detect_csv_encoding(local, "csv")
            count = sum(1 for _ in iter_parse_events(local, "csv"))
            if count == 0:
                raise ParseError("0 records; inspect source")
            report.append({"path": name, "status": "PASS", "encoding": encoding,
                           "record_count": count})
            print(f"[{i:02d}/27] PASS | {encoding:9s} | {count:5d} rows | {name}", flush=True)
        except Exception as exc:
            report.append({"path": name, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{i:02d}/27] FAIL | {name} | {type(exc).__name__}: {exc}", flush=True)
    destination = ROOT / "reports" / "m3_4h_encoding_audit_v2.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"repo": REPO, "revision": REVISION,
                                      "files": report}, indent=2, ensure_ascii=False), encoding="utf-8")
    success = sum(x["status"] == "PASS" for x in report)
    fallback = sum(x.get("encoding") == "cp1252" for x in report)
    print(f"AUDIT SUMMARY: {success}/27 PASS | {27-success} FAIL | CP1252 FALLBACK: {fallback}")
    print("Report:", destination.relative_to(ROOT).as_posix())
    if success < 27:
        print("M3.4H.6 FOOTBALL-DATA AUDIT: REVIEW_REQUIRED")
        raise SystemExit(1)
    print("M3.4H.6 FOOTBALL-DATA AUDIT: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-football-data", action="store_true",
                        help="Download all 27 pinned source CSVs to HF cache and preflight them without PostgreSQL writes")
    args = parser.parse_args()
    regression()
    if args.audit_football_data:
        audit_all()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"M3.4H.6: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)
