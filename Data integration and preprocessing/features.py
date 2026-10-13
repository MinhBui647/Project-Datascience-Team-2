
import json
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from integrate import OUT_DIR

WINDOW = 5
ELO_START, ELO_NEW_TEAM, ELO_K, ELO_HOME_ADV, ELO_CARRY = 1500, 1450, 20, 65, 0.8
ID_COLS = ["match_id", "season", "date", "kickoff_time", "gameweek", "home_club_id", "home_team",
           "away_club_id", "away_team", "status", "provisional", "postponed"]
TARGETS = ["home_goals", "away_goals", "result"]
# FotMob style-of-play form: feature -> (matches.csv column without home_/away_, own team or opponent)
FM_FORM = {
    "possession5": ("possession", "for"), "pass_accuracy5": ("pass_accuracy", "for"),
    "big_chances5": ("big_chances", "for"), "big_chances_against5": ("big_chances", "against"),
    "box_shots5": ("shots_inside_box", "for"), "box_shots_against5": ("shots_inside_box", "against"),
    "box_touches5": ("touches_opp_box", "for"), "box_touches_against5": ("touches_opp_box", "against"),
    "xgot5": ("fm_xgot", "for"), "xgot_against5": ("fm_xgot", "against"),
}
FORM_KEYS = ["form", "wins5", "draws5", "losses5", "points5", "goals_for5", "goals_against5", "shots_on_target5",
             "xg5", "xga5", "opp_elo5", "perf_vs_elo5", *FM_FORM, "played", "ppg", "goals_for_pg",
             "goals_against_pg", "rest_days"]
DIFF_KEYS = ["points5", "goals_for5", "goals_against5", "shots_on_target5", "xg5", "xga5", "ppg", "perf_vs_elo5",
             "possession5", "big_chances5", "big_chances_against5", "box_touches5", "xgot5", "xgot_against5"]


def points(gf, ga):
    return 3 if gf > ga else 1 if gf == ga else 0


def mean(values):
    vals = [v for v in values if v is not None and not pd.isna(v)]
    return float(np.mean(vals)) if vals else np.nan


def elo_multiplier(goal_diff):
    gd = abs(goal_diff)
    return 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


class TeamState:
    def __init__(self):
        self.last = deque(maxlen=WINDOW)                                  # dicts of the last 5 matches
        self.venue = {"home": deque(maxlen=WINDOW), "away": deque(maxlen=WINDOW)}
        self.season, self.played, self.pts, self.gf, self.ga = None, 0, 0, 0, 0
        self.last_date = None

    def new_season(self, season):
        if self.season != season:
            self.season, self.played, self.pts, self.gf, self.ga = season, 0, 0, 0, 0


def team_features(st, venue, date, postponed):
    last, ven = list(st.last), list(st.venue[venue])
    f = {
        "form": "".join(m["res"] for m in last) or np.nan,
        "wins5": sum(m["res"] == "W" for m in last) if last else np.nan,
        "draws5": sum(m["res"] == "D" for m in last) if last else np.nan,
        "losses5": sum(m["res"] == "L" for m in last) if last else np.nan,
        "points5": mean(m["pts"] for m in last),
        "goals_for5": mean(m["gf"] for m in last),
        "goals_against5": mean(m["ga"] for m in last),
        "shots_on_target5": mean(m["sot"] for m in last),
        "xg5": mean(m["xg"] for m in last),
        "xga5": mean(m["xga"] for m in last),
        "opp_elo5": mean(m["opp_elo"] for m in last),          # how strong the last 5 opponents were
        "perf_vs_elo5": mean(m["perf_vs_elo"] for m in last),  # > 0: better results than Elo expected
        **{k: mean(m[k] for m in last) for k in FM_FORM},
        f"{venue}_points5": mean(m["pts"] for m in ven),
        f"{venue}_goals_for5": mean(m["gf"] for m in ven),
        f"{venue}_goals_against5": mean(m["ga"] for m in ven),
        "played": st.played,
        "ppg": st.pts / st.played if st.played else np.nan,
        "goals_for_pg": st.gf / st.played if st.played else np.nan,
        "goals_against_pg": st.ga / st.played if st.played else np.nan,
        "rest_days": (date - st.last_date).days if st.last_date is not None and not postponed else np.nan,
    }
    return f


