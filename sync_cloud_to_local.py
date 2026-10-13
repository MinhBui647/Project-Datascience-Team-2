"""
sync_cloud_to_local.py
Pulls/downloads raw_data directly from Hugging Face Dataset Hub to local repository.
Allows any team member to fetch the complete dataset locally with 1 click.
Repository: Toibinguz/epl-premier-league-dataset
"""

import os
import sys
import time
from pathlib import Path
from huggingface_hub import snapshot_download

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
    return None


def main():
    print("=" * 75)
    print("  SYNC CLOUD -> LOCAL (Download from Hugging Face Hub)")
    print(f"  Source Repository: https://huggingface.co/datasets/{REPO_ID}")
    print(f"  Destination Path : {RAW_DIR}")
    print("=" * 75)

    token = get_token()
    start_t = time.time()

    try:
        print("[*] Downloading raw_data, 2026_2027_new, and clean from Hugging Face...")
        downloaded_path = snapshot_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            allow_patterns=["raw_data/*", "2026_2027_new/*", "clean/*"],
            local_dir=str(ROOT_DIR),
            token=token,
        )
        elapsed = time.time() - start_t
        print(f"[+] Cloud -> Local sync completed successfully in {elapsed:.1f}s! ✅")
        print(f"[+] Raw data is up to date in: {RAW_DIR}")

        # Check if unpacking is requested (--unpack) or if match folders are missing
        unpack_flag = "--unpack" in sys.argv
        import tarfile

        understat_tar = RAW_DIR / "understat" / "understat_matches_cache.tar.gz"
        understat_match_dir = RAW_DIR / "understat" / "cache_matches"
        if understat_tar.exists() and (unpack_flag or not understat_match_dir.exists() or len(list(understat_match_dir.glob("*.json"))) == 0):
            print(f"[*] Unpacking Understat matches archive ({understat_tar.name})...")
            with tarfile.open(understat_tar, "r:gz") as tar:
                tar.extractall(path=RAW_DIR / "understat")
            print(f"[+] Understat match cache unpacked to {understat_match_dir}")

        sofascore_tar = RAW_DIR / "sofascore" / "sofascore_matches_cache.tar.gz"
        sofascore_match_dir = RAW_DIR / "sofascore" / "matches"
        if sofascore_tar.exists() and (unpack_flag or not sofascore_match_dir.exists() or len(list(sofascore_match_dir.glob("*.json"))) == 0):
            print(f"[*] Unpacking FotMob/Sofascore matches archive ({sofascore_tar.name})...")
            with tarfile.open(sofascore_tar, "r:gz") as tar:
                tar.extractall(path=RAW_DIR / "sofascore")
            print(f"[+] FotMob/Sofascore match cache unpacked to {sofascore_match_dir}")

    except Exception as e:
        print(f"[!] Sync error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

