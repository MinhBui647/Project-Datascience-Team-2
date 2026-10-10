"""
crawler_pipeline/extractors/fotmob_ext.py
Fetches FotMob fixtures, lineups, spatial shot maps (x, y coordinates), and granular team match stats.
"""

import os
import json
import time
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional
from crawler_pipeline.config import HEADERS, RAW_DIR


def fetch_fotmob_matches(season: str = "2026/2027") -> List[Dict[str, Any]]:
    """Fetch FotMob schedule and results for Premier League (league id 47)."""
    url = f"https://www.fotmob.com/api/data/leagues?id=47&season={urllib.parse.quote(season)}"
    print(f"[*] [FotMob] Fetching season fixtures from: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    matches = data.get("fixtures", {}).get("allMatches", [])
    print(f"    -> FotMob: {len(matches)} matches scheduled.")

    # Persist raw fixtures in raw_data/sofascore/
    raw_target_dir = RAW_DIR / "sofascore"
    raw_target_dir.mkdir(parents=True, exist_ok=True)
    with open(raw_target_dir / f"fotmob_fixtures_{season.replace('/', '_')}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return matches


def fetch_fotmob_match_detail(match_id: int, cache_dir: str) -> Optional[Dict[str, Any]]:
    """Fetch detailed match stats, lineups, and shot map for a finished match with caching."""
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{match_id}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    url = f"https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"
    req = urllib.request.Request(url, headers=HEADERS)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
        except Exception:
            time.sleep(1.0)
    return None