def table_positions(teams_in_season, state, season):
    rows = []
    for t in teams_in_season:
        s = state[t]
        cur = s.season == season
        rows.append((-(s.pts if cur else 0), -((s.gf - s.ga) if cur else 0), -(s.gf if cur else 0), t))
    return {t: i + 1 for i, (*_, t) in enumerate(sorted(rows))}


def implied_probs(df):
    odds = {}
    for side in ["home", "draw", "away"]:
        odds[side] = df[f"avg_{side}_odds"].fillna(df[f"b365_{side}_odds"])
    inv = pd.DataFrame({k: 1 / v for k, v in odds.items()})
    total = inv.sum(axis=1, min_count=3)
    return {f"odds_{k}_prob": inv[k] / total for k in inv}


def build_features(matches):
    df = matches.copy()
    df["match_dt"] = pd.to_datetime(df.date)
    df["is_played"] = df.status.eq("played")
    df = df.sort_values(["is_played", "match_dt", "kickoff_time"], ascending=[False, True, True],
                        na_position="last", kind="stable")
    df = pd.concat([df[df.is_played], df[~df.is_played]]).reset_index(drop=True)

    season_teams = df.groupby("season").apply(lambda g: sorted(set(g.home_club_id) | set(g.away_club_id)))
    first_season = df.season.min()
    state = defaultdict(TeamState)
    elo, elo_season = {}, None
    h2h = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []

    for r in df.itertuples(index=False):
        home, away, season = r.home_club_id, r.away_club_id, r.season
        if r.is_played and season != elo_season: 
            if elo_season is not None:
                elo = {t: ELO_CARRY * v + (1 - ELO_CARRY) * ELO_START for t, v in elo.items()}
            elo_season = season
        for t in (home, away):
            state[t].new_season(season)

        default = ELO_START if season == first_season else ELO_NEW_TEAM
        rh, ra = elo.get(home, default), elo.get(away, default)
        pos = table_positions(season_teams[season], state, season)
        postponed = bool(r.postponed) if not pd.isna(r.postponed) else False
        f = {"match_id": r.match_id, "home_elo": rh, "away_elo": ra, "elo_diff": rh - ra,
             "elo_home_win_prob": 1 / (1 + 10 ** ((ra - rh - ELO_HOME_ADV) / 400)),
             "home_position": pos[home] if state[home].played else np.nan,
             "away_position": pos[away] if state[away].played else np.nan}
        for side, team in [("home", home), ("away", away)]:
            for k, v in team_features(state[team], side, r.match_dt, postponed).items():
                f[f"{side}_{k}"] = v
        past = list(h2h[frozenset((home, away))])
        f["h2h_matches"] = len(past)
        f["h2h_home_points"] = mean(points(hg, ag) if h == home else points(ag, hg) for h, hg, ag in past)
        f["h2h_home_goal_diff"] = mean((hg - ag) if h == home else (ag - hg) for h, hg, ag in past)
        rows.append(f)

        if not r.is_played or pd.isna(r.home_goals) or pd.isna(r.away_goals):
            continue
        hg, ag = int(r.home_goals), int(r.away_goals)
        surprise = (1.0 if hg > ag else 0.5 if hg == ag else 0.0) - 1 / (1 + 10 ** ((ra - rh - ELO_HOME_ADV) / 400))
        delta = ELO_K * elo_multiplier(hg - ag) * surprise
        elo[home], elo[away] = rh + delta, ra - delta
        for side, opp, team, gf, ga, sot, xg, xga, opp_elo, perf in [
                ("home", "away", home, hg, ag, r.home_shots_on_target, r.home_xg, r.away_xg, ra, surprise),
                ("away", "home", away, ag, hg, r.away_shots_on_target, r.away_xg, r.home_xg, rh, -surprise)]:
            st = state[team]
            m = {"res": "W" if gf > ga else "D" if gf == ga else "L", "pts": points(gf, ga),
                 "gf": gf, "ga": ga, "sot": sot, "xg": xg, "xga": xga, "opp_elo": opp_elo, "perf_vs_elo": perf,
                 **{k: getattr(r, f"{side if who == 'for' else opp}_{col}") for k, (col, who) in FM_FORM.items()}}
            st.last.append(m)
            st.venue[side].append(m)
            st.played, st.pts, st.gf, st.ga = st.played + 1, st.pts + m["pts"], st.gf + gf, st.ga + ga
            st.last_date = r.match_dt
        h2h[frozenset((home, away))].append((home, hg, ag))

    feats = pd.DataFrame(rows)
    out = df.drop(columns=["match_dt", "is_played"]).merge(feats, on="match_id", how="left")
    for k, v in implied_probs(out).items():
        out[k] = v
    out["position_diff"] = out.home_position - out.away_position
    for c in DIFF_KEYS:
        out[f"diff_{c}"] = out[f"home_{c}"] - out[f"away_{c}"]
    return out


