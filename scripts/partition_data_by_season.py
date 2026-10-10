"""
scripts/partition_data_by_season.py
Partition all collected raw EPL datasets into seasonal and group-specific directories:
data/<season>/<group>/raw/

Requirements:
- Scope: 5 EPL seasons (2021_2022 to 2025_2026)
- 7 Entity Groups:
  01_club_attributes
  02_player_attributes
  03_match_context
  04_match_performance
  05_player_match_stats
  06_league_standings
  07_betting_odds
- Preserve all original columns intact ("chưa cần tách cột")
"""

import os
import shutil
import json
import pandas as pd

ROOT_DIR = r"z:\Desktop\Bet"
RAW_DIR = os.path.join(ROOT_DIR, "raw")
DATA_DIR = os.path.join(ROOT_DIR, "data")

SEASONS = [
    "2021_2022",
    "2022_2023",
    "2023_2024",
    "2024_2025",
    "2025_2026",
]

GROUPS = [
    "01_club_attributes",
    "02_player_attributes",
    "03_match_context",
    "04_match_performance",
    "05_player_match_stats",
    "06_league_standings",
    "07_betting_odds",
]


def audit_df(df: pd.DataFrame, file_name: str, group: str, season: str):
    """Generate metadata summary for a dataframe."""
    total_cells = df.shape[0] * df.shape[1] if df.shape[0] > 0 else 0
    null_cells = int(df.isnull().sum().sum())
    null_pct = round((null_cells / total_cells * 100), 2) if total_cells > 0 else 0.0

    # Top missing columns
    null_by_col = df.isnull().sum()
    cols_with_null = null_by_col[null_by_col > 0].sort_values(ascending=False)
    top_missing = {
        col: {
            "missing_count": int(cnt),
            "missing_pct": round(cnt / len(df) * 100, 2)
        }
        for col, cnt in cols_with_null.head(5).items()
    }

    return {
        "file_name": file_name,
        "group": group,
        "season": season,
        "rows": len(df),
        "columns_count": len(df.columns),
        "columns": df.columns.tolist(),
        "null_cells": null_cells,
        "null_pct": null_pct,
        "top_missing_columns": top_missing,
    }


