# Danh Mục Đặc Trưng (Features) & Ứng Viên API Khả Thi (EPL 5 Mùa Gần Nhất)

Tài liệu này đóng vai trò là **từ điển đặc trưng (Feature Catalog)** trung tâm cho toàn bộ pipeline dữ liệu. Tài liệu phân loại các đặc trưng cần trích rút cho 5 mùa giải Premier League (2021/22 đến 2025/26) theo **7 nhóm dữ liệu**, đồng thời chỉ định cụ thể các **ứng viên API khả thi** và **giới hạn kỹ thuật** tương ứng.

---

## 1. Bảng Tổng Hợp Ứng Viên API & Phạm Vi Phụ Trách

| API / Nguồn Dữ Liệu | Loại Hình | Nhóm Đặc Trưng Phụ Trách Chính | Giới Hạn Cốt Lõi |
|---|---|---|---|
| **Football-Data.co.uk** | File CSV Tĩnh | Nhóm 4 (Tỷ số, Sút, Thẻ, Góc), Nhóm 7 (Kèo 1X2, Handicap, O/U) | Không có dữ liệu cầu thủ và xG |
| **Understat** | Internal JSON Payload | Nhóm 4 (xG, xGA, xGChain, xGBuildup), Nhóm 5 (Shotmap x, y) | Chỉ có từ 2014, thiếu stats phòng ngự/thẻ |
| **FPL Official API** | Public REST API (Free) | Nhóm 1 (Sức mạnh đội), Nhóm 2 (Hồ sơ cầu thủ, ICT, chấn thương) | Chỉ lưu mùa giải hiện tại |
| **Sofascore v1** | Internal REST API | Nhóm 3 (Đội hình T-60), Nhóm 5 (Rating, Duels, Touches) | Cloudflare WAF, cần giả lập TLS trình duyệt |
| **Transfermarkt** | Open Mirror / Scraper | Nhóm 1 (Giá trị CLB, Quỹ lương), Nhóm 2 (Định giá cầu thủ, HĐ) | Cloudflare bảo vệ; cập nhật dạng batch |
| **Open-Meteo** | Public Weather API | Nhóm 3 (Thời tiết: Nhiệt độ, mưa, gió theo tọa độ SVĐ) | Cần tọa độ địa lý (Lat, Lon) của sân |
| **FBref** | HTML Web Scraper | Nhóm 4 (PPDA, Progressive Passes, SCA, GCA, PSxG) | **Khóa IP 1 giờ nếu vượt 20 req/phút** |

---

## 2. Chi Tiết Các Đặc Trưng & Ứng Viên Theo 7 Nhóm Dữ Liệu

---

### NHÓM 1: THỰC THỂ ĐỘI BÓNG (CLUB / TEAM ATTRIBUTES)
*Thư mục lưu trữ: `data/organized/<season>/01_club_attributes/`*

#### Danh mục đặc trưng:
* `team_id`: Mã định danh chuẩn hóa (ví dụ: `ARS`, `MCI`, `LIV`, `CHE`, `MUN`).
* `team_name`, `official_name`: Tên phổ thông và tên đăng ký chính thức.
* `founded_year`: Năm thành lập CLB.
* `stadium_name`, `stadium_capacity`: Tên sân nhà và sức chứa tối đa.
* `pitch_surface`, `pitch_dimensions`: Mặt cỏ (tự nhiên/lai) và kích thước sân ($105\text{m} \times 68\text{m}$).
* `stadium_lat`, `stadium_lon`: Tọa độ địa lý sân nhà (dùng để truy vấn thời tiết và tính khoảng cách di chuyển).
* `market_value_total_eur`: Tổng giá trị đội hình ước tính theo mùa (€).
* `wage_bill_annual_gbp`: Tổng quỹ lương hàng năm (£).
* `avg_squad_age`: Tuổi trung bình của đội hình thi đấu.
* `foreign_players_ratio`: Tỷ lệ cầu thủ ngoại binh trong danh sách đăng ký.
* `manager_name`, `manager_appointed_date`: HLV trưởng và ngày bắt đầu dẫn dắt.

