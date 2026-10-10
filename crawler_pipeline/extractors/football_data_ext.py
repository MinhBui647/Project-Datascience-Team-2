"""
crawler_pipeline/extractors/football_data_ext.py
Fetches match scores, half-time results, fouls, cards, and bookmaker odds from Football-Data.co.uk.
"""

import io
import urllib.request
import pandas as pd
from crawler_pipeline.config import HEADERS


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
    df = df.dropna(how="all")
    print(f"    -> Downloaded {len(df)} matches, {len(df.columns)} columns.")
    return df

