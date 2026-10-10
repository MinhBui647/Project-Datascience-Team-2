"""
crawler_pipeline/extractors/understat_ext.py
Fetches Understat expected goals (xG, xA, forecasts, xGChain, xGBuildup) for Premier League.
"""

import gzip
import json
import urllib.request
from typing import Dict, Any
from crawler_pipeline.config import HEADERS, RAW_DIR


def fetch_understat_data(year: int = 2026) -> Dict[str, Any]:
    """Fetch live Understat league JSON for EPL for given start year."""
    url = f"https://understat.com/getLeagueData/EPL/{year}"
    print(f"[*] [Understat] Fetching from: {url}")
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "X-Requested-With": "XMLHttpRequest", "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        try:
            content = gzip.decompress(raw).decode("utf-8")
        except Exception:
            content = raw.decode("utf-8")
        data = json.loads(content)

    # Persist raw dump in raw_data/
    raw_target_dir = RAW_DIR / "understat"
    raw_target_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_target_dir / f"understat_raw_{year}_{year+1}.json"
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"    -> Saved raw dump to {raw_file}")

    print(f"    -> Downloaded Understat: {len(data.get('dates', []))} fixtures, {len(data.get('players', []))} players.")
    return data

