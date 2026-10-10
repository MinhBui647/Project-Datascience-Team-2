# Từ Điển Dữ Liệu Premier League (Data Dictionary)

Tài liệu bàn giao mô tả chi tiết toàn bộ các trường thuộc tính, kiểu dữ liệu, quan hệ khóa chính/khóa ngoại và đơn vị đo lường cho hệ sinh thái dữ liệu chuẩn hóa của 6 mùa giải Premier League (2021/22 - 2026/27), bao gồm 1.950+ trận đấu, tất cả kèo cá cược, số liệu mô phỏng, thống kê nâng cao CLB và cầu thủ FPL/Understat/FotMob.

---

## 1. Cấu Trúc Tổng Thể

```
data/
├── 00_master_entities/
│   ├── club_mapping.csv            (Từ điển ánh xạ 29 CLB qua Football-Data, Transfermarkt, Understat, FPL, FotMob)
│   ├── clubs.csv                   (Thông tin tĩnh bất biến của 29 CLB: sân vận động, tọa độ, sức chứa, tuổi TB)
│   ├── players.csv                 (Hồ sơ nhân khẩu học & sự nghiệp tĩnh của 1.761 cầu thủ: vị trí, chân thuận, ĐTQG, đỉnh giá)
│   └── player_mapping.csv          (Từ điển ánh xạ chéo định danh 1.761 cầu thủ giữa Transfermarkt, FotMob, Understat, Opta và FPL)
│
├── <season>/                       (2021_2022 -> 2026_2027)
│   ├── clubs/
│   │   ├── club_season_profile.csv      (20 dòng: Chỉ số sức mạnh FPL tổng quát, công, thủ sân nhà/khách)
│   │   └── club_match_performance.csv   (760 dòng: 52 chỉ số gồm xG, chuyền, tắc bóng, tranh chấp, tạt bóng, chuyền dài, sơ đồ, FDR)
│   │
│   ├── matches/
│   │   ├── match_context.csv            (380 dòng: 44 trường gồm bối cảnh, tỷ số hiệp/trận, sơ đồ chiến thuật, BXH trước trận, FDR, xG, thời tiết, HLV)
│   │   ├── match_lineups.csv            (~15.180 dòng: Đội hình xuất phát & dự bị, số áo, vị trí, băng đội trưởng)
│   │   └── match_betting_odds.csv       (380 dòng: 98-124 cột gồm 1X2 mở/chốt mọi nhà cái, AH, O/U, overround, xác suất ngụ ý, odds drift)
│   │
│   ├── players/
│   │   ├── player_valuations.csv        (Timeline định giá quá khứ dạng As-Of phục vụ merge_asof trước giờ đấu)
│   │   ├── player_match_stats.csv       (~11.300 dòng: 32 trường gồm số phút, bàn thắng, sút bóng, xG/xGOT, Opta FPL xG/xA/xGI/xGC, ICT Index)
│   │   └── player_season_understat.csv  (~550 dòng: Chỉ số kiến thiết bóng nâng cao Understat cả mùa gồm xGChain, xGBuildup, key passes)
│   │
│   └── events/
│       └── match_shots.csv              (~10.000 dòng: 29 trường với 100% cầu thủ, tọa độ sân x/y, tọa độ cầu môn on-goal x/y, xG, xGOT)
```

---

## 2. Chi Tiết Các Bảng Thực Thể Dùng Chung (`data/00_master_entities/`)

### 2.1. `club_mapping.csv` (Ánh Xạ Đa Nguồn)
* Bảng tra cứu chéo định danh 27 câu lạc bộ tham dự Premier League qua 5 mùa.
* **Khóa chính:** `club_id` (Chuẩn hóa 3 ký tự: `ARS`, `AVL`, `BOU`, `BRE`, `BHA`, `BUR`, `CHE`, `CRY`, `EVE`, `FUL`, `IPS`, `LEE`, `LEI`, `LIV`, `LUT`, `MCI`, `MUN`, `NEW`, `NOR`, `NFO`, `SHU`, `SOU`, `SUN`, `TOT`, `WAT`, `WHU`, `WOL`).

