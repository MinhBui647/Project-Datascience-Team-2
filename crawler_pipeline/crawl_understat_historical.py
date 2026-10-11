"""
crawler_pipeline/crawl_understat_historical.py
Downloads granular match-level data (shots with coordinates x,y and rosters with xGChain, xGBuildup)
for all Premier League matches (2014/15 to 2026/27) from Understat.

Features:
- Fetches from Understat official JSON endpoint: https://understat.com/getMatchData/{match_id}
- Stores raw match payloads into raw_data/understat/cache_matches/{match_id}.json
- Resumable: skips already downloaded matches
- Stop flag: create crawler_pipeline/stop_understat.flag to stop safely anytime
- Live logging to crawler_pipeline/understat_crawler.log and console
"""

import os
import sys
import glob
import json
import time
import gzip
import datetime
import urllib.request
import urllib.error
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
UNDERSTAT_RAW_DIR = ROOT_DIR / "raw_data" / "understat"
CACHE_DIR = UNDERSTAT_RAW_DIR / "cache_matches"
LOG_FILE = ROOT_DIR / "crawler_pipeline" / "understat_crawler.log"
STOP_FLAG = ROOT_DIR / "crawler_pipeline" / "stop_understat.flag"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://understat.com/",
}


def log_message(msg: str) -> None:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{now_str}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")


def collect_target_matches():
    raw_files = sorted(glob.glob(str(UNDERSTAT_RAW_DIR / "understat_raw_*.json")))
    all_matches = []
    for f in raw_files:
        season_name = Path(f).stem.replace("understat_raw_", "")
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            dates = data.get("dates", [])
            for m in dates:
                if m.get("isResult") is True and "id" in m:
                    h_title = m.get("h", {}).get("title", "Home")
                    a_title = m.get("a", {}).get("title", "Away")
                    h_goals = m.get("goals", {}).get("h", "0")
                    a_goals = m.get("goals", {}).get("a", "0")
                    all_matches.append({
                        "id": str(m["id"]),
                        "season": season_name,
                        "date": m.get("datetime", ""),
                        "match_str": f"{h_title} {h_goals} - {a_goals} {a_title}",
                    })
        except Exception as e:
            log_message(f"[!] Error reading {f}: {e}")
    return all_matches


def fetch_match_data(match_id: str, max_retries: int = 3):
    url = f"https://understat.com/getMatchData/{match_id}"
    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_bytes = resp.read()
                if raw_bytes[:2] == b"\x1f\x8b":
                    raw_bytes = gzip.decompress(raw_bytes)
                text = raw_bytes.decode("utf-8")
                data = json.loads(text)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_sec = attempt * 5
                log_message(f"    [429 Rate Limit] Match {match_id}: backing off for {wait_sec}s (attempt {attempt}/{max_retries})...")
                time.sleep(wait_sec)
            elif e.code in (500, 502, 503, 504):
                time.sleep(attempt * 2)
            else:
                log_message(f"    [HTTP {e.code}] Match {match_id} error: {e}")
                return None
        except Exception as e:
            if attempt == max_retries:
                log_message(f"    [Fetch Error] Match {match_id}: {e}")
            time.sleep(2)
    return None


def run_crawler():
    if STOP_FLAG.exists():
        try:
            STOP_FLAG.unlink()
        except Exception:
            pass

    log_message("=" * 80)
    log_message("STARTING UNDERSTAT HISTORICAL MATCH DETAILS CRAWLER")
    log_message(f"Target Cache Directory: {CACHE_DIR}")
    log_message(f"Log File: {LOG_FILE}")
    log_message(f"Stop Flag: {STOP_FLAG} (Create this file to stop safely)")
    log_message("=" * 80)

    matches = collect_target_matches()
    total = len(matches)
    log_message(f"[+] Total completed matches identified: {total}")

    # Determine remaining
    pending = []
    for m in matches:
        target_json = CACHE_DIR / f"{m['id']}.json"
        if not target_json.exists() or target_json.stat().st_size < 100:
            pending.append(m)

    already_done = total - len(pending)
    log_message(f"[+] Matches already cached: {already_done} | Remaining to fetch: {len(pending)}")

    if not pending:
        log_message("[+] All matches are already cached! Done.")
        return

    start_time = time.time()
    downloaded_in_run = 0

    for idx, m in enumerate(pending, 1):
        if STOP_FLAG.exists():
            log_message("[!] STOP FLAG DETECTED. Halting crawler gracefully...")
            STOP_FLAG.unlink(missing_ok=True)
            break

        m_id = m["id"]
        target_json = CACHE_DIR / f"{m_id}.json"

        data = fetch_match_data(m_id)
        if data:
            with open(target_json, "w", encoding="utf-8") as fp:
                json.dump(data, fp)

            size_kb = target_json.stat().st_size / 1024
            downloaded_in_run += 1
            elapsed = time.time() - start_time
            rate = downloaded_in_run / elapsed if elapsed > 0 else 1
            rem_sec = (len(pending) - idx) / rate if rate > 0 else 0
            eta_str = str(datetime.timedelta(seconds=int(rem_sec)))

            overall_done = already_done + idx
            pct = (overall_done / total) * 100

            log_message(
                f"[{overall_done:5d}/{total}] ({pct:5.1f}%) | {m['season']} | ID: {m_id:6s} | {m['match_str'][:35]:35s} | {size_kb:5.1f} KB | ETA: {eta_str}"
            )
        else:
            log_message(f"[!] FAILED: ID {m_id} ({m['match_str']})")

        # Polite delay to prevent rate limiting
        time.sleep(0.35)

    log_message("=" * 80)
    log_message(f"UNDERSTAT CRAWLER FINISHED. Newly downloaded: {downloaded_in_run} matches.")
    log_message("=" * 80)


if __name__ == "__main__":
    run_crawler()

