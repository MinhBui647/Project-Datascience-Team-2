#!/usr/bin/env python3
"""M3.2 read-only parser smoke test: synthetic formats + archived FPL pilot.

Run from project root:
    python tests/m3_2_parser_smoke.py
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from raw_parser import ArchiveMember, ParseError, ParsedRecord, iter_parse_events

RECEIPT = ROOT / "reports" / "m3_1_archive_pilot_receipt.json"
EXPECTED_REPO = "Toibinguz/epl-premier-league-dataset"
EXPECTED_COMMIT = "4c6bec3a2d89f36496f40c854ba8de374a7b2b36"
EXPECTED_RELATIVE_PATH = "2026_2027_new/fpl_fixtures_latest.json"
EXPECTED_PILOT_SHA256 = "2e156340aada4b71aaa96c0946ba15ee62c87a1975e9f423f7f8400015e6031e"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS |", message)


def reject(fn, expected_type=ParseError) -> None:
    try:
        fn()
    except expected_type:
        return
    raise AssertionError(f"Expected {expected_type.__name__} not raised")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def synthetic_checks(temp: Path) -> None:
    csv_path = temp / "rows.csv"
    csv_path.write_text('team,score\n"One, United",2\nTwo,1\n', encoding="utf-8")
    rows = list(iter_parse_events(csv_path, "csv"))
    check(len(rows) == 2 and rows[0].record_locator == "row:1" and
          rows[0].raw_data == {"team": "One, United", "score": "2"}, "CSV rows and locators")

    compressed = temp / "rows.csv.gz"
    with gzip.open(compressed, "wb") as handle:
        handle.write(csv_path.read_bytes())
    rows_gz = list(iter_parse_events(compressed, "csv.gz"))
    check(rows == rows_gz, "CSV.GZ matches CSV records")

    csv_path.write_text("team,team\na,b\n", encoding="utf-8")
    reject(lambda: list(iter_parse_events(csv_path, "csv")))
    check(True, "Duplicate CSV headers rejected")

    path_json = temp / "items.json"
    path_json.write_text('[{"id":1}, 5, null]', encoding="utf-8")
    items = list(iter_parse_events(path_json, "json"))
    check(len(items) == 3 and items[0].record_locator == "item:0" and
          items[1].raw_data == {"_raw_value": 5} and
          items[2].raw_data == {"_raw_value": None}, "JSON array items and scalar wrappers")

    path_json.write_text('{"events":[1,2],"teams":{"x":1}}', encoding="utf-8")
    obj = list(iter_parse_events(path_json, "json"))
    check(len(obj) == 1 and obj[0].record_kind == "json_object" and
          obj[0].record_locator == "root", "Nested JSON object is preserved as one record")

    path_json.write_text('{"id":1,"id":2}', encoding="utf-8")
    reject(lambda: list(iter_parse_events(path_json, "json")))
    check(True, "Duplicate JSON object keys rejected")

    tar_path = temp / "tiny.tar.gz"
    content = b'{"rosters":{},"shots":{}}'
    with tarfile.open(tar_path, "w:gz") as tar:
        for name, data in (("cache_matches/123.json", content), ("README.txt", b"hello")):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    events = list(iter_parse_events(tar_path, "tar.gz"))
    members = [v for v in events if isinstance(v, ArchiveMember)]
    records = [v for v in events if isinstance(v, ParsedRecord)]
    check(len(members) == 2 and len(records) == 1 and
          members[0].member_sha256 == hashlib.sha256(content).hexdigest() and
          members[1].parse_state == "skipped" and
          records[0].record_kind == "tar_json" and
          records[0].record_locator == "member:0:root", "TAR.GZ members, checksum, skip policy")

    bad_tar = temp / "unsafe.tar.gz"
    with tarfile.open(bad_tar, "w:gz") as tar:
        item = tarfile.TarInfo("../evil.json")
        item.size = len(content)
        tar.addfile(item, io.BytesIO(content))
    reject(lambda: list(iter_parse_events(bad_tar, "tar.gz")))
    check(True, "Unsafe TAR traversal rejected")


def check_real_pilot() -> None:
    if not RECEIPT.is_file():
        raise RuntimeError(f"Missing M3.1 pilot receipt: {RECEIPT}")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8-sig"))
    if (receipt.get("repo_id") != EXPECTED_REPO or
            receipt.get("pinned_commit") != EXPECTED_COMMIT or
            receipt.get("relative_path") != EXPECTED_RELATIVE_PATH or
            receipt.get("sha256") != EXPECTED_PILOT_SHA256 or
            receipt.get("format") != "json"):
        raise RuntimeError("M3.1 receipt does not match the verified pinned pilot")
    relative_path = receipt.get("archive_path", "")
    if not relative_path or not isinstance(relative_path, str):
        raise RuntimeError("M3.1 receipt missing archive_path")
    archive = (ROOT / relative_path).resolve()
    archive_root = (ROOT / "raw-archive-v2" / "objects").resolve()
    if not archive.is_relative_to(archive_root):
        raise RuntimeError("Receipt archive path is outside V2 immutable archive")
    if sha256(archive) != EXPECTED_PILOT_SHA256:
        raise RuntimeError("M3.1 archived bytes do not match expected SHA-256")
    check(True, "M3.1 archive receipt + SHA-256 verified")
    n = 0
    first_id = None
    kinds = Counter()
    for record in iter_parse_events(archive, "json"):
        if not isinstance(record, ParsedRecord):
            raise AssertionError("Unexpected non-record event in plain JSON")
        if first_id is None:
            first_id = record.raw_data.get("id")
        if record.record_locator != f"item:{n}":
            raise AssertionError(f"Incorrect locator for fixture item #{n}")
        kinds[record.record_kind] += 1
        n += 1
    check(n == 380 and kinds == {"json_item": 380}, "Real FPL fixtures: 380 JSON records")
    print("Sample first fixture ID:", first_id)
    print("Parsers tested: csv, csv.gz, json, tar.gz")
    print("Parsed records:", n)
    print("PostgreSQL writes: 0 | V1 writes: 0")


def main() -> None:
    print("=== M3.2 | RAW PARSER ENGINE SMOKE TEST ===")
    with tempfile.TemporaryDirectory() as directory:
        synthetic_checks(Path(directory))
    check_real_pilot()
    print("M3.2 RESULT: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"M3.2 RESULT: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        sys.exit(1)