| Trường | Kiểu | Mô Tả |
|---|:---:|---|
| `club_id` | `VARCHAR(3)` (PK) | Mã chuẩn 3 chữ cái dùng làm khóa liên kết toàn hệ thống |
| `name_standard` | `VARCHAR(50)` | Tên chuẩn hóa tiếng Anh |
| `name_football_data` | `VARCHAR(50)` | Tên trên Football-Data.co.uk |
| `name_transfermarkt` | `VARCHAR(50)` | Tên trên Transfermarkt |
| `name_understat` | `VARCHAR(50)` | Tên trên Understat |
| `name_fpl` | `VARCHAR(50)` | Tên trên Fantasy Premier League API |
| `name_fotmob` | `VARCHAR(50)` | Tên trên FotMob REST API |
| `tm_club_id` | `INTEGER` | Mã số ID của CLB trên Transfermarkt |
| `fpl_team_code` | `INTEGER` | Mã code của CLB trên FPL API |
| `fotmob_team_id` | `INTEGER` | Mã số ID của CLB trên FotMob |

---

### 2.2. `clubs.csv` (Thực Thể Câu Lạc Bộ Bất Biến)
* **Khóa chính:** `club_id`.

| Trường | Kiểu | Đơn Vị / Miền Giá Trị | Mô Tả Ý Nghĩa |
|---|:---:|:---:|---|
| `club_id` | `VARCHAR(3)` | PK (`ARS`, `MCI`,...) | Mã định danh chuẩn hóa duy nhất của CLB |
| `club_name` | `VARCHAR(50)` | Text | Tên chính thức của câu lạc bộ |
| `stadium_name` | `VARCHAR(100)` | Text | Tên sân vận động nhà |
| `stadium_capacity`| `INTEGER` | Chỗ ngồi ($11.000 - 75.000$) | Sức chứa tối đa của sân vận động |
| `latitude` | `FLOAT` | Độ thập phân | Vĩ độ địa lý của sân vận động (dùng truy vấn thời tiết Open-Meteo) |
| `longitude` | `FLOAT` | Độ thập phân | Kinh độ địa lý của sân vận động |
| `squad_size` | `INTEGER` | Cầu thủ ($25 - 38$) | Quy mô danh sách đăng ký đội một |
| `average_age` | `FLOAT` | Tuổi ($24.0 - 28.5$) | Độ tuổi trung bình của đội hình |
| `foreigners_percentage` | `FLOAT` | Tỷ lệ % ($50.0 - 85.0$) | Tỷ lệ cầu thủ ngoại binh trong đội hình |

---

### 2.3. `players.csv` (Hồ Sơ Nhân Khẩu Học & Sự Nghiệp Cầu Thủ Bất Biến)
* **Khóa chính:** `player_id` (Mã định danh Transfermarkt).

| Trường | Kiểu | Đơn Vị / Miền | Mô Tả Ý Nghĩa |
|---|:---:|:---:|---|
| `player_id` | `INTEGER` | PK | Mã số định danh duy nhất của cầu thủ trên Transfermarkt |
| `full_name` | `VARCHAR(100)` | Text | Họ và tên đầy đủ |
| `first_name`, `last_name` | `VARCHAR(50)` | Text | Họ và tên tách rời |
| `date_of_birth`| `DATE` | `YYYY-MM-DD` | Ngày tháng năm sinh chuẩn |
| `country_of_citizenship` | `VARCHAR(50)` | Text | Quốc tịch chính thức |
| `country_of_birth` | `VARCHAR(50)` | Text | Nơi sinh |
| `height_cm` | `FLOAT` | Centimet ($165 - 204$) | Chiều cao của cầu thủ |
| `preferred_foot`| `VARCHAR(10)` | `right`, `left`, `both` | Chân thuận |
| `main_position`| `VARCHAR(20)` | `Goalkeeper`, `Defender`, `Midfield`, `Attack` | Khối vị trí thi đấu chính |
| `sub_position` | `VARCHAR(30)` | `Centre-Back`, `Centre-Forward`,... | Vị trí chiến thuật chi tiết |
| `peak_market_value_eur` | `BIGINT` | Euro (€) | Giá trị thị trường cao nhất từng đạt được trong sự nghiệp |
| `contract_expiration_date` | `DATE` | `YYYY-MM-DD` | Ngày hết hạn hợp đồng hiện tại |
| `international_caps` | `FLOAT` | Trận đấu | Số trận đã ra sân thi đấu cho Đội tuyển Quốc gia |
| `international_goals` | `FLOAT` | Bàn thắng | Số bàn thắng ghi được cho Đội tuyển Quốc gia |

---

### 2.4. `player_mapping.csv` (Từ Điển Ánh Xạ Chéo Đa Nguồn)
* Bảng tra cứu chéo mã định danh cầu thủ qua tất cả các nền tảng: Transfermarkt, FotMob, Understat, Opta, FPL.
* **Khóa chính:** `player_id` (Khóa Transfermarkt tương ứng 100% với `players.csv`).
* **Quy mô:** 1.645 cầu thủ (1.643 khớp FotMob - 99.9%, 1.243 khớp Understat - 100% cầu thủ có số phút ra sân, 1.579 khớp FPL - 96.0%).

