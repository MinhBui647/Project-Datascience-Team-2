"""
scripts/build_normalized_season_data.py
Build Phase 3 Fully Integrated Normalized CSV Datasets across all 5 EPL seasons (2021_2022 to 2025_2026):

Creates for each season in data/<season>/:
1. clubs/
   - club_season_profile.csv (20 clubs with FPL strength ratings)
   - club_match_performance.csv (760 rows = 380 matches x 2 teams with full FotMob advanced stats, crosses, long balls, passes by half, formation, FDR)
2. matches/
   - match_context.csv (380 matches with Understat xG & MC forecasts, weather, FotMob possession & big chances, tactical formations, league rank, FDR)
   - match_lineups.csv (~15,180 lineup player records with lineup_type, position, is_captain)
   - match_betting_odds.csv (380 matches with full bookmaker odds, closing odds, AH, O/U, overrounds, implied probs, odds drift)
3. players/
   - player_valuations.csv (Transfermarkt historical valuation timeline records)
   - player_match_stats.csv (~11,300 appearances enriched with FPL ICT, Opta expected metrics fpl_xg/xa/xgi/xgc, and shot aggregates shots_total/on_target/xg/xgot)
   - player_season_understat.csv (Understat player seasonal advanced playmaking metrics: xGChain, xGBuildup, npxG, key passes)
4. events/
   - match_shots.csv (~10,000 shots with 100% complete player information, pitch x/y, and goalmouth spatial coordinates)
"""

import os
import re
import glob
import json
import unicodedata
import pandas as pd
import numpy as np

ROOT_DIR = r"z:\Desktop\Bet"
RAW_DIR = os.path.join(ROOT_DIR, "raw")
DATA_DIR = os.path.join(ROOT_DIR, "data")
MASTER_DIR = os.path.join(DATA_DIR, "00_master_entities")

SEASONS = [
    "2021_2022",
    "2022_2023",
    "2023_2024",
    "2024_2025",
    "2025_2026",
]

SEASON_DATES = {
    "2021_2022": ("2021-07-01", "2022-06-30"),
    "2022_2023": ("2022-07-01", "2023-06-30"),
    "2023_2024": ("2023-07-01", "2024-06-30"),
    "2024_2025": ("2024-07-01", "2025-06-30"),
    "2025_2026": ("2025-07-01", "2026-06-30"),
}


