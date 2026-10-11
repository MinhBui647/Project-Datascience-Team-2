"""
crawler_pipeline/uploader/upload_all_raw.py
Directly commits and uploads the entire raw_data/ folder (all 10,909 files, 1.8 GB)
to Hugging Face Hub in a single unified operation, with live progress logging.
"""

import os
import sys
import time
import datetime
from pathlib import Path
from huggingface_hub import HfApi

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT_DIR / "raw_data"
LOG_FILE = ROOT_DIR / "crawler_pipeline" / "hf_upload.log"
TOKEN_FILE = ROOT_DIR / "crawler_pipeline" / "hf_access_key.txt"
REPO_ID = "Toibinguz/epl-premier-league-dataset"


def log(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{now}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")


def main():
    if not TOKEN_FILE.exists():
        log(f"ERROR: Token file not found at {TOKEN_FILE}")
        return

    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    api = HfApi(token=token)

    log("=" * 80)
    log(f"STARTING FULL RAW_DATA COMMIT TO HUGGING FACE: {REPO_ID}")
    log(f"Source Folder: {RAW_DIR}")
    log(f"Destination Path in Repo: raw_data/")
    log("=" * 80)

    # 1. First ensure README.md is uploaded
    readme_path = ROOT_DIR / "crawler_pipeline" / "uploader" / "README_DATASET.md"
    readme_content = """---
license: mit
task_categories:
- tabular-classification
- tabular-regression
- time-series-forecasting
tags:
- premier-league
- football
- soccer
- sports-analytics
- understat
- sofascore
- fbref
- transfermarkt
- open-meteo
- fantasy-premier-league
- betting-odds
size_categories:
- 1M<n<10M
---

# Premier League Master Raw Dataset (2000 - 2027) ⚽

Comprehensive raw ingestion dataset for the English Premier League covering **27 seasons** from 2000/01 through 2026/27 across all 7 data feeds.

## 📊 Dataset Structure (`raw_data/`)

| Feed / Source | Record Count / Files | Size | Temporal Coverage | Granular Features |
| :--- | :--- | :--- | :--- | :--- |
| **`raw_data/sofascore/`** | 6,146 files | ~980 MB | 2010 – 2027 | Granular match details JSON with shot maps $(x,y)$, player ratings, lineups, passing, substitutions, momentum. |
| **`raw_data/fbref/`** | 16 files | ~283 MB | 2017 – 2025 | Match-level advanced micro-stats for players: Pressures, Progressive Passes/Carries, PSxG, Aerial Duels, SCA, GCA. |
| **`raw_data/transfermarkt/`** | 15 files | ~245 MB | 2000 – 2026 | Player valuations (2000-2026), full game events (minute-by-minute goals/cards/subs), starting lineups, transfer histories and fees. |
| **`raw_data/understat/`** | 4,633 files | ~179 MB | 2014 – 2027 | 4,611 match details JSONs with shot coordinates $(X,Y)$, xG per shot, and player micro-stats (`xGChain`, `xGBuildup`). |
| **`raw_data/fpl_api/`** | 37 files | ~54 MB | 2016 – 2026 | Gameweek-by-gameweek player micro-stats: ICT Index, Threat, Creativity, Influence, Bonus Points, Cost. |
| **`raw_data/open_meteo/`** | 30 files | ~51 MB | 2000 – 2026 | Match kickoff stadium weather (2021-2026) and 68,000+ daily UK regional meteorological records (2000-2021). |
| **`raw_data/football_data_co_uk/`** | 32 files | ~4 MB | 2000 – 2027 | Full results, halftime scores, shots, shots on target, corners, fouls, cards, referees, attendance, and multi-bookmaker odds. |

**Total Ingestion Footprint**: **10,909 files | ~1.80 GB** of clean, multi-modal football data.
"""
    try:
        log("[*] Uploading dataset README.md...")
        api.upload_file(
            path_or_fileobj=readme_content.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message="Add dataset card README.md",
        )
        log("[+] README.md uploaded successfully.")
    except Exception as e:
        log(f"[!] Warning on README upload: {e}")

    # 2. Upload the entire raw_data folder
    log("[*] Uploading entire raw_data/ folder (10,909 files, ~1.8 GB)...")
    log("[*] Note: Hugging Face uploads all files/blobs in parallel before creating the commit.")
    log("[*] Please wait while files are being transferred...")
    start_t = time.time()

    try:
        commit_info = api.upload_folder(
            folder_path=str(RAW_DIR),
            path_in_repo="raw_data",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message="Add complete Premier League raw_data (all 7 feeds, 10,909 files, ~1.8GB)",
        )
        elapsed = time.time() - start_t
        log(f"[+] SUCCESS! Entire raw_data uploaded and committed in {elapsed:.1f}s!")
        log(f"[+] Commit URL: https://huggingface.co/datasets/{REPO_ID}")
    except Exception as e:
        log(f"[!] Upload error: {e}")


if __name__ == "__main__":
    main()

