# 📖 EPL Master Raw Data Catalog & Dictionary (2000 - 2027)

> **Mục đích tài liệu**: Tài liệu này đóng vai trò là **Từ điển Dữ liệu Thô (Data Dictionary)** cho toàn bộ dự án Premier League, giúp các thành viên trong nhóm làm nhiệm vụ trích rút tri thức (Knowledge Extraction), kỹ nghệ đặc trưng (Feature Engineering), Trực quan hóa (Visualization) và Huấn luyện mô hình Học máy (Machine Learning) dễ dàng tra cứu, truy xuất và kết nối các nguồn dữ liệu.

---

## 🏛️ Cấu Trúc Tổng Thể Thư Mục `raw_data/`

Toàn bộ dữ liệu thô được lưu trữ tập trung tại thư mục `raw_data/` (đã được cấu hình trong `.gitignore` để không làm nặng kho lưu trữ Git và được đồng bộ tự động lên [Hugging Face Hub: Toibinguz/epl-premier-league-dataset](https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset)):

```text
raw_data/
├── fbref/               : 16 files  | 283.5 MB | Thống kê vi mô cầu thủ nâng cao (StatsBomb)
├── transfermarkt/       : 15 files  | 244.7 MB | Định giá, diễn biến từng phút, đội hình, chuyển nhượng
├── sofascore/           : 6,146 files | 980.4 MB | Tọa độ sút (x,y), sơ đồ, chấm điểm, áp lực từng trận
├── understat/           : 4,633 files | 179.0 MB | Tọa độ sút (X,Y), xG từng cú sút, xGChain, xGBuildup
├── fpl_api/             : 37 files  |  53.5 MB | Micro-stats từng Gameweek (ICT Index, threat, bonus)
├── open_meteo/          : 30 files  |  51.4 MB | Thời tiết vi mô từng sân và dữ liệu khí tượng UK
└── football_data_co_uk/ : 32 files  |   3.9 MB | Tỷ số 27 mùa, thẻ phạt, góc, trọng tài, odds 1X2, O/U
----------------------------------------------------------------------------------------------------
TỔNG CỘNG                 : 10,909 files | ~1.80 GB Dữ liệu vi mô sạch
```

---

## 🔍 Chi Tiết Từng Nguồn Dữ Liệu

### 1. `raw_data/fbref/` (StatsBomb Advanced Analytics)
* **Phạm vi thời gian**: 2017/18 đến 2024/25 (8 mùa giải StatsBomb theo dõi Ngoại Hạng Anh).
* **Định dạng**: File CSV chuẩn.
* **Danh sách file & Đặc trưng**:
  1. `ENG_M_1st_passing_player_advanced_match_stats.csv` (45.9 MB):
     * Cột chính: `Player`, `Match_Date`, `Home_Team`, `Away_Team`, `PrgP` (Progressive Passes), `KP` (Key Passes), `PPA` (Passes into Penalty Area), `CrsPA` (Crosses into Penalty Area), tỷ lệ chuyền ngắn/trung bình/dài.
  2. `ENG_M_1st_defense_player_advanced_match_stats.csv` (43.6 MB):
     * Cột chính: `Pressures` (áp sát 1/3 sân phòng ngự/trung tuyến/tấn công), `Tkl` (Tackles), `TklW`, `Blocks`, `Int` (Interceptions), `Tkl+Int`, `Clr` (Clearances), `Err` (lỗi dẫn đến dứt điểm).
  3. `ENG_M_1st_possession_player_advanced_match_stats.csv` (44.2 MB):
     * Cột chính: `Touches` (chạm bóng theo từng khu vực), `PrgC` (Progressive Carries - dẫn bóng tịnh tiến), `Succ` (qua người thành công), `Att` (thử qua người), `Dis` (bị cướp bóng).
  4. `ENG_M_1st_misc_player_advanced_match_stats.csv` (42.5 MB):
     * Cột chính: `Won` (tranh chấp trên không thắng), `Lost` (thua trên không), `Won%`, `Recov` (thu hồi bóng), `Fls` (phạm lỗi), `Fld` (bị phạm lỗi).
  5. `ENG_M_1st_match_shooting.csv` (20.3 MB):
     * Cột chính: Chi tiết từng cú sút trên FBref, `Distance` (khoảng cách đến khung thành tính bằng mét), `PSxG` (Post-Shot Expected Goals), bộ phận dứt điểm.
  6. `ENG_M_1st_summary_player_advanced_match_stats.csv` (4.6 MB):
     * Cột chính: `xG`, `npxG`, `xAG`, `SCA` (Shot-Creating Actions), `GCA` (Goal-Creating Actions).
  7. `ENG_M_1st_keeper_player_advanced_match_stats.csv` (3.2 MB):
     * Cột chính: `PSxG+/-` (chỉ số bàn thua ngăn chặn kỳ vọng của thủ môn), `Stp%` (tỷ lệ cắt đường tạt bóng).
  8. `ENG_M_1st_match_summary.csv` (18.3 MB): Timeline biên bản diễn biến trận đấu.
  9. Các file `*_team_advanced_match_stats.csv`: Thống kê tổng hợp cấp CLB cho từng trận tương ứng.