#### Ứng viên API & Giới hạn:
* **FPL API (`/bootstrap-static/`):** Lấy ID đội, tên viết tắt, chỉ số sức mạnh công/thủ (`strength_attack_home`, `strength_defence_away`). Không có hạn chế về rate limit.
* **Transfermarkt Open Dataset:** Cung cấp định giá đội hình, quỹ lương, độ tuổi trung bình qua 5 mùa giải. Cần ánh xạ tên đội (Name matching) vì Transfermarkt dùng tên tiếng Đức/quốc tế (ví dụ: `Arsenal FC`, `Manchester City`).
* **TheSportsDB:** Cung cấp tọa độ địa lý và sức chứa sân vận động (gọi 1 lần lưu dùng chung cho cả 5 mùa).

---

### NHÓM 2: THỰC THỂ CẦU THỦ (PLAYER ATTRIBUTES)
*Thư mục lưu trữ: `data/organized/<season>/02_player_attributes/`*

#### Danh mục đặc trưng:
* `player_id`, `full_name`, `date_of_birth`, `age`: Hồ sơ nhân khẩu học.
* `nationality_primary`: Quốc tịch gốc.
* `height_cm`, `weight_kg`: Chiều cao và thể trọng.
* `preferred_foot`: Chân thuận (`Left`, `Right`, `Both`).
* `primary_position`: Vị trí chính (`GK`, `CB`, `LB`, `RB`, `DM`, `CM`, `AM`, `LW`, `RW`, `ST`).
* `market_value_eur`: Giá trị thị trường theo mốc thời gian (€).
* `contract_expires`: Ngày hết hạn hợp đồng.
* `fpl_cost`: Mức định giá cầu thủ trong giải (£m).
* `fpl_ict_index`: Chỉ số tác động toàn diện FPL (`influence`, `creativity`, `threat`).
* `injury_status`, `chance_of_playing`: Tình trạng y tế và khả năng ra sân vòng đấu tới ($0\% - 100\%$).
* `season_minutes_played`, `season_goals`, `season_assists`, `season_xg`, `season_xa`: Đóng góp tích lũy từ đầu mùa.

#### Ứng viên API & Giới hạn:
* **FPL API:** Nguồn lý tưởng nhất cho vị trí, giá tiền, xG tích lũy, ICT index và tình trạng chấn thương. Hạn chế: Endpoint chỉ mở cho mùa giải hiện tại; đối với các mùa lịch sử (21/22 - 24/25), phải dùng các bản mirror lưu trữ của cộng đồng FPL (`vaastav/Fantasy-Premier-League` trên GitHub).
* **Transfermarkt Mirror:** Bổ sung chiều cao, cân nặng, chân thuận, thời hạn hợp đồng và giá trị thị trường.

---

### NHÓM 3: BỐI CẢNH & ĐIỀU KIỆN TRẬN ĐẤU (MATCH METADATA & ENVIRONMENT)
*Thư mục lưu trữ: `data/organized/<season>/03_match_context/`*

#### Danh mục đặc trưng:
* `match_id`: Mã định danh thống nhất trận đấu (`SHA256(Season + Date + Home + Away)`).
* `gameweek`: Số thứ tự vòng đấu ($1 \rightarrow 38$).
* `kickoff_datetime_utc`: Thời điểm khai cuộc chính xác.
* `rest_days_home`, `rest_days_away`: Số ngày nghỉ ngơi kể từ trận đấu trước đó.
* `attendance`, `stadium_fill_rate`: Số lượng khán giả và tỷ lệ lấp đầy sân (%).
* `weather_temp_c`, `weather_rain_mm`, `weather_wind_speed_kmh`: Nhiệt độ, lượng mưa, tốc độ gió tại thời điểm bóng lăn.
* `referee_name`: Tên trọng tài chính.
* `home_formation`, `away_formation`: Sơ đồ chiến thuật xuất phát (`4-3-3`, `3-4-2-1`).
* `home_starting_xi_ids`, `away_starting_xi_ids`: Danh sách 11 cầu thủ đá chính mỗi đội.
* `home_squad_rotation_count`, `away_squad_rotation_count`: Số vị trí thay đổi so với đội hình trận trước.