| Trường | Kiểu | Miền Giá Trị | Mô Tả Ý Nghĩa |
|---|:---:|:---:|---|
| `player_id` | `INTEGER` (PK) | 1.645 mã duy nhất | Mã định danh chuẩn Transfermarkt làm khóa trung tâm |
| `player_name` | `VARCHAR(100)` | Text | Họ và tên chuẩn hóa của cầu thủ |
| `fotmob_id` | `INTEGER` | Nullable | Mã số định danh cầu thủ trên FotMob (liên kết với `events/match_shots.csv`) |
| `fotmob_name` | `VARCHAR(100)` | Text | Tên hiển thị của cầu thủ trên FotMob |
| `understat_id` | `INTEGER` | Nullable | Mã số định danh cầu thủ trên Understat (liên kết với `players/player_season_understat.csv`) |
| `understat_name` | `VARCHAR(100)` | Text | Tên hiển thị của cầu thủ trên Understat |
| `opta_id` | `VARCHAR(20)` | Nullable | Mã định danh Opta tiêu chuẩn trích xuất từ dữ liệu ra sân FotMob |
| `fpl_name` | `VARCHAR(100)` | Text | Tên cầu thủ trên hệ thống Fantasy Premier League API |


---

## 3. Chi Tiết Các Bảng Chuẩn Hóa Theo Mùa (`data/<season>/`)

### 3.1. Thư Mục `matches/`

#### Bảng `match_context.csv` (Bối Cảnh, Kết Quả, Sơ Đồ & Dự Báo Mô Phỏng)
* **Khóa chính:** `match_id` (Format: `EPL_{SEASON}_GW{ROUND:02d}_{HOME}_{AWAY}`).
* **Số dòng:** Đúng $380$ dòng / mùa. Tổng 5 mùa = $1.900$ trận.

| Trường | Kiểu | Mô Tả |
|---|:---:|---|
| `match_id` | `VARCHAR(40)` (PK) | Mã định danh chuẩn toàn giải (ví dụ: `EPL_2024_2025_GW01_MUN_FUL`) |
| `season` | `VARCHAR(10)` | Mùa giải (`2021_2022` $\rightarrow$ `2025_2026`) |
| `gameweek` | `INTEGER` | Vòng đấu chính thức ($1 \rightarrow 38$) |
| `date`, `time` | `DATE`, `TIME` | Ngày và giờ khai cuộc địa phương |
| `home_club_id`, `away_club_id` | `VARCHAR(3)` (FK) | Mã CLB đội chủ nhà và đội khách |
| `ft_home_goals`, `ft_away_goals` | `INTEGER` | Bàn thắng cả trận (Full-Time) |
| `ft_result` | `CHAR(1)` | Kết quả (`H`: Home thắng, `D`: Hòa, `A`: Away thắng) |
| `ht_home_goals`, `ht_away_goals` | `INTEGER` | Bàn thắng hiệp 1 (Half-Time) |
| `ht_result` | `CHAR(1)` | Kết quả hiệp 1 |
| `home_formation`, `away_formation` | `VARCHAR(30)` | Sơ đồ chiến thuật ra sân của 2 đội (`4-3-3 Attacking`, `4-2-3-1`, `3-5-2 flat`,...) |
| `home_league_rank`, `away_league_rank` | `FLOAT` | Vị trí trên bảng xếp hạng của 2 đội trước giờ bóng lăn ($1 \rightarrow 20$) |
| `fdr_home`, `fdr_away` | `INTEGER` | Chỉ số độ khó trận đấu FPL Fixture Difficulty Rating ($1$ = Dễ nhất, $5$ = Khó nhất) |
| `home_xg`, `away_xg` | `FLOAT` | Bàn thắng kỳ vọng cả trận của 2 đội (từ Understat) |
| `forecast_win`, `forecast_draw`, `forecast_loss` | `FLOAT` | Xác suất dự báo mô phỏng Monte Carlo 10.000 lần (Understat) |
| `home_possession_pct`, `away_possession_pct` | `FLOAT` | Tỷ lệ kiểm soát bóng (%) của 2 đội (từ FotMob) |
| `home_big_chances`, `away_big_chances` | `FLOAT` | Số cơ hội ăn bàn mười mươi tạo ra trong trận |
| `home_xg_open_play`, `away_xg_open_play` | `FLOAT` | xG từ bóng sống |
| `home_xg_set_play`, `away_xg_set_play` | `FLOAT` | xG từ các tình huống cố định |
| `home_xg_on_target`, `away_xg_on_target` | `FLOAT` | Tổng xG của các pha dứt điểm trúng đích ($xGOT$) |
| `referee` | `VARCHAR(50)` | Trọng tài chính điều khiển trận đấu |
| `attendance` | `FLOAT` | Số lượng khán giả đến sân thực tế |
| `home_manager`, `away_manager` | `VARCHAR(50)` | HLV trưởng của 2 đội trong trận |
| `stadium` | `VARCHAR(100)` | Sân vận động tổ chức trận |
| `weather_temperature_c` | `FLOAT` | Nhiệt độ không khí (°C) tại thời điểm bóng lăn |
| `weather_humidity_pct` | `FLOAT` | Độ ẩm tương đối (%) |
| `weather_precipitation_mm` | `FLOAT` | Lượng mưa tức thời (mm) |
| `weather_wind_speed_kmh` | `FLOAT` | Tốc độ gió (km/h) |
| `weather_code` | `INTEGER` | Mã thời tiết chuẩn WMO (0: Quang đãng, 61-65: Mưa,...) |

