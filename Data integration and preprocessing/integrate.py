
import json
import re
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW_DIR = ROOT / "data" / "raw_data"
MASTER_DIR = ROOT / "data" / "00_master_entities"
OUT_DIR = ROOT / "data" / "clean"
EXTRA_MAPPING = OUT_DIR / "club_mapping_extra.csv"  

FIRST_SEASON, LAST_SEASON = 2000, 2026  
CURRENT_SEASON = f"{LAST_SEASON}/{str(LAST_SEASON + 1)[-2:]}"  
KEY = ["season", "home_club_id", "away_club_id"]
MAX_DATE_GAP_DAYS = 1

FOTMOB_DIR = RAW_DIR / "sofascore"                          # the folder holds FotMob files despite its name
FOTMOB_ARCHIVE = FOTMOB_DIR / "sofascore_matches_cache.tar.gz"  # matchDetails JSON, 2010/11 -> 2026/27
FOTMOB_NEW_DIR = FOTMOB_DIR / "cache_matches"               # matchDetails downloaded later by stage 4
FOTMOB_EXTRACT = OUT_DIR / "fotmob_extracted.csv"           # parsed archive, avoids re-reading 6,000 JSON files
# FotMob stat key -> column name (home_<name> / away_<name>); "fm_" where another source has the same concept
FM_STATS = {
    "BallPossesion": "possession", "big_chance": "big_chances", "big_chance_missed_title": "big_chances_missed",
    "shots_inside_box": "shots_inside_box", "shots_outside_box": "shots_outside_box",
    "passes": "passes", "accurate_passes": "accurate_passes", "touches_opp_box": "touches_opp_box",
    "matchstats.headers.tackles": "tackles", "interceptions": "interceptions", "clearances": "clearances",
    "duel_won": "duels_won", "keeper_saves": "saves",
    "expected_goals": "fm_xg", "expected_goals_non_penalty": "fm_npxg",
    "expected_goals_open_play": "fm_xg_open_play", "expected_goals_set_play": "fm_xg_set_play",
    "expected_goals_on_target": "fm_xgot",
}
FM_FLOAT = ["fm_xg", "fm_npxg", "fm_xg_open_play", "fm_xg_set_play", "fm_xgot", "pass_accuracy"]
FM_COLUMNS = [f"{side}_{c}" for c in list(FM_STATS.values()) + ["pass_accuracy"] for side in ("home", "away")]

FD_COLUMNS = {
    "Div": "league_code", "Time": "kickoff_time",
    "FTHG": "home_goals", "FTAG": "away_goals", "FTR": "result",
    "HTHG": "ht_home_goals", "HTAG": "ht_away_goals", "HTR": "ht_result",
    "HS": "home_shots", "AS": "away_shots",
    "HST": "home_shots_on_target", "AST": "away_shots_on_target",
    "HC": "home_corners", "AC": "away_corners",
    "HF": "home_fouls", "AF": "away_fouls",
    "HY": "home_yellow_cards", "AY": "away_yellow_cards",
    "HR": "home_red_cards", "AR": "away_red_cards",
    "Referee": "referee", "Attendance": "fd_attendance",
    "B365H": "b365_home_odds", "B365D": "b365_draw_odds", "B365A": "b365_away_odds",
    "PSH": "pinnacle_home_odds", "PSD": "pinnacle_draw_odds", "PSA": "pinnacle_away_odds",
    "AvgH": "avg_home_odds", "AvgD": "avg_draw_odds", "AvgA": "avg_away_odds",
    "B365CH": "b365_close_home_odds", "B365CD": "b365_close_draw_odds", "B365CA": "b365_close_away_odds",
    "PSCH": "pinnacle_close_home_odds", "PSCD": "pinnacle_close_draw_odds", "PSCA": "pinnacle_close_away_odds",
    "AvgCH": "avg_close_home_odds", "AvgCD": "avg_close_draw_odds", "AvgCA": "avg_close_away_odds",
}
FD_FALLBACK = {"avg_home_odds": "BbAvH", "avg_draw_odds": "BbAvD", "avg_away_odds": "BbAvA"}
INT_COLUMNS = ["home_goals", "away_goals", "ht_home_goals", "ht_away_goals", "home_shots", "away_shots",
               "home_shots_on_target", "away_shots_on_target", "home_corners", "away_corners",
               "home_fouls", "away_fouls", "home_yellow_cards", "away_yellow_cards",
               "home_red_cards", "away_red_cards", "fd_attendance"]


