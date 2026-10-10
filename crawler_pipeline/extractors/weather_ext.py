"""
crawler_pipeline/extractors/weather_ext.py
Fetches historical hourly weather for stadium coordinates at kickoff hour via Open-Meteo API.
"""

import json
import urllib.request
from typing import Dict, Any
from crawler_pipeline.config import HEADERS, RAW_DIR

_WEATHER_CACHE = {}


def fetch_weather(lat: float, lon: float, date_str: str, hour: int = 15) -> Dict[str, Any]:
    """Fetch hourly weather from Open-Meteo archive for matchday with disk caching."""
    cache_dir = RAW_DIR / "open_meteo"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "weather_cache_2026_2027.json"

    global _WEATHER_CACHE
    if not _WEATHER_CACHE and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _WEATHER_CACHE = json.load(f)
        except Exception:
            _WEATHER_CACHE = {}

    key = f"{lat:.2f}_{lon:.2f}_{date_str}_{hour}"
    if key in _WEATHER_CACHE:
        return _WEATHER_CACHE[key]

    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat:.4f}&longitude={lon:.4f}&start_date={date_str}&end_date={date_str}"
        f"&hourly=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    res = {"temp": 15.0, "humidity": 70.0, "precipitation": 0.0, "wind": 12.0, "weather_code": 0}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            hourly = data.get("hourly", {})
            h_idx = min(hour, len(hourly.get("temperature_2m", [])) - 1)
            res = {
                "temp": hourly.get("temperature_2m", [None])[h_idx],
                "humidity": hourly.get("relative_humidity_2m", [None])[h_idx],
                "precipitation": hourly.get("precipitation", [0.0])[h_idx],
                "wind": hourly.get("wind_speed_10m", [None])[h_idx],
                "weather_code": hourly.get("weather_code", [0])[h_idx],
            }
    except Exception:
        pass

    _WEATHER_CACHE[key] = res
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(_WEATHER_CACHE, f, ensure_ascii=False)
    except Exception:
        pass

    return res