---

#### Bảng `match_betting_odds.csv` (Tỷ Lệ Kèo, Biên Nhà Cái & Tín Hiệu Thị Trường)
* **Khóa chính:** `match_id` ($380$ dòng / mùa).

| Nhóm Thuộc Tính | Các Cột | Mô Tả |
|---|---|---|
| **Định Danh** | `match_id`, `season`, `gameweek`, `home_club_id`, `away_club_id` | Khóa chính và thông tin tham chiếu trận |
| **Biên Lợi Nhuận (Overround)** | `overround_open`, `overround_close` | Biên lợi nhuận trung bình của nhà cái mở phiên và chốt phiên (%) |
| **Xác Suất Ngụ Ý (Implied Prob)** | `prob_h_open`, `prob_d_open`, `prob_a_open` | Xác suất thắng/hòa/thua ngụ ý chuẩn hóa mở phiên (%) |
| **Xác Suất Chốt Phiên** | `prob_h_close`, `prob_d_close`, `prob_a_close` | Xác suất ngụ ý chuẩn hóa chốt phiên sát giờ đấu (%) |
| **Biến Động Kèo (Odds Drift)** | `odds_drift_h`, `odds_drift_d`, `odds_drift_a` | Tỷ lệ biến động kèo giữa mở và chốt $(O_{close} - O_{open}) / O_{open} \times 100\%$ |
| **Kèo 1X2 Mở Phiên** | `B365H/D/A`, `BWH/D/A`, `IWH/D/A`, `PSH/D/A`, `WHH/D/A`, `VCH/D/A`, `AvgH/D/A`, `MaxH/D/A` | Kèo Châu Âu mở phiên của Bet365, Betway, Interwetten, Pinnacle, William Hill, VC, Market Average, Market Max |
| **Kèo 1X2 Chốt Phiên** | `B365CH/CD/CA`, `BWCH/CD/CA`, `IWCH/CD/CA`, `PSCH/CD/CA`, `WHCH/CD/CA`, `VCCH/CD/CA`, `AvgCH/CD/CA`, `MaxCH/CD/CA` | Kèo Châu Âu chốt phiên sát giờ bóng lăn |
| **Kèo Tài / Xỉu 2.5 Bàn** | `B365>2.5`, `B365<2.5`, `P>2.5`, `P<2.5`, `Avg>2.5`, `Avg<2.5`, `Max>2.5`, `Max<2.5` | Kèo Tài / Xỉu mở phiên |
| **Kèo Tài / Xỉu Chốt Phiên** | `B365C>2.5`, `B365C<2.5`, `PC>2.5`, `PC<2.5`, `AvgC>2.5`, `AvgC<2.5`, `MaxC>2.5`, `MaxC<2.5` | Kèo Tài / Xỉu chốt phiên |
| **Kèo Chấp Châu Á (Asian Handicap)** | `AHh`, `B365AHH`, `B365AHA`, `PAHH`, `PAHA`, `AvgAHH`, `AvgAHA`, `MaxAHH`, `MaxAHA` | Mức chấp và tỷ lệ cược mở phiên |
| **Kèo Chấp Châu Á Chốt Phiên** | `AHCh`, `B365CAHH`, `B365CAHA`, `PCAHH`, `PCAHA`, `AvgCAHH`, `AvgCAHA`, `MaxCAHH`, `MaxCAHA` | Mức chấp và tỷ lệ cược chốt phiên |

---

