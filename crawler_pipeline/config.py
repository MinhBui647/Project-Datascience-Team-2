"""
crawler_pipeline/config.py
Central configuration and path resolution for the EPL Crawler & Ingestion Pipeline.
"""

import os
from pathlib import Path

# Base directory resolution
PIPELINE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PIPELINE_DIR.parent
DATA_DIR = ROOT_DIR / "data"
MASTER_DIR = DATA_DIR / "00_master_entities"
RAW_DIR = ROOT_DIR / "raw"
TOKEN_FILE = ROOT_DIR / "access_token_hf.txt"

# Default Repository ID for Hugging Face
DEFAULT_HF_REPO_ID = os.environ.get("HF_REPO_ID", "Toibinguz/epl-premier-league-dataset")

# Standard HTTP Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

