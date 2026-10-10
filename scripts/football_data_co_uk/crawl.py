"""
Script thu thap du lieu 5 mua EPL gan nhat tu Football-Data.co.uk
Luu ket qua tho vao raw/football_data_co_uk/
"""
import urllib.request
from pathlib import Path
import pandas as pd

SEASONS = {
    "2021_2022": "2122",
    "2022_2023": "2223",
    "2023_2024": "2324",
    "2024_2025": "2425",
    "2025_2026": "2526",
}

RAW_DIR = Path("raw/football_data_co_uk")
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

summary = []

for season_name, code in SEASONS.items():
    url = f"https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
    dest = RAW_DIR / f"epl_{season_name}.csv"
    print(f"[*] Dang tai {season_name} ({code}) tu: {url}")
    
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        f.write(resp.read())
        
    # Kiem tra tinh toan ven
    try:
        df = pd.read_csv(dest, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(dest, encoding="cp1252")
        
    # Loai bo cac dong trong neu co
    df = df.dropna(how="all")
    # Luu lai ban sach encoding utf-8
    df.to_csv(dest, index=False, encoding="utf-8")
    
    # Kiem tra cac cot cot loi
    core_cols = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "Referee"]
    has_core = all(c in df.columns for c in core_cols)
    
    stat = {
        "season": season_name,
        "file": str(dest),
        "matches": len(df),
        "columns": len(df.columns),
        "start_date": df["Date"].iloc[0] if "Date" in df.columns and len(df) else "N/A",
        "end_date": df["Date"].iloc[-1] if "Date" in df.columns and len(df) else "N/A",
        "has_core_cols": has_core,
        "size_kb": round(dest.stat().st_size / 1024, 2)
    }
    summary.append(stat)
    print(f"    -> Thanh cong: {stat['matches']} tran, {stat['columns']} cot ({stat['size_kb']} KB)")

print("\n--- TONG KET THU THAP FOOTBALL-DATA.CO.UK ---")
res_df = pd.DataFrame(summary)
print(res_df.to_string(index=False))

