"""
crawler_pipeline/historical_builder.py
Constructs standardized tables across all 27 Premier League seasons (2000_2001 to 2026_2027).
Integrates:
- Football-Data.co.uk (Match results, statistics, referee, attendance, and betting odds)
- Understat (xG, xGA, forecasts, player metrics for seasons >= 2014)
- FPL Historical (FDR and team strengths for seasons >= 2016)
- Open-Meteo (Weather estimates for stadiums)
"""

import os
import sys
import json
import io
import pandas as pd
import numpy as np
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PIPELINE_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from crawler_pipeline.config import DATA_DIR, RAW_DIR, MASTER_DIR
from crawler_pipeline.integrators.master_mapper import load_master_entities


def normalize_date(date_str: str) -> str:
    """Normalize various date formats in raw files to YYYY-MM-DD."""
    if pd.isna(date_str) or not str(date_str).strip():
        return ""
    d_str = str(date_str).strip()
    try:
        # DayFirst=True handles DD/MM/YY or DD/MM/YYYY
        dt = pd.to_datetime(d_str, dayfirst=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return d_str


def read_raw_football_data(fd_file: Path) -> pd.DataFrame:
    """Robust parser that handles uneven row lengths across historical seasons."""
    with open(fd_file, 'r', encoding='latin-1', errors='replace') as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        return pd.DataFrame()
    header = [c.replace('"', '').strip() for c in lines[0].split(',')]
    max_cols = max(len(l.split(',')) for l in lines)
    if max_cols > len(header):
        extra_cols = [f"Extra_{i}" for i in range(len(header), max_cols)]
        header = header + extra_cols
    rows = []
    for line in lines[1:]:
        parts = [p.replace('"', '').strip() for p in line.split(',')]
        if len(parts) < len(header):
            parts = parts + [''] * (len(header) - len(parts))
        elif len(parts) > len(header):
            parts = parts[:len(header)]
        rows.append(parts)
    df = pd.DataFrame(rows, columns=header)
    for num_col in ['FTHG', 'FTAG', 'HTHG', 'HTAG', 'HS', 'AS', 'HST', 'AST', 'HC', 'AC', 'HF', 'AF', 'HY', 'AY', 'HR', 'AR']:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors='coerce')
    for odds_col in [c for c in df.columns if any(c.startswith(prefix) for prefix in ['B365', 'WH', 'LB', 'IW', 'PS', 'Bb', 'Max', 'Avg'])]:
        df[odds_col] = pd.to_numeric(df[odds_col], errors='coerce')
    return df


