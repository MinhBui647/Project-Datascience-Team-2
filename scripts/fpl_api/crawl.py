"""
Script thu thap toan bo du lieu Fantasy Premier League (FPL) 5 mua EPL gan nhat (2021/22 - 2025/26)
Nguon: Mirror chinh thuc vaastav/Fantasy-Premier-League
Luu tru vao raw/fpl_api/
"""
import urllib.request
from pathlib import Path
import pandas as pd

RAW_DIR = Path("raw/fpl_api")
RAW_DIR.mkdir(parents=True, exist_ok=True)

SEASONS = {
    "2021_2022": "2021-22",
    "2022_2023": "2022-23",
    "2023_2024": "2023-24",
    "2024_2025": "2024-25",
    "2025_2026": "2025-26",
}

BASE_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def download_file(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        return True
    except Exception as e:
        print(f"    [!] Loi khi tai {url}: {e}")
        return False

print("=== BAT DAU THU THAP DU LIEU FPL (5 MUA EPL: 2021 - 2026) ===")

all_fixtures = []
all_teams = []
season_summaries = []

for s_label, s_code in SEASONS.items():
    print(f"\n[*] Dang xu ly mua {s_label} ({s_code})...")
    
    # 1. Tai merged_gw.csv (Chi tiet tung Gameweek cua moi cau thu)
    gw_url = f"{BASE_URL}/{s_code}/gws/merged_gw.csv"
    gw_dest = RAW_DIR / f"fpl_merged_gw_{s_label}.csv"
    if download_file(gw_url, gw_dest):
        gw_df = pd.read_csv(gw_dest, low_memory=False)
        print(f"    -> [merged_gw] {len(gw_df)} dong ({len(gw_df.columns)} cot) - {round(gw_dest.stat().st_size / (1024*1024), 2)} MB")
        gw_count = len(gw_df)
    else:
        gw_count = 0
        
    # 2. Tai cleaned_players.csv (Ho so tong hop cau thu ca mua)
    p_url = f"{BASE_URL}/{s_code}/cleaned_players.csv"
    p_dest = RAW_DIR / f"fpl_cleaned_players_{s_label}.csv"
    if download_file(p_url, p_dest):
        p_df = pd.read_csv(p_dest, low_memory=False)
        print(f"    -> [cleaned_players] {len(p_df)} cau thu")
        p_count = len(p_df)
    else:
        p_count = 0
        
    # 3. Tai teams.csv (Thong tin CLB va diem suc manh FPL)
    t_url = f"{BASE_URL}/{s_code}/teams.csv"
    t_dest = RAW_DIR / f"fpl_teams_{s_label}.csv"
    if download_file(t_url, t_dest):
        t_df = pd.read_csv(t_dest)
        t_df["season"] = s_label
        all_teams.append(t_df)
        print(f"    -> [teams] {len(t_df)} CLB")
        
    # 4. Tai fixtures.csv (Lich thi dau & do kho FDR cua ban to chuc)
    f_url = f"{BASE_URL}/{s_code}/fixtures.csv"
    f_dest = RAW_DIR / f"fpl_fixtures_{s_label}.csv"
    if download_file(f_url, f_dest):
        f_df = pd.read_csv(f_dest)
        f_df["season"] = s_label
        all_fixtures.append(f_df)
        print(f"    -> [fixtures] {len(f_df)} tran dau")
        
    season_summaries.append({
        "season": s_label,
        "players": p_count,
        "gameweek_records": gw_count,
        "teams": len(t_df) if "t_df" in locals() else 0,
        "fixtures": len(f_df) if "f_df" in locals() else 0
    })

# Hop nhat bang teams va fixtures toan bo 5 mua
if all_teams:
    combined_teams = pd.concat(all_teams, ignore_index=True)
    combined_teams.to_csv(RAW_DIR / "fpl_teams_5seasons.csv", index=False)
    print(f"\n[+] Da luu fpl_teams_5seasons.csv: {len(combined_teams)} dong tong the.")

if all_fixtures:
    combined_fixtures = pd.concat(all_fixtures, ignore_index=True)
    combined_fixtures.to_csv(RAW_DIR / "fpl_fixtures_1900.csv", index=False)
    print(f"[+] Da luu fpl_fixtures_1900.csv: {len(combined_fixtures)} tran dau qua 5 mua.")

print("\n--- TONG KET THU THAP FPL DATA ---")
sum_df = pd.DataFrame(season_summaries)
print(sum_df.to_string(index=False))
print("\n=== HOAN TAT THU THAP FPL DATA ===")

