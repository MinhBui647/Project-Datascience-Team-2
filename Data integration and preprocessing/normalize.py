import json
import re

import pandas as pd

from integrate import EXTRA_MAPPING, FM_COLUMNS, FM_FLOAT, MASTER_DIR, OUT_DIR

LEAGUE = "Premier League"
COUNT_COLUMNS = ["home_goals", "away_goals", "ht_home_goals", "ht_away_goals", "home_shots", "away_shots",
                 "home_shots_on_target", "away_shots_on_target", "home_corners", "away_corners",
                 "home_fouls", "away_fouls", "home_yellow_cards", "away_yellow_cards",
                 "home_red_cards", "away_red_cards", "attendance"]
TEXT_COLUMNS = ["referee", "stadium", "home_manager", "away_manager", "home_formation", "away_formation"]
# misspelled / truncated referee names in the raw files (after initial+surname normalization)
REFEREE_FIXES = {"D Ellaray": "D Elleray", "D Gallagh": "D Gallagher", "D Gallaghe": "D Gallagher"}


def team_names():
    cols = ["club_id", "name_standard"]
    m = pd.concat([pd.read_csv(MASTER_DIR / "club_mapping.csv")[cols],
                   pd.read_csv(EXTRA_MAPPING)[cols]])
    return dict(zip(m.club_id, m.name_standard))


def clean_text(s):
    return s.astype("string").str.strip().str.replace(r"\s+", " ", regex=True).replace("", pd.NA)


def normalize_referee(name):
    """'Mike Dean' / 'M Dean' / 'Dean, M. L' / 'M. L. Dean' -> 'M Dean'."""
    if pd.isna(name):
        return name
    s = str(name)
    if "," in s:  # "Surname, First" -> "First Surname"
        surname, first = s.split(",", 1)
        s = f"{first} {surname}"
    tokens = s.replace(".", " ").split()
    if len(tokens) == 1:
        return tokens[0]
    surname = tokens[-1]
    return f"{tokens[0][0].upper()} {surname[0].upper()}{surname[1:]}"


def formation_shape(s):
    return s.str.extract(r"^(\d(?:-\d)+)", expand=False)


def check_formats(df):
    """Count values that do not have the expected format; stage 3 decides what to do with them."""
    checks = {
        "date_not_iso": ~df.date.astype(str).str.fullmatch(r"\d{4}-\d{2}-\d{2}"),
        "kickoff_time_not_hh_mm": df.kickoff_time.notna() & ~df.kickoff_time.astype(str).str.fullmatch(r"\d{2}:\d{2}"),
        "result_not_hda": ~df.result.isin(["H", "D", "A"]),
        "ht_result_not_hda": df.ht_result.notna() & ~df.ht_result.isin(["H", "D", "A"]),
        "goals_missing": df.home_goals.isna() | df.away_goals.isna(),
        "team_name_missing": df.home_team.isna() | df.away_team.isna(),
        "match_id_duplicated": df.match_id.duplicated(keep=False),
    }
    return {k: int(v.sum()) for k, v in checks.items()}


def main():
    df = pd.read_csv(OUT_DIR / "integrated.csv", low_memory=False)

    # league, season, ids
    df["league"] = LEAGUE
    df["season_start_year"] = df.season.str[:4].astype(int)
    df["match_id"] = ("EPL_" + df.season_start_year.astype(str) + "_" + (df.season_start_year + 1).astype(str)
                      + "_" + df.home_club_id + "_" + df.away_club_id)

    # date and time
    df["date"] = pd.to_datetime(df.date, errors="coerce").dt.strftime("%Y-%m-%d")
    t = pd.to_datetime(df.kickoff_time, format="%H:%M", errors="coerce")
    df["kickoff_time"] = t.dt.strftime("%H:%M").where(t.notna(), df.kickoff_time)

    # teams
    names = team_names()
    df["home_team"] = df.home_club_id.map(names)
    df["away_team"] = df.away_club_id.map(names)

    # scores
    for c in COUNT_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    for c in ["result", "ht_result"]:
        df[c] = clean_text(df[c]).str.upper()
    df["score"] = df.home_goals.astype("string") + "-" + df.away_goals.astype("string")
    df["ht_score"] = df.ht_home_goals.astype("string") + "-" + df.ht_away_goals.astype("string")

    # text, referee, formation
    for c in TEXT_COLUMNS:
        df[c] = clean_text(df[c])
    raw_referee = df.referee.copy()
    df["referee"] = df.referee.map(normalize_referee).replace(REFEREE_FIXES).astype("string")
    changed = raw_referee.notna() & (raw_referee != df.referee)
    ref_map = (pd.DataFrame({"referee_raw": raw_referee[changed], "referee": df.referee[changed]})
               .value_counts().reset_index(name="matches").sort_values(["referee", "referee_raw"]))
    for side in ["home", "away"]:
        df[f"{side}_formation_shape"] = formation_shape(df[f"{side}_formation"])

    # odds / xG as floats, FotMob counts as integers
    for c in [c for c in df.columns if c.endswith("_odds")] + ["home_xg", "away_xg"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in FM_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if not c.endswith(tuple(FM_FLOAT)):
            df[c] = df[c].round().astype("Int64")
    df["fm_match_id"] = pd.to_numeric(df.fm_match_id, errors="coerce").astype("Int64")

    order = ["match_id", "league", "league_code", "season", "season_start_year", "date", "kickoff_time",
             "home_club_id", "home_team", "away_club_id", "away_team",
             "score", "home_goals", "away_goals", "result", "ht_score", "ht_home_goals", "ht_away_goals", "ht_result",
             "home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
             "home_corners", "away_corners", "home_fouls", "away_fouls",
             "home_yellow_cards", "away_yellow_cards", "home_red_cards", "away_red_cards",
             "home_xg", "away_xg", "attendance", "stadium", "referee", "home_manager", "away_manager",
             "home_formation", "home_formation_shape", "away_formation", "away_formation_shape"] + FM_COLUMNS + \
        [c for c in df.columns if c.endswith("_odds")] + ["fm_match_id", "source_flags"]
    df = df[order]

    report = {
        "rows": len(df),
        "format_issues": check_formats(df),
        "referees": {"spellings_before": int(raw_referee.nunique()), "after": int(df.referee.nunique()),
                     "rows_changed": int(changed.sum()),
                     # possibly a typo of another referee - reviewed in stage 3
                     "single_match_names": sorted(df.referee.value_counts()[lambda s: s == 1].index.tolist())},
        "formations": {"labels": int(pd.concat([df.home_formation, df.away_formation]).nunique()),
                       "shapes": int(pd.concat([df.home_formation_shape, df.away_formation_shape]).nunique())},
    }
    df.to_csv(OUT_DIR / "normalized.csv", index=False)
    ref_map.to_csv(OUT_DIR / "referee_mapping.csv", index=False)
    with open(OUT_DIR / "normalize_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"normalized.csv : {len(df)} matches x {df.shape[1]} columns")
    print(f"referees       : {report['referees']}")
    print(f"formations     : {report['formations']}")
    issues = {k: v for k, v in report["format_issues"].items() if v}
    print(f"format issues  : {issues or 'none'}")


if __name__ == "__main__":
    main()
