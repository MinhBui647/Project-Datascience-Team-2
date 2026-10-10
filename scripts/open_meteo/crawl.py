"""
Script thu thap thoi tiet lich su 5 mua EPL (2021/22 - 2025/26) tu Open-Meteo API
Tich hop co che Rate Limiter 7.5s va Exponential Backoff chong HTTP 429
Luu vao raw/open_meteo/
"""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
import pandas as pd

RAW_DIR = Path("raw/open_meteo")
STADIUMS_DIR = RAW_DIR / "stadiums"
STADIUMS_DIR.mkdir(parents=True, exist_ok=True)

# 27 CLB EPL va toa do san nha
STADIUM_COORDS = {
    "Arsenal": (51.5549, -0.1084, "Emirates Stadium"),
    "Aston Villa": (52.5091, -1.8848, "Villa Park"),
    "Bournemouth": (50.7352, -1.8383, "Vitality Stadium"),
    "Brentford": (51.4908, -0.2887, "Gtech Community Stadium"),
    "Brighton": (50.8616, -0.0837, "AMEX Stadium"),
    "Burnley": (53.7890, -2.2302, "Turf Moor"),
    "Chelsea": (51.4816, -0.1909, "Stamford Bridge"),
    "Crystal Palace": (51.3983, -0.0855, "Selhurst Park"),
    "Everton": (53.4389, -2.9664, "Goodison Park"),
    "Fulham": (51.4749, -0.2217, "Craven Cottage"),
    "Ipswich": (52.0553, 1.1448, "Portman Road"),
    "Leeds": (53.7778, -1.5722, "Elland Road"),
    "Leicester": (52.6203, -1.1422, "King Power Stadium"),
    "Liverpool": (53.4308, -2.9608, "Anfield"),
    "Luton": (51.8841, -0.4316, "Kenilworth Road"),
    "Man City": (53.4831, -2.2004, "Etihad Stadium"),
    "Manchester City": (53.4831, -2.2004, "Etihad Stadium"),
    "Man United": (53.4631, -2.2913, "Old Trafford"),
    "Manchester United": (53.4631, -2.2913, "Old Trafford"),
    "Newcastle": (54.9756, -1.6217, "St James Park"),
    "Newcastle United": (54.9756, -1.6217, "St James Park"),
    "Norwich": (52.6221, 1.3090, "Carrow Road"),
    "Nottingham Forest": (52.9400, -1.1328, "The City Ground"),
    "Nott'm Forest": (52.9400, -1.1328, "The City Ground"),
    "Sheffield United": (53.3703, -1.4709, "Bramall Lane"),
    "Southampton": (50.9058, -1.3911, "St Marys Stadium"),
    "Sunderland": (54.9144, -1.3883, "Stadium of Light"),
    "Tottenham": (51.6042, -0.0662, "Tottenham Hotspur Stadium"),
    "Watford": (51.6498, -0.4015, "Vicarage Road"),
    "West Ham": (51.5387, -0.0166, "London Stadium"),
    "Wolves": (52.5902, -2.1304, "Molineux Stadium"),
    "Wolverhampton Wanderers": (52.5902, -2.1304, "Molineux Stadium"),
}

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
}

print("=== BAT DAU THU THAP THOI TIET OPEN-METEO (EPL 5 MUA: 2021 - 2026) ===")

unique_stadiums = {}
for team, (lat, lon, s_name) in STADIUM_COORDS.items():
    key = (round(lat, 4), round(lon, 4))
    if key not in unique_stadiums:
        unique_stadiums[key] = {"stadium_name": s_name, "lat": lat, "lon": lon, "teams": [team]}
    else:
        unique_stadiums[key]["teams"].append(team)

print(f"[*] Tong so san van dong can truy van: {len(unique_stadiums)} san.")

weather_cache = {}

