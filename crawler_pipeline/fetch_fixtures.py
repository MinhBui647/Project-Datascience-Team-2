
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CURRENT_SEASON = 2026
UA = {"User-Agent": "Mozilla/5.0"}

# Understat team name -> epl_final.csv (football-data) team name; names not listed are identical
UNDERSTAT_TO_FD = {
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Queens Park Rangers": "QPR",
    "West Bromwich Albion": "West Brom",
    "Wolverhampton Wanderers": "Wolves",
}


def season_label(year, sep="_"):
    return f"{year}{sep}{str(year + 1)[-2:]}"


def fetch_season_matches(year):
    """All matches of one Understat EPL season (played and scheduled)."""
    r = requests.get(
        f"https://understat.com/getLeagueData/EPL/{year}",
        headers={**UA, "X-Requested-With": "XMLHttpRequest", "Referer": f"https://understat.com/league/EPL/{year}"},
        timeout=30,
    )
    r.raise_for_status()
    rows = []
    for m in r.json()["dates"]:
        fc = m.get("forecast") or {}
        rows.append({
            "understat_id": int(m["id"]),
            "Season": season_label(year, "/"),
            "KickOff": m["datetime"],
            "HomeTeam": UNDERSTAT_TO_FD.get(m["h"]["title"], m["h"]["title"]),
            "AwayTeam": UNDERSTAT_TO_FD.get(m["a"]["title"], m["a"]["title"]),
            "IsPlayed": bool(m["isResult"]),
            "HomeGoals": m["goals"]["h"],
            "AwayGoals": m["goals"]["a"],
            "HomeXG": m["xG"]["h"],
            "AwayXG": m["xG"]["a"],
            "ForecastHome": fc.get("w"),
            "ForecastDraw": fc.get("d"),
            "ForecastAway": fc.get("l"),
        })
    df = pd.DataFrame(rows)
    for c in ["HomeGoals", "AwayGoals", "HomeXG", "AwayXG", "ForecastHome", "ForecastDraw", "ForecastAway"]:
        df[c] = pd.to_numeric(df[c])
    df[["HomeGoals", "AwayGoals"]] = df[["HomeGoals", "AwayGoals"]].astype("Int64")
    df["KickOff"] = pd.to_datetime(df.KickOff)
    df["MatchDate"] = df.KickOff.dt.strftime("%Y-%m-%d")
    df = df.sort_values(["KickOff", "understat_id"]).reset_index(drop=True)
    return add_rounds(df)


def add_rounds(df):
    """Round = the n-th league match of each team; a match gets the larger n of its two teams.
    Postponed matches therefore keep the round they are played in, not the original one."""
    n = {}
    rounds = []
    for h, a in zip(df.HomeTeam, df.AwayTeam):
        n[h], n[a] = n.get(h, 0) + 1, n.get(a, 0) + 1
        rounds.append(max(n[h], n[a]))
    df["Round"] = rounds
    return df


def next_round(df, max_span_days=6):
    """Upcoming matches in which each team plays at most once, starting from the next kick-off."""
    upcoming = df[~df.IsPlayed]
    if upcoming.empty:
        return upcoming
    first = upcoming.KickOff.iloc[0]
    teams, picked = set(), []
    for i, m in upcoming.iterrows():
        if (m.KickOff - first).days > max_span_days or m.HomeTeam in teams or m.AwayTeam in teams:
            continue
        teams |= {m.HomeTeam, m.AwayTeam}
        picked.append(i)
    return upcoming.loc[picked]


FIXTURE_COLS = ["Season", "Round", "MatchDate", "KickOff", "HomeTeam", "AwayTeam", "IsPlayed",
                "HomeGoals", "AwayGoals", "HomeXG", "AwayXG", "ForecastHome", "ForecastDraw", "ForecastAway"]


def main(year=CURRENT_SEASON):
    out = DATA_DIR / season_label(year)
    out.mkdir(parents=True, exist_ok=True)
    df = fetch_season_matches(year)
    nr = next_round(df)
    df[FIXTURE_COLS].to_csv(out / "fixtures.csv", index=False)
    nr[FIXTURE_COLS[:6]].to_csv(out / "next_round.csv", index=False)
    print(f"{season_label(year)}: {df.IsPlayed.sum()}/{len(df)} matches played -> {out / 'fixtures.csv'}")
    if nr.empty:
        print("No upcoming matches.")
    else:
        print(f"Next round ({len(nr)} matches) -> {out / 'next_round.csv'}")
        print(nr[["Round", "KickOff", "HomeTeam", "AwayTeam"]].to_string(index=False))
    return nr


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else CURRENT_SEASON)