def season_label(start_year):
    return f"{start_year}/{str(start_year + 1)[-2:]}"  


# ---------------------------------------------------------------- club mapping
def load_club_mapping():
    cols = ["club_id", "name_standard", "name_football_data", "name_understat", "name_transfermarkt", "tm_club_id",
            "fotmob_team_id"]
    master = pd.read_csv(MASTER_DIR / "club_mapping.csv")[cols]
    extra = pd.read_csv(EXTRA_MAPPING)[cols]
    m = pd.concat([master, extra], ignore_index=True)
    dup = m.club_id[m.club_id.duplicated()].tolist()
    if dup:
        raise ValueError(f"club_id defined twice in club mappings: {dup}")

    def lookup(col):
        s = m.dropna(subset=[col])
        return dict(zip(s[col], s.club_id))

    tm = m.dropna(subset=["tm_club_id"])
    fm = m.dropna(subset=["fotmob_team_id"])
    return {
        "football_data": lookup("name_football_data"),
        "understat": lookup("name_understat"),
        "transfermarkt": dict(zip(tm.tm_club_id.astype(int), tm.club_id)),
        "fotmob": dict(zip(fm.fotmob_team_id.astype(int), fm.club_id)),  # by id: FotMob names change over time
    }


def map_teams(df, cols, mapping, source):
    for c in cols:
        unknown = sorted(set(df[c].dropna()) - set(mapping))
        if unknown:
            raise ValueError(f"[{source}] team names without club_id - add them to club_mapping_extra.csv: {unknown}")
    return [df[c].map(mapping) for c in cols]


# ---------------------------------------------------------------- sources
def parse_fd_date(s):
    """football-data mixes dd/mm/yy (older files) and dd/mm/yyyy."""
    four = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    two = pd.to_datetime(s, format="%d/%m/%y", errors="coerce")
    return four.fillna(two)


def load_football_data(club_map):
    frames = []
    for year in range(FIRST_SEASON, LAST_SEASON + 1):
        code = f"{year % 100:02d}{(year + 1) % 100:02d}"
        path = RAW_DIR / "football_data_co_uk" / f"football_data_raw_{code}.csv"
        raw = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip", low_memory=False)
        raw.columns = raw.columns.str.replace("ï»¿", "", regex=False).str.replace("﻿", "", regex=False).str.strip()
        raw = raw.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])  # trailing blank rows

        df = pd.DataFrame({"season": season_label(year), "date": parse_fd_date(raw.Date.astype(str).str.strip())})
        df["home_club_id"], df["away_club_id"] = map_teams(raw, ["HomeTeam", "AwayTeam"], club_map, "football-data")
        for src, dst in FD_COLUMNS.items():
            df[dst] = raw[src].values if src in raw else np.nan
        for dst, src in FD_FALLBACK.items():
            if src in raw:
                df[dst] = df[dst].fillna(pd.Series(raw[src].values, index=df.index))
        frames.append(df)

    fd = pd.concat(frames, ignore_index=True)
    for c in INT_COLUMNS:
        fd[c] = pd.to_numeric(fd[c], errors="coerce").round().astype("Int64")
    for c in [c for c in fd.columns if c.endswith("_odds")]:
        fd[c] = pd.to_numeric(fd[c], errors="coerce")
    bad_dates = fd.date.isna().sum()
    if bad_dates:
        raise ValueError(f"[football-data] {bad_dates} dates could not be parsed")
    return fd


def load_understat(club_map):
    u = pd.read_csv(RAW_DIR / "understat" / "understat_matches_all_2014_2027.csv")
    u = u[u.is_result.astype(str).str.lower() == "true"].copy()
    start = u.season.str[:4].astype(int)
    u = u[(start >= FIRST_SEASON) & (start <= LAST_SEASON)].copy()
    u["season"] = u.season.str[:4].astype(int).map(season_label)
    u["home_club_id"], u["away_club_id"] = map_teams(u, ["home_team", "away_team"], club_map, "understat")
    out = pd.DataFrame({
        "season": u.season, "home_club_id": u.home_club_id, "away_club_id": u.away_club_id,
        "us_date": pd.to_datetime(u.datetime).dt.normalize(),
        "us_home_goals": u.home_goals.astype("Int64"), "us_away_goals": u.away_goals.astype("Int64"),
        "home_xg": u.home_xg.astype(float), "away_xg": u.away_xg.astype(float),
    })
    return out


