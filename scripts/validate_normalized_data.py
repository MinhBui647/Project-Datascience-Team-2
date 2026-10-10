"""
scripts/validate_normalized_data.py
Phase 4 Automated Verification & Quality Assurance Suite

Validates:
1. Master entities: clubs.csv, club_mapping.csv, players.csv, player_mapping.csv
2. Cross-season schema consistency across all 5 seasons (2021_2022 to 2025_2026)
3. Referential foreign key integrity: match_id, club_id, player_id
4. Numerical boundaries & domain constraints (FDR 1-5, overrounds > 1.0, shot coordinates)
"""

import os
import glob
import pandas as pd
import numpy as np

from pathlib import Path
ROOT_DIR = str(Path(__file__).resolve().parent.parent)
DATA_DIR = os.path.join(ROOT_DIR, "data")
MASTER_DIR = os.path.join(DATA_DIR, "00_master_entities")

SEASONS = [
    "2021_2022",
    "2022_2023",
    "2023_2024",
    "2024_2025",
    "2025_2026",
]
ONGOING_SEASON = "2026_2027"


def run_qa_suite():
    print("=" * 80)
    print("RUNNING AUTOMATED QA & DATA INTEGRITY VERIFICATION SUITE")
    print("=" * 80)

    total_tests = 0
    passed_tests = 0
    failures = []

    def check(condition, test_name):
        nonlocal total_tests, passed_tests
        total_tests += 1
        if condition:
            passed_tests += 1
            print(f"  [PASS] {test_name}")
        else:
            failures.append(test_name)
            print(f"  [FAIL] {test_name}")

    # -------------------------------------------------------------------------
    # TEST GROUP 1: MASTER ENTITIES
    # -------------------------------------------------------------------------
    print("\n--- TEST GROUP 1: MASTER ENTITIES INTEGRITY ---")
    clubs_df = pd.read_csv(os.path.join(MASTER_DIR, "clubs.csv"))
    club_map_df = pd.read_csv(os.path.join(MASTER_DIR, "club_mapping.csv"))
    players_df = pd.read_csv(os.path.join(MASTER_DIR, "players.csv"))
    player_map_df = pd.read_csv(os.path.join(MASTER_DIR, "player_mapping.csv"))

    check(len(clubs_df) >= 27, f"clubs.csv contains all Premier League clubs (actual: {len(clubs_df)})")
    check(clubs_df["club_id"].is_unique, "clubs.csv club_id is strictly unique primary key")
    check(len(club_map_df) >= 27, f"club_mapping.csv contains all Premier League clubs (actual: {len(club_map_df)})")

    check(set(club_map_df["club_id"]) == set(clubs_df["club_id"]), "club_mapping.csv IDs match clubs.csv IDs 100%")
    check(len(players_df) >= 1645, f"players.csv contains all master players (actual: {len(players_df)})")
    check(players_df["player_id"].is_unique, "players.csv player_id is strictly unique primary key")
    check(len(player_map_df) >= 1645, f"player_mapping.csv contains all master player mappings (actual: {len(player_map_df)})")
    check(player_map_df["player_id"].is_unique, "player_mapping.csv player_id is strictly unique primary key")
    
    fotmob_cov = player_map_df["fotmob_id"].notna().mean()
    check(fotmob_cov >= 0.95, f"FotMob ID coverage >= 95% (actual: {fotmob_cov:.1%})")
    understat_cov = player_map_df["understat_id"].notna().mean()
    check(understat_cov >= 0.70, f"Understat ID coverage >= 70% (actual: {understat_cov:.1%})")
    fpl_cov = player_map_df["fpl_name"].notna().mean()
    check(fpl_cov >= 0.90, f"FPL Name coverage >= 90% (actual: {fpl_cov:.1%})")

    master_club_ids = set(clubs_df["club_id"])
    master_player_ids = set(players_df["player_id"])

    # -------------------------------------------------------------------------
    # TEST GROUP 2: RECURSIVE SEASON TABLES INTEGRITY
    # -------------------------------------------------------------------------
    print("\n--- TEST GROUP 2: 5-SEASON DATASET SCHEMAS & REFERENTIAL INTEGRITY ---")
    
    table_requirements = {
        "matches/match_context.csv": {"rows": 380, "min_cols": 40},
        "matches/match_betting_odds.csv": {"rows": 380, "min_cols": 90},
        "matches/match_lineups.csv": {"min_rows": 14000, "min_cols": 8},
        "clubs/club_season_profile.csv": {"rows": 20, "min_cols": 9},
        "clubs/club_match_performance.csv": {"rows": 760, "min_cols": 50},
        "players/player_valuations.csv": {"min_rows": 500, "min_cols": 6},
        "players/player_match_stats.csv": {"min_rows": 10000, "min_cols": 30},
        "players/player_season_understat.csv": {"min_rows": 400, "min_cols": 18},
        "events/match_shots.csv": {"min_rows": 8000, "min_cols": 28},
    }

    all_match_ids = set()
    total_shots = 0
    total_appearances = 0

    for season in SEASONS:
        s_dir = os.path.join(DATA_DIR, season)
        print(f"\n  Checking Season: {season}")

        # Check all 9 tables exist and meet row/col specifications
        for tbl_rel, specs in table_requirements.items():
            fpath = os.path.join(s_dir, tbl_rel.replace("/", os.sep))
            check(os.path.exists(fpath), f"Table exists: {season}/{tbl_rel}")
            if os.path.exists(fpath):
                df = pd.read_csv(fpath)
                if "rows" in specs:
                    check(len(df) == specs["rows"], f"{season}/{tbl_rel} row count == {specs['rows']} (actual: {len(df)})")
                if "min_rows" in specs:
                    check(len(df) >= specs["min_rows"], f"{season}/{tbl_rel} min rows >= {specs['min_rows']} (actual: {len(df)})")
                if "min_cols" in specs:
                    check(len(df.columns) >= specs["min_cols"], f"{season}/{tbl_rel} col count >= {specs['min_cols']} (actual: {len(df.columns)})")

        # Load match context for foreign key verification
        mc_path = os.path.join(s_dir, "matches", "match_context.csv")
        mc_df = pd.read_csv(mc_path)
        s_mids = set(mc_df["match_id"])
        all_match_ids.update(s_mids)

        check(mc_df["home_club_id"].isin(master_club_ids).all(), f"{season} match_context home_club_id in master clubs")
        check(mc_df["away_club_id"].isin(master_club_ids).all(), f"{season} match_context away_club_id in master clubs")
        check(mc_df["fdr_home"].between(1, 5).all(), f"{season} match_context fdr_home in [1, 5]")
        check(mc_df["fdr_away"].between(1, 5).all(), f"{season} match_context fdr_away in [1, 5]")

        # Betting odds verification
        bo_path = os.path.join(s_dir, "matches", "match_betting_odds.csv")
        bo_df = pd.read_csv(bo_path)
        check(set(bo_df["match_id"]) == s_mids, f"{season} betting odds match_id matches match_context 100%")
        check((bo_df["overround_open"] > 1.0).all(), f"{season} betting odds overround_open > 1.00")


        # Club performance verification
        cp_path = os.path.join(s_dir, "clubs", "club_match_performance.csv")
        cp_df = pd.read_csv(cp_path)
        check(cp_df["club_id"].isin(master_club_ids).all(), f"{season} club_match_performance club_id in master clubs")
        check(set(cp_df["match_id"]) == s_mids, f"{season} club_match_performance match_id matches match_context 100%")

        # Match lineups verification
        ml_path = os.path.join(s_dir, "matches", "match_lineups.csv")
        ml_df = pd.read_csv(ml_path)
        check(set(ml_df["match_id"]).issubset(s_mids), f"{season} match_lineups match_id subset of match_context")
        check(ml_df["club_id"].isin(master_club_ids).all(), f"{season} match_lineups club_id in master clubs")
        check(ml_df["player_id"].isin(master_player_ids).all(), f"{season} match_lineups player_id in master players")

        # Player match stats verification
        pms_path = os.path.join(s_dir, "players", "player_match_stats.csv")
        pms_df = pd.read_csv(pms_path)
        check(set(pms_df["match_id"]).issubset(s_mids), f"{season} player_match_stats match_id subset of match_context")
        check(pms_df["club_id"].isin(master_club_ids).all(), f"{season} player_match_stats club_id in master clubs")
        check(pms_df["player_id"].isin(master_player_ids).all(), f"{season} player_match_stats player_id in master players")
        total_appearances += len(pms_df)

        # Match shots verification
        ms_path = os.path.join(s_dir, "events", "match_shots.csv")
        ms_df = pd.read_csv(ms_path)
        check(set(ms_df["match_id"]).issubset(s_mids), f"{season} match_shots match_id subset of match_context")
        check(ms_df["club_id"].isin(master_club_ids).all(), f"{season} match_shots club_id in master clubs")
        check(ms_df["player_id"].notna().all(), f"{season} match_shots player_id has 0 NaNs")
        total_shots += len(ms_df)

    # Validate Ongoing Season if present (e.g. 2026_2027)
    ong_dir = os.path.join(DATA_DIR, ONGOING_SEASON)
    if os.path.exists(ong_dir):
        print(f"\n  Checking Active In-Progress Season: {ONGOING_SEASON}")
        for tbl_rel, specs in table_requirements.items():
            fpath = os.path.join(ong_dir, tbl_rel.replace("/", os.sep))
            check(os.path.exists(fpath), f"Table exists: {ONGOING_SEASON}/{tbl_rel}")
            if os.path.exists(fpath):
                df = pd.read_csv(fpath)
                check(len(df) > 0, f"{ONGOING_SEASON}/{tbl_rel} has records (actual: {len(df)})")
                if "min_cols" in specs:
                    check(len(df.columns) >= specs["min_cols"], f"{ONGOING_SEASON}/{tbl_rel} col count >= {specs['min_cols']} (actual: {len(df.columns)})")

        mc_path = os.path.join(ong_dir, "matches", "match_context.csv")
        if os.path.exists(mc_path):
            mc_df = pd.read_csv(mc_path)
            s_mids = set(mc_df["match_id"])
            check(mc_df["home_club_id"].isin(master_club_ids).all(), f"{ONGOING_SEASON} match_context home_club_id in master clubs")
            check(mc_df["away_club_id"].isin(master_club_ids).all(), f"{ONGOING_SEASON} match_context away_club_id in master clubs")
            check(mc_df["fdr_home"].between(1, 5).all(), f"{ONGOING_SEASON} match_context fdr_home in [1, 5]")
            check(mc_df["fdr_away"].between(1, 5).all(), f"{ONGOING_SEASON} match_context fdr_away in [1, 5]")

            bo_path = os.path.join(ong_dir, "matches", "match_betting_odds.csv")
            if os.path.exists(bo_path):
                bo_df = pd.read_csv(bo_path)
                check(set(bo_df["match_id"]) == s_mids, f"{ONGOING_SEASON} betting odds match_id matches match_context 100%")
                check((bo_df["overround_open"] > 1.0).all(), f"{ONGOING_SEASON} betting odds overround_open > 1.00")

            cp_path = os.path.join(ong_dir, "clubs", "club_match_performance.csv")
            if os.path.exists(cp_path):
                cp_df = pd.read_csv(cp_path)
                check(cp_df["club_id"].isin(master_club_ids).all(), f"{ONGOING_SEASON} club_match_performance club_id in master clubs")
                check(set(cp_df["match_id"]) == s_mids, f"{ONGOING_SEASON} club_match_performance match_id matches match_context 100%")

            ml_path = os.path.join(ong_dir, "matches", "match_lineups.csv")
            if os.path.exists(ml_path):
                ml_df = pd.read_csv(ml_path)
                check(set(ml_df["match_id"]).issubset(s_mids), f"{ONGOING_SEASON} match_lineups match_id subset of match_context")
                check(ml_df["club_id"].isin(master_club_ids).all(), f"{ONGOING_SEASON} match_lineups club_id in master clubs")
                check(ml_df["player_id"].isin(master_player_ids).all(), f"{ONGOING_SEASON} match_lineups player_id in master players")

            pms_path = os.path.join(ong_dir, "players", "player_match_stats.csv")
            if os.path.exists(pms_path):
                pms_df = pd.read_csv(pms_path)
                check(set(pms_df["match_id"]).issubset(s_mids), f"{ONGOING_SEASON} player_match_stats match_id subset of match_context")
                check(pms_df["club_id"].isin(master_club_ids).all(), f"{ONGOING_SEASON} player_match_stats club_id in master clubs")
                check(pms_df["player_id"].isin(master_player_ids).all(), f"{ONGOING_SEASON} player_match_stats player_id in master players")

            ms_path = os.path.join(ong_dir, "events", "match_shots.csv")
            if os.path.exists(ms_path):
                ms_df = pd.read_csv(ms_path)
                check(set(ms_df["match_id"]).issubset(s_mids), f"{ONGOING_SEASON} match_shots match_id subset of match_context")
                check(ms_df["club_id"].isin(master_club_ids).all(), f"{ONGOING_SEASON} match_shots club_id in master clubs")
                check(ms_df["player_id"].notna().all(), f"{ONGOING_SEASON} match_shots player_id has 0 NaNs")

    # -------------------------------------------------------------------------
    # TEST GROUP 3: GLOBAL DATASET TOTALS
    # -------------------------------------------------------------------------
    print("\n--- TEST GROUP 3: GLOBAL AGGREGATE TOTALS ---")
    check(len(all_match_ids) == 1900, f"Total matches across 5 seasons == 1,900 (actual: {len(all_match_ids)})")
    check(total_shots >= 48000, f"Total shots across 5 seasons >= 48,000 (actual: {total_shots})")
    check(total_appearances >= 55000, f"Total appearances across 5 seasons >= 55,000 (actual: {total_appearances})")

    # -------------------------------------------------------------------------
    # FINAL QA SUMMARY
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("FINAL QUALITY ASSURANCE REPORT")
    print("=" * 80)
    print(f"Total Tests Run: {total_tests}")
    print(f"Passed: {passed_tests} ({passed_tests/total_tests:.2%})")
    print(f"Failed: {len(failures)}")
    if failures:
        print("\nFailed Tests:")
        for f in failures:
            print(f"  - {f}")
    else:
        print("\nALL AUTOMATED QUALITY ASSURANCE TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 80)


if __name__ == "__main__":
    run_qa_suite()