def clean_name(val):
    """Normalize string for fuzzy player matching: lowercase, strip accents & punctuation."""
    if not isinstance(val, str):
        return ""
    val = "".join(c for c in unicodedata.normalize("NFD", val) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", val.lower().strip())


def parse_val_pct(val):
    """Parse string of format '393 (88%)' into tuple of (393.0, 88.0)."""
    if pd.isna(val):
        return np.nan, np.nan
    val_str = str(val).strip()
    m = re.match(r"(\d+)\s*\(([\d\.]+)%\)", val_str)
    if m:
        return float(m.group(1)), float(m.group(2))
    try:
        return float(val_str), np.nan
    except Exception:
        return np.nan, np.nan


def load_club_mapping():
    """Load club mapping dictionary."""
    mapping = pd.read_csv(os.path.join(MASTER_DIR, "club_mapping.csv"))
    fd_to_id = dict(zip(mapping["name_football_data"], mapping["club_id"]))
    tm_to_id = dict(zip(mapping["tm_club_id"], mapping["club_id"]))
    u_to_id = dict(zip(mapping["name_understat"], mapping["club_id"]))
    fpl_code_to_id = dict(zip(mapping["fpl_team_code"], mapping["club_id"]))
    fpl_name_to_id = dict(zip(mapping["name_fpl"], mapping["club_id"]))
    fm_name_to_id = dict(zip(mapping["name_fotmob"], mapping["club_id"]))
    # Common variations in FotMob match details
    fm_name_to_id["Tottenham"] = "TOT"
    fm_name_to_id["Brighton"] = "BHA"
    fm_name_to_id["Bournemouth"] = "BOU"
    fm_name_to_id["West Ham"] = "WHU"
    fm_name_to_id["Wolves"] = "WOL"
    return {
        "mapping_df": mapping,
        "fd": fd_to_id,
        "tm": tm_to_id,
        "u": u_to_id,
        "fpl_code": fpl_code_to_id,
        "fpl_name": fpl_name_to_id,
        "fm_name": fm_name_to_id,
    }


def compute_market_metrics(df):
    """Compute market overround, normalized implied probabilities, and odds drift."""
    if "AvgH" in df.columns:
        oh, od, oa = df["AvgH"], df["AvgD"], df["AvgA"]
    elif "B365H" in df.columns:
        oh, od, oa = df["B365H"], df["B365D"], df["B365A"]
    elif "PSH" in df.columns:
        oh, od, oa = df["PSH"], df["PSD"], df["PSA"]
    else:
        oh, od, oa = pd.Series(np.nan, index=df.index), pd.Series(np.nan, index=df.index), pd.Series(np.nan, index=df.index)

    sum_inv_open = (1.0 / oh) + (1.0 / od) + (1.0 / oa)
    df["overround_open"] = ((sum_inv_open - 1.0) * 100).round(2)
    df["prob_h_open"] = (((1.0 / oh) / sum_inv_open) * 100).round(2)
    df["prob_d_open"] = (((1.0 / od) / sum_inv_open) * 100).round(2)
    df["prob_a_open"] = (((1.0 / oa) / sum_inv_open) * 100).round(2)

    if "AvgCH" in df.columns:
        ch, cd, ca = df["AvgCH"], df["AvgCD"], df["AvgCA"]
    elif "B365CH" in df.columns:
        ch, cd, ca = df["B365CH"], df["B365CD"], df["B365CA"]
    elif "PSCH" in df.columns:
        ch, cd, ca = df["PSCH"], df["PSCD"], df["PSCA"]
    else:
        ch, cd, ca = pd.Series(np.nan, index=df.index), pd.Series(np.nan, index=df.index), pd.Series(np.nan, index=df.index)

    sum_inv_close = (1.0 / ch) + (1.0 / cd) + (1.0 / ca)
    df["overround_close"] = ((sum_inv_close - 1.0) * 100).round(2)
    df["prob_h_close"] = (((1.0 / ch) / sum_inv_close) * 100).round(2)
    df["prob_d_close"] = (((1.0 / cd) / sum_inv_close) * 100).round(2)
    df["prob_a_close"] = (((1.0 / ca) / sum_inv_close) * 100).round(2)

    df["odds_drift_h"] = (((ch - oh) / oh) * 100).round(2)
    df["odds_drift_d"] = (((cd - od) / od) * 100).round(2)
    df["odds_drift_a"] = (((ca - oa) / oa) * 100).round(2)
    return df


def enrich_player_appearances(app_df, fpl_df, shots_df, maps):
    """Enrich Transfermarkt appearances with FPL gameweek metrics and match shot aggregates."""
    fpl_df = fpl_df.copy()
    fpl_df["club_id"] = fpl_df["team"].map(maps["fpl_name"])
    fpl_played = fpl_df[fpl_df["minutes"] > 0].copy()
    fpl_played["c_name"] = fpl_played["name"].apply(clean_name)

    app_df = app_df.copy()
    app_df["gameweek"] = app_df["match_id"].str.extract(r"GW(\d+)")[0].astype(int)
    app_df["c_name"] = app_df["player_name"].apply(clean_name)

    fpl_metrics = [
        "total_points", "bonus", "bps", "ict_index", "influence",
        "creativity", "threat", "clean_sheets", "goals_conceded",
        "saves", "value", "xP", "transfers_balance", "selected",
        "expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded"
    ]
    for m in fpl_metrics:
        if m not in fpl_played.columns:
            fpl_played[m] = np.nan

    res_rows = []
    for (club, gw), g_app in app_df.groupby(["club_id", "gameweek"]):
        g_fpl = fpl_played[(fpl_played["club_id"] == club) & (fpl_played["GW"] == gw)]
        cand_list = g_fpl.to_dict("records")

        for _, tm_row in g_app.iterrows():
            tm_tokens = set(tm_row["c_name"].split())
            best_cand = None
            best_score = -1

            for cand in cand_list:
                cand_tokens = set(cand["c_name"].split())
                token_overlap = len(tm_tokens.intersection(cand_tokens))
                min_diff = abs(tm_row["minutes_played"] - cand["minutes"])

                score = 0
                if token_overlap > 0:
                    score += token_overlap * 10
                if tm_row["c_name"] in cand["c_name"] or cand["c_name"] in tm_row["c_name"]:
                    score += 15
                if min_diff == 0:
                    score += 20
                elif min_diff <= 5:
                    score += 10

                if score > best_score:
                    best_score = score
                    best_cand = cand

            r_dict = tm_row.to_dict()
            if best_cand is not None and best_score >= 20:
                for m in fpl_metrics:
                    r_dict[m] = best_cand.get(m)
                cand_list.remove(best_cand)
            else:
                for m in fpl_metrics:
                    r_dict[m] = np.nan
            res_rows.append(r_dict)

    enriched_df = pd.DataFrame(res_rows)
    rename_dict = {
        "total_points": "fpl_points",
        "bonus": "fpl_bonus",
        "bps": "fpl_bps",
        "value": "fpl_value",
        "xP": "fpl_expected_points",
        "selected": "fpl_selected_count",
        "expected_goals": "fpl_xg",
        "expected_assists": "fpl_xa",
        "expected_goal_involvements": "fpl_xgi",
        "expected_goals_conceded": "fpl_xgc",
    }
    enriched_df = enriched_df.rename(columns=rename_dict)

    # Aggregate shots for each player per match
    shots_df = shots_df.copy()
    shots_df["c_name"] = shots_df["player_name"].apply(clean_name)
    shots_agg = shots_df.groupby(["match_id", "c_name"]).agg(
        shots_total=("shot_id", "count"),
        shots_on_target=("is_on_target", lambda x: (x == True).sum()),
        shots_inside_box=("is_from_inside_box", lambda x: (x == True).sum()),
        shots_xg=("expected_goals", "sum"),
        shots_xgot=("expected_goals_on_target", "sum")
    ).reset_index()

    enriched_df = enriched_df.merge(shots_agg, on=["match_id", "c_name"], how="left")
    for c in ["shots_total", "shots_on_target", "shots_inside_box"]:
        enriched_df[c] = enriched_df[c].fillna(0).astype(int)
    for c in ["shots_xg", "shots_xgot"]:
        enriched_df[c] = enriched_df[c].fillna(0.0).round(4)

    keep_cols = [
        "match_id", "player_id", "club_id", "player_name", "minutes_played",
        "goals", "assists", "yellow_cards", "red_cards",
        "shots_total", "shots_on_target", "shots_inside_box", "shots_xg", "shots_xgot",
        "fpl_points", "fpl_bonus", "fpl_bps", "ict_index", "influence",
        "creativity", "threat", "clean_sheets", "goals_conceded",
        "saves", "fpl_xg", "fpl_xa", "fpl_xgi", "fpl_xgc",
        "fpl_value", "fpl_expected_points", "transfers_balance", "fpl_selected_count"
    ]
    return enriched_df[keep_cols]


def main():
    print("=" * 80)
    print("STARTING FULL ADVANCED INTEGRATION: MATCHES, CLUBS, PLAYERS & EVENTS")
    print("=" * 80)

    maps = load_club_mapping()
    fd_map = maps["fd"]
    tm_map = maps["tm"]
    u_map = maps["u"]
    fm_map = maps["fm_name"]
    fpl_name_map = maps["fpl_name"]

    print("\n[1/4] Loading global source files & preparing lookups...")
    tm_games_all = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_games_1900.csv"))
    tm_lineups_all = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_lineups.csv"))
    tm_app_all = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_appearances.csv"))
    tm_val_all = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_player_valuations.csv"))
    u_matches_all = pd.read_csv(os.path.join(RAW_DIR, "understat", "understat_matches_1900.csv"))
    weather_all = pd.read_csv(os.path.join(RAW_DIR, "open_meteo", "epl_weather_1900_matches.csv"))

    # FPL Fixture Difficulty Rating (FDR) 1900 matches lookup
    fpl_teams_5s = pd.read_csv(os.path.join(RAW_DIR, "fpl_api", "fpl_teams_5seasons.csv"))
    fpl_teams_5s["club_id"] = fpl_teams_5s["name"].map(fpl_name_map)
    fpl_team_id_map = dict(zip(zip(fpl_teams_5s["season"], fpl_teams_5s["id"]), fpl_teams_5s["club_id"]))

    fpl_fix_all = pd.read_csv(os.path.join(RAW_DIR, "fpl_api", "fpl_fixtures_1900.csv"))
    fpl_fix_all["h_id"] = fpl_fix_all.apply(lambda r: fpl_team_id_map.get((r["season"], r["team_h"])), axis=1)
    fpl_fix_all["a_id"] = fpl_fix_all.apply(lambda r: fpl_team_id_map.get((r["season"], r["team_a"])), axis=1)
    fdr_lookup = dict(zip(
        zip(fpl_fix_all["season"], fpl_fix_all["h_id"], fpl_fix_all["a_id"]),
        zip(fpl_fix_all["team_h_difficulty"], fpl_fix_all["team_a_difficulty"])
    ))

    # FotMob detailed shots (100% complete across 1900 matches)
    fotmob_shots_all = pd.read_csv(os.path.join(RAW_DIR, "sofascore", "fotmob_epl_47_details.csv"), low_memory=False)
    fotmob_shots_all["_match_dt"] = pd.to_datetime(fotmob_shots_all["match_time_utc"], format="mixed")

    # FotMob team match stats (all 1900 matches)
    fm_ts_path = os.path.join(RAW_DIR, "sofascore", "fotmob_team_match_stats.csv")
    fm_ts_all = pd.read_csv(fm_ts_path)
    fm_ts_all["_match_dt"] = pd.to_datetime(fm_ts_all["match_time_utc"], format="mixed")
    fm_ts_all["h_id"] = fm_ts_all["home_team"].map(fm_map)
    fm_ts_all["a_id"] = fm_ts_all["away_team"].map(fm_map)

    for season in SEASONS:
        print(f"\n=======================================================")
        print(f">>> PROCESSING SEASON: {season} <<<")
        print(f"=======================================================")
        start_year = int(season.split("_")[0])
        end_year = int(season.split("_")[1])
        d_start, d_end = SEASON_DATES[season]

        s_clubs_dir = os.path.join(DATA_DIR, season, "clubs")
        s_matches_dir = os.path.join(DATA_DIR, season, "matches")
        s_players_dir = os.path.join(DATA_DIR, season, "players")
        s_events_dir = os.path.join(DATA_DIR, season, "events")

        for d in [s_clubs_dir, s_matches_dir, s_players_dir, s_events_dir]:
            os.makedirs(d, exist_ok=True)

        # ---------------------------------------------------------------------
        # A. LOAD & MERGE MATCH LEVEL SOURCES
        # ---------------------------------------------------------------------
        fd_path = os.path.join(RAW_DIR, "football_data_co_uk", f"epl_{season}.csv")
        fd_df = pd.read_csv(fd_path)
        fd_df["home_club_id"] = fd_df["HomeTeam"].map(fd_map)
        fd_df["away_club_id"] = fd_df["AwayTeam"].map(fd_map)

        tm_games_s = tm_games_all[tm_games_all["season"] == start_year].copy()
        tm_games_s["home_club_id"] = tm_games_s["home_club_id"].map(tm_map)
        tm_games_s["away_club_id"] = tm_games_s["away_club_id"].map(tm_map)

        u_s = u_matches_all[u_matches_all["season"] == season].copy()
        u_s["home_club_id"] = u_s["home_team"].map(u_map)
        u_s["away_club_id"] = u_s["away_team"].map(u_map)

        we_s = weather_all[weather_all["season"] == season].copy()
        we_s["home_club_id"] = we_s["home_team"].map(u_map)
        we_s["away_club_id"] = we_s["away_team"].map(u_map)

        fm_ts_s = fm_ts_all[(fm_ts_all["_match_dt"] >= d_start) & (fm_ts_all["_match_dt"] <= d_end)].copy()
        fm_ts_s = fm_ts_s.drop_duplicates(subset=["h_id", "a_id"])

        matches_df = fd_df.copy()
        matches_df["season"] = season

        # Merge TM games (including formations, ranks, attendance, referee, stadium)
        matches_df = matches_df.merge(
            tm_games_s[[
                "home_club_id", "away_club_id", "game_id", "round", "attendance", "referee",
                "home_club_manager_name", "away_club_manager_name", "stadium",
                "home_club_formation", "away_club_formation", "home_club_position", "away_club_position"
            ]],
            on=["home_club_id", "away_club_id"],
            how="left"
        )

        # Merge Understat (including Monte Carlo simulation forecasts)
        matches_df = matches_df.merge(
            u_s[["home_club_id", "away_club_id", "understat_id", "home_xg", "away_xg", "forecast_win", "forecast_draw", "forecast_loss"]],
            on=["home_club_id", "away_club_id"],
            how="left"
        )

        # Merge Weather
        matches_df = matches_df.merge(
            we_s[["home_club_id", "away_club_id", "weather_temperature_c", "weather_humidity_pct", "weather_precipitation_mm", "weather_wind_speed_kmh", "weather_code"]],
            on=["home_club_id", "away_club_id"],
            how="left"
        )

        # Merge FotMob team possession and big chances to match context
        fm_context_cols = [
            "h_id", "a_id",
            "home_ball_possession", "away_ball_possession",
            "home_big_chances", "away_big_chances",
            "home_xg_open_play", "away_xg_open_play",
            "home_xg_set_play", "away_xg_set_play",
            "home_xg_on_target_(xgot)", "away_xg_on_target_(xgot)"
        ]
        matches_df = matches_df.merge(
            fm_ts_s[fm_context_cols],
            left_on=["home_club_id", "away_club_id"],
            right_on=["h_id", "a_id"],
            how="left"
        ).drop(columns=["h_id", "a_id"], errors="ignore")

        # Map FDR (Fixture Difficulty Rating)
        matches_df["fdr_home"] = matches_df.apply(lambda r: fdr_lookup.get((season, r["home_club_id"], r["away_club_id"]), (np.nan, np.nan))[0], axis=1)
        matches_df["fdr_away"] = matches_df.apply(lambda r: fdr_lookup.get((season, r["home_club_id"], r["away_club_id"]), (np.nan, np.nan))[1], axis=1)

        # Deterministic match_id format: EPL_{season}_GW{gameweek:02d}_{home}_{away}
        matches_df["gameweek"] = matches_df["round"].astype(str).str.extract(r"(\d+)")[0].fillna(0).astype(int)
        matches_df["match_id"] = matches_df.apply(
            lambda r: f"EPL_{season}_GW{r['gameweek']:02d}_{r['home_club_id']}_{r['away_club_id']}", axis=1
        )

        gid_to_mid = dict(zip(matches_df["game_id"], matches_df["match_id"]))
        pair_to_mid = dict(zip(zip(matches_df["home_club_id"], matches_df["away_club_id"]), matches_df["match_id"]))

        # ---------------------------------------------------------------------
        # 1. MATCH CONTEXT CSV
        # ---------------------------------------------------------------------
        match_context_cols = [
            "match_id", "season", "gameweek", "Date", "Time", "home_club_id", "away_club_id",
            "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR",
            "home_xg", "away_xg", "forecast_win", "forecast_draw", "forecast_loss",
            "home_ball_possession", "away_ball_possession", "home_big_chances", "away_big_chances",
            "home_xg_open_play", "away_xg_open_play", "home_xg_set_play", "away_xg_set_play",
            "home_xg_on_target_(xgot)", "away_xg_on_target_(xgot)",
            "home_club_formation", "away_club_formation",
            "home_club_position", "away_club_position",
            "fdr_home", "fdr_away",
            "referee", "attendance", "home_club_manager_name", "away_club_manager_name", "stadium",
            "weather_temperature_c", "weather_humidity_pct", "weather_precipitation_mm", "weather_wind_speed_kmh", "weather_code"
        ]
        out_context = matches_df[match_context_cols].rename(columns={
            "Date": "date",
            "Time": "time",
            "FTHG": "ft_home_goals",
            "FTAG": "ft_away_goals",
            "FTR": "ft_result",
            "HTHG": "ht_home_goals",
            "HTAG": "ht_away_goals",
            "HTR": "ht_result",
            "home_ball_possession": "home_possession_pct",
            "away_ball_possession": "away_possession_pct",
            "home_xg_on_target_(xgot)": "home_xg_on_target",
            "away_xg_on_target_(xgot)": "away_xg_on_target",
            "home_club_formation": "home_formation",
            "away_club_formation": "away_formation",
            "home_club_position": "home_league_rank",
            "away_club_position": "away_league_rank",
            "home_club_manager_name": "home_manager",
            "away_club_manager_name": "away_manager"
        })
        out_context_path = os.path.join(s_matches_dir, "match_context.csv")
        out_context.to_csv(out_context_path, index=False)
        print(f"  [1/9] Created {out_context_path}: {len(out_context)} matches (44 features: formations, ranks, FDR, forecasts, weather)")

        # ---------------------------------------------------------------------
        # 2. MATCH BETTING ODDS CSV
        # ---------------------------------------------------------------------
        matches_df = compute_market_metrics(matches_df)
        ar_idx = list(fd_df.columns).index("AR") if "AR" in fd_df.columns else 23
        fd_odds_cols = list(fd_df.columns)[ar_idx + 1:]

        odds_meta_cols = [
            "match_id", "season", "gameweek", "home_club_id", "away_club_id",
            "overround_open", "overround_close",
            "prob_h_open", "prob_d_open", "prob_a_open",
            "prob_h_close", "prob_d_close", "prob_a_close",
            "odds_drift_h", "odds_drift_d", "odds_drift_a"
        ]
        full_odds_cols = odds_meta_cols + [c for c in fd_odds_cols if c in matches_df.columns and c not in odds_meta_cols]
        out_odds = matches_df[full_odds_cols].copy()
        out_odds_path = os.path.join(s_matches_dir, "match_betting_odds.csv")
        out_odds.to_csv(out_odds_path, index=False)
        print(f"  [2/9] Created {out_odds_path}: {len(out_odds)} matches with {len(full_odds_cols)} betting odds & signals")

        # ---------------------------------------------------------------------
        # 3. MATCH LINEUPS CSV
        # ---------------------------------------------------------------------
        s_gids = set(tm_games_s["game_id"])
        lineups_s = tm_lineups_all[tm_lineups_all["game_id"].isin(s_gids)].copy()
        lineups_s["match_id"] = lineups_s["game_id"].map(gid_to_mid)
        lineups_s["club_id"] = lineups_s["club_id"].map(tm_map)

        lineups_cols = [
            "match_id", "club_id", "player_id", "player_name", "type", "position", "number", "team_captain"
        ]
        out_lineups = lineups_s[lineups_cols].rename(columns={
            "type": "lineup_type",
            "number": "jersey_number",
            "team_captain": "is_captain"
        })
        out_lineups_path = os.path.join(s_matches_dir, "match_lineups.csv")
        out_lineups.to_csv(out_lineups_path, index=False)
        print(f"  [3/9] Created {out_lineups_path}: {len(out_lineups)} lineup player records")

        # ---------------------------------------------------------------------
        # 4. CLUB SEASON PROFILE CSV
        # ---------------------------------------------------------------------
        fpl_t_path = os.path.join(RAW_DIR, "fpl_api", f"fpl_teams_{season}.csv")
        fpl_t_df = pd.read_csv(fpl_t_path)
        fpl_t_df["club_id"] = fpl_t_df["code"].map(maps["fpl_code"])

        club_profile = fpl_t_df[[
            "club_id", "name", "strength_overall_home", "strength_overall_away",
            "strength_attack_home", "strength_attack_away", "strength_defence_home", "strength_defence_away"
        ]].copy()
        club_profile["season"] = season
        out_cp_path = os.path.join(s_clubs_dir, "club_season_profile.csv")
        club_profile.to_csv(out_cp_path, index=False)
        print(f"  [4/9] Created {out_cp_path}: {len(club_profile)} clubs season profile")

        # ---------------------------------------------------------------------
        # 5. CLUB MATCH PERFORMANCE CSV (760 rows = 380 x 2)
        # ---------------------------------------------------------------------
        fm_lookup = fm_ts_s.set_index(["h_id", "a_id"]).to_dict("index")

        home_perf_rows = []
        away_perf_rows = []

        for _, r in matches_df.iterrows():
            mid = r["match_id"]
            gw = r["gameweek"]
            h_id = r["home_club_id"]
            a_id = r["away_club_id"]
            fm_row = fm_lookup.get((h_id, a_id), {})

            h_acc_pass, h_pass_pct = parse_val_pct(fm_row.get("home_accurate_passes"))
            a_acc_pass, a_pass_pct = parse_val_pct(fm_row.get("away_accurate_passes"))
            h_cross, h_cross_pct = parse_val_pct(fm_row.get("home_accurate_crosses"))
            a_cross, a_cross_pct = parse_val_pct(fm_row.get("away_accurate_crosses"))
            h_lball, h_lball_pct = parse_val_pct(fm_row.get("home_accurate_long_balls"))
            a_lball, a_lball_pct = parse_val_pct(fm_row.get("away_accurate_long_balls"))
            h_g_duel, h_g_duel_pct = parse_val_pct(fm_row.get("home_ground_duels_won"))
            a_g_duel, a_g_duel_pct = parse_val_pct(fm_row.get("away_ground_duels_won"))
            h_a_duel, h_a_duel_pct = parse_val_pct(fm_row.get("home_aerial_duels_won"))
            a_a_duel, a_a_duel_pct = parse_val_pct(fm_row.get("away_aerial_duels_won"))
            h_drib, h_drib_pct = parse_val_pct(fm_row.get("home_successful_dribbles"))
            a_drib, a_drib_pct = parse_val_pct(fm_row.get("away_successful_dribbles"))

            # Home side row
            h_dict = {
                "match_id": mid,
                "season": season,
                "gameweek": gw,
                "club_id": h_id,
                "opponent_club_id": a_id,
                "venue": "Home",
                "formation": r["home_club_formation"],
                "league_rank_before_match": r["home_club_position"],
                "fixture_difficulty_rating": r["fdr_home"],
                "goals_for": r["FTHG"],
                "goals_against": r["FTAG"],
                "result": "W" if r["FTR"] == "H" else ("D" if r["FTR"] == "D" else "L"),
                "xg": r["home_xg"],
                "shots": r["HS"],
                "shots_on_target": r["HST"],
                "corners": r["HC"],
                "fouls": r["HF"],
                "yellow_cards": r["HY"],
                "red_cards": r["HR"],
                # FotMob Advanced Metrics
                "possession_pct": fm_row.get("home_ball_possession"),
                "touches_in_opposition_box": fm_row.get("home_touches_in_opposition_box"),
                "big_chances": fm_row.get("home_big_chances"),
                "big_chances_missed": fm_row.get("home_big_chances_missed"),
                "passes": fm_row.get("home_passes"),
                "accurate_passes": h_acc_pass,
                "pass_accuracy_pct": h_pass_pct,
                "accurate_crosses": h_cross,
                "cross_accuracy_pct": h_cross_pct,
                "accurate_long_balls": h_lball,
                "long_ball_accuracy_pct": h_lball_pct,
                "throws": fm_row.get("home_throws"),
                "own_half_passes": fm_row.get("home_own_half"),
                "opposition_half_passes": fm_row.get("home_opposition_half"),
                "shots_inside_box": fm_row.get("home_shots_inside_box"),
                "shots_outside_box": fm_row.get("home_shots_outside_box"),
                "blocked_shots": fm_row.get("home_blocked_shots"),
                "hit_woodwork": fm_row.get("home_hit_woodwork"),
                "tackles": fm_row.get("home_tackles"),
                "interceptions": fm_row.get("home_interceptions"),
                "blocks": fm_row.get("home_blocks"),
                "clearances": fm_row.get("home_clearances"),
                "keeper_saves": fm_row.get("home_keeper_saves"),
                "duels_won": fm_row.get("home_duels_won"),
                "ground_duels_won": h_g_duel,
                "ground_duels_won_pct": h_g_duel_pct,
                "aerial_duels_won": h_a_duel,
                "aerial_duels_won_pct": h_a_duel_pct,
                "successful_dribbles": h_drib,
                "successful_dribbles_pct": h_drib_pct,
                "xg_open_play": fm_row.get("home_xg_open_play"),
                "xg_set_play": fm_row.get("home_xg_set_play"),
                "xg_non_penalty": fm_row.get("home_xg_non-penalty"),
                "xg_on_target": fm_row.get("home_xg_on_target_(xgot)"),
                "offsides": fm_row.get("home_offsides"),
            }
            home_perf_rows.append(h_dict)

            # Away side row
            a_dict = {
                "match_id": mid,
                "season": season,
                "gameweek": gw,
                "club_id": a_id,
                "opponent_club_id": h_id,
                "venue": "Away",
                "formation": r["away_club_formation"],
                "league_rank_before_match": r["away_club_position"],
                "fixture_difficulty_rating": r["fdr_away"],
                "goals_for": r["FTAG"],
                "goals_against": r["FTHG"],
                "result": "W" if r["FTR"] == "A" else ("D" if r["FTR"] == "D" else "L"),
                "xg": r["away_xg"],
                "shots": r["AS"],
                "shots_on_target": r["AST"],
                "corners": r["AC"],
                "fouls": r["AF"],
                "yellow_cards": r["AY"],
                "red_cards": r["AR"],
                # FotMob Advanced Metrics
                "possession_pct": fm_row.get("away_ball_possession"),
                "touches_in_opposition_box": fm_row.get("away_touches_in_opposition_box"),
                "big_chances": fm_row.get("away_big_chances"),
                "big_chances_missed": fm_row.get("away_big_chances_missed"),
                "passes": fm_row.get("away_passes"),
                "accurate_passes": a_acc_pass,
                "pass_accuracy_pct": a_pass_pct,
                "accurate_crosses": a_cross,
                "cross_accuracy_pct": a_cross_pct,
                "accurate_long_balls": a_lball,
                "long_ball_accuracy_pct": a_lball_pct,
                "throws": fm_row.get("away_throws"),
                "own_half_passes": fm_row.get("away_own_half"),
                "opposition_half_passes": fm_row.get("away_opposition_half"),
                "shots_inside_box": fm_row.get("away_shots_inside_box"),
                "shots_outside_box": fm_row.get("away_shots_outside_box"),
                "blocked_shots": fm_row.get("away_blocked_shots"),
                "hit_woodwork": fm_row.get("away_hit_woodwork"),
                "tackles": fm_row.get("away_tackles"),
                "interceptions": fm_row.get("away_interceptions"),
                "blocks": fm_row.get("away_blocks"),
                "clearances": fm_row.get("away_clearances"),
                "keeper_saves": fm_row.get("away_keeper_saves"),
                "duels_won": fm_row.get("away_duels_won"),
                "ground_duels_won": a_g_duel,
                "ground_duels_won_pct": a_g_duel_pct,
                "aerial_duels_won": a_a_duel,
                "aerial_duels_won_pct": a_a_duel_pct,
                "successful_dribbles": a_drib,
                "successful_dribbles_pct": a_drib_pct,
                "xg_open_play": fm_row.get("away_xg_open_play"),
                "xg_set_play": fm_row.get("away_xg_set_play"),
                "xg_non_penalty": fm_row.get("away_xg_non-penalty"),
                "xg_on_target": fm_row.get("away_xg_on_target_(xgot)"),
                "offsides": fm_row.get("away_offsides"),
            }
            away_perf_rows.append(a_dict)

        club_perf = pd.concat([pd.DataFrame(home_perf_rows), pd.DataFrame(away_perf_rows)], ignore_index=True)
        club_perf = club_perf.sort_values(["gameweek", "match_id"])
        out_perf_path = os.path.join(s_clubs_dir, "club_match_performance.csv")
        club_perf.to_csv(out_perf_path, index=False)
        print(f"  [5/9] Created {out_perf_path}: {len(club_perf)} club performance records (52 features: crosses, long balls, passes by half, formations, ranks, FDR)")

        # ---------------------------------------------------------------------
        # 6. PLAYER VALUATIONS CSV
        # ---------------------------------------------------------------------
        s_pids = set(lineups_s["player_id"])
        val_s = tm_val_all[
            (tm_val_all["player_id"].isin(s_pids)) & (tm_val_all["date"] <= f"{end_year}-06-30")
        ].copy()
        out_val_path = os.path.join(s_players_dir, "player_valuations.csv")
        val_s.to_csv(out_val_path, index=False)
        print(f"  [6/9] Created {out_val_path}: {len(val_s)} valuation timeline records")

        # ---------------------------------------------------------------------
        # 7. MATCH SHOTS CSV (Complete pitch + goalmouth coordinates)
        # ---------------------------------------------------------------------
        fm_shots_s = fotmob_shots_all[
            (fotmob_shots_all["_match_dt"] >= d_start) & (fotmob_shots_all["_match_dt"] <= d_end)
        ].copy()

        fm_shots_s["h_id"] = fm_shots_s["home_team"].map(fm_map)
        fm_shots_s["a_id"] = fm_shots_s["away_team"].map(fm_map)
        fm_shots_s["match_id"] = fm_shots_s.apply(
            lambda r: pair_to_mid.get((r["h_id"], r["a_id"])), axis=1
        )
        fm_shots_s["club_id"] = fm_shots_s.apply(
            lambda r: r["h_id"] if r["team_id"] == r["home_team_id"] else r["a_id"], axis=1
        )

        shot_export_cols = [
            "id", "match_id", "club_id", "player_id", "player_name", "first_name", "last_name",
            "min", "min_added", "period",
            "x", "y", "on_goal_shot_x", "on_goal_shot_y", "on_goal_shot_zoom_ratio",
            "blocked_x", "blocked_y", "goal_crossed_y", "goal_crossed_z",
            "is_on_target", "is_blocked", "is_from_inside_box", "is_saved_off_line", "is_own_goal",
            "expected_goals", "expected_goals_on_target", "shot_type", "situation", "event_type"
        ]
        out_shots = fm_shots_s[shot_export_cols].rename(columns={"id": "shot_id", "min": "minute"})
        out_shots_path = os.path.join(s_events_dir, "match_shots.csv")
        out_shots.to_csv(out_shots_path, index=False)
        print(f"  [7/9] Created {out_shots_path}: {len(out_shots)} shots with player details & goalmouth spatial targets")

        # ---------------------------------------------------------------------
        # 8. PLAYER MATCH STATS CSV (Enriched with FPL metrics & shot aggregates)
        # ---------------------------------------------------------------------
        app_s = tm_app_all[tm_app_all["game_id"].isin(s_gids)].copy()
        app_s["match_id"] = app_s["game_id"].map(gid_to_mid)
        app_s["club_id"] = app_s["player_club_id"].map(tm_map)

        app_base = app_s[[
            "match_id", "player_id", "club_id", "player_name", "minutes_played",
            "goals", "assists", "yellow_cards", "red_cards"
        ]].copy()

        fpl_gw_path = os.path.join(RAW_DIR, "fpl_api", f"fpl_merged_gw_{season}.csv")
        fpl_gw_df = pd.read_csv(fpl_gw_path, low_memory=False)

        enriched_app = enrich_player_appearances(app_base, fpl_gw_df, out_shots, maps)
        out_app_path = os.path.join(s_players_dir, "player_match_stats.csv")
        enriched_app.to_csv(out_app_path, index=False)
        print(f"  [8/9] Created {out_app_path}: {len(enriched_app)} player records (32 features: fpl_xg/xa/xgi/xgc, shots_total/on_target/xg/xgot)")

        # ---------------------------------------------------------------------
        # 9. PLAYER SEASON UNDERSTAT CSV (xGChain, xGBuildup, key_passes)
        # ---------------------------------------------------------------------
        u_p_path = os.path.join(DATA_DIR, season, "02_player_attributes", "raw", f"understat_players_{season}.csv")
        if os.path.exists(u_p_path):
            u_p_df = pd.read_csv(u_p_path)
            u_p_df["club_id"] = u_p_df["team_title"].map(maps["u"])
            u_p_export = u_p_df[[
                "id", "player_name", "club_id", "season", "games", "time",
                "goals", "xG", "assists", "xA", "shots", "key_passes",
                "yellow_cards", "red_cards", "position", "npg", "npxG",
                "xGChain", "xGBuildup"
            ]].rename(columns={
                "id": "understat_player_id",
                "time": "minutes",
                "xG": "xg",
                "xA": "xa",
                "npxG": "npxg",
                "xGChain": "xg_chain",
                "xGBuildup": "xg_buildup"
            })
            out_u_p_path = os.path.join(s_players_dir, "player_season_understat.csv")
            u_p_export.to_csv(out_u_p_path, index=False)
            print(f"  [9/9] Created {out_u_p_path}: {len(u_p_export)} Understat seasonal playmaking records (xGChain, xGBuildup, key_passes)")

    print("\n" + "=" * 80)
    print("ALL 5 SEASONS FULLY INTEGRATED WITH ALL ADVANCED METRICS!")
    print("=" * 80)


if __name__ == "__main__":
    main()
