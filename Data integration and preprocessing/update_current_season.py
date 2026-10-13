import io
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

import clean
import integrate
import normalize
from integrate import CURRENT_SEASON, LAST_SEASON, OUT_DIR, RAW_DIR

UA = {"User-Agent": "Mozilla/5.0"}
SEASON_CODE = f"{LAST_SEASON % 100:02d}{(LAST_SEASON + 1) % 100:02d}"         # "2627"
SEASON_DIR_LABEL = f"{LAST_SEASON}_{LAST_SEASON + 1}"                          # "2026_2027"
FD_FILE = RAW_DIR / "football_data_co_uk" / f"football_data_raw_{SEASON_CODE}.csv"
US_FILE = RAW_DIR / "understat" / "understat_matches_all_2014_2027.csv"
FPL_FIXTURES_FILE = RAW_DIR / "fpl_api" / f"fpl_fixtures_{SEASON_DIR_LABEL}.csv"
FPL_TEAMS_FILE = RAW_DIR / "fpl_api" / f"fpl_teams_{SEASON_DIR_LABEL}.csv"
FM_FIXTURES_FILE = integrate.FOTMOB_DIR / f"fotmob_fixtures_{SEASON_DIR_LABEL}.json"
FM_API = "https://www.fotmob.com/api/data"
MATCHES_FILE = OUT_DIR / "matches.csv"
UPDATE_LOG = OUT_DIR / "update_log.csv"
NEXT_ROUND_WINDOW = pd.Timedelta(days=1)  # the whole next round is added only this close to its first kick-off


# ---------------------------------------------------------------- 1. pull raw data
def pull_football_data():
    r = requests.get(f"https://www.football-data.co.uk/mmz4281/{SEASON_CODE}/E0.csv", headers=UA, timeout=30)
    r.raise_for_status()
    new = pd.read_csv(io.BytesIO(r.content), encoding="latin-1").dropna(subset=["HomeTeam", "FTHG"])
    old_rows = 0
    if FD_FILE.exists():
        old_rows = len(pd.read_csv(FD_FILE, encoding="latin-1").dropna(subset=["HomeTeam", "FTHG"]))
    if len(new) < old_rows:  # a snapshot must never lose matches
        print(f"  football-data: remote has {len(new)} matches < local {old_rows}, local file kept")
        return 0
    FD_FILE.write_bytes(r.content)
    print(f"  football-data: {old_rows} -> {len(new)} matches")
    return len(new) - old_rows


def pull_understat():
    r = requests.get(f"https://understat.com/getLeagueData/EPL/{LAST_SEASON}", timeout=30,
                     headers={**UA, "X-Requested-With": "XMLHttpRequest",
                              "Referer": f"https://understat.com/league/EPL/{LAST_SEASON}"})
    r.raise_for_status()
    rows = []
    for m in r.json()["dates"]:
        fc = m.get("forecast") or {}
        rows.append({"season": SEASON_DIR_LABEL, "understat_id": int(m["id"]), "datetime": m["datetime"],
                     "is_result": bool(m["isResult"]), "home_team": m["h"]["title"], "away_team": m["a"]["title"],
                     "home_goals": m["goals"]["h"], "away_goals": m["goals"]["a"],
                     "home_xg": m["xG"]["h"], "away_xg": m["xG"]["a"],
                     "forecast_home": fc.get("w"), "forecast_draw": fc.get("d"), "forecast_away": fc.get("l")})
    new = pd.DataFrame(rows)
    old = pd.read_csv(US_FILE)
    played_before = int(old[old.season == SEASON_DIR_LABEL].is_result.astype(str).eq("True").sum())
    merged = pd.concat([old[~old.understat_id.isin(new.understat_id)], new], ignore_index=True)  # upsert
    merged = merged.sort_values(["season", "datetime", "understat_id"]).reset_index(drop=True)
    merged.to_csv(US_FILE, index=False)
    played_now = int(new.is_result.sum())
    print(f"  understat: {played_before} -> {played_now} played matches of {len(new)}")
    return played_now - played_before