* **Cách đọc trong Python**:
  ```python
  import pandas as pd
  df_defense = pd.read_csv("raw_data/fbref/ENG_M_1st_defense_player_advanced_match_stats.csv")
  df_passing = pd.read_csv("raw_data/fbref/ENG_M_1st_passing_player_advanced_match_stats.csv")
  ```

---

### 2. `raw_data/transfermarkt/` (Thị Trường & Sự Kiện Chuyển Nhượng)
* **Phạm vi thời gian**:
  * Định giá: 2000 đến 2026.
  * Diễn biến trận đấu, đội hình: 2012 đến 2026.
* **Định dạng**: File CSV nén gzip (`.csv.gz`).
* **Danh sách file & Đặc trưng**:
  1. `tfm_player_valuations_full.csv.gz` (7.2 MB, > 650,000 dòng): Định giá thị trường của mọi cầu thủ từ năm 2000 đến nay theo từng mốc ngày cập nhật.
  2. `tfm_games_full.csv.gz` (5.0 MB, 5,320 trận EPL): Lịch thi đấu, sân vận động, trọng tài, huấn luyện viên 2 đội, sơ đồ ra sân.
  3. `tfm_game_events_full.csv.gz` (43.4 MB): Toàn bộ sự kiện từng phút: phút ghi bàn, kiến tạo, thẻ phạt, thay người ra/vào kèm mã ID cầu thủ.
  4. `tfm_game_lineups_full.csv.gz` (120.0 MB): Đội hình xuất phát, danh sách dự bị, số áo, vị trí, băng đội trưởng từ năm 2012 đến nay.
  5. `tfm_transfers_full.csv.gz` (5.5 MB): Toàn bộ lịch sử chuyển nhượng, mức phí chuyển nhượng (transfer fee), câu lạc bộ đi/đến.
  6. `tfm_appearances_full.csv.gz` (45.3 MB): Số phút thi đấu, bàn thắng, kiến tạo, thẻ phạt của từng cầu thủ trong từng trận.
  7. `tfm_players_full.csv.gz`, `tfm_clubs_full.csv.gz`: Hồ sơ thông tin cá nhân (chiều cao, quốc tịch, chân thuận, ngày sinh, hợp đồng).

* **Cách đọc trong Python**:
  ```python
  import pandas as pd
  df_events = pd.read_csv("raw_data/transfermarkt/tfm_game_events_full.csv.gz", compression="gzip")
  df_lineups = pd.read_csv("raw_data/transfermarkt/tfm_game_lineups_full.csv.gz", compression="gzip")
  ```

---

### 3. `raw_data/sofascore/` (FotMob/Sofascore Match Engine)
* **Phạm vi thời gian**: 2010/11 đến 2026/27 (17 mùa giải).
* **Định dạng**: File JSON chi tiết theo từng trận đấu.
* **Cấu trúc**:
  * `raw_data/sofascore/cache_matches/{match_id}.json` (> 6,000 file JSON):
    * `content.shotmap.shots`: Mảng chứa tọa độ thực $x, y$ của từng cú dứt điểm, góc sút, cầu thủ dứt điểm, xG, xGOT (xG trúng đích).
    * `content.lineup.lineup`: Đội hình ra sân, vị trí hiển thị trên sa bàn, điểm số cầu thủ (FotMob Rating), số lần chạm bóng, tỷ lệ chuyền bóng chính xác.
    * `content.matchFacts`: Tỷ lệ cầm bóng, số đường chuyền, big chances, số pha cản phá.
    * `content.momentum`: Chỉ số áp lực thế trận theo từng phút.

* **Cách đọc trong Python**:
  ```python
  import json
  with open("raw_data/sofascore/cache_matches/3411675.json", "r", encoding="utf-8") as f:
      match_data = json.load(f)
  shots = match_data.get("content", {}).get("shotmap", {}).get("shots", [])
  ```

---