#### Ứng viên API & Giới hạn:
* **Open-Meteo API:** Truy vấn lịch sử thời tiết chuẩn xác theo giờ dựa trên `stadium_lat`, `stadium_lon` và `kickoff_datetime_utc`. Hoàn toàn miễn phí, hạn mức 10.000 req/ngày.
* **Sofascore API (`/event/{id}/lineups`):** Lấy đội hình đá chính và sơ đồ chiến thuật. Hạn chế: Phải có `event_id` của Sofascore và xử lý trễ an toàn 3-4s để không bị Cloudflare chặn.
* **Football-Data.co.uk:** Cung cấp sẵn tên trọng tài chính và ngày giờ thi đấu.

---

### NHÓM 4: THỐNG KÊ DIỄN BIẾN & TRẬN ĐẤU (MATCH PERFORMANCE & EVENT DATA)
*Thư mục lưu trữ: `data/organized/<season>/04_match_performance/`*

#### Danh mục đặc trưng:
* **Tỷ số:** `ht_home_goals`, `ht_away_goals` (Hiệp 1); `ft_home_goals`, `ft_away_goals` (Cả trận).
* **Sút bóng:** `shots_total`, `shots_on_target`, `shots_off_target`, `shots_blocked`, `hit_woodwork`.
* **Kiểm soát & Phân phối:** `possession_pct`, `passes_total`, `passes_accuracy_pct`, `corners`, `offsides`.
* **Phòng ngự & Kỷ luật:** `fouls`, `yellow_cards`, `red_cards`, `tackles_total`, `interceptions`, `clearances`, `saves`.
* **Chỉ số nâng cao:** `xg`, `npxg`, `xga`, `psxg`, `xg_diff` ($xG - Goals$), `ppda` (cường độ pressing), `field_tilt` (áp đảo 1/3 sân cuối), `progressive_passes`, `progressive_carries`, `sca`, `gca`.

#### Ứng viên API & Giới hạn:
* **Football-Data.co.uk:** Cung cấp toàn bộ tỷ số, sút, sút trúng đích, phạt góc, thẻ phạt, phạm lỗi cho $1.900$ trận chỉ bằng 5 lượt tải file CSV (nhanh < 5 giây).
* **Understat:** Cung cấp $xG$, $xGA$, $npxG$, $xGChain$, $xGBuildup$. Hạn chế: Thiếu số liệu phạt góc, thẻ phạt và tắc bóng.
* **FBref:** Nguồn duy nhất có PPDA, SCA, GCA, Progressive Passes. **Hạn chế nghiêm trọng:** Giới hạn 20 req/phút. Cào $1.900$ trận mất tối thiểu ~2 giờ và dễ bị khóa IP nếu script gặp lỗi lặp.

---

### NHÓM 5: THỐNG KÊ CÁ NHÂN CẦU THỦ TRONG TRẬN (PLAYER MATCH-LEVEL STATS)
*Thư mục lưu trữ: `data/organized/<season>/05_player_match_stats/`*

#### Danh mục đặc trưng:
* `minutes_played`: Số phút thi đấu thực tế.
* `player_rating`: Điểm đánh giá thuật toán ($1.0 - 10.0$).
* `player_xg`, `player_xa`: xG và xA cá nhân tạo ra trong trận đấu.
* `shots_total`, `shots_on_target`: Số pha dứt điểm của cầu thủ.
* `key_passes`: Đường chuyền tạo cơ hội dứt điểm.
* `touches_total`, `touches_in_box`: Số lần chạm bóng và chạm bóng trong vòng cấm đối phương.
* `ground_duels_won_pct`, `aerial_duels_won_pct`: Tỷ lệ thắng tranh chấp tay đôi và không chiến.
* `dispossessed`, `turnovers_bad_control`: Mất bóng và khống chế bóng lỗi.
* `shot_map_events`: Danh sách tọa độ cú sút $(x, y)$, cự ly, góc sút, bộ phận chân/đầu.

