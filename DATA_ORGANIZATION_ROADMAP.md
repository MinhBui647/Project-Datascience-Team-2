# Kế Hoạch Tổ Chức & Chuẩn Hóa Dữ Liệu Premier League (CSV Pipeline)

> **Dự án:** Machine Learning & Data Mining - Dự đoán Kết quả & Kèo Bóng đá Ngoại hạng Anh (EPL)  
> **Phạm vi dữ liệu:** 5 mùa giải hoàn chỉnh (`2021_2022` đến `2025_2026`) - Tổng cộng $1.900$ trận đấu.  
> **Định dạng ưu tiên:** Toàn bộ lưu trữ dưới dạng **File CSV chuẩn hóa**, không phụ thuộc DBMS server.

---

## 1. Mục Tiêu & Nguyên Tắc Cốt Lõi

1. **Giải quyết triệt để sự lệch quy mô (Granularity Alignment):**
   * Tách bạch dữ liệu thành **4 tầng quy mô thời gian**:
     * **Tầng 0 (Bất biến):** Dùng chung cho cả 5 mùa (Thông tin nhân khẩu học cầu thủ, thông số sân vận động).
     * **Tầng 1 (Cấp mùa):** Biến động 1 năm / lần (Quỹ lương, giá trị đội hình cả mùa, sức mạnh FPL seasonal).
     * **Tầng 2 (Cấp trận / vòng):** Biến động từng trận 90 phút (Thời tiết, trọng tài, đội hình ra sân, thống kê trận, tỷ lệ kèo mở/chốt, phút thi đấu cầu thủ).
     * **Tầng 3 (Cấp sự kiện / cú sút):** Biến động từng phút (Tọa độ dứt điểm $x, y$, góc sút, xG cú sút).
2. **Nguyên tắc không rò rỉ dữ liệu (No Data Leakage):**
   * Tuyệt đối không đưa số liệu tổng kết cả mùa vào từng trận đấu cụ thể.
   * Định giá cầu thủ và bảng xếp hạng trước trận phải được tổ chức ở dạng dòng thời gian sẵn sàng cho cơ chế ghép Point-in-Time (`as-of join`).
3. **Bảo tồn dữ liệu thô (Raw Preservation):**
   * Mọi thư mục con đều duy trì thư mục `raw/` chứa nguyên vẹn 100% cột gốc của các nguồn (Football-Data, Transfermarkt, Understat, FPL, FotMob, Open-Meteo).

---

## 2. Kế Hoạch Triển Khai Chi Tiết (4 Giai Đoạn)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ GIAI ĐOẠN 1: CHUẨN HÓA KHÓA ĐỊNH DANH (ENTITY RESOLUTION & ID MAPPING)       │
│ • Bảng map 27 CLB chuẩn hóa (ARS, MCI, LIV...)                             │
│ • Mã định danh 1.900 trận đấu duy nhất (match_id)                           │
│ • Ánh xạ ID cầu thủ giữa các nguồn (Transfermarkt, FPL, Understat)          │
├─────────────────────────────────────────────────────────────────────────────┤
│ GIAI ĐOẠN 2: TẠO KHU VỰC THỰC THỂ DÙNG CHUNG (MASTER ENTITIES - TẦNG 0)     │
│ • data/00_master_entities/clubs.csv (27 CLB: sân, sức chứa, tọa độ Lat/Lon) │
│ • data/00_master_entities/players.csv (~1.600 cầu thủ: ngày sinh, vị trí)   │
├─────────────────────────────────────────────────────────────────────────────┤
│ GIAI ĐOẠN 3: XÂY DỰNG CÁC TẬP CSV CHUẨN HÓA THEO MÙA (SEASONS - TẦNG 1,2,3)│
│ • data/<season>/clubs/ (Profile mùa + Match performance 760 dòng)           │
│ • data/<season>/matches/ (Match context + Lineups + Odds Bet365/Pinnacle)    │
│ • data/<season>/players/ (Player valuations as-of + Player match stats)      │
│ • data/<season>/events/ (Shotmap tọa độ x, y từng cú sút ~10.000 dòng)       │
├─────────────────────────────────────────────────────────────────────────────┤
│ GIAI ĐOẠN 4: KIỂM ĐỊNH TOÀN VẸN & XUẤT TỪ ĐIỂN DỮ LIỆU (QA & HANDOVER)      │
│ • Kiểm tra khóa ngoại (Referential Integrity)                               │
│ • Kiểm toán tỷ lệ thiếu (Missing Rate) và tính trùng lặp                     │
│ • Bàn giao file DATA_DICTIONARY.md cho nhóm làm Model/Feature Engineering   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### GIAI ĐOẠN 1: Chuẩn Hóa Khóa Định Danh (Entity Resolution & ID Mapping)

