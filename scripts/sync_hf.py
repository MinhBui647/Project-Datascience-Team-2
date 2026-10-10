"""
scripts/sync_hf.py
Upload entire normalized data/ directory to Hugging Face Hub Dataset.
"""

import os
import sys
from pathlib import Path
from huggingface_hub import HfApi

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
TOKEN_FILE = ROOT_DIR / "access_token_hf.txt"

REPO_ID = os.environ.get("HF_REPO_ID", "Toibinguz/epl-premier-league-dataset")
TOKEN = os.environ.get("HF_TOKEN")

if not TOKEN and TOKEN_FILE.exists():
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        TOKEN = f.read().strip()

if not TOKEN:
    print("[!] No Hugging Face token found in environment (HF_TOKEN) or access_token_hf.txt")
    sys.exit(1)

print("=" * 80)
print(f"CONNECTING TO HUGGING FACE DATASET REPO: {REPO_ID}")
print("=" * 80)

api = HfApi(token=TOKEN)

try:
    who = api.whoami()
    print(f"[+] Authenticated successfully as: {who.get('name')}")
except Exception as e:
    print(f"[!] Authentication error: {e}")
    sys.exit(1)

# Ensure repository exists
try:
    api.repo_info(repo_id=REPO_ID, repo_type="dataset")
    print(f"[+] Repository '{REPO_ID}' exists on Hugging Face.")
except Exception:
    print(f"[*] Creating public dataset repository '{REPO_ID}'...")
    api.create_repo(repo_id=REPO_ID, repo_type="dataset", private=False)
    print(f"[+] Repository '{REPO_ID}' created successfully.")

print(f"[*] Uploading all datasets from {DATA_DIR}...")
commit_msg = "feat: initial upload of Premier League 2021-2027 normalized datasets"

api.upload_folder(
    folder_path=str(DATA_DIR),
    repo_id=REPO_ID,
    repo_type="dataset",
    commit_message=commit_msg,
)

print("\n" + "=" * 80)
print(f"SUCCESS: ALL DATASETS UPLOADED TO: https://huggingface.co/datasets/{REPO_ID}")
print("=" * 80)
