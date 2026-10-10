"""
crawler_pipeline/extractors/fpl_ext.py
Fetches Official Fantasy Premier League API (FPL) bootstrap-static and fixture difficulty ratings (FDR).
"""

import json
import urllib.request
from typing import Tuple, Dict, Any, List
from crawler_pipeline.config import HEADERS


def fetch_fpl_data() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Fetch live FPL bootstrap-static and fixtures list."""
    print("[*] [FPL] Fetching Fantasy Premier League API data...")
    # 1. Bootstrap
    b_url = "https://fantasy.premierleague.com/api/bootstrap-static/"
    req = urllib.request.Request(b_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        bootstrap = json.loads(resp.read().decode("utf-8"))

    # 2. Fixtures
    f_url = "https://fantasy.premierleague.com/api/fixtures/"
    req2 = urllib.request.Request(f_url, headers=HEADERS)
    with urllib.request.urlopen(req2, timeout=20) as resp2:
        fixtures = json.loads(resp2.read().decode("utf-8"))

    print(f"    -> FPL: {len(bootstrap.get('teams', []))} teams, {len(bootstrap.get('elements', []))} players, {len(fixtures)} fixtures.")
    return bootstrap, fixtures