def pull_fpl():
    fixtures = requests.get("https://fantasy.premierleague.com/api/fixtures/", headers=UA, timeout=30).json()
    teams = requests.get("https://fantasy.premierleague.com/api/bootstrap-static/", headers=UA, timeout=30).json()["teams"]
    pd.DataFrame(fixtures).to_csv(FPL_FIXTURES_FILE, index=False)
    pd.DataFrame(teams)[["id", "code", "name", "short_name"]].to_csv(FPL_TEAMS_FILE, index=False)
    print(f"  fpl: {len(fixtures)} fixtures, {len(teams)} teams")


def pull_fotmob():
    """Season fixtures -> fotmob_fixtures_<season>.json; every finished match not yet stored (with stats)
    -> raw_data/sofascore/cache_matches/<FotMob match id>.json. De-duplication by FotMob match id."""
    r = requests.get(f"{FM_API}/leagues", params={"id": 47, "season": f"{LAST_SEASON}/{LAST_SEASON + 1}"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    league = r.json()
    finished = [m for m in league["fixtures"]["allMatches"]
                if m["status"].get("finished") and not m["status"].get("cancelled")]
    old_finished = 0
    if FM_FIXTURES_FILE.exists():
        old = json.loads(FM_FIXTURES_FILE.read_text(encoding="utf-8"))
        old_finished = sum(bool(m["status"].get("finished")) for m in old["fixtures"]["allMatches"])
    if len(finished) >= old_finished:  # a snapshot must never lose results
        FM_FIXTURES_FILE.write_text(json.dumps(league, ensure_ascii=False), encoding="utf-8")

    known = integrate.read_fotmob_details()
    have_stats = set(known.fm_match_id[known.home_possession.notna()].astype(int))
    todo = [int(m["id"]) for m in finished if int(m["id"]) not in have_stats]
    integrate.FOTMOB_NEW_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for match_id in todo:
        d = requests.get(f"{FM_API}/matchDetails", params={"matchId": match_id}, headers=UA, timeout=30)
        if d.ok and integrate.parse_fotmob_match(d.json()):
            (integrate.FOTMOB_NEW_DIR / f"{match_id}.json").write_text(d.text, encoding="utf-8")
            saved += 1
        time.sleep(0.5)
    print(f"  fotmob: {len(finished)} finished matches, {saved} new match details saved")
    return saved


def pull_raw():
    for name, fn in [("football-data", pull_football_data), ("understat", pull_understat), ("fpl", pull_fpl),
                     ("fotmob", pull_fotmob)]:
        try:
            fn()
        except Exception as e:  # keep going with the raw files already on disk
            print(f"  {name}: download failed ({e}); using local raw file")


# ---------------------------------------------------------------- 3. matches.csv
def fpl_fixtures():
    fx = pd.read_csv(FPL_FIXTURES_FILE)
    teams = pd.read_csv(FPL_TEAMS_FILE)
    mapping = pd.read_csv(integrate.MASTER_DIR / "club_mapping.csv")
    name_to_id = dict(zip(mapping.name_fpl, mapping.club_id))
    team_id = dict(zip(teams.id, teams.name.map(name_to_id)))
    unknown = sorted(set(teams.name[teams.name.map(name_to_id).isna()]))
    if unknown:
        raise ValueError(f"FPL team names without club_id - fix name_fpl in club_mapping.csv: {unknown}")
    kickoff_utc = pd.to_datetime(fx.kickoff_time, utc=True)  # empty when postponed without a new date
    kickoff = kickoff_utc.dt.tz_convert("Europe/London")
    return pd.DataFrame({
        "gameweek": fx.event.astype("Int64"),  # empty when postponed without a new gameweek
        "home_club_id": fx.team_h.map(team_id), "away_club_id": fx.team_a.map(team_id),
        "kickoff_utc": kickoff_utc,
        "date": kickoff.dt.strftime("%Y-%m-%d"), "kickoff_time": kickoff.dt.strftime("%H:%M"),
    })


def base_row(home, away):
    names = normalize.team_names()
    return {"match_id": f"EPL_{LAST_SEASON}_{LAST_SEASON + 1}_{home}_{away}", "league": normalize.LEAGUE,
            "league_code": "E0", "season": CURRENT_SEASON, "season_start_year": LAST_SEASON,
            "home_club_id": home, "home_team": names[home], "away_club_id": away, "away_team": names[away],
            "season_complete": False, "attendance_imputed": False}


def result_fields(hg, ag):
    hg, ag = int(hg), int(ag)
    return {"home_goals": hg, "away_goals": ag, "score": f"{hg}-{ag}",
            "result": "H" if hg > ag else "A" if hg < ag else "D", "status": "played", "provisional": True}


def provisional_results(played_ids):
    """Played matches of the current season not yet in football-data: score + xG from Understat
    (FotMob score when Understat does not have it yet) + FotMob match stats."""
    maps = integrate.load_club_mapping()
    us = pd.read_csv(US_FILE)
    us = us[(us.season == SEASON_DIR_LABEL) & us.is_result.astype(str).eq("True")]
    fm = integrate.load_fotmob(maps["fotmob"])
    fm = {(r.home_club_id, r.away_club_id): r for r in fm[fm.season == CURRENT_SEASON].itertuples()}
    rows = {}
    for r in us.itertuples():
        row = base_row(maps["understat"][r.home_team], maps["understat"][r.away_team])
        if row["match_id"] in played_ids:
            continue
        kickoff = pd.Timestamp(r.datetime, tz="UTC").tz_convert("Europe/London")  # Understat is UTC
        row.update({"date": kickoff.strftime("%Y-%m-%d"), "kickoff_time": kickoff.strftime("%H:%M"),
                    **result_fields(r.home_goals, r.away_goals),
                    "home_xg": r.home_xg, "away_xg": r.away_xg, "source_flags": "US"})
        rows[(row["home_club_id"], row["away_club_id"])] = row
    for key, r in fm.items():
        row = rows.get(key) or base_row(*key)
        if row["match_id"] in played_ids:
            continue
        if key not in rows:  # result known to FotMob only
            row.update({"date": r.fm_date.strftime("%Y-%m-%d"), "kickoff_time": r.fm_kickoff_time,
                        **result_fields(r.fm_home_goals, r.fm_away_goals), "source_flags": ""})
        row.update({c: getattr(r, c) for c in integrate.FM_COLUMNS + ["fm_match_id"]})
        row["source_flags"] = "+".join(filter(None, [row["source_flags"], "FM"]))
        rows[key] = row
    return pd.DataFrame(list(rows.values()))


def upcoming_matches(fixtures, played_ids, now):
    fixtures = fixtures.assign(match_id=[base_row(h, a)["match_id"]
                                         for h, a in zip(fixtures.home_club_id, fixtures.away_club_id)])
    first_kickoff = fixtures.dropna(subset=["gameweek"]).groupby("gameweek").kickoff_utc.min()
    started = first_kickoff[first_kickoff <= now]
    current_gw = int(started.index.max()) if len(started) else 0
    later = first_kickoff[first_kickoff.index > current_gw]
    next_gw = int(later.index.min()) if len(later) else None
    include_next = next_gw is not None and now >= later[next_gw] - NEXT_ROUND_WINDOW

    unplayed = fixtures[~fixtures.match_id.isin(played_ids)]
    postponed = (unplayed.gameweek.isna() | unplayed.kickoff_utc.isna() | (unplayed.gameweek < current_gw)
                 | (unplayed.kickoff_utc < now - NEXT_ROUND_WINDOW))  # kicked off > 1 day ago, still no result
    keep = postponed | (unplayed.gameweek == current_gw)
    if include_next:
        keep |= unplayed.gameweek == next_gw
    rows = []
    for (_, r), is_postponed in zip(unplayed[keep].iterrows(), postponed[keep]):
        row = base_row(r.home_club_id, r.away_club_id)
        row.update({"date": r.date, "kickoff_time": r.kickoff_time, "status": "upcoming",
                    "postponed": bool(is_postponed), "provisional": False, "source_flags": "FPL"})
        rows.append(row)
    return pd.DataFrame(rows), current_gw, next_gw, include_next


def update_matches(now, rebuild=False):
    cleaned = pd.read_csv(OUT_DIR / "cleaned.csv", low_memory=False).assign(provisional=False, postponed=False)
    columns = list(cleaned.columns) + ["gameweek"]

    old = pd.read_csv(MATCHES_FILE, low_memory=False) if MATCHES_FILE.exists() and not rebuild \
        else pd.DataFrame(columns=columns)
    removed_upcoming = old[old.status == "upcoming"]
    kept = old[(old.status == "played") & ~old.provisional.astype(bool)]  # final rows are never dropped
    # ... but refreshed from cleaned.csv, so a new source / column reaches them; old values only fill gaps
    kept = (cleaned.set_index("match_id").reindex(kept.match_id).combine_first(kept.set_index("match_id"))
            .reset_index())
    replaced_provisional = old[(old.status == "played") & old.provisional.astype(bool)]

    new_played = cleaned[~cleaned.match_id.isin(kept.match_id)]                 # de-duplication by match_id
    played_ids = set(kept.match_id) | set(new_played.match_id)
    provisional = provisional_results(played_ids)
    played_ids |= set(provisional.match_id) if len(provisional) else set()

    fixtures = fpl_fixtures()
    upcoming, current_gw, next_gw, include_next = upcoming_matches(fixtures, played_ids, now)

    out = pd.concat([kept, new_played, provisional, upcoming], ignore_index=True).reindex(columns=columns)
    out["postponed"] = out.postponed.fillna(False).astype(bool)
    gw_map = dict(zip([base_row(h, a)["match_id"] for h, a in zip(fixtures.home_club_id, fixtures.away_club_id)],
                      fixtures.gameweek))
    current = out.season == CURRENT_SEASON
    out.loc[current, "gameweek"] = out.loc[current, "match_id"].map(gw_map)
    out["gameweek"] = out.gameweek.astype("Int64")
    for c in clean.RANGES:
        if c not in clean.FLOAT_COLUMNS:
            out[c] = pd.to_numeric(out[c], errors="coerce").round().astype("Int64")
    out["fm_match_id"] = pd.to_numeric(out.fm_match_id, errors="coerce").astype("Int64")
    out = out.sort_values(["season", "date", "kickoff_time", "home_club_id"], na_position="first",
                          kind="stable").reset_index(drop=True)
    out.to_csv(MATCHES_FILE, index=False)

    added_final = new_played[new_played.season == CURRENT_SEASON] if len(old) else new_played.iloc[0:0]
    summary = {
        "run_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "removed_upcoming": len(removed_upcoming),
        "added_played": len(new_played) if len(old) else 0,
        "replaced_provisional": int(replaced_provisional.match_id.isin(new_played.match_id).sum()),
        "provisional": len(provisional),
        "current_gameweek": current_gw, "next_gameweek": next_gw, "next_round_included": include_next,
        "upcoming": len(upcoming),
        "postponed": int(upcoming.postponed.sum()) if len(upcoming) else 0,
        "rows_total": len(out),
    }
    return out, summary, removed_upcoming, added_final, provisional, upcoming


def main():
    rebuild, offline = "--rebuild" in sys.argv, "--offline" in sys.argv
    now = pd.Timestamp(sys.argv[sys.argv.index("--now") + 1], tz="UTC") if "--now" in sys.argv \
        else pd.Timestamp(datetime.now(timezone.utc))
    print("1/4 Pulling new raw data ..." if not offline else "1/4 Offline: using raw files on disk")
    if not offline:
        pull_raw()
    print("2/4 Rebuilding stages 1-3 ...")
    integrate.main()
    normalize.main()
    clean.main()
    print("3/4 Updating matches.csv ...")
    out, s, _, added, provisional, upcoming = update_matches(now, rebuild)
    pd.DataFrame([s]).to_csv(UPDATE_LOG, mode="a", header=not UPDATE_LOG.exists(), index=False)
    print("4/4 Done.\n")

    show = ["date", "kickoff_time", "home_team", "away_team", "score"]
    print(f"matches.csv: {s['rows_total']} rows ({(out.status == 'played').sum()} played, "
          f"{(out.status == 'upcoming').sum()} upcoming)")
    print(f"removed old upcoming : {s['removed_upcoming']}")
    print(f"added played (final) : {s['added_played']}" + (f"\n{added[show].to_string(index=False)}" if len(added) else ""))
    print(f"provisional (Understat, waiting for football-data): {s['provisional']}"
          + (f"\n{provisional[show].to_string(index=False)}" if len(provisional) else ""))
    print(f"round in progress = gameweek {s['current_gameweek']}, next = gameweek {s['next_gameweek']} "
          f"({'included: starts within 1 day' if s['next_round_included'] else 'not included yet'})")
    print(f"upcoming: {s['upcoming']} matches ({s['postponed']} postponed)"
          + (f"\n{upcoming[show[:4] + ['postponed']].to_string(index=False)}" if len(upcoming) else ""))


if __name__ == "__main__":
    main()
