"""
crawler_pipeline/run.py
Master Orchestrator CLI for EPL Crawling, Integration, QA, and Hugging Face Sync.
Can be executed directly from terminal or triggered via GitHub Actions.
"""

import os
import sys
import argparse
from pathlib import Path

# Ensure project root is in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from crawler_pipeline.config import DEFAULT_HF_REPO_ID, TOKEN_FILE
from crawler_pipeline.integrators.season_builder import build_season
from crawler_pipeline.uploader.qa_validator import run_qa_suite
from crawler_pipeline.uploader.hf_uploader import sync_to_huggingface


def main():
    parser = argparse.ArgumentParser(description="Premier League Automated Ingestion & Sync Pipeline")
    parser.add_argument("--season", default="2026_2027", help="Target EPL season (default: 2026_2027)")
    parser.add_argument("--sync-hf", action="store_true", help="Sync datasets to Hugging Face Hub")
    parser.add_argument("--repo-id", default=DEFAULT_HF_REPO_ID, help="Hugging Face Dataset repo ID")
    parser.add_argument("--token", default=None, help="Hugging Face API token")
    parser.add_argument("--skip-qa", action="store_true", help="Skip QA suite verification")
    args = parser.parse_args()

    # Resolve Hugging Face Token
    token = args.token or os.environ.get("HF_TOKEN")
    if not token and TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception:
            pass

    # 1. Run Crawl & Integration
    build_season(season=args.season)

    # 2. Run Quality Assurance Suite
    if not args.skip_qa:
        print("\nRunning automated QA checks...")
        qa_ok = run_qa_suite()
        if not qa_ok:
            print("\n[!] QA verification failed! Aborting sync.")
            sys.exit(1)

    # 3. Sync to Hugging Face
    if args.sync_hf or token:
        if token:
            sync_to_huggingface(repo_id=args.repo_id, token=token, season=args.season)
        else:
            print("\n[!] Note: --sync-hf requested but no HF_TOKEN found in arguments, environment, or access_token_hf.txt.")
            print("    Please set the HF_TOKEN secret in GitHub repository settings to enable automated push.")


if __name__ == "__main__":
    main()