### 4. `raw_data/understat/` (xG & Advanced Chain Metrics)
* **Phạm vi thời gian**: 2014/15 đến 2026/27 (13 mùa giải).
* **Định dạng**: File JSON chi tiết theo từng trận đấu và file CSV mùa giải.
* **Cấu trúc**:
  * `raw_data/understat/cache_matches/{match_id}.json` (4,611 file JSON):
    * `shots`: Toàn bộ cú sút của đội nhà (`h`) và đội khách (`a`) với tọa độ `X`, `Y` (chuẩn hóa 0 đến 1), `xG`, `situation` (OpenPlay, SetPiece, FromCorner, Penalty), `shotType` (LeftFoot, RightFoot, Head), `lastAction` (Pass, Cross, Throughball...), `player`, `player_assisted`.
    * `rosters`: Danh sách cầu thủ thi đấu kèm hai chỉ số vi mô nâng cao:
      * **`xGChain`**: Tổng xG của mọi chuỗi tấn công mà cầu thủ tham gia.
      * **`xGBuildup`**: Tổng xG của các chuỗi tấn công mà cầu thủ tham gia triển khai từ tuyến dưới (không tính cú dứt điểm và đường chuyền tạo cơ hội).
  * `understat_matches_all_2014_2027.csv`: Tổng hợp 4,940 trận (tổng xG, xGA, xác suất thắng/hòa/thua theo mô hình dự báo).
  * `understat_players_all_2014_2027.csv`: 6,845 bản ghi cầu thủ theo mùa giải.

* **Cách đọc trong Python**:
  ```python
  import json
  with open("raw_data/understat/cache_matches/16376.json", "r", encoding="utf-8") as f:
      data = json.load(f)
  home_shots = data.get("shots", {}).get("h", [])
  home_roster = data.get("rosters", {}).get("h", {})
  ```

---

### 5. `raw_data/football_data_co_uk/` (Kết Quả & Kèo Nhà Cái)
* **Phạm vi thời gian**: Trọn vẹn **27 mùa giải** (2000/01 đến 2026/27).
* **Định dạng**: File CSV (`football_data_raw_<season>.csv`).
* **Đặc trưng**:
  * Kết quả: Tỷ số cả trận (`FTHG`, `FTAG`, `FTR`), tỷ số hiệp 1 (`HTHG`, `HTAG`, `HTR`).
  * Chỉ số trận đấu: Sút bóng (`HS`, `AS`), sút trúng đích (`HST`, `AST`), phạt góc (`HC`, `AC`), phạm lỗi (`HF`, `AF`), thẻ vàng (`HY`, `AY`), thẻ đỏ (`HR`, `AR`).
  * Trọng tài (`Referee`), số lượng khán giả (`Attendance`).
  * Tỷ lệ cược từ các nhà cái lớn nhất thế giới: Bet365 (`B365H`, `B365D`, `B365A`), Pinnacle (`PSH`, `PSD`, `PSA`), William Hill (`WHH`), Ladbrokes (`LBH`), Betfair Exchange, Kèo Châu Á (Asian Handicap: `AHh`, `BbAv<H`), Tài Xỉu (Over/Under: `BbAv>2.5`).

---

### 6. `raw_data/fpl_api/` (Fantasy Premier League Micro-Stats)
* **Phạm vi thời gian**: 2016/17 đến 2025/26 (10 mùa) + Mùa hiện tại 2026/27.
* **Định dạng**: File CSV theo mùa và file JSON snapshot.
* **Đặc trưng**:
  * `fpl_merged_gw_<season>.csv`: Chỉ số vi mô của từng cầu thủ trong từng vòng đấu (Gameweek):
    * `influence`, `creativity`, `threat`, `ict_index`.
    * `total_points`, `bonus`, `bps`, `minutes`, `value` (giá cầu thủ), `selected` (tỷ lệ sở hữu).
  * `fpl_bootstrap_latest.json`: Danh mục đội bóng, danh sách cầu thủ hiện tại của mùa 2026/27.

---

### 7. `raw_data/open_meteo/` (Dữ Liệu Khí Tượng & Thời Tiết)
* **Phạm vi thời gian**: 2000 đến 2026.
* **Đặc trưng**:
  * `epl_weather_archive_2000_2021.csv`: 68,481 bản ghi thời tiết ngày tại 9 khu vực địa lý UK lớn từ năm 2000 đến 2021 (từ mô hình ERA5 Reanalysis).
  * `stadiums/`: Tọa độ GPS (Vĩ độ / Kinh độ) của hơn 25 sân vận động Premier League dùng để truy vấn thời tiết real-time.

---

## 🛠️ Quy Ước Ghép Nối Dữ Liệu (Entity Joining Guide)

Để join các nguồn dữ liệu này lại với nhau phục vụ Machine Learning:
1. **Khóa cấp Trận đấu**: Ghép theo `Match_Date` (chuẩn hóa `YYYY-MM-DD`) + `Home_Team_Standard` + `Away_Team_Standard`.
2. **Khóa cấp Cầu thủ**: Ghép theo `Player_Name_Clean` + `Team_Standard` + `Season`.

