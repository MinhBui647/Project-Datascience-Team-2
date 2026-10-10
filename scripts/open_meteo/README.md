# Hướng Dẫn & Chiến Lược Crawl: Open-Meteo Historical Weather API

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
Open-Meteo là API thời tiết mở, phục vụ truy vấn điều kiện khí hậu lịch sử tại đúng thời điểm và địa điểm diễn ra từng trận đấu EPL.

* **Nhóm 3: Bối Cảnh & Điều Kiện Trận Đấu**
  * `weather_temperature_c`: Nhiệt độ tại mặt sân (°C) lúc bóng lăn.
  * `weather_rain_mm`: Lượng mưa thực tế (mm).
  * `weather_precipitation_probability`: Xác suất có mưa (%).
  * `weather_wind_speed_kmh`: Vận tốc gió (km/h) - ảnh hưởng tới các cú sút xa và bóng bổng.
  * `weather_relative_humidity_pct`: Độ ẩm không khí (%).

---

## 2. Hạn Chế Kỹ Thuật
1. **Yêu cầu đầu vào:** Phải có tọa độ địa lý chính xác (`latitude`, `longitude`) của sân vận động và thời gian thi đấu (`kickoff_datetime`).
2. **Hạn mức API:** Miễn phí **10.000 requests / ngày** cho mục đích phi thương mại. Với $1.900$ trận đấu qua 5 mùa, ta chỉ tiêu tốn chưa đến 20% quota của 1 ngày!

---

## 3. Chiến Lược Thu Thập

### Endpoint Lịch Sử:
* `GET https://archive-api.open-meteo.com/v1/archive`
* Tham số:
  * `latitude={lat}&longitude={lon}`
  * `start_date={YYYY-MM-DD}&end_date={YYYY-MM-DD}`
  * `hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m`
  * `timezone=auto`

### Tối ưu hóa hiệu năng (Batch Optimization):
* Không gọi từng trận riêng lẻ. Có thể gom nhóm các trận diễn ra trên cùng một sân vận động trong 1 tháng/1 mùa để truy vấn 1 lần duy nhất cho toàn bộ mùa giải của sân đó!
* Ví dụ: Sân Emirates (`lat=51.5549, lon=-0.1084`) chỉ cần gọi 1 request từ `2021-08-01` đến `2022-05-30` là có đủ thời tiết cho toàn bộ 19 trận sân nhà của Arsenal.

---

## 4. Đoạn Mã Mẫu (Crawler Script Template)

```python
import json
import requests
import pandas as pd
from pathlib import Path

RAW_DIR = Path("raw/open_meteo")
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Tọa độ sân nhà các CLB EPL tiêu biểu
STADIUM_COORDS = {
    "Arsenal": {"lat": 51.5549, "lon": -0.1084},
    "Man City": {"lat": 53.4831, "lon": -2.2004},
    "Liverpool": {"lat": 53.4308, "lon": -2.9608},
    "Chelsea": {"lat": 51.4816, "lon": -0.1909},
    "Man United": {"lat": 53.4631, "lon": -2.2913},
}

def fetch_stadium_season_weather(team: str, start_date: str, end_date: str):
    coords = STADIUM_COORDS.get(team)
    if not coords:
        return None
        
    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={coords['lat']}&longitude={coords['lon']}&"
        f"start_date={start_date}&end_date={end_date}&"
        f"hourly=temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m&"
        f"timezone=Europe/London"
    )
    resp = requests.get(url, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        df = pd.DataFrame(data["hourly"])
        out_file = RAW_DIR / f"{team}_{start_date[:4]}_{end_date[:4]}_weather.csv"
        df.to_csv(out_file, index=False)
        print(f"Saved weather for {team} to {out_file}")
        return df
    return None
```

