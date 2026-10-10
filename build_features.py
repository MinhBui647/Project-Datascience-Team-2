
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from fetch_fixtures import CURRENT_SEASON, DATA_DIR, fetch_season_matches, next_round

OUT_DIR = DATA_DIR / "features"
FIRST_UNDERSTAT_SEASON = 2014
FORM_WINDOW = 5     # matches used for rolling form
H2H_WINDOW = 5      # previous meetings used for head-to-head
ELO_K, ELO_HOME_ADV, ELO_CARRY, ELO_NEW_TEAM = 20, 65, 0.8, 1450

# transfermarkt_clubs.csv club name -> epl_final.csv team name
TM_TO_FD = {
    "AFC Bournemouth": "Bournemouth", "Arsenal FC": "Arsenal", "Aston Villa": "Aston Villa",
    "Birmingham City": "Birmingham", "Blackburn Rovers": "Blackburn", "Blackpool FC": "Blackpool",
    "Bolton Wanderers": "Bolton", "Brentford FC": "Brentford", "Brighton & Hove Albion": "Brighton",
    "Burnley FC": "Burnley", "Cardiff City": "Cardiff", "Chelsea FC": "Chelsea", "Coventry City": "Coventry",
    "Crystal Palace": "Crystal Palace", "Everton FC": "Everton", "Fulham FC": "Fulham",
    "Huddersfield Town": "Huddersfield", "Hull City": "Hull", "Ipswich Town": "Ipswich",
    "Leeds United": "Leeds", "Leicester City": "Leicester", "Liverpool FC": "Liverpool", "Luton Town": "Luton",
    "Manchester City": "Man City", "Manchester United": "Man United", "Middlesbrough FC": "Middlesbrough",
    "Newcastle United": "Newcastle", "Norwich City": "Norwich", "Nottingham Forest": "Nott'm Forest",
    "Queens Park Rangers": "QPR", "Reading FC": "Reading", "Sheffield United": "Sheffield United",
    "Southampton FC": "Southampton", "Stoke City": "Stoke", "Sunderland AFC": "Sunderland",
    "Swansea City": "Swansea", "Tottenham Hotspur": "Tottenham", "Watford FC": "Watford",
    "West Bromwich Albion": "West Brom", "West Ham United": "West Ham", "Wigan Athletic": "Wigan",
    "Wolverhampton Wanderers": "Wolves",
}


# ---------------------------------------------------------------- loading
def load_understat_xg():
    """Per-match xG for every Understat season; past seasons are cached, the current one is refetched."""
    cache = DATA_DIR / "understat_matches.csv"
    old = pd.read_csv(cache) if cache.exists() else pd.DataFrame(columns=["Season"])
    current = f"{CURRENT_SEASON}/{str(CURRENT_SEASON + 1)[-2:]}"
    frames = [old[old.Season != current]]
    have = set(frames[0].Season)
    for year in range(FIRST_UNDERSTAT_SEASON, CURRENT_SEASON + 1):
        season = f"{year}/{str(year + 1)[-2:]}"
        if season in have:
            continue
        print(f"  fetching Understat {season}")
        frames.append(fetch_season_matches(year))
        time.sleep(1)
    xg = pd.concat([f for f in frames if len(f)], ignore_index=True)
    xg["IsPlayed"] = xg.IsPlayed.astype(bool)
    xg.to_csv(cache, index=False)
    return xg


def load_squad_values():
    rows = []
    for f in sorted(DATA_DIR.glob("*_*/transfermarkt_clubs.csv")):
        d = pd.read_csv(f)
        year = int(f.parent.name[:4])
        rows += [{"year": year, "team": TM_TO_FD.get(c, c), "value": v}
                 for c, v in zip(d.club, d.total_value_eur)]
    sv = pd.DataFrame(rows)
    # relative to the league median of that season, so values are comparable across years
    sv["value_rel"] = sv.value / sv.groupby("year").value.transform("median")
    return sv.set_index(["year", "team"]).value_rel.to_dict()


