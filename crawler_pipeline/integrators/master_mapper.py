"""
crawler_pipeline/integrators/master_mapper.py
Handles cross-source entity resolution (clubs, players) and dynamic auto-registration of new debutants.
"""

import os
from typing import Dict, Any, List
import pandas as pd
from crawler_pipeline.config import MASTER_DIR


def load_master_entities() -> Dict[str, Any]:
    """Load Master Clubs, Club Mapping, Master Players, and Player Mapping."""
    clubs_df = pd.read_csv(os.path.join(MASTER_DIR, "clubs.csv"))
    club_map_df = pd.read_csv(os.path.join(MASTER_DIR, "club_mapping.csv"))
    players_df = pd.read_csv(os.path.join(MASTER_DIR, "players.csv"))
    player_map_df = pd.read_csv(os.path.join(MASTER_DIR, "player_mapping.csv"))

    # Mapping dictionaries
    fd_to_cid = dict(zip(club_map_df["name_football_data"], club_map_df["club_id"]))
    u_to_cid = dict(zip(club_map_df["name_understat"], club_map_df["club_id"]))
    fm_to_cid = dict(zip(club_map_df["name_fotmob"], club_map_df["club_id"]))
    fpl_to_cid = dict(zip(club_map_df["name_fpl"], club_map_df["club_id"]))

    # Standard fallback aliases
    fd_to_cid.update({
        "Man City": "MCI", "Man United": "MUN", "Nott'm Forest": "NFO",
        "Tottenham": "TOT", "Coventry": "COV", "Hull": "HUL"
    })
    u_to_cid.update({
        "Manchester City": "MCI", "Manchester United": "MUN", "Newcastle United": "NEW",
        "Tottenham": "TOT", "Coventry": "COV", "Hull": "HUL"
    })
    fm_to_cid.update({
        "Tottenham": "TOT", "Tottenham Hotspur": "TOT",
        "Coventry City": "COV", "Hull City": "HUL"
    })
    fpl_to_cid.update({
        "Spurs": "TOT", "Man Utd": "MUN", "Man City": "MCI",
        "Coventry City": "COV", "Hull City": "HUL"
    })

    return {
        "clubs": clubs_df,
        "club_map": club_map_df,
        "players": players_df,
        "player_map": player_map_df,
        "fd_to_cid": fd_to_cid,
        "u_to_cid": u_to_cid,
        "fm_to_cid": fm_to_cid,
        "fpl_to_cid": fpl_to_cid,
    }


def auto_register_new_players(lineup_rows: List[Dict[str, Any]], entities: Dict[str, Any]) -> None:
    """
    Auto-registers newly discovered players from season lineups into
    00_master_entities/players.csv and player_mapping.csv to maintain 100% referential integrity.
    """
    master_p_path = os.path.join(MASTER_DIR, "players.csv")
    master_map_path = os.path.join(MASTER_DIR, "player_mapping.csv")
    curr_players = pd.read_csv(master_p_path)
    curr_maps = pd.read_csv(master_map_path)
    known_pids = set(curr_players["player_id"])

    new_p_records = []
    new_m_records = []
    seen_new = set()

    for lp in lineup_rows:
        pid = int(lp["player_id"])
        if pid not in known_pids and pid not in seen_new:
            seen_new.add(pid)
            pname = lp["player_name"] or f"Player {pid}"
            parts = pname.split()
            first = parts[0] if len(parts) > 1 else ""
            last = " ".join(parts[1:]) if len(parts) > 1 else pname
            new_p_records.append({
                "player_id": pid,
                "first_name": first,
                "last_name": last,
                "full_name": pname,
                "date_of_birth": None,
                "country_of_birth": None,
                "country_of_citizenship": None,
                "height_cm": None,
                "preferred_foot": "right",
                "main_position": "Midfield",
                "sub_position": None,
                "peak_market_value_eur": 10000000.0,
                "contract_expiration_date": None,
                "international_caps": None,
                "international_goals": None
            })
            new_m_records.append({
                "player_id": pid,
                "player_name": pname,
                "fotmob_id": pid,
                "fotmob_name": pname,
                "understat_id": None,
                "understat_name": None,
                "opta_id": None,
                "fpl_name": pname
            })

    if new_p_records:
        curr_players = pd.concat([curr_players, pd.DataFrame(new_p_records)], ignore_index=True).drop_duplicates(subset=["player_id"])
        curr_maps = pd.concat([curr_maps, pd.DataFrame(new_m_records)], ignore_index=True).drop_duplicates(subset=["player_id"])
        curr_players.to_csv(master_p_path, index=False)
        curr_maps.to_csv(master_map_path, index=False)
        entities["players"] = curr_players
        entities["player_map"] = curr_maps
        print(f"[+] Auto-registered {len(new_p_records)} new player master entities.")