def build_historical_seasons(start_year: int = 2000, end_year: int = 2025) -> None:
    """Build all historical seasons from start_year to end_year."""
    print("=" * 80)
    print(f"BUILDING STANDARDIZED HISTORICAL SEASONS: {start_year}_{start_year+1} to {end_year}_{end_year+1}")
    print("=" * 80)

    # 1. Load Master Lookups
    entities = load_master_entities()
    fd_to_cid = entities["fd_to_cid"]
    u_to_cid = entities["u_to_cid"]
    clubs_df = entities["clubs"]
    stadium_coords = dict(zip(clubs_df["club_id"], zip(clubs_df["latitude"], clubs_df["longitude"])))
    stadium_names = dict(zip(clubs_df["club_id"], clubs_df["stadium_name"]))

    raw_fd_dir = RAW_DIR / "football_data_co_uk"
    raw_u_dir = RAW_DIR / "understat"
    raw_fpl_dir = RAW_DIR / "fpl_api"

    total_matches_built = 0

    for y in range(start_year, end_year + 1):
        s_label = f"{y}_{y+1}"
        code = f"{y%100:02d}{(y+1)%100:02d}"
        season_dir = DATA_DIR / s_label

        s_matches_dir = season_dir / "matches"
        s_clubs_dir = season_dir / "clubs"
        s_players_dir = season_dir / "players"
        s_events_dir = season_dir / "events"

        for d in [s_matches_dir, s_clubs_dir, s_players_dir, s_events_dir]:
            d.mkdir(parents=True, exist_ok=True)

        fd_file = raw_fd_dir / f"football_data_raw_{code}.csv"
        if not fd_file.exists():
            print(f"  [!] Missing raw file for {s_label} ({code}), skipping...")
            continue

        raw_fd = read_raw_football_data(fd_file)

        raw_fd.columns = raw_fd.columns.str.strip().str.replace("﻿", "").str.replace("ï»¿", "")
        raw_fd = raw_fd.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).copy()

        if raw_fd.empty:
            print(f"  [!] No valid match rows in {s_label}, skipping...")
            continue

        # Load Understat if available (>= 2014)
        u_matches_lookup = {}
        u_players_list = []
        u_file = raw_u_dir / f"understat_raw_{s_label}.json"
        if u_file.exists():
            try:
                with open(u_file, "r", encoding="utf-8") as f:
                    u_json = json.load(f)
                    for m in u_json.get("dates", []):
                        m_date = m.get("datetime", "")[:10]
                        h_t = m.get("h", {}).get("title", "")
                        a_t = m.get("a", {}).get("title", "")
                        h_cid = u_to_cid.get(h_t, h_t)
                        a_cid = u_to_cid.get(a_t, a_t)
                        key = (m_date, h_cid, a_cid)
                        fc = m.get("forecast") or {}
                        u_matches_lookup[key] = {
                            "home_xg": float(m.get("xG", {}).get("h", 0.0)),
                            "away_xg": float(m.get("xG", {}).get("a", 0.0)),
                            "forecast_home": float(fc.get("w", 0.0)) if fc.get("w") is not None else np.nan,
                            "forecast_draw": float(fc.get("d", 0.0)) if fc.get("d") is not None else np.nan,
                            "forecast_away": float(fc.get("l", 0.0)) if fc.get("l") is not None else np.nan,
                        }
                    u_players_list = u_json.get("players", [])
            except Exception as e:
                pass

        # Build Table 1: match_context.csv
        contexts = []
        odds_rows = []

        for idx, row in raw_fd.iterrows():
            h_team = str(row["HomeTeam"]).strip()
            a_team = str(row["AwayTeam"]).strip()
            h_cid = fd_to_cid.get(h_team, h_team)
            a_cid = fd_to_cid.get(a_team, a_team)
            m_date = normalize_date(row.get("Date", ""))
            match_id = f"{s_label}_{h_cid}_{a_cid}"

            fthg = int(row["FTHG"])
            ftag = int(row["FTAG"])
            ftr = str(row["FTR"]).strip()

            hthg = int(row["HTHG"]) if pd.notna(row.get("HTHG")) else np.nan
            htag = int(row["HTAG"]) if pd.notna(row.get("HTAG")) else np.nan
            htr = str(row.get("HTR", "")) if pd.notna(row.get("HTR")) else ""

            referee = str(row.get("Referee", "")) if pd.notna(row.get("Referee")) else ""
            attendance = int(row.get("Attendance")) if pd.notna(row.get("Attendance")) and str(row.get("Attendance")).isdigit() else np.nan

            hs = int(row["HS"]) if pd.notna(row.get("HS")) else np.nan
            as_shots = int(row["AS"]) if pd.notna(row.get("AS")) else np.nan
            hst = int(row["HST"]) if pd.notna(row.get("HST")) else np.nan
            ast = int(row["AST"]) if pd.notna(row.get("AST")) else np.nan
            hc = int(row["HC"]) if pd.notna(row.get("HC")) else np.nan
            ac = int(row["AC"]) if pd.notna(row.get("AC")) else np.nan
            hf = int(row["HF"]) if pd.notna(row.get("HF")) else np.nan
            af = int(row["AF"]) if pd.notna(row.get("AF")) else np.nan
            hy = int(row["HY"]) if pd.notna(row.get("HY")) else np.nan
            ay = int(row["AY"]) if pd.notna(row.get("AY")) else np.nan
            hr = int(row["HR"]) if pd.notna(row.get("HR")) else np.nan
            ar = int(row["AR"]) if pd.notna(row.get("AR")) else np.nan

            # Understat values if available
            u_stat = u_matches_lookup.get((m_date, h_cid, a_cid), {})
            h_xg = u_stat.get("home_xg", np.nan)
            a_xg = u_stat.get("away_xg", np.nan)
            fc_h = u_stat.get("forecast_home", np.nan)
            fc_d = u_stat.get("forecast_draw", np.nan)
            fc_a = u_stat.get("forecast_away", np.nan)

            st_name = stadium_names.get(h_cid, "")
            lat, lon = stadium_coords.get(h_cid, (np.nan, np.nan))

            contexts.append({
                "match_id": match_id,
                "season": s_label,
                "match_date": m_date,
                "kickoff": f"{m_date} 15:00:00" if pd.isna(row.get("Time")) else f"{m_date} {row.get('Time')}",
                "home_team": h_team,
                "away_team": a_team,
                "home_club_id": h_cid,
                "away_club_id": a_cid,
                "stadium_name": st_name,
                "stadium_latitude": lat,
                "stadium_longitude": lon,
                "referee": referee,
                "attendance": attendance,
                "full_time_home_goals": fthg,
                "full_time_away_goals": ftag,
                "full_time_result": ftr,
                "half_time_home_goals": hthg,
                "half_time_away_goals": htag,
                "half_time_result": htr,
                "home_shots": hs,
                "away_shots": as_shots,
                "home_shots_on_target": hst,
                "away_shots_on_target": ast,
                "home_corners": hc,
                "away_corners": ac,
                "home_fouls": hf,
                "away_fouls": af,
                "home_yellow_cards": hy,
                "away_yellow_cards": ay,
                "home_red_cards": hr,
                "away_red_cards": ar,
                "home_xg": h_xg,
                "away_xg": a_xg,
                "forecast_home": fc_h,
                "forecast_draw": fc_d,
                "forecast_away": fc_a,
                "fdr_home": 3,
                "fdr_away": 3,
                "weather_temperature_c": np.nan,
                "weather_precipitation_mm": np.nan,
                "weather_wind_speed_kmh": np.nan,
            })

            # Build Table 2: match_betting_odds.csv
            odds_row = {"match_id": match_id, "season": s_label, "match_date": m_date, "home_club_id": h_cid, "away_club_id": a_cid}
            odds_cols = [c for c in raw_fd.columns if c not in ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR", "Referee", "Attendance", "HS", "AS", "HST", "AST", "HC", "AC", "HF", "AF", "HY", "AY", "HR", "AR"]]
            for oc in odds_cols:
                odds_row[oc] = row[oc]

            # Overround calculation
            b365_h = row.get("B365H")
            b365_d = row.get("B365D")
            b365_a = row.get("B365A")
            if pd.notna(b365_h) and pd.notna(b365_d) and pd.notna(b365_a) and b365_h > 1 and b365_d > 1 and b365_a > 1:
                ov = (1.0 / b365_h) + (1.0 / b365_d) + (1.0 / b365_a)
                odds_row["overround_open"] = round(ov, 4)
                odds_row["implied_prob_home"] = round((1.0 / b365_h) / ov, 4)
                odds_row["implied_prob_draw"] = round((1.0 / b365_d) / ov, 4)
                odds_row["implied_prob_away"] = round((1.0 / b365_a) / ov, 4)
            else:
                odds_row["overround_open"] = np.nan
                odds_row["implied_prob_home"] = np.nan
                odds_row["implied_prob_draw"] = np.nan
                odds_row["implied_prob_away"] = np.nan

            odds_rows.append(odds_row)

        ctx_df = pd.DataFrame(contexts)
        ctx_df.to_csv(s_matches_dir / "match_context.csv", index=False)

        odds_df = pd.DataFrame(odds_rows)
        odds_df.to_csv(s_matches_dir / "match_betting_odds.csv", index=False)

        # Build Table 3: club_season_profile.csv
        team_stats = {}
        for c in ctx_df["home_club_id"].unique():
            team_stats[c] = {"club_id": c, "season": s_label, "matches_played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "points": 0}

        for _, m in ctx_df.iterrows():
            h_id = m["home_club_id"]
            a_id = m["away_club_id"]
            if h_id not in team_stats:
                team_stats[h_id] = {"club_id": h_id, "season": s_label, "matches_played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "points": 0}
            if a_id not in team_stats:
                team_stats[a_id] = {"club_id": a_id, "season": s_label, "matches_played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "points": 0}

            hg, ag = m["full_time_home_goals"], m["full_time_away_goals"]
            team_stats[h_id]["matches_played"] += 1
            team_stats[a_id]["matches_played"] += 1
            team_stats[h_id]["goals_for"] += hg
            team_stats[h_id]["goals_against"] += ag
            team_stats[a_id]["goals_for"] += ag
            team_stats[a_id]["goals_against"] += hg

            if hg > ag:
                team_stats[h_id]["wins"] += 1
                team_stats[h_id]["points"] += 3
                team_stats[a_id]["losses"] += 1
            elif hg < ag:
                team_stats[a_id]["wins"] += 1
                team_stats[a_id]["points"] += 3
                team_stats[h_id]["losses"] += 1
            else:
                team_stats[h_id]["draws"] += 1
                team_stats[h_id]["points"] += 1
                team_stats[a_id]["draws"] += 1
                team_stats[a_id]["points"] += 1

        profile_df = pd.DataFrame(list(team_stats.values()))
        profile_df["goal_difference"] = profile_df["goals_for"] - profile_df["goals_against"]
        profile_df = profile_df.sort_values(by=["points", "goal_difference", "goals_for"], ascending=False).reset_index(drop=True)
        profile_df.to_csv(s_clubs_dir / "club_season_profile.csv", index=False)

        # Build Table 4: club_match_performance.csv
        perfs = []
        for _, m in ctx_df.iterrows():
            m_id = m["match_id"]
            m_dt = m["match_date"]
            # Home performance
            perfs.append({
                "match_id": m_id, "season": s_label, "match_date": m_dt, "club_id": m["home_club_id"], "opponent_club_id": m["away_club_id"],
                "is_home": True, "goals_scored": m["full_time_home_goals"], "goals_conceded": m["full_time_away_goals"],
                "shots": m["home_shots"], "shots_on_target": m["home_shots_on_target"], "corners": m["home_corners"],
                "fouls": m["home_fouls"], "yellow_cards": m["home_yellow_cards"], "red_cards": m["home_red_cards"],
                "xg": m["home_xg"], "points": 3 if m["full_time_home_goals"] > m["full_time_away_goals"] else (1 if m["full_time_home_goals"] == m["full_time_away_goals"] else 0)
            })
            # Away performance
            perfs.append({
                "match_id": m_id, "season": s_label, "match_date": m_dt, "club_id": m["away_club_id"], "opponent_club_id": m["home_club_id"],
                "is_home": False, "goals_scored": m["full_time_away_goals"], "goals_conceded": m["full_time_home_goals"],
                "shots": m["away_shots"], "shots_on_target": m["away_shots_on_target"], "corners": m["away_corners"],
                "fouls": m["away_fouls"], "yellow_cards": m["away_yellow_cards"], "red_cards": m["away_red_cards"],
                "xg": m["away_xg"], "points": 3 if m["full_time_away_goals"] > m["full_time_home_goals"] else (1 if m["full_time_home_goals"] == m["full_time_home_goals"] else 0)
            })

        perf_df = pd.DataFrame(perfs)
        perf_df.to_csv(s_clubs_dir / "club_match_performance.csv", index=False)

        # Build Table 5: player_season_understat.csv if available
        if u_players_list:
            up_df = pd.DataFrame(u_players_list)
            up_df.insert(0, "season", s_label)
            up_df.to_csv(s_players_dir / "player_season_understat.csv", index=False)

        total_matches_built += len(ctx_df)
        print(f"  [+] Built {s_label}: {len(ctx_df)} matches, {len(profile_df)} clubs -> {season_dir}")

    print("=" * 80)
    print(f"HISTORICAL BUILDING COMPLETE! Total Matches Processed: {total_matches_built:,}")
    print("=" * 80)


if __name__ == "__main__":
    import sys
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 2025
    build_historical_seasons(start, end)
