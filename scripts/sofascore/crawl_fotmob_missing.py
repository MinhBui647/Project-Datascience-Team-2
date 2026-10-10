"""
scripts/sofascore/crawl_fotmob_missing.py
Crawl missing matchDetails and shotmaps from FotMob REST API for:
- 2023/2024: 2 missing matches
- 2024/2025: 340 missing matches
- 2025/2026: 380 missing matches
Total: 722 matches to reach 100% completeness (380/380 matches per season).

Appends new rows to raw/sofascore/fotmob_epl_47_details.csv.
Also creates raw/sofascore/fotmob_team_match_stats.csv for team-level advanced stats.
"""

import os
import json
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

ROOT_DIR = r"z:\Desktop\Bet"
RAW_DIR = os.path.join(ROOT_DIR, "raw", "sofascore")
CACHE_DIR = os.path.join(RAW_DIR, "cache_matches")
os.makedirs(CACHE_DIR, exist_ok=True)

CSV_FILE = os.path.join(RAW_DIR, "fotmob_epl_47_details.csv")
TEAM_STATS_FILE = os.path.join(RAW_DIR, "fotmob_team_match_stats.csv")

SEASONS_TO_FETCH = ["2023/2024", "2024/2025", "2025/2026"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def get_season_fixtures(season: str):
    """Fetch list of all fixtures for EPL season."""
    url = f"https://www.fotmob.com/api/data/leagues?id=47&season={urllib.parse.quote(season)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        matches = data["fixtures"]["allMatches"]
        return matches


def fetch_match_details(match_id: int):
    """Fetch matchDetails from FotMob API with local file caching."""
    cache_path = os.path.join(CACHE_DIR, f"{match_id}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    url = f"https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"
    req = urllib.request.Request(url, headers=HEADERS)
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
        except Exception as e:
            time.sleep(1.0 * (attempt + 1))
    return None


def parse_shotmap(match_info: dict, match_data: dict):
    """Parse shotmap into list of dicts matching fotmob_epl_47_details.csv schema."""
    general = match_data.get("general", {})
    content = match_data.get("content", {})
    shotmap = content.get("shotmap", {}).get("shots", [])

    match_id = int(general.get("matchId", match_info.get("id")))
    match_round = match_info.get("round", general.get("matchRound"))
    try:
        match_round = int(match_round)
    except Exception:
        match_round = None

    league_id = 47
    league_name = "Premier League"
    league_round_name = f"Premier League Round {match_round}" if match_round else "Premier League"
    parent_league_id = 47
    parent_league_season = general.get("parentLeagueSeason", match_info.get("season", ""))
    match_time_utc = match_info.get("status", {}).get("utcTime", general.get("matchTimeUTC", ""))

    home_team = general.get("homeTeam", {})
    away_team = general.get("awayTeam", {})
    home_team_id = home_team.get("id", match_info.get("home", {}).get("id"))
    away_team_id = away_team.get("id", match_info.get("away", {}).get("id"))
    home_team_name = home_team.get("name", match_info.get("home", {}).get("name"))
    away_team_name = away_team.get("name", match_info.get("away", {}).get("name"))
    home_team_color = home_team.get("color")
    away_team_color = away_team.get("color")

    rows = []
    for s in shotmap:
        on_goal = s.get("onGoalShot") or {}
        row = {
            "match_id": match_id,
            "match_round": match_round,
            "league_id": league_id,
            "league_name": league_name,
            "league_round_name": league_round_name,
            "parent_league_id": parent_league_id,
            "parent_league_season": parent_league_season,
            "match_time_utc": match_time_utc,
            "home_team_id": home_team_id,
            "home_team": home_team_name,
            "home_team_color": home_team_color,
            "away_team_id": away_team_id,
            "away_team": away_team_name,
            "away_team_color": away_team_color,
            "id": s.get("id"),
            "event_type": s.get("eventType"),
            "team_id": s.get("teamId"),
            "player_id": s.get("playerId"),
            "player_name": s.get("playerName"),
            "x": s.get("x"),
            "y": s.get("y"),
            "min": s.get("min"),
            "min_added": s.get("minAdded"),
            "is_blocked": s.get("isBlocked"),
            "is_on_target": s.get("isOnTarget"),
            "goal_crossed_y": s.get("goalCrossedY"),
            "expected_goals": s.get("expectedGoals"),
            "expected_goals_on_target": s.get("expectedGoalsOnTarget"),
            "shot_type": s.get("shotType"),
            "situation": s.get("situation"),
            "period": s.get("period"),
            "is_own_goal": s.get("isOwnGoal"),
            "on_goal_shot_x": on_goal.get("x"),
            "on_goal_shot_y": on_goal.get("y"),
            "on_goal_shot_zoom_ratio": on_goal.get("zoomRatio"),
            "first_name": s.get("firstName"),
            "last_name": s.get("lastName"),
            "team_color": s.get("teamColor"),
            "short_name": s.get("shortName"),
            "blocked_x": s.get("blockedX"),
            "blocked_y": s.get("blockedY"),
            "goal_crossed_z": s.get("goalCrossedZ"),
            "full_name": s.get("fullName"),
            "is_saved_off_line": s.get("isSavedOffLine"),
            "is_from_inside_box": s.get("isFromInsideBox"),
        }
        rows.append(row)
    return rows


def parse_team_stats(match_info: dict, match_data: dict):
    """Parse team match stats (tackles, passes, xG, possession, etc.)."""
    general = match_data.get("general", {})
    content = match_data.get("content", {})
    stats = content.get("stats", {})

    match_id = int(general.get("matchId", match_info.get("id")))
    home_team_id = general.get("homeTeam", {}).get("id")
    away_team_id = general.get("awayTeam", {}).get("id")
    match_time_utc = match_info.get("status", {}).get("utcTime", general.get("matchTimeUTC", ""))

    row = {
        "match_id": match_id,
        "match_time_utc": match_time_utc,
        "home_team": general.get("homeTeam", {}).get("name"),
        "away_team": general.get("awayTeam", {}).get("name"),
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
    }

    if isinstance(stats, dict) and "Periods" in stats:
        all_stats = stats["Periods"].get("All", {}).get("stats", [])
        for cat in all_stats:
            for item in cat.get("stats", []):
                t = item.get("title", "").replace(" ", "_").lower()
                st = item.get("stats", [])
                if len(st) == 2:
                    row[f"home_{t}"] = st[0]
                    row[f"away_{t}"] = st[1]
    return row


def main():
    print("=" * 70)
    print("CRAWLING MISSING FOTMOB EPL MATCHES TO COMPLETE DATASET")
    print("=" * 70)

    # 1. Identify missing matches
    existing_df = pd.read_csv(CSV_FILE, low_memory=False)
    existing_mids = set(existing_df["match_id"].dropna().astype(int).unique())
    print(f"Existing matches in fotmob_epl_47_details.csv: {len(existing_mids)}")

    missing_fixtures = []
    for s in SEASONS_TO_FETCH:
        print(f"\nFetching fixtures for season: {s}...")
        fixtures = get_season_fixtures(s)
        finished = [m for m in fixtures if m.get("status", {}).get("finished", False)]
        season_missing = [m for m in finished if int(m["id"]) not in existing_mids]
        print(f"  Finished: {len(finished)} | Missing in local CSV: {len(season_missing)}")
        for m in season_missing:
            m["season"] = s
            missing_fixtures.append(m)

    print(f"\nTOTAL MISSING MATCHES TO CRAWL: {len(missing_fixtures)}")
    if len(missing_fixtures) == 0:
        print("All matches already present! No crawl needed.")
        return

    # 2. Fetch match details concurrently with 4 workers
    print("\nFetching matchDetails via FotMob REST API (4 workers with local cache)...")
    t0 = time.time()
    all_new_shots = []
    all_team_stats = []
    success_count = 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_match = {
            executor.submit(fetch_match_details, int(m["id"])): m
            for m in missing_fixtures
        }

        for i, future in enumerate(as_completed(future_to_match), 1):
            m_info = future_to_match[future]
            try:
                m_data = future.result()
                if m_data:
                    shots = parse_shotmap(m_info, m_data)
                    all_new_shots.extend(shots)
                    
                    t_stat = parse_team_stats(m_info, m_data)
                    all_team_stats.append(t_stat)
                    
                    success_count += 1
            except Exception as e:
                print(f"Error parsing match {m_info.get('id')}: {e}")

            if i % 50 == 0 or i == len(missing_fixtures):
                elapsed = time.time() - t0
                print(f"  Progress: {i}/{len(missing_fixtures)} matches processed ({success_count} success, {len(all_new_shots)} shots) in {elapsed:.1f}s")

    print(f"\nFetch completed in {time.time()-t0:.2f}s! Total new shots parsed: {len(all_new_shots)}")

    # 3. Append to existing CSV
    if all_new_shots:
        print(f"\nAppending {len(all_new_shots)} new shot events to {CSV_FILE}...")
        new_df = pd.DataFrame(all_new_shots)
        # Ensure column order matches exactly
        for col in existing_df.columns:
            if col not in new_df.columns:
                new_df[col] = None
        new_df = new_df[existing_df.columns]

        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        combined_df.to_csv(CSV_FILE, index=False)
        print(f" -> Updated {CSV_FILE}: total rows = {len(combined_df)}, unique matches = {combined_df['match_id'].nunique()}")

    # 4. Save team advanced stats
    if all_team_stats:
        team_df = pd.DataFrame(all_team_stats)
        team_df.to_csv(TEAM_STATS_FILE, index=False)
        print(f" -> Saved team stats to {TEAM_STATS_FILE}: {len(team_df)} matches")

    print("\nSUCCESS: All missing FotMob matches successfully crawled and integrated!")


if __name__ == "__main__":
    main()