def load_transfermarkt(club_map):
    g = pd.read_csv(RAW_DIR / "transfermarkt" / "tfm_games_full.csv.gz", low_memory=False)
    g = g[(g.competition_id == "GB1") & g.season.between(FIRST_SEASON, LAST_SEASON)].copy()
    g["season"] = g.season.astype(int).map(season_label)
    g["home_club_id"], g["away_club_id"] = map_teams(g, ["home_club_id", "away_club_id"], club_map, "transfermarkt")
    out = pd.DataFrame({
        "season": g.season, "home_club_id": g.home_club_id, "away_club_id": g.away_club_id,
        "tm_date": pd.to_datetime(g.date),
        "tm_home_goals": g.home_club_goals.astype("Int64"), "tm_away_goals": g.away_club_goals.astype("Int64"),
        "tm_attendance": pd.to_numeric(g.attendance, errors="coerce").astype("Int64"),
        "stadium": g.stadium, "tm_referee": g.referee,
        "home_manager": g.home_club_manager_name, "away_manager": g.away_club_manager_name,
        "home_formation": g.home_club_formation, "away_formation": g.away_club_formation,
    })
    return out


def fm_number(v):
    """FotMob values: 64 / '1.88' / '583 (89%)' -> 64.0 / 1.88 / 583.0."""
    if v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else np.nan


def parse_fotmob_match(d):
    """One matchDetails JSON -> one flat row; None if it is not a finished Premier League match."""
    if not isinstance(d, dict) or "general" not in d:
        return None  # empty / error response stored in the archive
    g, status = d["general"], d["header"]["status"]
    abandoned = status.get("cancelled") or (status.get("reason") or {}).get("short") == "Ab"
    if 47 not in (g.get("leagueId"), g.get("parentLeagueId")) or not g.get("finished") or abandoned:
        return None
    teams = d["header"]["teams"]
    row = {"fm_match_id": int(g["matchId"]), "fm_kickoff_utc": g["matchTimeUTCDate"],
           "fm_home_team_id": int(g["homeTeam"]["id"]), "fm_away_team_id": int(g["awayTeam"]["id"]),
           "fm_home_goals": teams[0].get("score"), "fm_away_goals": teams[1].get("score")}
    periods = ((d.get("content") or {}).get("stats") or {}).get("Periods") or {}
    for group in (periods.get("All") or {}).get("stats", []):
        for s in group.get("stats", []):
            name, vals = FM_STATS.get(s.get("key")), s.get("stats") or []
            # a stat can appear in two groups (top stats + its own group): keep the first one
            if name and len(vals) == 2 and vals[0] is not None and f"home_{name}" not in row:
                row[f"home_{name}"], row[f"away_{name}"] = fm_number(vals[0]), fm_number(vals[1])
    return row


