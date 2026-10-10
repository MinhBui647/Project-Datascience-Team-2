"""One-step pipeline: collect -> normalize -> build ONE compact dataset for predicting the score and
Home/Draw/Away probabilities.

Usage:  python build_dataset.py              collect new matches of the current season and update the dataset
        python build_dataset.py --rebuild    recompute every row (e.g. after changing a feature definition)

Output: data/epl_dataset.csv - one row per match, snake_case columns, team names as in epl_final.csv
  status = "played"   : past matches with targets (home_goals, away_goals, result) -> training
  status = "upcoming" : next-round fixtures, targets empty                         -> prediction

Duplicates: a played match (season, date, home_team, away_team) already in the file is kept as is and
never added twice; only new matches are appended. Upcoming rows are replaced on every run, so a fixture
that has been played moves from "upcoming" to "played".

Every feature uses only matches played before the match itself (no leakage).
"""
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import pandas as pd

import update_epl_final
from build_features import FORM_WINDOW, add_elo, add_h2h, add_squad_value, add_team_form, load_matches
from fetch_fixtures import CURRENT_SEASON, DATA_DIR

DATASET = DATA_DIR / "epl_dataset.csv"
KEY = ["season", "date", "home_team", "away_team"]
w = FORM_WINDOW

# internal feature name -> dataset column name (also defines which columns are kept and their order)
COLUMNS = {
    "Season": "season", "MatchDate": "date", "HomeTeam": "home_team", "AwayTeam": "away_team",
    "status": "status",
    # targets
    "FullTimeHomeGoals": "home_goals", "FullTimeAwayGoals": "away_goals", "FullTimeResult": "result",
    # team strength
    "home_elo": "home_elo", "away_elo": "away_elo", "elo_diff": "elo_diff",
    "squad_value_log_ratio": "squad_value_log_ratio",
    # recent form, home team
    f"home_gf_last{w}": f"home_goals_for_avg{w}", f"home_ga_last{w}": f"home_goals_against_avg{w}",
    f"home_pts_last{w}": f"home_points_avg{w}", f"home_sot_last{w}": f"home_shots_on_target_avg{w}",
    f"home_xg_last{w}": f"home_xg_avg{w}", f"home_xga_last{w}": f"home_xga_avg{w}",
    f"home_venue_pts_last{w}": f"home_points_at_home_avg{w}",
    "home_season_ppg": "home_season_ppg", "home_rest_days": "home_rest_days",
    # recent form, away team
    f"away_gf_last{w}": f"away_goals_for_avg{w}", f"away_ga_last{w}": f"away_goals_against_avg{w}",
    f"away_pts_last{w}": f"away_points_avg{w}", f"away_sot_last{w}": f"away_shots_on_target_avg{w}",
    f"away_xg_last{w}": f"away_xg_avg{w}", f"away_xga_last{w}": f"away_xga_avg{w}",
    f"away_venue_pts_last{w}": f"away_points_away_avg{w}",
    "away_season_ppg": "away_season_ppg", "away_rest_days": "away_rest_days",
    # head to head
    "h2h_home_ppg": "h2h_home_ppg",
}


def compute():
    df = load_matches()
    df = add_elo(df)
    df = add_team_form(df)
    df = add_h2h(df)
    df = add_squad_value(df)
    df["status"] = df.IsPlayed.map({True: "played", False: "upcoming"})
    df["MatchDate"] = df.MatchDate.dt.strftime("%Y-%m-%d")
    out = df[list(COLUMNS)].rename(columns=COLUMNS)
    num = out.select_dtypes("float").columns.difference(["home_goals", "away_goals"])
    out[num] = out[num].round(4)
    out[["home_goals", "away_goals"]] = out[["home_goals", "away_goals"]].astype("Int64")
    return out


def merge(old, new):
    """Keep every played row already in the file, append only new played rows, refresh upcoming rows."""
    kept = old[old.status == "played"]
    have = set(map(tuple, kept[KEY].astype(str).values))
    played = new[new.status == "played"]
    fresh = played[[tuple(k) not in have for k in played[KEY].astype(str).values]]
    upcoming = new[new.status == "upcoming"]
    print(f"played: {len(kept)} kept, {len(fresh)} new | upcoming: {len(upcoming)}")
    return pd.concat([kept, fresh, upcoming], ignore_index=True)


def main(rebuild=False):
    print("1/3 Collecting new matches into epl_final.csv ...")
    update_epl_final.main([CURRENT_SEASON], refresh=False)  # this script builds its own upcoming rows
    print("2/3 Normalizing and computing features ...")
    new = compute()
    print("3/3 Writing dataset ...")
    if DATASET.exists() and not rebuild:
        out = merge(pd.read_csv(DATASET), new)
    else:
        out = new
    out[["home_goals", "away_goals"]] = out[["home_goals", "away_goals"]].astype("Int64")
    out.to_csv(DATASET, index=False)
    print(f"{DATASET.name}: {len(out)} rows x {out.shape[1]} columns")
    print(out[out.status == "upcoming"][["date", "home_team", "away_team", "elo_diff",
                                          f"home_points_avg{w}", f"away_points_avg{w}"]].to_string(index=False))


if __name__ == "__main__":
    main(rebuild="--rebuild" in sys.argv)
