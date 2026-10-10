"""
Script thu thap du lieu 5 mua EPL gan nhat (2021/22 - 2025/26) tu Understat
Lay toan bo 1.900 tran dau (xG, xGA, du bao W/D/L), 20 doi bong va toan bo cau thu (xG, xA, xGChain, xGBuildup).
Luu vao raw/understat/
"""
import gzip
import json
import urllib.request
from pathlib import Path
import pandas as pd

RAW_DIR = Path("raw/understat")
RAW_DIR.mkdir(parents=True, exist_ok=True)

SEASONS = [2021, 2022, 2023, 2024, 2025]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Encoding": "gzip, deflate",
}

all_matches = []
all_players = []
all_teams = []

print("=== BAT DAU THU THAP DU LIEU UNDERSTAT (5 MUA EPL: 2021 - 2025) ===")

for season in SEASONS:
    s_label = f"{season}_{season+1}"
    url = f"https://understat.com/getLeagueData/EPL/{season}"
    print(f"[*] Dang tai du lieu mua {s_label} tu {url}...")
    
    req = urllib.request.Request(url, headers={**HEADERS, "Referer": f"https://understat.com/league/EPL/{season}"})
    with urllib.request.urlopen(req) as resp:
        raw_bytes = resp.read()
        try:
            content = gzip.decompress(raw_bytes)
        except Exception:
            content = raw_bytes
        data = json.loads(content.decode("utf-8"))
        
    # Luu file JSON tho
    raw_json_file = RAW_DIR / f"understat_raw_{s_label}.json"
    with open(raw_json_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"    -> Da luu {raw_json_file.name} ({round(raw_json_file.stat().st_size / 1024, 2)} KB)")
    
    # 1. Trich xuat tran dau (dates)
    for m in data.get("dates", []):
        all_matches.append({
            "season": s_label,
            "understat_id": m.get("id"),
            "datetime": m.get("datetime"),
            "is_result": m.get("isResult"),
            "home_id": m.get("h", {}).get("id"),
            "home_team": m.get("h", {}).get("title"),
            "home_short": m.get("h", {}).get("short_title"),
            "away_id": m.get("a", {}).get("id"),
            "away_team": m.get("a", {}).get("title"),
            "away_short": m.get("a", {}).get("short_title"),
            "home_goals": m.get("goals", {}).get("h"),
            "away_goals": m.get("goals", {}).get("a"),
            "home_xg": float(m.get("xG", {}).get("h", 0)),
            "away_xg": float(m.get("xG", {}).get("a", 0)),
            "forecast_win": float(m.get("forecast", {}).get("w", 0)),
            "forecast_draw": float(m.get("forecast", {}).get("d", 0)),
            "forecast_loss": float(m.get("forecast", {}).get("l", 0)),
        })
        
    # 2. Trich xuat cau thu (players)
    for p in data.get("players", []):
        p_row = p.copy()
        p_row["season"] = s_label
        all_players.append(p_row)
        
    # 3. Trich xuat doi bong (teams)
    for t_id, t_info in data.get("teams", {}).items():
        all_teams.append({
            "season": s_label,
            "team_id": t_id,
            "team_title": t_info.get("title"),
            "history_count": len(t_info.get("history", []))
        })

# Xuat CSV tong hop
df_matches = pd.DataFrame(all_matches)
matches_csv = RAW_DIR / "understat_matches_1900.csv"
df_matches.to_csv(matches_csv, index=False, encoding="utf-8")
print(f"\n[+] Da luu understat_matches_1900.csv: {len(df_matches)} tran dau.")

df_players = pd.DataFrame(all_players)
players_csv = RAW_DIR / "understat_players_5seasons.csv"
df_players.to_csv(players_csv, index=False, encoding="utf-8")
print(f"[+] Da luu understat_players_5seasons.csv: {len(df_players)} ho so cau thu.")

df_teams = pd.DataFrame(all_teams)
teams_csv = RAW_DIR / "understat_teams_5seasons.csv"
df_teams.to_csv(teams_csv, index=False, encoding="utf-8")
print(f"[+] Da luu understat_teams_5seasons.csv: {len(df_teams)} ban ghi doi bong.")

# Tai them file du phong tu mirror vaastav/Fantasy-Premier-League
print("\n[*] Dang dong bo them du lieu tu mirror vaastav/Fantasy-Premier-League...")
mirror_seasons = {"2021-22": "2021_2022", "2022-23": "2022_2023", "2023-24": "2023_2024", "2024-25": "2024_2025"}
for v_season, out_season in mirror_seasons.items():
    m_url = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{v_season}/understat/understat_player.csv"
    dest = RAW_DIR / f"vaastav_understat_player_{out_season}.csv"
    try:
        urllib.request.urlretrieve(m_url, dest)
        print(f"    -> Da tai {dest.name}")
    except Exception as e:
        print(f"    -> Bo qua {v_season}: {e}")

print("\n=== HOAN TAT THU THAP UNDERSTAT ===")

