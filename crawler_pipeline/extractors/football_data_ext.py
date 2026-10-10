"""
crawler_pipeline/extractors/football_data_ext.py
Fetches match scores, half-time results, fouls, cards, and bookmaker odds from Football-Data.co.uk.
"""

import io
import requests
import pandas as pd
from crawler_pipeline.config import HEADERS, RAW_DIR


def fetch_football_data(season_code: str = "2627") -> pd.DataFrame:
    """Fetch live Football-Data.co.uk CSV for target season code (e.g. '2627')."""
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/E0.csv"
    print(f"[*] [Football-Data] Fetching from: {url}")
    raw_target_dir = RAW_DIR / "football_data_co_uk"
    raw_target_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_target_dir / f"football_data_raw_{season_code}.csv"

    content = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        content = resp.content
        with open(raw_file, "wb") as f:
            f.write(content)
        print(f"    -> Saved raw dump to {raw_file}")
    except Exception as e:
        print(f"    [!] Warning: Could not fetch from remote ({e}). Checking local cache...")
        if raw_file.exists():
            print(f"    -> Using local cache: {raw_file}")
            with open(raw_file, "rb") as f:
                content = f.read()
        else:
            raise

    try:
        df = pd.read_csv(io.BytesIO(content), encoding="utf-8")
    except Exception:
        df = pd.read_csv(io.BytesIO(content), encoding="cp1252")
    return df

