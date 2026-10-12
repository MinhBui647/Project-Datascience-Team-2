"""
crawler_pipeline/uploader/hf_uploader.py
Uploads active season (2026_2027_new) and raw data directly to Hugging Face Hub Dataset repo.
Ensures repository remains strictly clean with only 2026_2027_new and raw_data.
"""

import os
import sys
import datetime
from pathlib import Path
from huggingface_hub import HfApi

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SEASON_NEW_DIR = ROOT_DIR / "2026_2027_new"
RAW_DIR = ROOT_DIR / "raw_data"
DEFAULT_HF_REPO_ID = "Toibinguz/epl-premier-league-dataset"


def sync_to_huggingface(repo_id: str = DEFAULT_HF_REPO_ID, token: str = None, sync_raw: bool = False) -> None:
    """Sync 2026_2027_new (and optionally raw_data) to Hugging Face Dataset repository."""
    print("=" * 80)
    print(f"SYNCING TO HUGGING FACE DATASET: {repo_id}")
    print("=" * 80)

    if not token:
        token = os.environ.get("HF_TOKEN")

    try:
        api = HfApi(token=token)
        now_str = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        if sync_raw and RAW_DIR.exists():
            print("[*] Uploading raw_data (Strategy A mode) to Hugging Face...")
            api.upload_folder(
                folder_path=str(RAW_DIR),
                path_in_repo="raw_data",
                repo_id=repo_id,
                repo_type="dataset",
                ignore_patterns=["*/cache_matches/*", "*/matches/*"],
                commit_message=f"Sync raw_data (Strategy A) - {now_str}",
            )
            print("[+] raw_data synced successfully.")

        if SEASON_NEW_DIR.exists():
            print(f"[*] Uploading 2026_2027_new to Hugging Face...")
            api.upload_folder(
                folder_path=str(SEASON_NEW_DIR),
                path_in_repo="2026_2027_new",
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=f"Sync active season 2026_2027_new - {now_str}",
            )
            print("[+] 2026_2027_new synced successfully.")

        print(f"\nSUCCESS: Data synced to https://huggingface.co/datasets/{repo_id}")
    except Exception as e:
        print(f"[!] Hugging Face sync error: {e}")
