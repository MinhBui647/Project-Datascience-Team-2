"""
crawler_pipeline/crawl_fotmob_historical.py
Automated batch crawler for downloading 4,180 historical FotMob matchDetails JSONs (2010/11 to 2020/21).

Features:
- Live logging to stdout and crawler_pipeline/fotmob_crawler.log
- Live stop control: create 'crawler_pipeline/stop_crawler.flag' to stop anytime, or press Ctrl+C
- Automatic resumability: skips already downloaded matches
- Exponential backoff for network safety and rate-limit handling
"""

import os
import sys
import json
import time
import signal
import datetime
from pathlib import Path
import requests

# Base Paths
PIPELINE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PIPELINE_DIR.parent
RAW_SOFA_DIR = ROOT_DIR / "raw_data" / "sofascore"
CACHE_DIR = RAW_SOFA_DIR / "cache_matches"
LOG_FILE = PIPELINE_DIR / "fotmob_crawler.log"
STOP_FLAG_FILE = PIPELINE_DIR / "stop_crawler.flag"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Seasons in order (newest to oldest)
SEASONS = [
    "2020_2021", "2019_2020", "2018_2019", "2017_2018", "2016_2017",
    "2015_2016", "2014_2015", "2013_2014", "2012_2013", "2011_2012", "2010_2011"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fotmob.com/",
}

# Global stop flag
STOP_REQUESTED = False


def sigint_handler(sig, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    log_msg("\n[!] Received interrupt signal (Ctrl+C). Gracefully stopping after current request...")


signal.signal(signal.SIGINT, sigint_handler)


def log_msg(msg: str):
    """Write message to both stdout and log file with immediate flush."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{ts}] {msg}"
    print(formatted, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
            f.flush()
    except Exception:
        pass


def check_stop_flag() -> bool:
    """Check if stop flag file was created by user."""
    global STOP_REQUESTED
    if STOP_REQUESTED:
        return True
    if STOP_FLAG_FILE.exists():
        log_msg("[!] Detected stop flag file (stop_crawler.flag). Halting crawler safely.")
        return True
    return False


def load_all_match_ids():
    """Load all 4,180 match targets from the 11 season files."""
    targets = []
    for s in SEASONS:
        f = RAW_SOFA_DIR / f"fotmob_league_{s}.json"
        if not f.exists():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
            matches = d.get("fixtures", {}).get("allMatches", [])
            for m in matches:
                mid = str(m.get("id"))
                if mid:
                    h_name = m.get("home", {}).get("name", "Home")
                    a_name = m.get("away", {}).get("name", "Away")
                    score = m.get("status", {}).get("scoreStr", "vs")
                    targets.append({
                        "season": s,
                        "match_id": mid,
                        "desc": f"{h_name} {score} {a_name}"
                    })
        except Exception as e:
            log_msg(f"Error reading {f.name}: {e}")
    return targets


def run_crawler():
    log_msg("=" * 80)
    log_msg("STARTING FOTMOB HISTORICAL MATCH DETAILS CRAWLER (2010 - 2021)")
    log_msg(f"Target Cache Directory: {CACHE_DIR}")
    log_msg(f"Log File Location    : {LOG_FILE}")
    log_msg(f"Stop Control File     : {STOP_FLAG_FILE} (create this file to pause/stop)")
    log_msg("=" * 80)

    # Clean existing stop flag if present
    if STOP_FLAG_FILE.exists():
        try:
            STOP_FLAG_FILE.unlink()
        except Exception:
            pass

    targets = load_all_match_ids()
    total = len(targets)
    log_msg(f"Total target matches to process: {total:,}")

    existing_files = set(f.stem for f in CACHE_DIR.glob("*.json"))
    remaining_targets = [t for t in targets if t["match_id"] not in existing_files]
    already_done = total - len(remaining_targets)

    log_msg(f"Already cached matches: {already_done:,}")
    log_msg(f"Remaining to download : {len(remaining_targets):,}")

    if not remaining_targets:
        log_msg("[+] All 4,180 historical matches are already downloaded and cached!")
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    downloaded = 0
    errors = 0
    t_start = time.time()

    for idx, item in enumerate(remaining_targets, start=already_done + 1):
        if check_stop_flag():
            log_msg(f"Crawler stopped safely at match {idx - 1}/{total}. Progress preserved!")
            break

        mid = item["match_id"]
        season = item["season"]
        desc = item["desc"]
        dest = CACHE_DIR / f"{mid}.json"

        url = f"https://www.fotmob.com/api/data/matchDetails?matchId={mid}"
        success = False

        for attempt in range(1, 4):
            try:
                resp = session.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    with open(dest, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    downloaded += 1
                    success = True
                    sz_kb = dest.stat().st_size / 1024
                    
                    # Compute ETA
                    elapsed = time.time() - t_start
                    rate = downloaded / elapsed if elapsed > 0 else 1.0
                    left = total - idx
                    eta_sec = left / rate if rate > 0 else 0
                    eta_str = str(datetime.timedelta(seconds=int(eta_sec)))

                    pct = (idx / total) * 100
                    log_msg(f"[{idx:4d}/{total:4d}] ({pct:5.1f}%) | {season} | ID: {mid} | {desc} | {sz_kb:5.1f} KB | ETA: {eta_str}")
                    break
                elif resp.status_code == 429:
                    log_msg(f"  [!] Rate limited (429) on match {mid}. Sleeping 5 seconds (attempt {attempt})...")
                    time.sleep(5.0)
                else:
                    log_msg(f"  [-] HTTP {resp.status_code} for match {mid} (attempt {attempt})")
                    time.sleep(1.0)
            except Exception as e:
                log_msg(f"  [-] Connection error for match {mid}: {e} (attempt {attempt})")
                time.sleep(2.0)

        if not success:
            errors += 1
            log_msg(f"  [X] Failed to download match {mid} after 3 attempts.")

        # Respectful delay between calls
        time.sleep(0.4)

    total_time = time.time() - t_start
    log_msg("=" * 80)
    log_msg(f"CRAWLER RUN FINISHED in {datetime.timedelta(seconds=int(total_time))}")
    log_msg(f"New Matches Downloaded: {downloaded:,}")
    log_msg(f"Errors Encountered    : {errors}")
    log_msg(f"Total Matches in Cache: {len(list(CACHE_DIR.glob('*.json'))):,}")
    log_msg("=" * 80)


if __name__ == "__main__":
    run_crawler()
