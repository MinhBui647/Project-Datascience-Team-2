"""
crawler_pipeline.integrators
Entity resolution, master cross-mapping, and multi-source season dataset assembly.
"""

from .master_mapper import load_master_entities, auto_register_new_players
from .season_builder import build_season

__all__ = [
    "load_master_entities",
    "auto_register_new_players",
    "build_season",
]

