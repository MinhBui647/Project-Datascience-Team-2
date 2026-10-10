"""
crawler_pipeline/extractors/football_data_ext.py
Fetches match scores, half-time results, fouls, cards, and bookmaker odds from Football-Data.co.uk.
"""

import io
import urllib.request
import pandas as pd
from crawler_pipeline.config import HEADERS, RAW_DIR


def fetch_football_data(season_code: str = "2627") -> pd.DataFrame:
    """Fetch live Football-Data.co.uk CSV for target season code (e.g. '2627')."""
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/E0.csv"
    print(f"[*] [Football-Data] Fetching from: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        content = resp.read()
    try:
        df = pd.read_csv(io.BytesIO(content), encoding="utf-8")
    except Exception:
        df = pd.read_csv(io.BytesIO(content), encoding="cp1252")
    # Persist raw dump in raw_data/
    raw_target_dir = RAW_DIR / "football_data_co_uk"
    raw_target_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_target_dir / f"football_data_raw_{season_code}.csv"
    with open(raw_file, "wb") as f:
        f.write(content)
    print(f"    -> Saved raw dump to {raw_file}")
    return df

