"""Update data/epl_final.csv (one row per match) from football-data.co.uk, the source it was built from.

Usage:  python update_epl_final.py              current season only (2026 = 2026/27), run after every round
        python update_epl_final.py 2024 2025    specific seasons (start year)
        python update_epl_final.py --all        every season from 2000/01

Raw columns (FTHG, HS, HY, ...) are renamed to the epl_final.csv schema and dates become YYYY-MM-DD.
A match already in the file - same Season, MatchDate, HomeTeam, AwayTeam - is skipped, so the script
can be rerun any number of times without creating duplicates. A backup is written before saving.

Afterwards data/features/next_round_features.csv is deleted and rebuilt for the next unplayed round
(match_features.csv is rebuilt too). Add --no-refresh to only update epl_final.csv.
"""
import io
import shutil
import sys
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent / "data"
EPL_FINAL = DATA_DIR / "epl_final.csv"
CURRENT_SEASON = 2026
FIRST_SEASON = 2000
SOURCE_URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
KEY = ["Season", "MatchDate", "HomeTeam", "AwayTeam"]

# football-data raw column -> epl_final.csv column
COLUMN_MAP = {
    "FTHG": "FullTimeHomeGoals", "FTAG": "FullTimeAwayGoals", "FTR": "FullTimeResult",
    "HTHG": "HalfTimeHomeGoals", "HTAG": "HalfTimeAwayGoals", "HTR": "HalfTimeResult",
    "HS": "HomeShots", "AS": "AwayShots", "HST": "HomeShotsOnTarget", "AST": "AwayShotsOnTarget",
    "HC": "HomeCorners", "AC": "AwayCorners", "HF": "HomeFouls", "AF": "AwayFouls",
    "HY": "HomeYellowCards", "AY": "AwayYellowCards", "HR": "HomeRedCards", "AR": "AwayRedCards",
}
COLUMNS = ["Season", "MatchDate", "HomeTeam", "AwayTeam"] + list(COLUMN_MAP.values())


def season_name(year):
    return f"{year}/{str(year + 1)[-2:]}"           # 2026 -> "2026/27"


def season_code(year):
    return f"{year % 100:02d}{(year + 1) % 100:02d}"  # 2026 -> "2627"


def fetch_season(year):
    r = requests.get(SOURCE_URL.format(code=season_code(year)), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    raw = pd.read_csv(io.BytesIO(r.content), encoding="latin-1", on_bad_lines="skip")
    raw.columns = raw.columns.str.replace("﻿", "").str.replace("ï»¿", "")
    raw = raw.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).copy()  # drop blank / unplayed rows

    df = raw.rename(columns=COLUMN_MAP)
    df["Season"] = season_name(year)
    df["MatchDate"] = pd.to_datetime(df.Date, dayfirst=True).dt.strftime("%Y-%m-%d")
    for c in COLUMNS[4:]:
        if c not in df.columns:  # very old seasons may lack a stat
            df[c] = pd.NA
    df = df[COLUMNS]
    ints = [c for c in COLUMNS[4:] if not c.endswith("Result")]
    df[ints] = df[ints].apply(pd.to_numeric, errors="coerce").astype("Int64")
    return df


def refresh_next_round():
    """Delete the old next_round_features.csv and rebuild it from the latest schedule and results.
    Runs even when no new match was added, since fixtures can be rescheduled."""
    import build_features  # imported here: build_features is heavy and only needed for this step

    next_round_file = build_features.OUT_DIR / "next_round_features.csv"
    if next_round_file.exists():
        old_round = pd.read_csv(next_round_file)
        next_round_file.unlink()
        print(f"Removed old next round ({len(old_round)} fixtures, "
              f"{old_round.MatchDate.min() if len(old_round) else '-'} -> "
              f"{old_round.MatchDate.max() if len(old_round) else '-'})")
    build_features.main()  # also rewrites match_features.csv with the newly added matches


def main(years, refresh=True):
    update_matches(years)
    if refresh:
        refresh_next_round()


def update_matches(years):
    old = pd.read_csv(EPL_FINAL) if EPL_FINAL.exists() else pd.DataFrame(columns=COLUMNS)
    existing = set(map(tuple, old[KEY].astype(str).values))
    new_rows = []
    for year in years:
        df = fetch_season(year)
        is_new = [tuple(k) not in existing for k in df[KEY].astype(str).values]
        fresh = df[is_new]
        print(f"{season_name(year)}: {len(df)} matches at source, {len(df) - len(fresh)} already present, "
              f"{len(fresh)} added")
        new_rows.append(fresh)
        existing |= set(map(tuple, fresh[KEY].astype(str).values))

    new = pd.concat(new_rows, ignore_index=True) if new_rows else pd.DataFrame(columns=COLUMNS)
    if new.empty:
        print("Nothing new - epl_final.csv unchanged.")
        return
    if EPL_FINAL.exists():
        shutil.copy(EPL_FINAL, EPL_FINAL.with_name("epl_final_backup.csv"))
    out = pd.concat([old, new], ignore_index=True)
    out = out.sort_values(["Season", "MatchDate"], kind="stable")  # stable: keeps source order within a day
    out.to_csv(EPL_FINAL, index=False)
    print(f"epl_final.csv: {len(old)} -> {len(out)} matches (backup: epl_final_backup.csv)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--all" in args:
        years = range(FIRST_SEASON, CURRENT_SEASON + 1)
    else:
        years = [int(a) for a in args if a != "--no-refresh"] or [CURRENT_SEASON]
    main(years, refresh="--no-refresh" not in args)