*Do mỗi nguồn crawl sử dụng cách gọi tên và mã ID khác nhau, bước đầu tiên bắt buộc phải xây dựng từ điển ánh xạ chuẩn:*

1. **Bảng ánh xạ 27 Câu lạc bộ (`data/00_master_entities/club_mapping.csv`):**
   * Gồm các trường:
     * `club_id`: Khóa chuẩn 3 chữ cái (`ARS`, `AVL`, `BOU`, `BRE`, `BHA`, `BUR`, `CHE`, `CRY`, `EVE`, `FUL`, `IPS`, `LEE`, `LEI`, `LIV`, `LUT`, `MCI`, `MUN`, `NEW`, `NOR`, `NFO`, `SHU`, `SOU`, `TOT`, `WAT`, `WBA`, `WHU`, `WOL`).
     * `name_standard`: Tên chuẩn tiếng Anh (ví dụ: `Arsenal`).
     * `name_football_data`: Tên dùng trên Football-Data.co.uk (ví dụ: `Man City`, `Man United`, `Nott'm Forest`).
     * `name_transfermarkt`: Tên dùng trên Transfermarkt (ví dụ: `Arsenal FC`, `Nottingham Forest`).
     * `name_understat`: Tên dùng trên Understat (ví dụ: `Manchester City`).
     * `name_fpl`: Tên dùng trên Fantasy Premier League (ví dụ: `Man Utd`, `Wolves`).
     * `name_fotmob`: Tên dùng trên FotMob (ví dụ: `Tottenham Hotspur`).
     * `stadium_name`, `stadium_city`, `latitude`, `longitude`.

2. **Quy tắc tạo Mã định danh Trận đấu duy nhất (`match_id`):**
   * Cấu trúc chuẩn hóa: `EPL_{SEASON}_GW{ROUND}_{HOME_CLUB_ID}_{AWAY_CLUB_ID}`
   * *Ví dụ:* `EPL_2021_2022_GW01_BRE_ARS` (Trận Brentford vs Arsenal vòng 1 mùa 2021/22).
   * Khóa này thay thế toàn bộ các ID rời rạc của từng nguồn, giúp ghép bảng (JOIN) giữa Odds, Weather, Performance, Lineups chỉ bằng một cột duy nhất.

3. **Bảng ánh xạ Cầu thủ (`data/00_master_entities/player_mapping.csv`):**
   * Ánh xạ giữa `transfermarkt_player_id`, `fpl_element_id`, `understat_player_id` dựa trên họ tên, ngày sinh và CLB khoác áo.

---

### GIAI ĐOẠN 2: Tạo Khu Vực Thực Thể Dùng Chung (Master Entities - Tầng 0)

*Tạo thư mục `data/00_master_entities/` lưu trữ các thuộc tính bất biến của giải đấu trong cả 5 mùa:*

1. **`data/00_master_entities/clubs.csv` (27 dòng):**
   * `club_id` (PK): Mã CLB (`ARS`, `MCI`,...).
   * `club_name`: Tên CLB.
   * `founded_year`: Năm thành lập.
   * `stadium_name`: Tên sân nhà.
   * `stadium_capacity`: Sức chứa tối đa.
   * `pitch_length_m`, `pitch_width_m`: Kích thước mặt sân ($105\text{m} \times 68\text{m}$).
   * `latitude`, `longitude`: Tọa độ địa lý của sân (phục vụ lấy thời tiết và tính khoảng cách di chuyển).

2. **`data/00_master_entities/players.csv` (~1.600 dòng):**
   * `player_id` (PK): Mã định danh cầu thủ chuẩn.
   * `full_name`, `first_name`, `last_name`: Họ và tên.
   * `date_of_birth`: Ngày tháng năm sinh (chuẩn `YYYY-MM-DD`).
   * `country_of_citizenship`: Quốc tịch gốc.
   * `height_cm`: Chiều cao (cm).
   * `preferred_foot`: Chân thuận (`Left`, `Right`, `Both`).
   * `primary_position`: Vị trí thi đấu cơ bản (`GK`, `DF`, `MF`, `FW`).

