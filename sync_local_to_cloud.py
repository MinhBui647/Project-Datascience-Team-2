"""
sync_local_to_cloud.py
Synchronizes local raw_data/ directory to Hugging Face Dataset Hub.
Repository: Toibinguz/epl-premier-league-dataset
"""

import os
import sys
import time
from pathlib import Path
from huggingface_hub import HfApi

ROOT_DIR = Path(__file__).resolve().parent
RAW_DIR = ROOT_DIR / "raw_data"
TOKEN_FILE = ROOT_DIR / "crawler_pipeline" / "hf_access_key.txt"
FALLBACK_TOKEN_FILE = ROOT_DIR / "access_token_hf.txt"
REPO_ID = "Toibinguz/epl-premier-league-dataset"


def get_token():
    if "HF_TOKEN" in os.environ and os.environ["HF_TOKEN"].strip():
        return os.environ["HF_TOKEN"].strip()
    for f in [TOKEN_FILE, FALLBACK_TOKEN_FILE]:
        if f.exists():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                return t
    raise ValueError("Hugging Face API token not found in environment or key files.")


def main():
    print("=" * 75)
    print("  SYNC LOCAL -> CLOUD (Hugging Face Dataset Hub)")
    print(f"  Target Repository: https://huggingface.co/datasets/{REPO_ID}")
    print("=" * 75)

    if not RAW_DIR.exists():
        print(f"[!] Error: Local raw_data folder does not exist at {RAW_DIR}")
        sys.exit(1)

    token = get_token()
    api = HfApi(token=token)

    # 1. Update README.md catalog on HF
    catalog_file = ROOT_DIR / "RAW_DATA_CATALOG.md"
    if catalog_file.exists():
        try:
            print("[*] Updating Dataset Catalog README.md on Hugging Face...")
            api.upload_file(
                path_or_fileobj=str(catalog_file),
                path_in_repo="README.md",
                repo_id=REPO_ID,
                repo_type="dataset",
                commit_message="Update Dataset Catalog README.md",
            )
            print("[+] README.md catalog synced.")
        except Exception as e:
            print(f"[!] Warning on catalog sync: {e}")

    # 2. Upload raw_data folder (ignoring loose match cache if archives exist)
    print("[*] Uploading raw_data folder to Hugging Face Hub (Strategy A fast mode)...")
    start_t = time.time()
    try:
        api.upload_folder(
            folder_path=str(RAW_DIR),
            path_in_repo="raw_data",
            repo_id=REPO_ID,
            repo_type="dataset",
            ignore_patterns=["*/cache_matches/*", "*/matches/*"],
            commit_message="Sync raw_data from local to cloud (Strategy A)",
        )
        print("[+] raw_data folder synced.")
    except Exception as e:
        print(f"[!] Sync error on raw_data: {e}")
        sys.exit(1)

    # 3. Upload 2026_2027_new folder if it exists
    new_season_dir = ROOT_DIR / "2026_2027_new"
    if new_season_dir.exists():
        try:
            print("[*] Uploading 2026_2027_new folder to Hugging Face Hub...")
            api.upload_folder(
                folder_path=str(new_season_dir),
                path_in_repo="2026_2027_new",
                repo_id=REPO_ID,
                repo_type="dataset",
                commit_message="Sync 2026_2027_new active season matches and overviews",
            )
            print("[+] 2026_2027_new folder synced.")
        except Exception as e:
            print(f"[!] Warning on 2026_2027_new sync: {e}")

    elapsed = time.time() - start_t
    print(f"[+] Local -> Cloud sync completed successfully in {elapsed:.1f}s! ✅")
    print(f"[+] Dataset URL: https://huggingface.co/datasets/{REPO_ID}")


if __name__ == "__main__":
    main()

