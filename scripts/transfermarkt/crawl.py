"""
Script thu thap va loc du lieu EPL 5 mua gan nhat (2021 - 2025) tu Transfermarkt Mirror Dataset
Nguon: Cloudflare R2 bucket cua dcaribou/transfermarkt-datasets
Luu vao raw/transfermarkt/
"""
import gzip
import io
import urllib.request
from pathlib import Path
import pandas as pd

RAW_DIR = Path("raw/transfermarkt")
RAW_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def download_and_read_gz(filename: str) -> pd.DataFrame:
    url = f"{BASE_URL}/{filename}.csv.gz"
    print(f"[*] Dang tai {filename}.csv.gz tu R2 bucket...")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        content = gzip.decompress(resp.read())
    df = pd.read_csv(io.BytesIO(content), low_memory=False)
    print(f"    -> Da tai {filename}: {len(df)} dong tong the.")
    return df

print("=== BAT DAU THU THAP DU LIEU TRANSFERMARKT (EPL 5 MUA: 2021 - 2025) ===")

# 1. Games (Loc rieng EPL 'GB1' cho 5 mua 2021-2025)
games_df = download_and_read_gz("games")
epl_games = games_df[(games_df["competition_id"] == "GB1") & (games_df["season"].between(2021, 2025))].copy()
epl_games.sort_values(["season", "date"], inplace=True)
epl_game_ids = set(epl_games["game_id"])
epl_club_ids = set(epl_games["home_club_id"]).union(set(epl_games["away_club_id"]))

games_out = RAW_DIR / "epl_games_1900.csv"
epl_games.to_csv(games_out, index=False, encoding="utf-8")
print(f"[+] Da luu epl_games_1900.csv: {len(epl_games)} tran (du 380 tran x 5 mua = 1900 tran).")

# 2. Clubs (Loc cac CLB tham gia 5 mua EPL)
clubs_df = download_and_read_gz("clubs")
epl_clubs = clubs_df[clubs_df["club_id"].isin(epl_club_ids)].copy()
clubs_out = RAW_DIR / "epl_clubs.csv"
epl_clubs.to_csv(clubs_out, index=False, encoding="utf-8")
print(f"[+] Da luu epl_clubs.csv: {len(epl_clubs)} CLB.")

# 3. Game Lineups (Loc doi hinh cho 1900 tran)
lineups_df = download_and_read_gz("game_lineups")
epl_lineups = lineups_df[lineups_df["game_id"].isin(epl_game_ids)].copy()
lineups_out = RAW_DIR / "epl_lineups.csv"
epl_lineups.to_csv(lineups_out, index=False, encoding="utf-8")
print(f"[+] Da luu epl_lineups.csv: {len(epl_lineups)} luot ra san/du bi cua 1900 tran.")

# 4. Appearances (Loc thong ke chi tiet tran dau cua cau thu)
apps_df = download_and_read_gz("appearances")
epl_apps = apps_df[apps_df["game_id"].isin(epl_game_ids)].copy()
apps_out = RAW_DIR / "epl_appearances.csv"
epl_apps.to_csv(apps_out, index=False, encoding="utf-8")
print(f"[+] Da luu epl_appearances.csv: {len(epl_apps)} dong thong ke tran cua cau thu.")

# Danh sach player_id lien quan den EPL 5 mua
epl_player_ids = set(epl_lineups["player_id"]).union(set(epl_apps["player_id"]))

# 5. Players (Ho so nhan khau hoc & the chat cua cau thu)
players_df = download_and_read_gz("players")
epl_players = players_df[players_df["player_id"].isin(epl_player_ids)].copy()
players_out = RAW_DIR / "epl_players.csv"
epl_players.to_csv(players_out, index=False, encoding="utf-8")
print(f"[+] Da luu epl_players.csv: {len(epl_players)} ho so cau thu EPL.")

# 6. Player Valuations (Lich su bien dong gia tri cau thu)
vals_df = download_and_read_gz("player_valuations")
epl_vals = vals_df[vals_df["player_id"].isin(epl_player_ids)].copy()
vals_out = RAW_DIR / "epl_player_valuations.csv"
epl_vals.to_csv(vals_out, index=False, encoding="utf-8")
print(f"[+] Da luu epl_player_valuations.csv: {len(epl_vals)} ban ghi dinh gia thi truong.")

print("\n=== HOAN TAT THU THAP & LOC DU LIEU TRANSFERMARKT ===")