def read_fotmob_details():
    """Parsed archive (cached in FOTMOB_EXTRACT) + match files downloaded by stage 4, which win on the same id."""
    cached = None
    if FOTMOB_EXTRACT.exists() and FOTMOB_EXTRACT.stat().st_mtime >= FOTMOB_ARCHIVE.stat().st_mtime:
        cached = pd.read_csv(FOTMOB_EXTRACT)
        if not {f"home_{c}" for c in FM_STATS.values()} <= set(cached.columns):
            cached = None  # stat list changed since the cache was written
    if cached is None:
        print("  parsing FotMob archive (once, then cached) ...")
        rows = []
        with tarfile.open(FOTMOB_ARCHIVE) as tar:
            for member in tar:
                if member.isfile() and member.name.endswith(".json"):
                    try:
                        row = parse_fotmob_match(json.load(tar.extractfile(member)))
                    except ValueError:  # broken JSON
                        row = None
                    if row:
                        rows.append(row)
        cached = pd.DataFrame(rows)
        cached.to_csv(FOTMOB_EXTRACT, index=False)
    new = []
    for path in sorted(FOTMOB_NEW_DIR.glob("*.json")) if FOTMOB_NEW_DIR.exists() else []:
        try:
            row = parse_fotmob_match(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            row = None
        if row:
            new.append(row)
    new = pd.DataFrame(new, columns=cached.columns)
    return pd.concat([cached[~cached.fm_match_id.isin(new.fm_match_id)], new], ignore_index=True)


def load_fotmob(club_map):
    f = read_fotmob_details()
    kickoff = pd.to_datetime(f.fm_kickoff_utc, utc=True)
    start = kickoff.dt.year - (kickoff.dt.month < 8)  # seasons start in August (the COVID restart ended in July 2020)
    f = f.assign(season=start.map(season_label), _local=kickoff.dt.tz_convert("Europe/London"))
    f = f[start.between(FIRST_SEASON, LAST_SEASON)].copy()
    f["home_club_id"], f["away_club_id"] = map_teams(f, ["fm_home_team_id", "fm_away_team_id"], club_map, "fotmob")
    for c in [c for c in FM_COLUMNS if c not in f]:
        f[c] = np.nan
    for side in ["home", "away"]:
        f[f"{side}_pass_accuracy"] = (100 * f[f"{side}_accurate_passes"] / f[f"{side}_passes"]).round(1)
    out = pd.DataFrame({
        "season": f.season, "home_club_id": f.home_club_id, "away_club_id": f.away_club_id,
        "fm_date": f._local.dt.tz_localize(None).dt.normalize(), "fm_kickoff_time": f._local.dt.strftime("%H:%M"),
        "fm_match_id": f.fm_match_id.astype(int),
        "fm_home_goals": f.fm_home_goals.astype("Int64"), "fm_away_goals": f.fm_away_goals.astype("Int64"),
    })
    for c in FM_COLUMNS:
        out[c] = pd.to_numeric(f[c], errors="coerce")
    return out


# ---------------------------------------------------------------- integration
def check_unique_key(df, source):
    dup = df[df.duplicated(KEY, keep=False)]
    if len(dup):
        print(f"  warning: {source} has {len(dup)} rows sharing a (season, home, away) key - kept first")
    return df.drop_duplicates(KEY, keep="first"), len(dup)


def cross_check(df):
    rows = []
    for src, p in [("understat", "us"), ("transfermarkt", "tm"), ("fotmob", "fm")]:
        date_col = f"{p}_date"
        has = df[f"{p}_home_goals"].notna()
        score_diff = has & ((df[f"{p}_home_goals"] != df.home_goals) | (df[f"{p}_away_goals"] != df.away_goals))
        date_gap = (df[date_col] - df.date).dt.days.abs()
        date_diff = has & (date_gap > MAX_DATE_GAP_DAYS)
        for i in df.index[score_diff | date_diff]:
            r = df.loc[i]
            rows.append({
                "season": r.season, "date": r.date.date(), "home_club_id": r.home_club_id,
                "away_club_id": r.away_club_id, "source": src,
                "issue": "score" if score_diff[i] else "date",
                "football_data": f"{r.home_goals}-{r.away_goals} on {r.date.date()}",
                "other_source": f"{r[f'{p}_home_goals']}-{r[f'{p}_away_goals']} on {r[date_col].date()}",
            })
    return pd.DataFrame(rows, columns=["season", "date", "home_club_id", "away_club_id", "source", "issue",
                                       "football_data", "other_source"])


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    maps = load_club_mapping()

    print("Loading football-data ...")
    fd, fd_dups = check_unique_key(load_football_data(maps["football_data"]), "football-data")
    print("Loading Understat ...")
    us, us_dups = check_unique_key(load_understat(maps["understat"]), "understat")
    print("Loading Transfermarkt ...")
    tm, tm_dups = check_unique_key(load_transfermarkt(maps["transfermarkt"]), "transfermarkt")
    print("Loading FotMob ...")
    fm, fm_dups = check_unique_key(load_fotmob(maps["fotmob"]), "fotmob")

    df = fd.merge(us, on=KEY, how="left").merge(tm, on=KEY, how="left").merge(fm, on=KEY, how="left")
    mismatches = cross_check(df)

    # one value per concept: football-data first, secondary sources fill the gaps
    df["attendance"] = df.tm_attendance.fillna(df.fd_attendance)
    df["referee"] = df.referee.fillna(df.tm_referee)
    df["source_flags"] = "FD"
    for p, flag in [("us", "+US"), ("tm", "+TM"), ("fm", "+FM")]:
        df["source_flags"] += np.where(df[f"{p}_home_goals"].notna(), flag, "")

    df["date"] = df.date.dt.strftime("%Y-%m-%d")
    df = df.sort_values(["season", "date", "home_club_id"], kind="stable").reset_index(drop=True)
    keep = ["season", "date", "kickoff_time", "league_code", "home_club_id", "away_club_id",
            "home_goals", "away_goals", "result", "ht_home_goals", "ht_away_goals", "ht_result",
            "home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
            "home_corners", "away_corners", "home_fouls", "away_fouls",
            "home_yellow_cards", "away_yellow_cards", "home_red_cards", "away_red_cards",
            "home_xg", "away_xg", "attendance", "stadium", "referee",
            "home_manager", "away_manager", "home_formation", "away_formation"] + FM_COLUMNS + \
        [c for c in df.columns if c.endswith("_odds")] + ["fm_match_id", "source_flags"]
    df["fm_match_id"] = df.fm_match_id.astype("Int64")
    df[keep].to_csv(OUT_DIR / "integrated.csv", index=False)
    mismatches.to_csv(OUT_DIR / "mismatches.csv", index=False)

    per_season = df.groupby("season").agg(
        matches=("home_club_id", "size"),
        with_understat=("us_home_goals", lambda s: int(s.notna().sum())),
        with_transfermarkt=("tm_home_goals", lambda s: int(s.notna().sum())),
        with_fotmob=("fm_home_goals", lambda s: int(s.notna().sum())),
    )
    us_seasons, tm_seasons, fm_seasons = set(us.season), set(tm.season), set(fm.season)
    report = {
        "rows": len(df),
        "seasons": len(per_season),
        "current_season": {"season": CURRENT_SEASON,
                           "matches_played": int(per_season.matches.get(CURRENT_SEASON, 0))},
        "seasons_not_380_matches": per_season[(per_season.matches != 380) &
                                              (per_season.index != CURRENT_SEASON)].matches.to_dict(),
        "duplicate_keys_in_source": {"football_data": fd_dups, "understat": us_dups, "transfermarkt": tm_dups,
                                     "fotmob": fm_dups},
        "join_coverage": {
            "understat": f"{int(df.us_home_goals.notna().sum())} / "
                         f"{int(df.season.isin(us_seasons).sum())} matches of seasons it covers",
            "transfermarkt": f"{int(df.tm_home_goals.notna().sum())} / "
                             f"{int(df.season.isin(tm_seasons).sum())} matches of seasons it covers",
            "fotmob": f"{int(df.fm_home_goals.notna().sum())} / "
                      f"{int(df.season.isin(fm_seasons).sum())} matches of seasons it covers",
            "understat_rows_not_matched": int(len(us) - df.us_home_goals.notna().sum()),
            "transfermarkt_rows_not_matched": int(len(tm) - df.tm_home_goals.notna().sum()),
            "fotmob_rows_not_matched": int(len(fm) - df.fm_home_goals.notna().sum()),
            "fotmob_matches_without_stats": int((df.fm_home_goals.notna() & df.home_possession.isna()).sum()),
        },
        # played in a secondary source but missing from football-data (usually: raw file not refreshed yet)
        "not_in_football_data": [
            f"{r.season} {r.date.date()} {r.home_club_id}-{r.away_club_id} ({src})"
            for src, part, date_col in [("understat", us, "us_date"), ("transfermarkt", tm, "tm_date"),
                                        ("fotmob", fm, "fm_date")]
            for r in part.merge(fd[KEY], on=KEY, how="left", indicator=True)
                         .query("_merge == 'left_only'").rename(columns={date_col: "date"}).itertuples()
        ],
        "mismatches": mismatches.groupby(["source", "issue"]).size().to_dict() if len(mismatches) else {},
        "per_season": per_season.to_dict(orient="index"),
    }
    report["mismatches"] = {f"{k[0]}:{k[1]}": int(v) for k, v in report["mismatches"].items()}
    with open(OUT_DIR / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nstage1_integrated.csv : {len(df)} matches x {len(keep)} columns, {len(per_season)} seasons")
    print(f"current season        : {CURRENT_SEASON}, {report['current_season']['matches_played']} matches played")
    print(f"seasons != 380 matches: {report['seasons_not_380_matches']}")
    print(f"join coverage         : {report['join_coverage']}")
    print(f"source mismatches     : {report['mismatches'] or 'none'}  -> stage1_mismatches.csv")
    if report["not_in_football_data"]:
        print(f"not in football-data  : {report['not_in_football_data']}")


if __name__ == "__main__":
    main()
