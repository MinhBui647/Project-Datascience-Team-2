
import json

import numpy as np
import pandas as pd

from integrate import CURRENT_SEASON, FM_FLOAT, OUT_DIR

CLOSED_DOORS = (pd.Timestamp("2020-06-17"), pd.Timestamp("2021-05-23"))
SEASON_MATCHES = 380
REFEREE_MIN_MATCHES = 5
POSSESSION_TOLERANCE = 2  # home + away possession must be 100 +- 2 (rounding)

# column -> (min, max) valid range; None = unbounded
RANGES = {
    **{c: (0, 20) for c in ["home_goals", "away_goals", "ht_home_goals", "ht_away_goals"]},
    **{c: (0, 60) for c in ["home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target"]},
    **{c: (0, 30) for c in ["home_corners", "away_corners"]},
    **{c: (0, 50) for c in ["home_fouls", "away_fouls"]},
    **{c: (0, 11) for c in ["home_yellow_cards", "away_yellow_cards"]},
    **{c: (0, 20) for c in ["home_red_cards", "away_red_cards"]},
    **{c: (0, 10) for c in ["home_xg", "away_xg"]},
    "attendance": (0, 100_000),
    # FotMob
    **{f"{s}_{c}": rng for s in ("home", "away") for c, rng in {
        "possession": (0, 100), "pass_accuracy": (0, 100),
        "big_chances": (0, 20), "big_chances_missed": (0, 20),
        "shots_inside_box": (0, 60), "shots_outside_box": (0, 60),
        "passes": (0, 1500), "accurate_passes": (0, 1500), "touches_opp_box": (0, 150),
        "tackles": (0, 80), "interceptions": (0, 60), "clearances": (0, 150), "duels_won": (0, 200),
        "saves": (0, 30),
        **{c: (0, 10) for c in ["fm_xg", "fm_npxg", "fm_xg_open_play", "fm_xg_set_play", "fm_xgot"]},
    }.items()},
}
FLOAT_COLUMNS = {"home_xg", "away_xg"} | {f"{s}_{c}" for s in ("home", "away") for c in FM_FLOAT}

log = []


def record(df, mask, column, new_value, rule, reason):
    """Log every changed cell, then apply the change."""
    for i in df.index[mask]:
        log.append({"match_id": df.at[i, "match_id"], "column": column, "old_value": df.at[i, column],
                    "new_value": new_value if not isinstance(new_value, pd.Series) else new_value[i],
                    "rule": rule, "reason": reason})
    df.loc[mask, column] = new_value[mask] if isinstance(new_value, pd.Series) else new_value


# ---------------------------------------------------------------- rules
def drop_duplicates(df):
    df = df.assign(_filled=df.notna().sum(axis=1)).sort_values("_filled", ascending=False)
    dup = df.duplicated("match_id") | df.duplicated(["season", "home_club_id", "away_club_id"])
    for _, r in df[dup].iterrows():
        log.append({"match_id": r.match_id, "column": "*", "old_value": "row", "new_value": "dropped",
                    "rule": "R1", "reason": "duplicate match"})
    return df[~dup].drop(columns="_filled").sort_values(["season", "date", "home_club_id"]).reset_index(drop=True)


def fix_formats_and_ranges(df):
    parsed = pd.to_datetime(df.date, format="%Y-%m-%d", errors="coerce")
    record(df, parsed.isna() & df.date.notna(), "date", np.nan, "R2", "unparseable date")
    for col, (lo, hi) in RANGES.items():
        num = pd.to_numeric(df[col], errors="coerce")
        record(df, num.isna() & df[col].notna(), col, np.nan, "R2", "not a number")
        record(df, num.notna() & ((num < lo) | (num > hi)), col, np.nan, "R3", f"outside [{lo}, {hi}]")
    for col in [c for c in df.columns if c.endswith("_odds")]:
        num = pd.to_numeric(df[col], errors="coerce")
        record(df, num.notna() & (num <= 1), col, np.nan, "R3", "odds <= 1")


def outcome(home, away):
    return pd.Series(np.where(home > away, "H", np.where(home < away, "A", "D")), index=home.index)