---

### GIAI ĐOẠN 3: Xây Dựng Các Tập CSV Chuẩn Hóa Theo Mùa (Tầng 1, 2, 3)

*Cấu trúc thư mục của từng mùa giải (`data/<season>/`):*

```
data/<season>/
├── clubs/
│   ├── raw/                             (Giữ nguyên 3 file raw)
│   ├── club_season_profile.csv          [Tầng 1 - 20 dòng]
│   └── club_match_performance.csv       [Tầng 2 - 760 dòng]
│
├── matches/
│   ├── raw/                             (Giữ nguyên các file raw)
│   ├── match_context.csv                [Tầng 2 - 380 dòng]
│   ├── match_lineups.csv                [Tầng 2 - ~15.000 dòng]
│   └── match_betting_odds.csv           [Tầng 2 - 380 dòng]
│
├── players/
│   ├── raw/                             (Giữ nguyên 5 file raw)
│   ├── player_valuations.csv            [Tầng 1-2 - Timeline As-Of]
│   └── player_match_stats.csv           [Tầng 2 - ~11.000 dòng]
│
└── events/
    ├── raw/                             (Giữ nguyên các file raw)
    └── match_shots.csv                  [Tầng 3 - ~10.000 dòng]
```

#### Chi tiết cấu trúc từng file chuẩn hóa:

#### A. Khu vực `clubs/`
1. **`club_season_profile.csv` (Tầng 1 - 20 dòng / mùa):**
   * *Mô tả:* Snapshot thuộc tính toàn mùa của 20 đội bóng tham dự mùa giải đó.
   * *Trường dữ liệu:* `season`, `club_id`, `squad_size`, `avg_squad_age`, `foreigners_ratio`, `total_market_value_eur`, `fpl_strength_overall_home`, `fpl_strength_overall_away`, `fpl_strength_attack_home`, `fpl_strength_attack_away`, `fpl_strength_defence_home`, `fpl_strength_defence_away`.
2. **`club_match_performance.csv` (Tầng 2 - 760 dòng / mùa = 380 trận $\times$ 2 đội):**
   * *Mô tả:* Bảng phân tích hiệu suất chuyên môn của từng đội trong từng trận đấu cụ thể (thiết kế theo mô hình Team-Match để mô hình hóa dễ dàng).
   * *Trường dữ liệu:* `match_id`, `season`, `gameweek`, `club_id`, `opponent_club_id`, `venue` (`Home` / `Away`), `formation` (4-3-3, 3-4-2-1), `goals_for`, `goals_against`, `result` (`W`, `D`, `L`), `xg`, `shots_total`, `shots_on_target`, `corners`, `fouls_committed`, `yellow_cards`, `red_cards`, `possession_pct`, `passes_completed`, `tackles`, `interceptions`, `blocks`, `clearances`, `duels_won`.

#### B. Khu vực `matches/`
1. **`match_context.csv` (Tầng 2 - 380 dòng / mùa):**
   * *Mô tả:* Bối cảnh môi trường và metadata của từng trận đấu.
   * *Trường dữ liệu:* `match_id`, `season`, `gameweek`, `date`, `kickoff_time_utc`, `home_club_id`, `away_club_id`, `referee`, `attendance`, `home_manager`, `away_manager`, `weather_temperature_c`, `weather_humidity_pct`, `weather_precipitation_mm`, `weather_wind_speed_kmh`, `weather_code`, `ft_home_goals`, `ft_away_goals`, `ht_home_goals`, `ht_away_goals`.
2. **`match_lineups.csv` (Tầng 2 - ~15.000 dòng / mùa):**
   * *Mô tả:* Danh sách đội hình chi tiết được công bố T-60 phút trước giờ bóng lăn.
   * *Trường dữ liệu:* `match_id`, `club_id`, `player_id`, `player_name`, `lineup_type` (`starting_lineup` / `substitutes`), `position`, `jersey_number`, `is_captain`.