for idx, (coord, info) in enumerate(unique_stadiums.items(), 1):
    lat, lon = coord
    s_name = info["stadium_name"]
    clean_name = s_name.replace(" ", "_").replace("'", "")
    cache_file = STADIUMS_DIR / f"{clean_name}.json"
    
    if cache_file.exists():
        print(f"[{idx}/{len(unique_stadiums)}] [CACHE] Da co du lieu cho {s_name}.")
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        url = (
            f"https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}&"
            f"start_date=2021-08-01&end_date=2026-05-31&"
            f"hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code&"
            f"timezone=Europe/London"
        )
        print(f"[{idx}/{len(unique_stadiums)}] [REQUEST] Dang goi Open-Meteo cho {s_name} ({lat}, {lon})...")
        
        # Co che Retry voi backoff neu gap 429
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                success = True
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print(f"    [!] Gap HTTP 429 (Rate limit)! Cho 25 giay de giai toa han muc (Lan thu {attempt+1}/{max_retries})...")
                    time.sleep(25)
                else:
                    print(f"    [!] Loi HTTP {e.code}: {e.reason}")
                    break
            except Exception as e:
                print(f"    [!] Loi ket noi: {e}")
                time.sleep(5)
                
        if not success:
            print(f"    [X] Khong the tai {s_name}, bo qua.")
            continue
            
        # Gian cach an toan 7.5 giay giua cac request de khong vuot 10 req/phut
        time.sleep(7.5)
        
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humids = hourly.get("relative_humidity_2m", [])
    precips = hourly.get("precipitation", [])
    winds = hourly.get("wind_speed_10m", [])
    codes = hourly.get("weather_code", [])
    
    stadium_dict = {}
    for t_str, t_val, h_val, p_val, w_val, c_val in zip(times, temps, humids, precips, winds, codes):
        stadium_dict[t_str] = {
            "temperature_c": t_val,
            "humidity_pct": h_val,
            "precipitation_mm": p_val,
            "wind_speed_kmh": w_val,
            "weather_code": c_val,
            "weather_desc": WMO_CODES.get(c_val, "Unknown")
        }
    weather_cache[coord] = stadium_dict

print(f"\n[+] Da nap xong thoi tiet 5 nam cho {len(weather_cache)} toa do san.")

# 2. Map vao 1.900 tran dau tu Understat (co datetime chuan)
understat_matches = pd.read_csv("raw/understat/understat_matches_1900.csv")
print(f"[*] Dang map thoi tiet vao {len(understat_matches)} tran dau...")

match_weather_rows = []

for row in understat_matches.itertuples():
    h_team = row.home_team
    dt_str = str(row.datetime)  # e.g. "2021-08-13 19:00:00"
    
    coord_info = STADIUM_COORDS.get(h_team)
    if not coord_info:
        for k, v in STADIUM_COORDS.items():
            if k.lower() in h_team.lower() or h_team.lower() in k.lower():
                coord_info = v
                break
                
    if not coord_info:
        continue
        
    lat, lon, s_name = coord_info
    coord_key = (round(lat, 4), round(lon, 4))
    
    date_part = dt_str[:10]
    hour_part = dt_str[11:13] if len(dt_str) >= 13 else "15"
    hour_key = f"{date_part}T{hour_part}:00"
    
    w_info = weather_cache.get(coord_key, {}).get(hour_key, {})
    
    match_weather_rows.append({
        "understat_id": row.understat_id,
        "season": row.season,
        "datetime": row.datetime,
        "home_team": row.home_team,
        "away_team": row.away_team,
        "stadium_name": s_name,
        "latitude": lat,
        "longitude": lon,
        "weather_temperature_c": w_info.get("temperature_c"),
        "weather_humidity_pct": w_info.get("humidity_pct"),
        "weather_precipitation_mm": w_info.get("precipitation_mm"),
        "weather_wind_speed_kmh": w_info.get("wind_speed_kmh"),
        "weather_code": w_info.get("weather_code"),
        "weather_description": w_info.get("weather_desc")
    })

weather_df = pd.DataFrame(match_weather_rows)
out_file = RAW_DIR / "epl_weather_1900_matches.csv"
weather_df.to_csv(out_file, index=False, encoding="utf-8")

print(f"\n[+] Da luu epl_weather_1900_matches.csv: {len(weather_df)} tran dau.")
print("\n--- THONG KE MAU THOI TIET EPL 5 MUA ---")
print(weather_df[["weather_temperature_c", "weather_humidity_pct", "weather_precipitation_mm", "weather_wind_speed_kmh"]].describe().round(2))
print("\nTop 5 hien tuong thoi tiet pho bien nhat luc thi dau:")
print(weather_df["weather_description"].value_counts().head(5))
print("\n=== HOAN TAT THU THAP OPEN-METEO ===")