def fix_consistency(df):
    ok = df.home_goals.notna() & df.away_goals.notna()
    calc = outcome(df.home_goals, df.away_goals)
    record(df, ok & (df.result != calc), "result", calc, "R4", "result disagrees with score")

    bad_ht = (df.ht_home_goals > df.home_goals) | (df.ht_away_goals > df.away_goals)
    for c in ["ht_home_goals", "ht_away_goals", "ht_result", "ht_score"]:
        record(df, bad_ht.fillna(False), c, np.nan, "R5", "half-time goals > full-time goals")
    ok_ht = df.ht_home_goals.notna() & df.ht_away_goals.notna()
    calc_ht = outcome(df.ht_home_goals, df.ht_away_goals)
    record(df, ok_ht & df.ht_result.notna() & (df.ht_result != calc_ht), "ht_result", calc_ht, "R4",
           "ht_result disagrees with half-time score")

    for side in ["home", "away"]:
        bad = (df[f"{side}_shots_on_target"] > df[f"{side}_shots"]).fillna(False)
        for c in [f"{side}_shots", f"{side}_shots_on_target"]:
            record(df, bad, c, np.nan, "R6", "shots on target > shots")

    # FotMob
    total = df.home_possession + df.away_possession
    bad = ((total - 100).abs() > POSSESSION_TOLERANCE).fillna(False)
    for c in ["home_possession", "away_possession"]:
        record(df, bad, c, np.nan, "R10", "home + away possession != 100")
    for side in ["home", "away"]:
        for part, whole in [("accurate_passes", "passes"), ("big_chances_missed", "big_chances")]:
            bad = (df[f"{side}_{part}"] > df[f"{side}_{whole}"]).fillna(False)
            for c in [f"{side}_{part}", f"{side}_{whole}"] + ([f"{side}_pass_accuracy"] if whole == "passes" else []):
                record(df, bad, c, np.nan, "R11", f"{part} > {whole}")


def fix_referees(df):
    counts = df.groupby(["season", "referee"]).size()
    single = df.referee.notna() & (df.referee.map(df.referee.value_counts()) == 1)
    for i in df.index[single]:
        season, name = df.at[i, "season"], df.at[i, "referee"]
        surname = name.split(" ", 1)[-1]
        same = counts.loc[season]
        candidates = [r for r, n in same.items()
                      if r != name and r.split(" ", 1)[-1] == surname and n >= REFEREE_MIN_MATCHES]
        if len(candidates) == 1:
            record(df, df.index == i, "referee", candidates[0], "R7",
                   f"single-match name, {candidates[0]} officiated {same[candidates[0]]} matches in {season}")


def check_season_window(df):
    d = pd.to_datetime(df.date)
    start = pd.to_datetime(df.season_start_year.astype(str) + "-07-01")
    end = pd.to_datetime((df.season_start_year + 1).astype(str) + "-07-31")
    return df.loc[(d < start) | (d > end), "match_id"].tolist()


def fill_closed_doors(df):
    d = pd.to_datetime(df.date)
    mask = df.attendance.isna() & d.between(*CLOSED_DOORS)
    df["attendance_imputed"] = mask
    record(df, mask, "attendance", 0, "R9", "COVID: match behind closed doors")


# ---------------------------------------------------------------- report
def missing_report(df):
    cols = [c for c in df.columns if df[c].isna().any()]
    by_season = df.groupby("season")[cols].apply(lambda g: g.isna().mean().round(3))
    out = {}
    for c in cols:
        rates = by_season[c]
        structural = rates[rates == 1.0].index.tolist()  # column not published at all that season
        partial = rates[(rates > 0) & (rates < 1)]
        out[c] = {"missing_total": round(float(df[c].isna().mean()), 3),
                  "seasons_not_available": structural,
                  "seasons_partially_missing": partial.to_dict()}
    return out


def main():
    df = pd.read_csv(OUT_DIR / "normalized.csv", low_memory=False)
    n_in = len(df)

    df = drop_duplicates(df)
    fix_formats_and_ranges(df)
    fix_consistency(df)
    fix_referees(df)
    fill_closed_doors(df)
    outside_window = check_season_window(df)

    for c in [c for c in RANGES if c not in FLOAT_COLUMNS]:  # count columns back to integers
        df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    per_season = df.groupby("season").size()
    df["season_complete"] = df.season.map(per_season) == SEASON_MATCHES
    df["status"] = "played"

    df.to_csv(OUT_DIR / "cleaned.csv", index=False)
    log_df = pd.DataFrame(log, columns=["match_id", "column", "old_value", "new_value", "rule", "reason"])
    log_df.to_csv(OUT_DIR / "cleaning_log.csv", index=False)

    report = {
        "rows_in": n_in, "rows_out": len(df),
        "changes_by_rule": {f"{rule} {reason}": int(n)
                            for (rule, reason), n in log_df.groupby(["rule", "reason"]).size().items()},
        "incomplete_seasons": {s: int(n) for s, n in per_season[per_season != SEASON_MATCHES].items()},
        "current_season": CURRENT_SEASON,
        "matches_outside_season_window": outside_window,
        "not_errors": {"goals_greater_than_shots_on_target": int(((df.home_goals > df.home_shots_on_target) |
                                                                   (df.away_goals > df.away_shots_on_target)).sum()),
                       "explanation": "own goals are not shots of the scoring team"},
        "missing_values": missing_report(df),
    }
    with open(OUT_DIR / "clean_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print(f"cleaned.csv     : {len(df)} matches x {df.shape[1]} columns (in: {n_in})")
    print(f"changes by rule : {report['changes_by_rule'] or 'none'}")
    print(f"incomplete      : {report['incomplete_seasons']} (current season: {CURRENT_SEASON})")
    print(f"outside window  : {outside_window or 'none'}")


if __name__ == "__main__":
    main()
