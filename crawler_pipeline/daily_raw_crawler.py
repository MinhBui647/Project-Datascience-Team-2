"""
crawler_pipeline/daily_raw_crawler.py
Performs daily incremental raw crawling for the active Premier League season (2026/27).
Saves data into both:
1. raw_data/ (complete multi-season raw data store)
2. 2026_2027_new/ (dedicated current season folder with separate match files under matches/)

And syncs 2026_2027_new directly to Hugging Face Hub.
"""

import os
import sys
import json
import gzip
import time
import shutil
import datetime
import urllib.request
import urllib.error
from pathlib import Path
from huggingface_hub import HfApi

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT_DIR / "raw_data"
SEASON_NEW_DIR = ROOT_DIR / "2026_2027_new"
TOKEN_FILE = ROOT_DIR / "crawler_pipeline" / "hf_access_key.txt"
FALLBACK_TOKEN_FILE = ROOT_DIR / "access_token_hf.txt"
REPO_ID = "Toibinguz/epl-premier-league-dataset"

# Create directories
SEASON_NEW_DIR.mkdir(parents=True, exist_ok=True)
(SEASON_NEW_DIR / "matches" / "fotmob").mkdir(parents=True, exist_ok=True)
(SEASON_NEW_DIR / "matches" / "understat").mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def log(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def get_token():
    if "HF_TOKEN" in os.environ and os.environ["HF_TOKEN"].strip():
        return os.environ["HF_TOKEN"].strip()
    for f in [TOKEN_FILE, FALLBACK_TOKEN_FILE]:
        if f.exists():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                return t
    return None


def update_football_data(season_code="2627"):
    """Download the latest 2026/27 match and odds CSV from Football-Data.co.uk."""
    log("=" * 70)
    log("[1/5] Updating Football-Data.co.uk...")
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/E0.csv"
    out_file = RAW_DIR / "football_data_co_uk" / f"football_data_raw_{season_code}.csv"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read()
            if len(content) > 100:
                with open(out_file, "wb") as f:
                    f.write(content)
                # Copy to 2026_2027_new
                shutil.copy2(out_file, SEASON_NEW_DIR / out_file.name)
                log(f"[+] Successfully updated {out_file.name} ({len(content)/1024:.1f} KB)")
            else:
                log("[!] Football-Data returned empty response.")
    except Exception as e:
        log(f"[!] Error updating Football-Data: {e}")


def update_fpl_api():
    """Update latest Fantasy Premier League bootstrap-static and fixtures."""
    log("=" * 70)
    log("[2/5] Updating Fantasy Premier League (FPL API)...")
    out_dir = RAW_DIR / "fpl_api"
    out_dir.mkdir(parents=True, exist_ok=True)

    endpoints = [
        ("fpl_bootstrap_latest.json", "https://fantasy.premierleague.com/api/bootstrap-static/"),
        ("fpl_fixtures_latest.json", "https://fantasy.premierleague.com/api/fixtures/"),
    ]

    for fname, url in endpoints:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                target = out_dir / fname
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                # Copy to 2026_2027_new
                shutil.copy2(target, SEASON_NEW_DIR / fname)
                log(f"[+] Successfully updated {fname}")
        except Exception as e:
            log(f"[!] Error updating {fname}: {e}")


def update_understat(year="2026", season_tag="2026_2027"):
    """Update Understat league overview and fetch any new match details."""
    log("=" * 70)
    log("[3/5] Updating Understat (Overview + Granular Match Details)...")
    out_dir = RAW_DIR / "understat"
    cache_dir = out_dir / "cache_matches"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_match_dir = SEASON_NEW_DIR / "matches" / "understat"

    url = f"https://understat.com/getLeagueData/EPL/{year}"
    understat_headers = {
        **HEADERS,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://understat.com/league/EPL/{year}",
        "Accept-Encoding": "gzip",
    }

    overview_file = out_dir / f"understat_raw_{season_tag}.json"
    matches_to_fetch = []

    try:
        req = urllib.request.Request(url, headers=understat_headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
            with open(overview_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            shutil.copy2(overview_file, SEASON_NEW_DIR / overview_file.name)
            log(f"[+] Successfully updated {overview_file.name}")

            # Collect completed matches that need detail fetching
            for m in data.get("dates", []):
                if m.get("isResult") is True and "id" in m:
                    m_id = str(m["id"])
                    cache_file = cache_dir / f"{m_id}.json"
                    new_file = target_match_dir / f"{m_id}.json"
                    if not cache_file.exists() or cache_file.stat().st_size < 100:
                        matches_to_fetch.append(m_id)
                    elif not new_file.exists():
                        shutil.copy2(cache_file, new_file)
    except Exception as e:
        log(f"[!] Error fetching Understat league overview: {e}")

    # Fetch new match details
    if matches_to_fetch:
        log(f"[*] Found {len(matches_to_fetch)} new completed matches to crawl from Understat...")
        for m_id in matches_to_fetch:
            m_url = f"https://understat.com/getMatchData/{m_id}"
            m_req = urllib.request.Request(m_url, headers={**understat_headers, "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(m_req, timeout=15) as resp:
                    raw = resp.read()
                    if raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                    m_data = json.loads(raw.decode("utf-8"))
                    target = cache_dir / f"{m_id}.json"
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(m_data, f)
                    # Copy to 2026_2027_new/matches/understat/
                    shutil.copy2(target, target_match_dir / f"{m_id}.json")
                    log(f"    [+] Cached Understat Match {m_id}")
                time.sleep(0.3)
            except Exception as e:
                log(f"    [!] Failed to fetch Understat match {m_id}: {e}")
    else:
        log("[+] All completed Understat matches are already cached.")


def update_fotmob(season_tag="2026_2027"):
    """Update FotMob fixtures and fetch any new match details."""
    log("=" * 70)
    log("[4/5] Updating FotMob (Fixtures + Granular Match Details)...")
    out_dir = RAW_DIR / "sofascore"
    cache_dir = out_dir / "cache_matches"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_match_dir = SEASON_NEW_DIR / "matches" / "fotmob"

    url = "https://www.fotmob.com/api/data/leagues?id=47&season=2026%2F2027"
    overview_file = out_dir / f"fotmob_fixtures_{season_tag}.json"
    matches_to_fetch = []

    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            with open(overview_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            shutil.copy2(overview_file, SEASON_NEW_DIR / overview_file.name)
            log(f"[+] Successfully updated {overview_file.name}")

            # Scan fixtures
            fixtures = data.get("fixtures", {}).get("allMatches", [])
            for m in fixtures:
                m_id = str(m.get("id"))
                status = m.get("status", {})
                if status.get("finished", False) and m_id:
                    cache_file = cache_dir / f"{m_id}.json"
                    new_file = target_match_dir / f"{m_id}.json"
                    if not cache_file.exists() or cache_file.stat().st_size < 100:
                        matches_to_fetch.append(m_id)
                    elif not new_file.exists():
                        shutil.copy2(cache_file, new_file)
    except Exception as e:
        log(f"[!] Error fetching FotMob fixtures: {e}")

    # Fetch new match details
    if matches_to_fetch:
        log(f"[*] Found {len(matches_to_fetch)} new completed matches to crawl from FotMob...")
        for m_id in matches_to_fetch:
            m_url = f"https://www.fotmob.com/api/data/matchDetails?matchId={m_id}"
            m_req = urllib.request.Request(m_url, headers=HEADERS)
            try:
                with urllib.request.urlopen(m_req, timeout=15) as resp:
                    m_data = json.loads(resp.read().decode("utf-8"))
                    target = cache_dir / f"{m_id}.json"
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(m_data, f)
                    # Copy to 2026_2027_new/matches/fotmob/
                    shutil.copy2(target, target_match_dir / f"{m_id}.json")
                    log(f"    [+] Cached FotMob Match {m_id}")
                time.sleep(0.3)
            except Exception as e:
                log(f"    [!] Failed to fetch FotMob match {m_id}: {e}")
    else:
        log("[+] All completed FotMob matches are already cached.")


def update_open_meteo():
    """Copy weather cache for 2026/27."""
    log("=" * 70)
    log("[5/5] Updating Open-Meteo Weather...")
    w_src = RAW_DIR / "open_meteo" / "weather_cache_2026_2027.json"
    if w_src.exists():
        shutil.copy2(w_src, SEASON_NEW_DIR / w_src.name)
        log(f"[+] Weather cache file verified and copied: {w_src.name}")


def sync_season_to_hf():
    """Sync the 2026_2027_new folder (active season with separate matches) to Hugging Face."""
    token = get_token()
    if not token:
        log("[!] No HF token found, skipping Hugging Face upload.")
        return

    log("=" * 70)
    log(f"[*] Syncing 2026_2027_new folder to Hugging Face: {REPO_ID}...")
    api = HfApi(token=token)
    try:
        t0 = time.time()
        api.upload_folder(
            folder_path=str(SEASON_NEW_DIR),
            path_in_repo="2026_2027_new",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message=f"Daily update: Season 2026_2027 matches and feeds ({datetime.date.today()})",
        )
        elapsed = time.time() - t0
        log(f"[+] Successfully synced 2026_2027_new to Hugging Face in {elapsed:.1f}s! [OK]")
    except Exception as e:
        log(f"[!] Warning on HF season sync: {e}")


def run_daily_raw_pipeline(sync_hf: bool = True):
    log("=" * 80)
    log("STARTING DAILY PREMIER LEAGUE RAW CRAWL PIPELINE (2026/27)")
    log(f"Target Current Season Folder: {SEASON_NEW_DIR}")
    log("=" * 80)
    start_time = time.time()

    update_football_data("2627")
    update_fpl_api()
    update_understat("2026", "2026_2027")
    update_fotmob("2026_2027")
    update_open_meteo()

    if sync_hf:
        sync_season_to_hf()

    elapsed = time.time() - start_time
    log("=" * 80)
    log(f"DAILY RAW CRAWL COMPLETED IN {elapsed:.1f} SECONDS [OK]")
    log("=" * 80)


if __name__ == "__main__":
    do_sync = "--no-sync" not in sys.argv
    run_daily_raw_pipeline(sync_hf=do_sync)
