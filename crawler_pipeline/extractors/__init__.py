"""
crawler_pipeline.extractors
Individual source data extractors for Premier League APIs and web feeds.
"""

from .football_data_ext import fetch_football_data
from .understat_ext import fetch_understat_data
from .fotmob_ext import fetch_fotmob_matches, fetch_fotmob_match_detail
from .fpl_ext import fetch_fpl_data
from .weather_ext import fetch_weather

__all__ = [
    "fetch_football_data",
    "fetch_understat_data",
    "fetch_fotmob_matches",
    "fetch_fotmob_match_detail",
    "fetch_fpl_data",
    "fetch_weather",
]