#### Bảng `match_lineups.csv` (Đội Hình Ra Sân & Dự Bị)
* **Khóa ngoại:** `match_id` $\rightarrow$ `match_context.csv`, `player_id` $\rightarrow$ `00_master_entities/players.csv`.

| Trường | Kiểu | Mô Tả |
|---|:---:|---|
| `match_id` | `VARCHAR(40)` (FK) | Mã trận đấu |
| `club_id` | `VARCHAR(3)` (FK) | Mã CLB |
| `player_id` | `INTEGER` (FK) | Mã cầu thủ |
| `player_name` | `VARCHAR(100)` | Tên hiển thị của cầu thủ |
| `lineup_type` | `VARCHAR(20)` | `starting_lineup` (11 đá chính) hoặc `substitutes` (dự bị) |
| `position` | `VARCHAR(30)` | Vị trí đăng ký sơ đồ chiến thuật (Goalkeeper, Right-Back,...) |
| `jersey_number` | `FLOAT` | Số áo thi đấu |
| `is_captain` | `BOOLEAN` | Đội trưởng trận đấu (`True` / `False`) |

---

### 3.2. Thư Mục `clubs/`

#### Bảng `club_season_profile.csv` (Chỉ Số Sức Mạnh Đội Bóng Toàn Mùa)
* **Số dòng:** Đúng $20$ dòng / mùa.

| Trường | Kiểu | Mô Tả |
|---|:---:|---|
| `season` | `VARCHAR(10)` | Mùa giải |
| `club_id` | `VARCHAR(3)` (FK) | Mã CLB |
| `name` | `VARCHAR(50)` | Tên đội bóng |
| `strength_overall_home`, `away` | `INTEGER` | Đánh giá sức mạnh tổng quát FPL ($1.000 - 1.400$) sân nhà / sân khách |
| `strength_attack_home`, `away` | `INTEGER` | Chỉ số sức tấn công sân nhà / sân khách FPL |
| `strength_defence_home`, `away`| `INTEGER` | Chỉ số sức phòng ngự sân nhà / sân khách FPL |

---

#### Bảng `club_match_performance.csv` (52 Chỉ Số Hiệu Suất CLB Từng Trận)
* **Số dòng:** Đúng $760$ dòng / mùa ($380$ trận $\times$ $2$ đội).
* Cung cấp góc nhìn toàn diện ở cấp độ **Team-Match level**.

| Trường | Kiểu | Đơn Vị | Mô Tả |
|---|:---:|:---:|---|
| `match_id` | `VARCHAR(40)` (FK) | - | Mã trận đấu |
| `season`, `gameweek` | `VARCHAR(10)`, `INT` | - | Mùa giải và vòng đấu |
| `club_id`, `opponent_club_id` | `VARCHAR(3)` (FK) | - | Đội bóng và đối thủ |
| `venue` | `VARCHAR(5)` | `Home` / `Away` | Địa điểm thi đấu |
| `formation` | `VARCHAR(30)` | - | Sơ đồ chiến thuật đội triển khai (`4-3-3`, `4-2-3-1`,...) |
| `league_rank_before_match` | `FLOAT` | Hạng | Thứ hạng trên BXH trước giờ thi đấu |
| `fixture_difficulty_rating` | `INTEGER` | Thang 1-5 | Độ khó trận đấu FDR theo FPL |
| `goals_for`, `goals_against` | `INTEGER` | Bàn | Bàn thắng ghi được / Bàn thua |
| `result` | `CHAR(1)` | `W`, `D`, `L` | Kết quả trận đấu |
| `xg` | `FLOAT` | xG | Bàn thắng kỳ vọng tạo ra (Understat) |
| `shots`, `shots_on_target` | `INTEGER` | Pha sút | Tổng số dứt điểm / Dứt điểm trúng đích |
| `corners`, `fouls` | `INTEGER` | Lần | Số phạt góc / Số pha phạm lỗi |
| `yellow_cards`, `red_cards` | `INTEGER` | Thẻ | Số thẻ vàng / Thẻ đỏ nhận phải |
| `possession_pct` | `FLOAT` | % | Tỷ lệ kiểm soát bóng thực tế (FotMob) |
| `passes`, `accurate_passes` | `FLOAT` | Đường chuyền | Tổng số đường chuyền / Chuyền chính xác |
| `pass_accuracy_pct` | `FLOAT` | % | Tỷ lệ chuyền chính xác |
| `accurate_crosses`, `cross_accuracy_pct` | `FLOAT` | Lần, % | Tạt bóng chính xác và tỷ lệ % |
| `accurate_long_balls`, `long_ball_accuracy_pct`| `FLOAT` | Lần, % | Chuyền dài chính xác và tỷ lệ % |
| `throws` | `FLOAT` | Lần | Số quả ném biên |
| `own_half_passes` | `FLOAT` | Đường chuyền | Số đường chuyền bên phần sân nhà |
| `opposition_half_passes` | `FLOAT` | Đường chuyền | Số đường chuyền bên phần sân đối phương |
| `touches_in_opposition_box` | `FLOAT` | Chạm bóng | Số lần chạm bóng trong vòng cấm đối phương |
| `big_chances`, `big_chances_missed`| `FLOAT` | Cơ hội | Số cơ hội rõ rệt tạo ra / bỏ lỡ |
| `shots_inside_box`, `outside_box` | `FLOAT` | Pha sút | Dứt điểm trong / ngoài vòng 16m50 |
| `blocked_shots`, `hit_woodwork` | `FLOAT` | Pha sút | Cú sút bị chặn / Trúng xà ngang, cột dọc |
| `tackles`, `interceptions` | `FLOAT` | Pha bóng | Số pha tắc bóng / Cắt đường chuyền |
| `blocks`, `clearances` | `FLOAT` | Pha bóng | Chặn cú sút / Giải nguy phá bóng |
| `keeper_saves` | `FLOAT` | Pha bóng | Số pha cứu thua của thủ môn |
| `duels_won` | `FLOAT` | Pha bóng | Tổng số pha tranh chấp tay đôi chiến thắng |
| `ground_duels_won`, `_pct` | `FLOAT` | Lần, % | Tranh chấp bóng sệt thành công và tỷ lệ % |
| `aerial_duels_won`, `_pct` | `FLOAT` | Lần, % | Không chiến thành công và tỷ lệ % |
| `successful_dribbles`, `_pct` | `FLOAT` | Lần, % | Rê dắt bóng qua người thành công và tỷ lệ % |
| `xg_open_play`, `xg_set_play` | `FLOAT` | xG | xG từ bóng sống / xG từ tình huống cố định |
| `xg_non_penalty`, `xg_on_target` | `FLOAT` | xG | xG không phạt đền / xG trúng đích |
| `offsides` | `FLOAT` | Lần | Số lần rơi vào thế việt vị |

