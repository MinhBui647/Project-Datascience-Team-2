# Hướng Dẫn & Chiến Lược Crawl: Fantasy Premier League (FPL) API

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
FPL API là nguồn dữ liệu mở chính thức của Ngoại Hạng Anh, không yêu cầu xác thực và cung cấp bức tranh chi tiết về cầu thủ, sức mạnh CLB và tình trạng chấn thương.

* **Nhóm 1: Thực Thể Đội Bóng**
  * `strength_overall_home`, `strength_overall_away`: Sức mạnh tổng thể của CLB.
  * `strength_attack_home`, `strength_attack_away`: Sức mạnh tấn công sân nhà/khách.
  * `strength_defence_home`, `strength_defence_away`: Sức mạnh phòng ngự sân nhà/khách.
* **Nhóm 2: Thực Thể Cầu Thủ (100% Phụ trách)**
  * Danh sách ~600 cầu thủ: Tên, vị trí thi đấu (`element_type`: GK, DEF, MID, FWD), số áo, giá tiền (`now_cost`).
  * Chỉ số nâng cao: `ict_index`, `influence`, `creativity`, `threat`.
  * Y tế & Chấn thương: `news` (thông báo y tế từ CLB), `chance_of_playing_next_round` ($0\% - 100\%$).
* **Nhóm 3: Bối Cảnh Trận Đấu**
  * Chỉ số độ khó trận đấu của ban tổ chức (FDR - Fixture Difficulty Rating): `team_h_difficulty`, `team_a_difficulty` (thang điểm 1 - 5).

---

## 2. Hạn Chế Kỹ Thuật
1. **Lịch sử mùa giải cũ:** Endpoint trực tiếp `fantasy.premierleague.com/api/` chỉ phục vụ mùa giải hiện tại.
2. **Chiến lược lấy 5 mùa cũ (2021/22 - 2025/26):**
   * Cộng đồng FPL có kho lưu trữ GitHub mở cực kỳ uy tín: `https://github.com/vaastav/Fantasy-Premier-League`.
   * Kho lưu trữ này lưu toàn bộ bản snapshot JSON/CSV của FPL theo từng Gameweek và từng mùa giải từ 2016 đến nay.
   * Ta có thể cào trực tiếp raw CSV từ GitHub của `vaastav` cho 5 mùa vừa qua mà không sợ sai lệch!

---

## 3. Chiến Lược Thu Thập

### A. Đối với mùa giải đang diễn ra:
* `GET https://fantasy.premierleague.com/api/bootstrap-static/` (Lấy toàn bộ CLB, cầu thủ, vòng đấu).
* `GET https://fantasy.premierleague.com/api/fixtures/` (Lấy toàn bộ 380 trận đấu và FDR).
* `GET https://fantasy.premierleague.com/api/element-summary/{player_id}/` (Lịch sử chi tiết từng trận của cầu thủ).

### B. Đối với 5 mùa lịch sử (2021/22 đến 2025/26):
* Sử dụng GitHub raw content của repo `vaastav/Fantasy-Premier-League`:
  * URL mẫu: `https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/<season>/players_raw.csv`

---

## 4. Đoạn Mã Mẫu (Crawler Script Template)

```python
import json
import requests
import pandas as pd
from pathlib import Path

RAW_DIR = Path("raw/fpl_api")
RAW_DIR.mkdir(parents=True, exist_ok=True)

def fetch_current_fpl_bootstrap():
    url = "https://fantasy.premierleague.com/api/bootstrap-static/"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    
    with open(RAW_DIR / "fpl_bootstrap_current.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Xuất danh sách cầu thủ dạng bảng
    players = pd.DataFrame(data["elements"])
    players.to_csv(RAW_DIR / "fpl_players_current.csv", index=False)
    print(f"Fetched {len(players)} players from FPL API.")

def fetch_historical_fpl_season(season_code: str):
    """Tải snapshot lịch sử từ mirror repo vaastav/Fantasy-Premier-League (ví dụ: '2021-22')"""
    url = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season_code}/players_raw.csv"
    dest = RAW_DIR / f"fpl_players_{season_code.replace('-', '_')}.csv"
    resp = requests.get(url)
    if resp.status_code == 200:
        with open(dest, "wb") as f:
            f.write(resp.content)
        print(f"Successfully downloaded historical FPL data for {season_code}")
    else:
        print(f"Could not fetch {season_code}, status code: {resp.status_code}")
```