def load_matches():
    m = pd.read_csv(DATA_DIR / "epl_final.csv")
    m["IsPlayed"] = True

    xg = load_understat_xg()
    played_xg = xg[xg.IsPlayed][["Season", "HomeTeam", "AwayTeam", "HomeXG", "AwayXG"]]
    m = m.merge(played_xg, on=["Season", "HomeTeam", "AwayTeam"], how="left")
    missing = m[(m.Season >= f"{FIRST_UNDERSTAT_SEASON}/") & m.HomeXG.isna()]
    if len(missing):
        print(f"  warning: {len(missing)} matches since {FIRST_UNDERSTAT_SEASON} without Understat xG")

    # next round of the current season (taken from the schedule, not from epl_final.csv)
    sched = xg[xg.Season == xg.Season.max()].copy()
    sched["KickOff"] = pd.to_datetime(sched.KickOff)
    fixtures = next_round(sched.sort_values("KickOff"))
    fixtures = fixtures[["Season", "Round", "MatchDate", "HomeTeam", "AwayTeam"]].assign(IsPlayed=False)

    df = pd.concat([m, fixtures], ignore_index=True)
    df["MatchDate"] = pd.to_datetime(df.MatchDate)
    df["year"] = df.Season.str[:4].astype(int)
    return df.sort_values(["MatchDate", "IsPlayed"], ascending=[True, False], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- features
def add_elo(df):
    """Pre-match Elo ratings (goal-difference weighted, regressed toward 1500 between seasons)."""
    ratings, season = {}, None
    first_season = df.Season.iloc[0]
    home_elo, away_elo = [], []
    for r in df.itertuples():
        if r.Season != season:
            if season is not None:
                ratings = {t: ELO_CARRY * v + (1 - ELO_CARRY) * 1500 for t, v in ratings.items()}
            season = r.Season
        default = 1500 if r.Season == first_season else ELO_NEW_TEAM
        rh, ra = ratings.get(r.HomeTeam, default), ratings.get(r.AwayTeam, default)
        home_elo.append(rh)
        away_elo.append(ra)
        if not r.IsPlayed:
            continue
        exp_home = 1 / (1 + 10 ** ((ra - rh - ELO_HOME_ADV) / 400))
        gd = abs(r.FullTimeHomeGoals - r.FullTimeAwayGoals)
        score = 1.0 if r.FullTimeResult == "H" else 0.5 if r.FullTimeResult == "D" else 0.0
        mult = 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = ELO_K * mult * (score - exp_home)
        ratings[r.HomeTeam], ratings[r.AwayTeam] = rh + delta, ra - delta
    df["home_elo"], df["away_elo"] = home_elo, away_elo
    df["elo_diff"] = df.home_elo - df.away_elo
    df["elo_home_win_prob"] = 1 / (1 + 10 ** (-(df.elo_diff + ELO_HOME_ADV) / 400))
    return df


def team_rows(df):
    """One row per (match, team) so rolling stats can be computed per team."""
    sides = []
    for side, opp, venue in [("Home", "Away", "H"), ("Away", "Home", "A")]:
        sides.append(pd.DataFrame({
            "match": df.index, "team": df[f"{side}Team"], "venue": venue,
            "date": df.MatchDate, "season": df.Season,
            "gf": df[f"FullTime{side}Goals"], "ga": df[f"FullTime{opp}Goals"],
            "shots": df[f"{side}Shots"], "shots_against": df[f"{opp}Shots"],
            "sot": df[f"{side}ShotsOnTarget"], "sot_against": df[f"{opp}ShotsOnTarget"],
            "corners": df[f"{side}Corners"], "xg": df[f"{side}XG"], "xga": df[f"{opp}XG"],
        }))
    t = pd.concat(sides).sort_values(["date", "match"], kind="stable").reset_index(drop=True)
    t["pts"] = np.where(t.gf > t.ga, 3, np.where(t.gf == t.ga, 1, 0)).astype(float)
    t.loc[t.gf.isna(), "pts"] = np.nan
    return t


def rolling_prev(grouped, col, window):
    # shift(1): only matches strictly before the current one
    return grouped[col].transform(lambda s: s.shift(1).rolling(window, min_periods=1).mean())


def add_team_form(df):
    t = team_rows(df)
    w = FORM_WINDOW
    by_team = t.groupby("team", sort=False)
    form_cols = ["gf", "ga", "pts", "shots", "shots_against", "sot", "sot_against", "corners", "xg", "xga"]
    for c in form_cols:
        t[f"{c}_last{w}"] = rolling_prev(by_team, c, w)

    by_venue = t.groupby(["team", "venue"], sort=False)  # home form for the home team, away form for the away team
    for c in ["gf", "ga", "pts"]:
        t[f"venue_{c}_last{w}"] = rolling_prev(by_venue, c, w)

    by_season = t.groupby(["team", "season"], sort=False)
    t["season_played"] = by_season.cumcount()
    prev_pts = by_season.pts.transform(lambda s: s.shift(1).fillna(0).cumsum())
    prev_gd = by_season.gf.transform(lambda s: s.shift(1).fillna(0).cumsum()) - \
        by_season.ga.transform(lambda s: s.shift(1).fillna(0).cumsum())
    t["season_ppg"] = prev_pts / t.season_played.replace(0, np.nan)
    t["season_gd_per_game"] = prev_gd / t.season_played.replace(0, np.nan)

    t["rest_days"] = by_team.date.diff().dt.days

    feat_cols = [c for c in t.columns if c.endswith(f"_last{w}")] + \
        ["season_played", "season_ppg", "season_gd_per_game", "rest_days"]
    for venue, prefix in [("H", "home_"), ("A", "away_")]:
        part = t[t.venue == venue].set_index("match")[feat_cols].add_prefix(prefix)
        df = df.join(part)
    for c in [f"gf_last{w}", f"ga_last{w}", f"pts_last{w}", f"sot_last{w}", f"xg_last{w}", f"xga_last{w}", "season_ppg"]:
        df[f"diff_{c}"] = df[f"home_{c}"] - df[f"away_{c}"]
    return df


def add_h2h(df):
    """Home team's average points and goal difference in the last meetings (either venue)."""
    history = defaultdict(list)
    pts, gd, n = [], [], []
    for r in df.itertuples():
        key = frozenset((r.HomeTeam, r.AwayTeam))
        past = history[key][-H2H_WINDOW:]
        p, g = [], []
        for home, hg, ag in past:
            gf, ga = (hg, ag) if home == r.HomeTeam else (ag, hg)
            p.append(3 if gf > ga else 1 if gf == ga else 0)
            g.append(gf - ga)
        pts.append(np.mean(p) if p else np.nan)
        gd.append(np.mean(g) if g else np.nan)
        n.append(len(past))
        if r.IsPlayed:
            history[key].append((r.HomeTeam, r.FullTimeHomeGoals, r.FullTimeAwayGoals))
    df["h2h_matches"], df["h2h_home_ppg"], df["h2h_home_gd"] = n, pts, gd
    return df


def add_squad_value(df):
    """Squad value relative to the league median; falls back to the previous season when the
    current one is not available (e.g. Transfermarkt data for the running season)."""
    sv = load_squad_values()
    for side in ["Home", "Away"]:
        df[f"{side.lower()}_squad_value_rel"] = [
            sv.get((y, t), sv.get((y - 1, t), np.nan)) for y, t in zip(df.year, df[f"{side}Team"])
        ]
    df["squad_value_log_ratio"] = np.log(df.home_squad_value_rel / df.away_squad_value_rel)
    return df


# ---------------------------------------------------------------- main
ID_COLS = ["Season", "Round", "MatchDate", "HomeTeam", "AwayTeam"]
TARGET_COLS = ["FullTimeResult", "FullTimeHomeGoals", "FullTimeAwayGoals"]


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("Loading matches ...")
    df = load_matches()
    print("Computing features ...")
    df = add_elo(df)
    df = add_team_form(df)
    df = add_h2h(df)
    df = add_squad_value(df)

    raw_cols = set(pd.read_csv(DATA_DIR / "epl_final.csv", nrows=0).columns) | \
        {"HomeXG", "AwayXG", "IsPlayed", "year", "Round"}
    feature_cols = [c for c in df.columns if c not in raw_cols and c not in ID_COLS]
    df["MatchDate"] = df.MatchDate.dt.strftime("%Y-%m-%d")

    played = df[df.IsPlayed]
    upcoming = df[~df.IsPlayed]
    played[ID_COLS + feature_cols + TARGET_COLS].drop(columns="Round").to_csv(
        OUT_DIR / "match_features.csv", index=False)
    upcoming[ID_COLS + feature_cols].to_csv(OUT_DIR / "next_round_features.csv", index=False)

    print(f"match_features.csv      : {len(played)} matches x {len(feature_cols)} features")
    print(f"next_round_features.csv : {len(upcoming)} fixtures")
    print(upcoming[["MatchDate", "HomeTeam", "AwayTeam", "home_elo", "away_elo", "elo_home_win_prob",
                    f"home_pts_last{FORM_WINDOW}", f"away_pts_last{FORM_WINDOW}"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