---

### 3.3. Thư Mục `players/`

#### Bảng `player_valuations.csv` (Chuỗi Định Giá Quá Khứ As-Of)
* Dùng cho thuật toán ghép `pd.merge_asof(direction='backward')` lấy giá trị thị trường của cầu thủ tại thời điểm diễn ra trận đấu mà không bị rò rỉ dữ liệu tương lai (look-ahead bias).

| Trường | Kiểu | Mô Tả |
|---|:---:|---|
| `player_id` | `INTEGER` (FK) | Mã cầu thủ Transfermarkt |
| `date` | `DATE` | Ngày công bố mốc định giá |
| `market_value_in_eur` | `BIGINT` | Giá trị thị trường (€) chính thức tại mốc đó |
| `current_club_id` | `INTEGER` | ID CLB của cầu thủ tại thời điểm đó |

---

#### Bảng `player_match_stats.csv` (32 Chỉ Số Cầu Thủ Trong Trận: Bàn Thắng, Dứt Điểm, Opta FPL xG, ICT)
* **Số dòng:** ~$10.300 - 11.500$ dòng / mùa. Tỷ lệ ghép thành công FPL đạt **>95%**.

| Trường | Kiểu | Nguồn | Mô Tả Ý Nghĩa |
|---|:---:|:---:|---|
| `match_id` | `VARCHAR(40)` (FK) | TM | Mã trận đấu |
| `player_id` | `INTEGER` (FK) | TM | Mã cầu thủ |
| `club_id` | `VARCHAR(3)` (FK) | TM | Mã CLB |
| `player_name` | `VARCHAR(100)` | TM | Tên cầu thủ |
| `minutes_played` | `INTEGER` | TM | Số phút thực tế thi đấu trên sân ($1 - 90+$) |
| `goals`, `assists` | `INTEGER` | TM | Bàn thắng ghi được / Kiến tạo thành bàn |
| `yellow_cards`, `red_cards` | `INTEGER` | TM | Thẻ vàng / Thẻ đỏ nhận phải trong trận |
| `shots_total` | `INTEGER` | FotMob | **Tổng số cú sút** của cầu thủ thực hiện trong trận |
| `shots_on_target` | `INTEGER` | FotMob | **Số cú sút trúng đích** của cầu thủ |
| `shots_inside_box` | `INTEGER` | FotMob | **Số cú sút trong vòng cấm 16m50** |
| `shots_xg` | `FLOAT` | FotMob | **Tổng xG** từ tất cả các cú sút của cầu thủ trong trận |
| `shots_xgot` | `FLOAT` | FotMob | **Tổng xGOT** (bàn thắng kỳ vọng trúng đích) của cầu thủ |
| `fpl_points` | `FLOAT` | FPL | Tổng điểm kiếm được trong Fantasy Premier League |
| `fpl_bonus` | `FLOAT` | FPL | Điểm thưởng trực tiếp từ hệ thống FPL ($0 - 3$) |
| `fpl_bps` | `FLOAT` | FPL | Bonus Points System score (điểm hiệu suất kỹ thuật FPL) |
| `ict_index` | `FLOAT` | FPL | Chỉ số tổng hợp Influence, Creativity, Threat |
| `influence` | `FLOAT` | FPL | Điểm số đo lường mức độ ảnh hưởng trận đấu |
| `creativity` | `FLOAT` | FPL | Điểm số đo lường mức độ tạo cơ hội cho đồng đội |
| `threat` | `FLOAT` | FPL | Điểm số đo lường độ uy hiếp lên khung thành đối phương |
| `clean_sheets` | `FLOAT` | FPL | $1$ nếu đội bóng giữ sạch lưới trong thời gian thi đấu |
| `goals_conceded` | `FLOAT` | FPL | Số bàn thua phải nhận khi cầu thủ đang có mặt trên sân |
| `saves` | `FLOAT` | FPL | Số pha cứu thua trong trận (dành cho thủ môn) |
| `fpl_xg` | `FLOAT` | FPL (Opta) | **Bàn thắng kỳ vọng chính thức FPL** (có từ 2022/23 trở đi) |
| `fpl_xa` | `FLOAT` | FPL (Opta) | **Kiến tạo kỳ vọng chính thức FPL** |
| `fpl_xgi` | `FLOAT` | FPL (Opta) | **Tổng đóng góp bàn thắng kỳ vọng** ($xG + xA$) |
| `fpl_xgc` | `FLOAT` | FPL (Opta) | **Bàn thua kỳ vọng của đội** khi cầu thủ có mặt trên sân |
| `fpl_value` | `FLOAT` | FPL | Giá trị cầu thủ trong game FPL tại vòng đó (ví dụ: $125$ = £12.5m) |
| `fpl_expected_points` | `FLOAT` | FPL | Điểm kỳ vọng $xP$ do FPL thuật toán tính toán trước vòng đấu |
| `transfers_balance` | `FLOAT` | FPL | Biến động chuyển nhượng ròng của người chơi (in - out) trước vòng |
| `fpl_selected_count` | `FLOAT` | FPL | Số lượng người chơi sở hữu cầu thủ trong đội hình |