def feature_columns(out):
    return [c for c in out.columns if c not in ID_COLS + TARGETS and
            (c in {"h2h_matches", "h2h_home_points", "h2h_home_goal_diff", "position_diff"} or
             c.startswith(("home_elo", "away_elo", "elo_", "odds_", "diff_", "home_position", "away_position")) or
             any(c == f"{s}_{k}" for s in ("home", "away") for k in FORM_KEYS) or
             c in {"home_home_points5", "home_home_goals_for5", "home_home_goals_against5",
                   "away_away_points5", "away_away_goals_for5", "away_away_goals_against5"})]


def leakage_check(matches, full, cols, n_dates=5, seed=0):
    """Recompute features with only matches up to a cut-off date; features of matches ON that date
    must be identical, i.e. they never depend on the match itself or later matches."""
    played = matches[matches.status == "played"]
    dates = played.date.drop_duplicates().sample(n_dates, random_state=seed).sort_values()
    numeric = [c for c in cols if pd.api.types.is_numeric_dtype(full[c])]
    results = {}
    for d in dates:
        part = build_features(played[played.date <= d])
        a = part[part.date == d].set_index("match_id")[numeric].sort_index()
        b = full[full.match_id.isin(a.index)].set_index("match_id")[numeric].sort_index()
        results[d] = bool(np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True))
    return results


def main():
    matches = pd.read_csv(OUT_DIR / "matches.csv", low_memory=False)
    out = build_features(matches)
    cols = feature_columns(out)
    final = out[ID_COLS + cols + TARGETS].copy()
    ints = ["gameweek", "home_goals", "away_goals", "home_position", "away_position", "position_diff",
            "home_rest_days", "away_rest_days"] + \
        [f"{s}_{k}" for s in ("home", "away") for k in ("wins5", "draws5", "losses5", "played")]
    final[ints] = final[ints].apply(lambda s: pd.to_numeric(s, errors="coerce").round().astype("Int64"))
    final = final.sort_values(["status", "season", "date", "kickoff_time"], ascending=[True, True, True, True],
                              key=lambda s: s.map({"played": 0, "upcoming": 1}) if s.name == "status" else s,
                              kind="stable")
    final.to_csv(OUT_DIR / "features.csv", index=False)

    checks = leakage_check(matches, out, cols)
    played = final[final.status == "played"]
    report = {
        "rows": len(final), "played": len(played), "upcoming": int((final.status == "upcoming").sum()),
        "feature_columns": len(cols),
        "leakage_check_passed": all(checks.values()), "leakage_check_dates": checks,
        "missing_rate_played": {c: round(float(played[c].isna().mean()), 3) for c in cols if played[c].isna().any()},
        "missing_rate_upcoming": {c: round(float(final.loc[final.status == "upcoming", c].isna().mean()), 3)
                                  for c in cols if final.loc[final.status == "upcoming", c].isna().any()},
    }
    with open(OUT_DIR / "features_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"features.csv : {len(final)} rows ({report['played']} played, {report['upcoming']} upcoming), "
          f"{len(cols)} features")
    print(f"leakage check: {'passed' if report['leakage_check_passed'] else 'FAILED'} {checks}")
    up = final[final.status == "upcoming"]
    if len(up):
        print(up[["date", "home_team", "away_team", "home_elo", "away_elo", "home_position", "away_position",
                  "home_form", "away_form"]].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
