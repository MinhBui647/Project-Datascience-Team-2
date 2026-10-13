
import json
import sys

import numpy as np
import pandas as pd

from clean import RANGES
from integrate import CURRENT_SEASON, EXTRA_MAPPING, MASTER_DIR, OUT_DIR

KNOWN_INCOMPLETE = {"2003/04", "2004/05"}  # football-data raw files have 335 matches


def run_checks(m, f, feat_report):
    checks, warnings = {}, []

    def check(name, ok, detail=""):
        checks[name] = {"passed": bool(ok), "detail": detail}

    played, upcoming = m[m.status == "played"], m[m.status == "upcoming"]
    club_ids = set(pd.read_csv(MASTER_DIR / "club_mapping.csv").club_id) | set(pd.read_csv(EXTRA_MAPPING).club_id)

    # ---- matches.csv: keys and identifiers
    check("match_id unique", m.match_id.is_unique, f"{m.match_id.duplicated().sum()} duplicates")
    dup_key = m.duplicated(["season", "home_club_id", "away_club_id"]).sum()
    check("(season, home, away) unique", dup_key == 0, f"{dup_key} duplicates")
    check("home team != away team", (m.home_club_id != m.away_club_id).all())
    unknown = sorted((set(m.home_club_id) | set(m.away_club_id)) - club_ids)
    check("club_id known in club mappings", not unknown, str(unknown))
    check("team names filled", m.home_team.notna().all() and m.away_team.notna().all())
    check("league = Premier League", m.league.eq("Premier League").all())

    # ---- formats
    check("date is YYYY-MM-DD", m.date.astype(str).str.fullmatch(r"\d{4}-\d{2}-\d{2}").all())
    k = m.kickoff_time.dropna().astype(str)
    check("kickoff_time is HH:MM", k.str.fullmatch(r"\d{2}:\d{2}").all())
    check("status in {played, upcoming}", m.status.isin(["played", "upcoming"]).all())

    # ---- scores and results
    check("played matches have a score", played.home_goals.notna().all() and played.away_goals.notna().all())
    calc = np.where(played.home_goals > played.away_goals, "H",
                    np.where(played.home_goals < played.away_goals, "A", "D"))
    check("result matches the score", (played.result == calc).all(), f"{(played.result != calc).sum()} wrong")
    goals_text = played.home_goals.astype("Int64").astype(str) + "-" + played.away_goals.astype("Int64").astype(str)
    check("score text matches goals", (played.score == goals_text).all(), f"{(played.score != goals_text).sum()} wrong")
    ht = played.dropna(subset=["ht_home_goals", "ht_away_goals"])
    check("half-time goals <= full-time goals",
          ((ht.ht_home_goals <= ht.home_goals) & (ht.ht_away_goals <= ht.away_goals)).all())
    check("upcoming matches have no score", upcoming.home_goals.isna().all() and upcoming.result.isna().all())
    for col, (lo, hi) in RANGES.items():
        v = pd.to_numeric(m[col], errors="coerce").dropna()
        check(f"{col} in [{lo}, {hi}]", v.between(lo, hi).all(), f"min {v.min()}, max {v.max()}")
    odds = m[[c for c in m.columns if c.endswith("_odds")]]
    check("odds > 1", (odds.dropna(how="all") > 1).where(odds.notna(), True).all().all())
    poss = (m.home_possession + m.away_possession).dropna()
    check("FotMob possession home + away = 100 +- 2", poss.between(98, 102).all(), f"min {poss.min()}, max {poss.max()}")
    fm_seasons = played[played.season.between("2011/12", CURRENT_SEASON)]
    check("FotMob joined for every match since 2011/12", fm_seasons.fm_match_id.notna().all(),
          f"{fm_seasons.fm_match_id.isna().sum()} without FotMob")
    mism = pd.read_csv(OUT_DIR / "mismatches.csv")
    check("no score / date conflicts between sources", mism.empty, str(mism.groupby(["source", "issue"]).size()
                                                                       .to_dict()) if len(mism) else "")

    # ---- season structure
    for season, g in played.groupby("season"):
        teams = pd.concat([g.home_club_id, g.away_club_id])
        n_teams, n, per_team = teams.nunique(), len(g), teams.value_counts()
        if season == CURRENT_SEASON:
            continue
        ok = n_teams == 20 and n == 380 and (per_team == 38).all()
        if not ok and season in KNOWN_INCOMPLETE:
            warnings.append(f"{season}: {n} matches (known gap in the raw source)")
        else:
            check(f"{season}: 20 teams, 380 matches, 38 per team", ok,
                  f"{n_teams} teams, {n} matches")
    cur = m[m.season == CURRENT_SEASON]
    check("current season: each pair at most once per venue", not cur.duplicated(["home_club_id", "away_club_id"]).any())
    check("provisional / upcoming rows only in the current season",
          m[(m.status == "upcoming") | m.provisional.astype(bool)].season.eq(CURRENT_SEASON).all())
    if len(upcoming) == 0:
        warnings.append("no upcoming match (between rounds and none postponed)")

    # ---- features.csv
    check("features.csv has the same matches as matches.csv",
          set(f.match_id) == set(m.match_id) and len(f) == len(m))
    check("leakage self-check passed", feat_report.get("leakage_check_passed", False),
          str(feat_report.get("leakage_check_dates")))
    fu = f[f.status == "upcoming"]
    check("features: upcoming rows have no target", fu[["home_goals", "away_goals", "result"]].isna().all().all())
    check("features: upcoming rows have Elo", fu.home_elo.notna().all() and fu.away_elo.notna().all())
    check("elo_home_win_prob in [0, 1]", f.elo_home_win_prob.between(0, 1).all())
    probs = f[["odds_home_prob", "odds_draw_prob", "odds_away_prob"]].dropna()
    check("odds probabilities sum to 1", np.allclose(probs.sum(axis=1), 1))
    pos = pd.concat([f.home_position, f.away_position]).dropna()
    check("league position in [1, 20]", pos.between(1, 20).all())
    return checks, warnings


def main():
    m = pd.read_csv(OUT_DIR / "matches.csv", low_memory=False)
    f = pd.read_csv(OUT_DIR / "features.csv", low_memory=False)
    with open(OUT_DIR / "features_report.json", encoding="utf-8") as fh:
        feat_report = json.load(fh)

    checks, warnings = run_checks(m, f, feat_report)
    failed = [k for k, v in checks.items() if not v["passed"]]
    report = {"checks": len(checks), "passed": len(checks) - len(failed), "failed": failed,
              "warnings": warnings, "details": checks}
    with open(OUT_DIR / "qa_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)

    print(f"QA: {report['passed']}/{report['checks']} checks passed")
    for w in warnings:
        print(f"  warning: {w}")
    for k in failed:
        print(f"  FAILED : {k} ({checks[k]['detail']})")
    return not failed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
