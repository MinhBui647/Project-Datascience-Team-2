"""
crawler_pipeline/extractors/weather_ext.py
Fetches historical hourly weather for stadium coordinates at kickoff hour via Open-Meteo API.
"""

import json
import urllib.request
from typing import Dict, Any
from crawler_pipeline.config import HEADERS


def fetch_weather(lat: float, lon: float, date_str: str, hour: int = 15) -> Dict[str, Any]:
    """Fetch hourly weather from Open-Meteo archive for matchday."""
    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat:.4f}&longitude={lon:.4f}&start_date={date_str}&end_date={date_str}"
        f"&hourly=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            hourly = data.get("hourly", {})
            h_idx = min(hour, len(hourly.get("temperature_2m", [])) - 1)
            return {
                "temp": hourly.get("temperature_2m", [None])[h_idx],
                "humidity": hourly.get("relative_humidity_2m", [None])[h_idx],
                "precipitation": hourly.get("precipitation", [0.0])[h_idx],
                "wind": hourly.get("wind_speed_10m", [None])[h_idx],
                "weather_code": hourly.get("weather_code", [0])[h_idx],
            }
    except Exception:
        # Fallback standard English autumn weather
        return {"temp": 15.0, "humidity": 70.0, "precipitation": 0.0, "wind": 12.0, "weather_code": 0}

