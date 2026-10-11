"""
crawler_pipeline/uploader/push_raw_to_hf.py
Wipes the existing dataset on Hugging Face and pushes the complete 1.8 GB raw_data (all 7 feeds)
along with a comprehensive Dataset Card README.md.
"""

import os
import sys
import time
import datetime
from pathlib import Path
from huggingface_hub import HfApi, CommitOperationDelete

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT_DIR / "raw_data"
TOKEN_FILE = ROOT_DIR / "crawler_pipeline" / "hf_access_key.txt"
FALLBACK_TOKEN_FILE = ROOT_DIR / "access_token_hf.txt"
REPO_ID = "Toibinguz/epl-premier-league-dataset"


def get_token():
    if "HF_TOKEN" in os.environ and os.environ["HF_TOKEN"].strip():
        return os.environ["HF_TOKEN"].strip()
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    if FALLBACK_TOKEN_FILE.exists():
        tok = FALLBACK_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    raise ValueError("No Hugging Face token found in environment or key files.")


def wipe_dataset(api: HfApi, repo_id: str):
    print("=" * 80)
    print(f"STEP 1: WIPING EXISTING FILES ON HUGGING FACE REPO: {repo_id}")
    print("=" * 80)
    
    # Try deleting and recreating repo for a 100% clean git history
    try:
        print(f"[*] Attempting clean repo recreation: deleting {repo_id}...")
        api.delete_repo(repo_id=repo_id, repo_type="dataset")
        print(f"[+] Repo deleted. Recreating fresh empty repo...")
        time.sleep(2)
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=False)
        print(f"[+] Fresh repository created successfully.")
        return
    except Exception as e:
        print(f"[!] delete_repo not permitted or failed ({e}). Falling back to deleting all existing files via commit...")

    # Fallback: list all existing files and delete them in batches
    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    # Keep .gitattributes if exists
    to_delete = [f for f in files if f != ".gitattributes"]
    if not to_delete:
        print("[+] Repo is already empty.")
        return
    
    print(f"[*] Found {len(to_delete)} existing files to remove...")
    # Batch delete in chunks of 500
    chunk_size = 500
    for i in range(0, len(to_delete), chunk_size):
        chunk = to_delete[i:i + chunk_size]
        operations = [CommitOperationDelete(path_in_repo=p) for p in chunk]
        api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=f"Wipe old files (batch {i//chunk_size + 1})",
        )
        print(f"    [-] Deleted {min(i + chunk_size, len(to_delete))}/{len(to_delete)} files.")
    print("[+] All old files wiped successfully.")


def generate_readme():
    return """---
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

Comprehensive raw ingestion dataset for the English Premier League covering **27 seasons** from 2000/01 through 2026/27.

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

**Total Ingestion Footprint**: **> 10,900 files | ~1.80 GB** of clean, multi-modal football data.

---

## 🚀 How to Load and Explore in Python

```python
from huggingface_hub import snapshot_download
import pandas as pd
import json

# Download specific raw feed or entire raw_data
repo_path = snapshot_download(
    repo_id="Toibinguz/epl-premier-league-dataset",
    repo_type="dataset",
    allow_patterns="raw_data/fbref/*"
)

# Load FBref Player Defense stats
df_defense = pd.read_csv(f"{repo_path}/raw_data/fbref/ENG_M_1st_defense_player_advanced_match_stats.csv")
print(df_defense.head())
```
"""


def upload_raw_feeds(api: HfApi, repo_id: str):
    print("=" * 80)
    print(f"STEP 2: UPLOADING RAW DATA FEEDS TO HUGGING FACE: {repo_id}")
    print("=" * 80)
    
    # 1. Upload README.md first
    readme_content = generate_readme()
    print("[*] Uploading Dataset Card README.md...")
    api.upload_file(
        path_or_fileobj=readme_content.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="dataset",
        commit_message="Add Premier League Raw Dataset Card README.md",
    )
    print("[+] README.md uploaded.")

    # 2. Upload feeds in order of file count / size
    feeds = [
        ("football_data_co_uk", "Football-Data.co.uk 27 seasons (odds, cards, corners)"),
        ("open_meteo", "Open-Meteo UK historical weather records"),
        ("fpl_api", "FPL Gameweek micro-stats (2016-2026)"),
        ("transfermarkt", "Transfermarkt full events, lineups, valuations, transfers"),
        ("fbref", "FBref player & team advanced match stats (pressures, progressive passes)"),
        ("understat", "Understat 4,611 matches (shot coordinates x,y and xGChain)"),
        ("sofascore", "FotMob/Sofascore 6,081 matches (shot maps, ratings, lineups)"),
    ]

    for feed_name, desc in feeds:
        feed_dir = RAW_DIR / feed_name
        if not feed_dir.exists():
            print(f"[!] Warning: {feed_dir} does not exist, skipping.")
            continue
        
        print("-" * 80)
        print(f"[*] Uploading feed: {feed_name.upper()} ({desc})...")
        start_t = time.time()
        
        # Upload folder directly
        api.upload_folder(
            folder_path=str(feed_dir),
            path_in_repo=f"raw_data/{feed_name}",
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"Upload raw_data/{feed_name} - {desc}",
        )
        elapsed = time.time() - start_t
        print(f"[+] Finished uploading raw_data/{feed_name} in {elapsed:.1f}s.")

    print("=" * 80)
    print(f"SUCCESS: ALL 7 RAW FEEDS UPLOADED TO https://huggingface.co/datasets/{repo_id}")
    print("=" * 80)


if __name__ == "__main__":
    token = get_token()
    api = HfApi(token=token)
    wipe_dataset(api, REPO_ID)
    upload_raw_feeds(api, REPO_ID)