---

#### Bảng `player_season_understat.csv` (Hồ Sơ Kiến Thiết & Tấn Công Nâng Cao Cả Mùa Understat)
* **Số dòng:** ~$530 - 570$ cầu thủ / mùa.
* Chứa các chỉ số đo lường vai trò phát triển bóng và làm bóng độc quyền của Understat.

| Trường | Kiểu | Mô Tả Ý Nghĩa |
|---|:---:|---|
| `understat_player_id` | `INTEGER` | Mã cầu thủ trên Understat |
| `player_name` | `VARCHAR(100)` | Tên cầu thủ |
| `club_id` | `VARCHAR(3)` (FK) | Mã CLB chuẩn hóa |
| `season` | `VARCHAR(10)` | Mùa giải |
| `games`, `minutes` | `INTEGER` | Số trận thi đấu và tổng số phút ra sân trong mùa |
| `goals`, `xg` | `FLOAT` | Bàn thắng thực tế / Bàn thắng kỳ vọng cả mùa |
| `assists`, `xa` | `FLOAT` | Kiến tạo thực tế / Kiến tạo kỳ vọng cả mùa |
| `shots`, `key_passes` | `INTEGER` | Tổng số cú sút / Số đường chuyền tạo cơ hội cho đồng đội |
| `npg`, `npxg` | `FLOAT` | Bàn thắng không tính phạt đền / xG không phạt đền |
| `xg_chain` | `FLOAT` | **Tổng xG của toàn bộ chuỗi kiểm soát bóng** mà cầu thủ tham gia chạm bóng (đo lường đóng góp vào các pha bóng thành bàn) |
| `xg_buildup` | `FLOAT` | **Tổng xG chuỗi phát triển bóng** loại trừ cú sút và đường chuyền quyết định (đo lường vai trò điều tiết, kiến thiết từ tuyến dưới) |

