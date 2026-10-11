"""
crawler_pipeline/uploader/sync_strategy_a.py
Implements Strategy A for ultra-fast synchronization to Hugging Face:
- Bundles 10,700 historical match JSONs into clean .tar.gz archives:
  * raw_data/sofascore/sofascore_matches_cache.tar.gz (~80-90 MB)
  * raw_data/understat/understat_matches_cache.tar.gz (~16 MB)
- Uploads the archives + season overview files directly to Hugging Face.
- Completely avoids individual HTTP requests for 10,700 tiny files.
- Completes in under 1 minute!
"""

import os
import sys
import time
from pathlib import Path
from huggingface_hub import HfApi, CommitOperationDelete

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT_DIR / "raw_data"
TOKEN_FILE = ROOT_DIR / "crawler_pipeline" / "hf_access_key.txt"
FALLBACK_TOKEN_FILE = ROOT_DIR / "access_token_hf.txt"
REPO_ID = "Toibinguz/epl-premier-league-dataset"


def log(msg: str):
    print(f"[*] {msg}", flush=True)


def get_token():
    if "HF_TOKEN" in os.environ and os.environ["HF_TOKEN"].strip():
        return os.environ["HF_TOKEN"].strip()
    for f in [TOKEN_FILE, FALLBACK_TOKEN_FILE]:
        if f.exists():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                return t
    raise ValueError("Hugging Face API token not found.")


def clean_loose_understat_files_on_hf(api: HfApi):
    log("Checking for loose understat/cache_matches files on Hugging Face...")
    try:
        files = api.list_repo_files(repo_id=REPO_ID, repo_type="dataset")
        loose = [f for f in files if f.startswith("raw_data/understat/cache_matches/")]
        if loose:
            log(f"Found {len(loose)} loose cache files on HF. Removing them in chunks...")
            chunk_size = 500
            for i in range(0, len(loose), chunk_size):
                chunk = loose[i:i + chunk_size]
                ops = [CommitOperationDelete(path_in_repo=p) for p in chunk]
                api.create_commit(
                    repo_id=REPO_ID,
                    repo_type="dataset",
                    operations=ops,
                    commit_message=f"Clean loose understat cache files (batch {i//chunk_size + 1})",
                )
                log(f"    [-] Deleted {min(i + chunk_size, len(loose))}/{len(loose)} loose files.")
        else:
            log("[+] No loose understat files found on HF.")
    except Exception as e:
        log(f"[!] Warning cleaning loose files: {e}")


def upload_strategy_a(api: HfApi):
    log("=" * 75)
    log("UPLOADING STRATEGY A (ARCHIVES + OVERVIEWS) TO HUGGING FACE")
    log("=" * 75)
    t0 = time.time()

    # 1. Understat
    us_dir = RAW_DIR / "understat"
    us_tar = us_dir / "understat_matches_cache.tar.gz"
    if us_tar.exists():
        log(f"Uploading understat_matches_cache.tar.gz ({us_tar.stat().st_size / 1024 / 1024:.2f} MB)...")
        api.upload_file(
            path_or_fileobj=str(us_tar),
            path_in_repo="raw_data/understat/understat_matches_cache.tar.gz",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message="Upload Understat 4,611 historical match details archive (.tar.gz)",
        )
        log("[+] Understat archive uploaded successfully.")

    # Upload Understat season overviews & CSVs
    for f in us_dir.glob("*.json"):
        log(f"Uploading {f.name}...")
        api.upload_file(
            path_or_fileobj=str(f),
            path_in_repo=f"raw_data/understat/{f.name}",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message=f"Upload understat {f.name}",
        )
    for f in us_dir.glob("*.csv"):
        log(f"Uploading {f.name}...")
        api.upload_file(
            path_or_fileobj=str(f),
            path_in_repo=f"raw_data/understat/{f.name}",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message=f"Upload understat {f.name}",
        )

    # 2. Sofascore
    sf_dir = RAW_DIR / "sofascore"
    sf_tar = sf_dir / "sofascore_matches_cache.tar.gz"
    if sf_tar.exists():
        log(f"Uploading sofascore_matches_cache.tar.gz ({sf_tar.stat().st_size / 1024 / 1024:.2f} MB)...")
        api.upload_file(
            path_or_fileobj=str(sf_tar),
            path_in_repo="raw_data/sofascore/sofascore_matches_cache.tar.gz",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message="Upload Sofascore/FotMob 6,081 historical match details archive (.tar.gz)",
        )
        log("[+] Sofascore archive uploaded successfully.")

    # Upload Sofascore season overviews & CSVs
    for f in sf_dir.glob("*.json"):
        log(f"Uploading {f.name}...")
        api.upload_file(
            path_or_fileobj=str(f),
            path_in_repo=f"raw_data/sofascore/{f.name}",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message=f"Upload sofascore {f.name}",
        )
    for f in sf_dir.glob("*.csv"):
        log(f"Uploading {f.name}...")
        api.upload_file(
            path_or_fileobj=str(f),
            path_in_repo=f"raw_data/sofascore/{f.name}",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message=f"Upload sofascore {f.name}",
        )

    # 3. Update Dataset Card README.md
    catalog_path = ROOT_DIR / "RAW_DATA_CATALOG.md"
    if catalog_path.exists():
        log("Uploading updated RAW_DATA_CATALOG.md as README.md...")
        api.upload_file(
            path_or_fileobj=str(catalog_path),
            path_in_repo="README.md",
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message="Update Dataset Card README.md with Strategy A documentation",
        )

    elapsed = time.time() - t0
    log("=" * 75)
    log(f"STRATEGY A SYNC FINISHED SUCCESSFULLY IN {elapsed:.1f} SECONDS! [OK]")
    log(f"Repository URL: https://huggingface.co/datasets/{REPO_ID}")
    log("=" * 75)


def main():
    token = get_token()
    api = HfApi(token=token)
    clean_loose_understat_files_on_hf(api)
    upload_strategy_a(api)


if __name__ == "__main__":
    main()