3. **`match_betting_odds.csv` (Tầng 2 - 380 dòng / mùa):**
   * *Mô tả:* Tỷ lệ kèo cá cược thị trường mở phiên và chốt phiên.
   * *Trường dữ liệu:* `match_id`, Kèo 1X2 Mở (`b365_h`, `b365_d`, `b365_a`, `pinnacle_h`, `pinnacle_d`, `pinnacle_a`, `avg_h`, `avg_d`, `avg_a`, `max_h`, `max_d`, `max_a`), Kèo 1X2 Chốt (`b365c_h`, `b365c_d`, `b365c_a`, `pinnaclec_h`, `pinnaclec_d`, `pinnaclec_a`, `avgc_h`, `avgc_d`, `avgc_a`), Kèo Tài/Xỉu 2.5 (`b365_over25`, `b365_under25`, `pinnacle_over25`, `pinnacle_under25`), Kèo Chấp Châu Á (`ah_line`, `b365_ah_home`, `b365_ah_away`, `pinnacle_ah_home`, `pinnacle_ah_away`).

#### C. Khu vực `players/`
1. **`player_valuations.csv` (Tầng 1-2):**
   * *Mô tả:* Lịch sử định giá Transfermarkt của các cầu thủ thi đấu trong mùa tính đến hết mùa giải đó, phục vụ việc tìm kiếm giá trị thị trường có hiệu lực trước trận đấu (`merge_asof(direction='backward')`).
   * *Trường dữ liệu:* `player_id`, `date`, `market_value_in_eur`, `club_id`.
2. **`player_match_stats.csv` (Tầng 2 - ~11.000 dòng / mùa):**
   * *Mô tả:* Thông số cá nhân của từng cầu thủ trong từng trận đấu.
   * *Trường dữ liệu:* `match_id`, `player_id`, `club_id`, `minutes_played`, `goals`, `assists`, `yellow_cards`, `red_cards`, `player_rating` (từ FotMob/Sofascore), `fpl_points`, `fpl_bonus`, `ict_index`, `influence`, `creativity`, `threat`.

#### D. Khu vực `events/`
1. **`match_shots.csv` (Tầng 3 - ~10.000 dòng / mùa):**
   * *Mô tả:* Toàn bộ các cú sút trong mùa kèm tọa độ chi tiết trên sân.
   * *Trường dữ liệu:* `shot_id`, `match_id`, `club_id`, `player_id`, `minute`, `period` (`FirstHalf`/`SecondHalf`), `x` ($0 - 105$), `y` ($0 - 68$), `is_on_target`, `is_blocked`, `expected_goals` ($xG$), `expected_goals_on_target` ($xGOT$), `shot_type` (`RightFoot`, `LeftFoot`, `Head`), `situation` (`RegularPlay`, `SetPiece`, `Corner`, `FastBreak`, `Penalty`).

---

### GIAI ĐOẠN 4: Kiểm Định Toàn Vẹn & Xuất Từ Điển Dữ Liệu (QA & Handover)

1. **Kiểm tra Tính Toàn Vẹn Khóa Ngoại (Referential Integrity):**
   * 100% `club_id` xuất hiện trong các bảng mùa phải nằm trong `data/00_master_entities/clubs.csv`.
   * 100% `match_id` trong `odds`, `lineups`, `performance`, `shots` phải khớp tuyệt đối với `matches/match_context.csv`.
2. **Kiểm toán Số lượng Bản ghi (Record Completeness):**
   * Đảm bảo đủ chính xác **380 trận / mùa** và tổng **1.900 trận** trên toàn bộ 5 mùa giải.
3. **Biên soạn Từ Điển Dữ Liệu (`DATA_DICTIONARY.md`):**
   * Ghi chú rõ ý nghĩa từng cột, đơn vị đo lường (cm, €, °C, mm, km/h), kiểu dữ liệu (`int`, `float`, `string`, `datetime`) và công thức tính.
   * Đóng gói tài liệu để chuyển giao cho các thành viên phụ trách Feature Engineering & Modeling.

---

## 3. Các Bước Sẵn Sàng Tiến Hành Tiếp Theo

Khi bạn sẵn sàng chuyển sang phần thực thi, chúng ta sẽ lần lượt thực hiện theo thứ tự:
1. **Bước 1:** Tạo file từ điển ánh xạ 27 CLB (`data/00_master_entities/club_mapping.csv`) và trích xuất bảng thực thể tĩnh `clubs.csv`, `players.csv`.
2. **Bước 2:** Viết script chuẩn hóa tự động gom và sinh ra các file CSV phân tầng cho từng mùa giải (`clubs/`, `matches/`, `players/`, `events/`).
3. **Bước 3:** Chạy bộ kiểm thử tự động xác nhận tính toàn vẹn và xuất báo cáo nghiệm thu hoàn tất.