---

### 3.4. Thư Mục `events/`

#### Bảng `match_shots.csv` (Sự Kiện Cú Sút Kèm Cầu Thủ & Tọa Độ Không Gian Mặt Khung Thành)
* **Số dòng:** ~$10.000$ dòng / mùa. Tổng cộng **49.415 cú sút** qua 5 mùa.
* **Đặc tính:** **100% bản ghi có thông tin cầu thủ dứt điểm**, tọa độ sân $(x, y)$, và tọa độ không gian chính xác tại mặt phẳng khung thành (`on_goal_shot_x/y`).

| Trường | Kiểu | Đơn Vị / Miền | Mô Tả |
|---|:---:|:---:|---|
| `shot_id` | `BIGINT` (PK) | Mã duy nhất | ID định danh của cú sút (từ FotMob) |
| `match_id` | `VARCHAR(40)` (FK) | Mã trận | Liên kết với `matches/match_context.csv` |
| `club_id` | `VARCHAR(3)` (FK) | Mã đội | Đội bóng thực hiện pha dứt điểm |
| `player_id` | `INTEGER` | ID cầu thủ | Mã số cầu thủ thực hiện dứt điểm |
| `player_name` | `VARCHAR(100)` | Text | Tên cầu thủ thực hiện (100% không rỗng) |
| `first_name`, `last_name` | `VARCHAR(50)` | Text | Tách biệt tên và họ |
| `minute` | `INTEGER` | Phút ($1 - 100$) | Thời điểm thực hiện cú sút trong trận |
| `min_added` | `FLOAT` | Phút bù giờ | Thời điểm trong phút bù giờ (ví dụ: $+3$) |
| `period` | `VARCHAR(15)` | `FirstHalf`, `SecondHalf` | Hiệp đấu |
| `x` | `FLOAT` | $0 - 105\text{m}$ | Tọa độ dọc trên sân (hướng về khung thành đối phương) |
| `y` | `FLOAT` | $0 - 68\text{m}$ | Tọa độ ngang trên sân ($34\text{m}$ là trục giữa sân) |
| `on_goal_shot_x` | `FLOAT` | Tọa độ khung thành | Vị trí ngang bóng hướng tới trên mặt phẳng cầu môn ($0.0 - 2.0$) |
| `on_goal_shot_y` | `FLOAT` | Tọa độ khung thành | Vị trí cao thấp bóng hướng tới trên mặt phẳng cầu môn ($0.0$ là mặt đất) |
| `on_goal_shot_zoom_ratio` | `FLOAT` | Tỷ lệ phóng | Hệ số phóng khung hình cầu môn |
| `blocked_x`, `blocked_y` | `FLOAT` | Tọa độ sân | Tọa độ vị trí bóng bị đối phương chặn lại |
| `goal_crossed_y`, `goal_crossed_z`| `FLOAT` | Tọa độ vạch vôi | Tọa độ chính xác khi bóng vượt qua vạch vôi vào lưới |
| `is_on_target` | `BOOLEAN` | `True` / `False` | Cú sút trúng đích (đi vào trong khung thành) |
| `is_blocked` | `BOOLEAN` | `True` / `False` | Cú sút bị hậu vệ đối phương chặn lại |
| `is_from_inside_box` | `BOOLEAN` | `True` / `False` | Vị trí dứt điểm diễn ra bên trong vòng cấm 16m50 |
| `is_saved_off_line` | `BOOLEAN` | `True` / `False` | Bóng được cản phá ngay trên vạch vôi khung thành |
| `is_own_goal` | `BOOLEAN` | `True` / `False` | Pha đá phản lưới nhà |
| `expected_goals` | `FLOAT` | $0.00 - 0.99$ | Bàn thắng kỳ vọng ($xG$) dựa trên góc sút, khoảng cách, bối cảnh |
| `expected_goals_on_target` | `FLOAT` | $0.00 - 0.99$ | Bàn thắng kỳ vọng trúng đích ($xGOT$) dựa trên quỹ đạo bay và vận tốc |
| `shot_type` | `VARCHAR(20)` | `RightFoot`, `LeftFoot`, `Head` | Bộ phận dứt điểm (chân phải, chân trái, đánh đầu) |
| `situation` | `VARCHAR(20)` | `RegularPlay`, `SetPiece`,... | Tình huống (bóng sống, cố định, phạt góc, phản công, penalty) |
| `event_type` | `VARCHAR(20)` | `Goal`, `AttemptSaved`, `Miss` | Kết quả pha bóng (Bàn thắng, Bị thủ môn cản phá, Đi ra ngoài) |