#### Ứng viên API & Giới hạn:
* **Understat (`shotsData` payload):** Tọa độ cú sút $(x, y)$, loại tình huống và xG của từng cầu thủ. Rất nhẹ và không bị chặn.
* **Sofascore API (`/lineups` & `/shotmap`):** Cung cấp ratings, tranh chấp duels, touches. Hạn chế: Khối lượng dữ liệu bùng nổ ($1.900 \text{ trận} \times 28 \text{ cầu thủ} \approx 53.000$ dòng bản ghi).

---

### NHÓM 6: MÙA GIẢI & BẢNG XẾP HẠNG (LEAGUE & SEASON DATA)
*Thư mục lưu trữ: `data/organized/<season>/06_league_standings/`*

#### Danh mục đặc trưng:
* `table_rank_home`, `table_rank_away`: Thứ hạng trên BXH trước giờ bóng lăn.
* `points_home`, `points_away`: Tổng điểm số tích lũy.
* `season_ppg_home`, `season_ppg_away`: Điểm trung bình mỗi trận từ đầu mùa.
* `season_gd_per_game`: Hiệu số bàn thắng trung bình mỗi trận.
* `form_points_last5`: Điểm giành được trong 5 trận gần nhất.
* `form_xg_avg_last5`, `form_xga_avg_last5`: xG và xGA trung bình 5 trận gần nhất.
* `home_venue_ppg`, `away_venue_ppg`: Điểm trung bình tại sân nhà/sân khách tương ứng.
* `h2h_matches_played`, `h2h_home_wins`, `h2h_home_ppg`: Thống kê đối đầu trực tiếp 5 trận gần nhất.
* `home_elo`, `away_elo`, `elo_diff`: Điểm số Elo sức mạnh động trước trận.

#### Ứng viên API & Giới hạn:
* **Tính toán nội bộ (Local Feature Engineering Engine):**
  * Không gọi bất kỳ API bên ngoài nào!
  * Toàn bộ nhóm này được tính bằng logic Pandas / NumPy (`.shift(1).rolling()`) từ dữ liệu lịch sử của Nhóm 4.
  * Tốc độ: Chạy mất dưới 10 giây cho toàn bộ 5 mùa giải, đảm bảo **100% không bị rò rỉ dữ liệu (No Data Leakage)**.

---

### NHÓM 7: THỊ TRƯỜNG CÁ CƯỢC & TỶ LỆ KÈO (BETTING & MARKET ODDS)
*Thư mục lưu trữ: `data/organized/<season>/07_betting_odds/`*

#### Danh mục đặc trưng:
* **Kèo 1X2 Mở phiên (Opening):** `b365_h`, `b365_d`, `b365_a`, `pinnacle_h`, `pinnacle_d`, `pinnacle_a`, `avg_h`, `avg_d`, `avg_a`, `max_h`, `max_d`, `max_a`.
* **Kèo 1X2 Chốt phiên (Closing):** `b365c_h`, `b365c_d`, `b365c_a`, `pinnaclec_h`, `pinnaclec_d`, `pinnaclec_a`, `avgc_h`, `avgc_d`, `avgc_a`.
* **Kèo Tài/Xỉu 2.5 bàn:** `b365_over25`, `b365_under25`, `pinnacle_over25`, `pinnacle_under25`, `avg_over25`, `avg_under25` (cả Opening và Closing).
* **Kèo Chấp Châu Á (Asian Handicap):** `ah_line` (mức chấp), `b365_ah_home`, `b365_ah_away`, `pinnacle_ah_home`, `pinnacle_ah_away` (cả Opening và Closing).
* **Chỉ số thị trường:** `implied_prob_home`, `implied_prob_draw`, `implied_prob_away` (xác suất ngầm định sau khi loại trừ biên phế `overround`).

#### Ứng viên API & Giới hạn:
* **Football-Data.co.uk:** Có sẵn toàn bộ các cột trên cho cả 5 mùa giải trong các file `E0.csv`. Không có giới hạn, tải về ngay lập tức.