def main():
    print("=" * 60)
    print("STARTING DATA PARTITIONING BY SEASON & ENTITY GROUP")
    print("=" * 60)

    # 1. Load multi-season master datasets into memory
    print("\n[1/4] Loading master multi-season files from raw/...")
    
    # Transfermarkt
    print(" - Loading Transfermarkt master datasets...")
    tm_games = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_games_1900.csv"))
    tm_lineups = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_lineups.csv"))
    tm_appearances = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_appearances.csv"))
    tm_players = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_players.csv"))
    tm_clubs = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_clubs.csv"))
    tm_val = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_player_valuations.csv"))

    # FBref & FotMob
    print(" - Loading FBref and FotMob master datasets...")
    fbref = pd.read_csv(os.path.join(RAW_DIR, "fbref", "fbref_epl_team_advanced_match_stats.csv"))
    fotmob = pd.read_csv(os.path.join(RAW_DIR, "sofascore", "fotmob_epl_47_details.csv"), low_memory=False)
    fotmob["_match_dt"] = pd.to_datetime(fotmob["match_time_utc"], format="mixed")

    # FotMob Team Stats (crawled advanced stats: tackles, passes, duels, etc.)
    fotmob_ts_path = os.path.join(RAW_DIR, "sofascore", "fotmob_team_match_stats.csv")
    fotmob_team_stats = None
    if os.path.exists(fotmob_ts_path):
        fotmob_team_stats = pd.read_csv(fotmob_ts_path)
        fotmob_team_stats["_match_dt"] = pd.to_datetime(fotmob_team_stats["match_time_utc"], format="mixed")

    # Open-Meteo Weather
    print(" - Loading Open-Meteo weather master dataset...")
    weather = pd.read_csv(os.path.join(RAW_DIR, "open_meteo", "epl_weather_1900_matches.csv"))

    # Understat
    print(" - Loading Understat master datasets...")
    u_matches = pd.read_csv(os.path.join(RAW_DIR, "understat", "understat_matches_1900.csv"))
    u_players = pd.read_csv(os.path.join(RAW_DIR, "understat", "understat_players_5seasons.csv"))
    u_teams = pd.read_csv(os.path.join(RAW_DIR, "understat", "understat_teams_5seasons.csv"))

    # 2. Iterate each season and partition
    print("\n[2/4] Partitioning files into data/<season>/<group>/raw/ ...")
    audit_records = []

    for season in SEASONS:
        print(f"\n>>> Processing Season: {season} <<<")
        start_year = int(season.split("_")[0])
        end_year = int(season.split("_")[1])

        # Create 7 group folders for this season
        group_raw_dirs = {}
        for grp in GROUPS:
            g_dir = os.path.join(DATA_DIR, season, grp, "raw")
            os.makedirs(g_dir, exist_ok=True)
            group_raw_dirs[grp] = g_dir

        # Helper to save and audit
        def save_and_record(df: pd.DataFrame, file_name: str, target_groups: list):
            for grp in target_groups:
                out_path = os.path.join(group_raw_dirs[grp], file_name)
                df.to_csv(out_path, index=False)
                rec = audit_df(df, file_name, grp, season)
                rec["file_size_kb"] = round(os.path.getsize(out_path) / 1024, 1)
                audit_records.append(rec)

        # -------------------------------------------------------------------------
        # A. Transfermarkt Datasets
        # -------------------------------------------------------------------------
        # Games (Matches)
        sub_tm_games = tm_games[tm_games["season"] == start_year].copy()
        season_game_ids = set(sub_tm_games["game_id"])
        
        # Clubs: 20 clubs participating in this season's games
        season_club_ids = set(sub_tm_games["home_club_id"]).union(set(sub_tm_games["away_club_id"]))
        sub_tm_clubs = tm_clubs[tm_clubs["club_id"].isin(season_club_ids)].copy()
        
        # Lineups & Appearances: matches in this season
        sub_tm_lineups = tm_lineups[tm_lineups["game_id"].isin(season_game_ids)].copy()
        sub_tm_app = tm_appearances[tm_appearances["game_id"].isin(season_game_ids)].copy()
        
        # Players: active players in this season's lineups/appearances
        season_player_ids = set(sub_tm_lineups["player_id"]).union(set(sub_tm_app["player_id"]))
        sub_tm_players = tm_players[tm_players["player_id"].isin(season_player_ids)].copy()
        
        # Valuations: market valuations up to season end for active players (enables Point-in-Time as-of join without data leakage or pre-season gaps)
        sub_tm_val = tm_val[
            (tm_val["player_id"].isin(season_player_ids)) & (tm_val["date"] <= f"{end_year}-06-30")
        ].copy()

        # Save TM datasets to appropriate groups
        # Clubs -> 01_club_attributes
        save_and_record(sub_tm_clubs, f"transfermarkt_clubs_{season}.csv", ["01_club_attributes"])

        # Players & Valuations -> 02_player_attributes
        save_and_record(sub_tm_players, f"transfermarkt_players_{season}.csv", ["02_player_attributes"])
        save_and_record(sub_tm_val, f"transfermarkt_player_valuations_{season}.csv", ["02_player_attributes"])

        # Games & Lineups -> 03_match_context (and Games -> 04_match_performance)
        save_and_record(sub_tm_games, f"transfermarkt_games_{season}.csv", ["03_match_context", "04_match_performance"])
        save_and_record(sub_tm_lineups, f"transfermarkt_lineups_{season}.csv", ["03_match_context"])

        # Appearances -> 05_player_match_stats
        save_and_record(sub_tm_app, f"transfermarkt_appearances_{season}.csv", ["05_player_match_stats"])

        # -------------------------------------------------------------------------
        # B. Football-Data.co.uk Datasets
        # -------------------------------------------------------------------------
        fd_src = os.path.join(RAW_DIR, "football_data_co_uk", f"epl_{season}.csv")
        if os.path.exists(fd_src):
            sub_fd = pd.read_csv(fd_src)
            # Match performance (goals, shots, cards, corners) -> 04_match_performance
            # Betting odds (Bet365, Pinnacle, AH, O/U) -> 07_betting_odds
            save_and_record(sub_fd, f"football_data_{season}.csv", ["04_match_performance", "07_betting_odds"])

        # -------------------------------------------------------------------------
        # C. Understat Datasets
        # -------------------------------------------------------------------------
        sub_umatches = u_matches[u_matches["season"] == season].copy()
        sub_uplayers = u_players[u_players["season"] == season].copy()
        sub_uteams = u_teams[u_teams["season"] == season].copy()

        # Matches -> 04_match_performance
        save_and_record(sub_umatches, f"understat_matches_{season}.csv", ["04_match_performance"])

        # Players -> 02_player_attributes
        save_and_record(sub_uplayers, f"understat_players_{season}.csv", ["02_player_attributes"])

        # Teams -> 01_club_attributes & 06_league_standings
        save_and_record(sub_uteams, f"understat_teams_{season}.csv", ["01_club_attributes", "06_league_standings"])

        # Optional vaastav Understat mirror (if exists)
        vaastav_u = os.path.join(RAW_DIR, "understat", f"vaastav_understat_player_{season}.csv")
        if os.path.exists(vaastav_u):
            sub_vu = pd.read_csv(vaastav_u)
            save_and_record(sub_vu, f"vaastav_understat_player_{season}.csv", ["02_player_attributes"])

        # -------------------------------------------------------------------------
        # D. FPL API Datasets
        # -------------------------------------------------------------------------
        # Cleaned Players -> 02_player_attributes
        fpl_cp_src = os.path.join(RAW_DIR, "fpl_api", f"fpl_cleaned_players_{season}.csv")
        if os.path.exists(fpl_cp_src):
            sub_fpl_cp = pd.read_csv(fpl_cp_src)
            save_and_record(sub_fpl_cp, f"fpl_cleaned_players_{season}.csv", ["02_player_attributes"])

        # Fixtures -> 03_match_context
        fpl_fix_src = os.path.join(RAW_DIR, "fpl_api", f"fpl_fixtures_{season}.csv")
        if os.path.exists(fpl_fix_src):
            sub_fpl_fix = pd.read_csv(fpl_fix_src)
            save_and_record(sub_fpl_fix, f"fpl_fixtures_{season}.csv", ["03_match_context"])

        # Teams -> 01_club_attributes & 06_league_standings
        fpl_t_src = os.path.join(RAW_DIR, "fpl_api", f"fpl_teams_{season}.csv")
        if os.path.exists(fpl_t_src):
            sub_fpl_t = pd.read_csv(fpl_t_src)
            save_and_record(sub_fpl_t, f"fpl_teams_{season}.csv", ["01_club_attributes", "06_league_standings"])

        # Merged Gameweek -> 05_player_match_stats
        fpl_gw_src = os.path.join(RAW_DIR, "fpl_api", f"fpl_merged_gw_{season}.csv")
        if os.path.exists(fpl_gw_src):
            sub_fpl_gw = pd.read_csv(fpl_gw_src)
            save_and_record(sub_fpl_gw, f"fpl_merged_gw_{season}.csv", ["05_player_match_stats"])

        # -------------------------------------------------------------------------
        # E. Open-Meteo Weather Dataset
        # -------------------------------------------------------------------------
        sub_weather = weather[weather["season"] == season].copy()
        save_and_record(sub_weather, f"open_meteo_weather_{season}.csv", ["03_match_context"])

        # -------------------------------------------------------------------------
        # F. FBref Advanced Team Stats
        # -------------------------------------------------------------------------
        sub_fbref = fbref[fbref["Season_End_Year"] == end_year].copy()
        if len(sub_fbref) > 0:
            # Contains Formations for 03_match_context and Advanced stats for 04_match_performance
            save_and_record(sub_fbref, f"fbref_team_advanced_{season}.csv", ["03_match_context", "04_match_performance"])

        # -------------------------------------------------------------------------
        # G. FotMob / Sofascore Event Details (Filtered strictly by match datetime)
        # -------------------------------------------------------------------------
        sub_fotmob = fotmob[
            (fotmob["_match_dt"] >= f"{start_year}-07-01") & 
            (fotmob["_match_dt"] <= f"{end_year}-06-30")
        ].drop(columns=["_match_dt"]).copy()
        if len(sub_fotmob) > 0:
            save_and_record(sub_fotmob, f"fotmob_details_{season}.csv", ["05_player_match_stats"])

        # FotMob Team Stats -> 04_match_performance
        if fotmob_team_stats is not None:
            sub_fts = fotmob_team_stats[
                (fotmob_team_stats["_match_dt"] >= f"{start_year}-07-01") & 
                (fotmob_team_stats["_match_dt"] <= f"{end_year}-06-30")
            ].drop(columns=["_match_dt"]).copy()
            if len(sub_fts) > 0:
                save_and_record(sub_fts, f"fotmob_team_stats_{season}.csv", ["04_match_performance"])

        print(f" -> Completed Season {season}: all target raw folders populated.")

    # 3. Export audit metadata report
    print("\n[3/4] Generating audit report...")
    audit_file = os.path.join(DATA_DIR, "dataset_audit_report.json")
    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(audit_records, f, indent=2, ensure_ascii=False)
    print(f" -> Saved audit report to {audit_file}")

    # 4. Verification Check
    print("\n[4/4] Verification check across all 35 directories...")
    total_files_created = len(audit_records)
    print(f"Total partitioned CSV instances created: {total_files_created}")
    
    # Summary by group and season
    summary_df = pd.DataFrame(audit_records)
    pivot = summary_df.pivot_table(index="group", columns="season", values="file_name", aggfunc="count", fill_value=0)
    print("\nFile count per group and season:")
    print(pivot)

    print("\nSUCCESS: All datasets successfully partitioned and verified!")


if __name__ == "__main__":
    main()

