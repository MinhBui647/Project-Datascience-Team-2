# Ma Trận Đặc Trưng EPL & Đánh Giá Giới Hạn API Thu Thập Toàn Diện

Tài liệu này chuẩn hóa và mở rộng toàn bộ **7 nhóm thực thể** từ tài liệu phân tích khả thi (`Phân tích khả thi crawler bóng đá.pdf`), tập trung **chuyên biệt cho Giải Ngoại Hạng Anh (English Premier League - EPL)**. Tài liệu liệt kê danh mục đặc trưng (Features) cực đại có thể trích xuất, gán các ứng viên API/Crawler khả thi nhất (Public & Internal), và phân tích chi tiết các giới hạn kỹ thuật (Rate limits, Anti-bot, Volume bottleneck) khi tiến hành thu thập quy mô lớn.

---

## 1. Bảng Tổng Hợp 7 Nhóm Thực Thể & Nguồn Dữ Liệu Tối Ưu Cho EPL

| Nhóm Thực Thể (Theo PDF) | Số Lượng Đặc Trưng Ước Tính | Ứng Viên API / Nguồn Chính | Độ Phức Tạp Thu Thập | Điểm Nghẽn Lớn Nhất |
|---|:---:|---|:---:|---|
| **1. Đội bóng (Club / Team)** | 25+ | FPL API, TheSportsDB, Transfermarkt | Thấp | Dữ liệu tài chính/quỹ lương bị bảo vệ bởi Cloudflare |
| **2. Cầu thủ (Player Profile)** | 35+ | FPL Official API, Transfermarkt | Trung bình | Lịch sử chấn thương nhiều năm không có API mở |
| **3. Bối cảnh Trận đấu (Match Context)** | 30+ | Sofascore API, Open-Meteo, PL PulseLive | Trung bình | Đội hình chính thức chỉ xuất hiện ở mốc T-60 phút |
| **4. Diễn biến & Trận đấu (Match Performance)** | 50+ | Understat, FBref, Football-Data.co.uk | Cao | FBref khóa IP (20 req/min), Understat thiếu stats phòng ngự |
| **5. Cá nhân trong Trận (Player Match Stats)** | 40+ | Sofascore v1, Understat, FotMob | Rất cao | Bùng nổ số lượng request ($380 \text{ trận} \times 22 \text{ cầu thủ}$) |
| **6. Mùa giải & BXH (League & Season)** | 20+ | Football-Data.co.uk, FPL API | Rất thấp | Cực kỳ dễ lấy, không có rào cản kỹ thuật |
| **7. Tỷ lệ Kèo (Betting Odds)** | 25+ | Football-Data.co.uk (Lịch sử), API-Football (Live) | Thấp - Cao | Odds lịch sử miễn phí; Odds sát giờ thi đấu bị giới hạn gói cước |

---

## 2. Chi Tiết Từng Nhóm Đặc Trưng, Ứng Viên API & Giới Hạn Kỹ Thuật

---

### NHÓM 1: THỰC THỂ ĐỘI BÓNG (CLUB / TEAM ATTRIBUTES)

Nhóm này cung cấp thông tin nền tảng, cơ sở vật chất và tiềm lực tài chính của 20 CLB Ngoại Hạng Anh.

#### A. Danh mục đặc trưng có thể trích rút

| Tên Đặc Trưng (Feature) | Kiểu Dữ Liệu | Ý Nghĩa / Đơn Vị | Ví Dụ Giá Trị |
|---|:---:|---|---|
| `team_id` | `VARCHAR(20)` | Mã định danh chuẩn hóa | `"MCI"`, `"ARS"`, `"LIV"` |
| `team_name` | `VARCHAR(50)` | Tên thông dụng | `"Manchester City"`, `"Arsenal"` |
| `official_name` | `VARCHAR(100)` | Tên đăng ký đầy đủ | `"Manchester City Football Club"` |
| `founded_year` | `INT` | Năm thành lập CLB | `1880`, `1886` |
| `city` | `VARCHAR(50)` | Thành phố đặt trụ sở | `"Manchester"`, `"London"` |
| `stadium_name` | `VARCHAR(100)` | Tên sân vận động nhà | `"Etihad Stadium"`, `"Emirates Stadium"` |
| `stadium_capacity` | `INT` | Sức chứa tối đa của sân | `53400`, `60704` |
| `pitch_surface` | `VARCHAR(30)` | Mặt cỏ SVĐ | `"Desso GrassMaster"`, `"Hybrid Turf"` |
| `pitch_length_m` | `FLOAT` | Chiều dài sân (mét) | `105.0` |
| `pitch_width_m` | `FLOAT` | Chiều rộng sân (mét) | `68.0` |
| `stadium_lat` | `FLOAT` | Vĩ độ địa lý sân | `53.4831` |
| `stadium_lon` | `FLOAT` | Kinh độ địa lý sân | `-2.2004` |
| `owner_name` | `VARCHAR(100)` | Chủ sở hữu / Tập đoàn | `"City Football Group"` |
| `manager_name` | `VARCHAR(50)` | HLV trưởng đương nhiệm | `"Pep Guardiola"`, `"Mikel Arteta"` |
| `manager_appointed_date`| `DATE` | Ngày bổ nhiệm HLV | `2016-07-01` |
| `market_value_total_eur`| `BIGINT` | Tổng giá trị đội hình (€) | `1260000000` |
| `wage_bill_annual_gbp` | `BIGINT` | Tổng quỹ lương năm (£) | `200000000` |
| `avg_squad_age` | `FLOAT` | Tuổi trung bình đội hình | `26.4` |
| `foreign_players_count` | `INT` | Số lượng cầu thủ ngoại | `18` |
| `foreign_players_ratio` | `FLOAT` | Tỷ lệ cầu thủ ngoại binh | `0.72` |
| `preferred_formation` | `VARCHAR(20)` | Sơ đồ chiến thuật sở trường | `"4-3-3"`, `"4-2-3-1"` |

#### B. Ứng viên API phù hợp nhất
1. **FPL API (`GET https://fantasy.premierleague.com/api/bootstrap-static/`):**
   - Lấy toàn bộ 20 CLB: ID, tên viết tắt (TLA), sức mạnh tổng quan (`strength_overall_home`, `strength_attack_home`, `strength_defence_away`).
2. **TheSportsDB (`GET https://www.thesportsdb.com/api/v1/json/3/search_all_teams.php?l=English%20Premier%20League`):**
   - Trả về sức chứa SVĐ, tọa độ địa lý, năm thành lập, kích thước mặt sân.
3. **Bộ dữ liệu mở Transfermarkt (`dcaribou/transfermarkt-datasets` trên GitHub):**
   - Cung cấp toàn bộ giá trị đội hình và quỹ lương theo từng mùa.

#### C. Phân tích giới hạn kỹ thuật
* **TheSportsDB:** Miễn phí cho giáo dục nhưng không cập nhật real-time khi CLB đổi chủ/HLV.
* **Transfermarkt:** Có **Cloudflare Turnstile/CAPTCHA**. Không thể crawl trực tiếp hằng ngày bằng thư viện `requests` thông thường; bắt buộc phải lấy qua bộ dữ liệu trung gian (mirror dataset) hoặc chạy scraper định kỳ theo kỳ chuyển nhượng hè/đông.

---

### NHÓM 2: THỰC THỂ CẦU THỦ (PLAYER ATTRIBUTES)

Thông tin về từng cá nhân cầu thủ trong biên chế 20 CLB Ngoại Hạng Anh (~600 cầu thủ mỗi mùa).

#### A. Danh mục đặc trưng có thể trích rút

| Tên Đặc Trưng | Kiểu Dữ Liệu | Ý Nghĩa / Đơn Vị | Nguồn Tối Ưu |
|---|:---:|---|---|
| `player_id` | `VARCHAR(50)` | Mã ID cầu thủ | FPL / Transfermarkt |
| `full_name` | `VARCHAR(100)` | Tên đầy đủ | FPL / Transfermarkt |
| `date_of_birth` | `DATE` | Ngày tháng năm sinh | FPL / Transfermarkt |
| `age` | `INT` | Tuổi tại thời điểm xét | Tính toán nội bộ |
| `nationality_primary` | `VARCHAR(50)` | Quốc tịch chính | Transfermarkt |
| `height_cm` | `INT` | Chiều cao (cm) | Transfermarkt / FPL |
| `weight_kg` | `INT` | Cân nặng (kg) | Transfermarkt |
| `preferred_foot` | `VARCHAR(10)` | Chân thuận (`Left`, `Right`, `Both`) | Transfermarkt / Sofascore |
| `primary_position` | `VARCHAR(10)` | Vị trí chính (`GK`, `CB`, `LB`, `RB`, `DM`, `CM`, `AM`, `LW`, `RW`, `ST`) | FPL (`element_type`) |
| `market_value_eur` | `BIGINT` | Giá trị chuyển nhượng hiện tại (€) | Transfermarkt |
| `weekly_wage_gbp` | `INT` | Lương tuần (£) | Capology / FBref |
| `contract_expires` | `DATE` | Ngày hết hạn hợp đồng | Transfermarkt |
| `injury_status` | `VARCHAR(50)` | Tình trạng chấn thương | FPL (`news`) |
| `chance_of_playing` | `FLOAT` | Khả năng ra sân vòng tới ($0 - 1.0$) | FPL (`chance_of_playing_next_round`) |
| `suspension_status` | `BOOLEAN` | Đang bị treo giò hay không | FPL / Transfermarkt |
| `fpl_cost` | `FLOAT` | Giá tiền trong game FPL (£m) | FPL (`now_cost / 10`) |
| `fpl_selected_by_pct` | `FLOAT` | Tỷ lệ người chơi sở hữu (%) | FPL (`selected_by_percent`) |
| `fpl_form` | `FLOAT` | Điểm phong độ FPL 30 ngày qua | FPL (`form`) |
| `season_minutes_played`| `INT` | Tổng số phút đã đá mùa này | FPL (`minutes`) |
| `season_goals` | `INT` | Tổng bàn thắng từ đầu mùa | FPL (`goals_scored`) |
| `season_assists` | `INT` | Tổng kiến tạo từ đầu mùa | FPL (`assists`) |
| `season_clean_sheets` | `INT` | Tổng trận giữ sạch lưới | FPL (`clean_sheets`) |
| `season_xg` | `FLOAT` | Tổng xG cá nhân từ đầu mùa | FPL / Understat |
| `season_xa` | `FLOAT` | Tổng xA cá nhân từ đầu mùa | FPL / Understat |
| `fpl_ict_index` | `FLOAT` | Chỉ số ảnh hưởng toàn diện ICT | FPL (`ict_index`) |

#### B. Ứng viên API phù hợp nhất
1. **FPL API (`GET https://fantasy.premierleague.com/api/bootstrap-static/`):**
   - Trả về danh sách toàn bộ ~600 cầu thủ EPL chỉ trong **1 request duy nhất**.
   - Cung cấp sẵn: vị trí, số áo, tỷ lệ chấn thương, số bàn, số kiến tạo, xG, xA, chỉ số ICT Index.
2. **Transfermarkt Scraper / Open Mirror:**
   - Bổ sung: chiều cao, cân nặng, chân thuận, giá trị thị trường, ngày hết hạn hợp đồng.

#### C. Phân tích giới hạn kỹ thuật
* **FPL API:** Hoàn toàn **không giới hạn**, tốc độ trả lời JSON cực nhanh (< 500ms). Nhược điểm: Chỉ lưu mùa giải hiện tại; khi mùa giải mới bắt đầu, dữ liệu mùa cũ sẽ bị lưu trữ riêng.
* **Lịch sử chấn thương sâu (Medical History):** Cực kỳ khó cào tự động vì không có API mở nào cung cấp timeline chấn thương 5 mùa giải trước miễn phí. Giải pháp là chỉ dùng thuộc tính trạng thái trước trận (`is_injured`, `chance_of_playing`) của FPL.

---

### NHÓM 3: BỐI CẢNH & ĐIỀU KIỆN TRẬN ĐẤU (MATCH METADATA & ENVIRONMENT)

Bao gồm các yếu tố môi trường, lịch thi đấu, tổ trọng tài và đặc biệt là **Đội hình xuất phát ở mốc T-60 phút**.

#### A. Danh mục đặc trưng có thể trích rút

| Tên Đặc Trưng | Kiểu Dữ Liệu | Ý Nghĩa / Đơn Vị | Thời Điểm Thu Thập Khả Thi |
|---|:---:|---|---|
| `match_id` | `VARCHAR(64)` | Hash định danh duy nhất trận đấu | Trước mùa / Lịch đấu |
| `gameweek` | `INT` | Vòng đấu ($1 \rightarrow 38$) | Trước mùa |
| `kickoff_datetime_utc`| `DATETIME` | Thời gian bóng lăn (UTC) | Trước mùa |
| `is_weekend` | `BOOLEAN` | Trận đá cuối tuần hay giữa tuần | Trước mùa |
| `rest_days_home` | `INT` | Số ngày nghỉ của đội nhà | Trước trận |
| `rest_days_away` | `INT` | Số ngày nghỉ của đội khách | Trước trận |
| `stadium_attendance` | `INT` | Lượng khán giả thực tế trên sân | Sau trận |
| `stadium_fill_rate` | `FLOAT` | Tỷ lệ lấp đầy SVĐ (%) | Sau trận |
| `weather_temperature_c`| `FLOAT` | Nhiệt độ tại SVĐ (°C) | Sát giờ trận đấu |
| `weather_condition` | `VARCHAR(30)` | Tình trạng (`Clear`, `Rain`, `Snow`, `Fog`)| Sát giờ trận đấu |
| `weather_wind_speed_kmh`| `FLOAT` | Tốc độ gió (km/h) | Sát giờ trận đấu |
| `referee_name` | `VARCHAR(50)` | Trọng tài chính điều khiển | T-24 giờ |
| `referee_yellow_card_avg`| `FLOAT`| Trung bình số thẻ vàng/trận của trọng tài | T-24 giờ (Tính lịch sử) |
| `referee_penalty_avg`| `FLOAT` | Trung bình số penalty/trận của trọng tài | T-24 giờ (Tính lịch sử) |
| `var_referee_name` | `VARCHAR(50)` | Trọng tài phòng VAR | T-24 giờ |
| `home_formation` | `VARCHAR(20)` | Sơ đồ xuất phát đội nhà (`4-3-3`, `3-5-2`) | **T-60 phút** |
| `away_formation` | `VARCHAR(20)` | Sơ đồ xuất phát đội khách | **T-60 phút** |
| `home_starting_xi_ids`| `LIST[INT]` | Danh sách ID 11 cầu thủ đá chính nhà | **T-60 phút** |
| `away_starting_xi_ids`| `LIST[INT]` | Danh sách ID 11 cầu thủ đá chính khách | **T-60 phút** |
| `home_bench_ids` | `LIST[INT]` | Danh sách cầu thủ ngồi dự bị đội nhà | **T-60 phút** |
| `away_bench_ids` | `LIST[INT]` | Danh sách cầu thủ ngồi dự bị đội khách | **T-60 phút** |
| `home_squad_rotation_count`| `INT` | Số vị trí thay đổi so với trận trước (nhà) | **T-60 phút** |
| `away_squad_rotation_count`| `INT` | Số vị trí thay đổi so với trận trước (khách) | **T-60 phút** |
| `home_lineup_avg_xg90`| `FLOAT` | Tổng/TB xG90 của 11 cầu thủ đá chính nhà | **T-60 phút** |
| `away_lineup_avg_xg90`| `FLOAT` | Tổng/TB xG90 của 11 cầu thủ đá chính khách| **T-60 phút** |

#### B. Ứng viên API phù hợp nhất
1. **Sofascore API (`GET https://api.sofascore.com/api/v1/event/{event_id}/lineups`):**
   - Nguồn số 1 để lấy danh sách 11 cầu thủ đá chính kèm vị trí tọa độ $(x, y)$ trên sơ đồ chiến thuật trước giờ bóng lăn 60 phút.
2. **Open-Meteo Historical Weather API (`https://archive-api.open-meteo.com/v1/archive`):**
   - **Hoàn toàn miễn phí, không cần API Key, không giới hạn**: Cung cấp lịch sử thời tiết chính xác theo giờ dựa trên tọa độ `(lat, lon)` của sân vận động (nhiệt độ, lượng mưa, độ ẩm, tốc độ gió).
3. **Premier League PulseLive API (`GET https://footballapi.pulselive.com/football/fixtures`):**
   - Cung cấp phân công tổ trọng tài (Trọng tài chính, 2 trợ lý biên, trọng tài bàn, tổ VAR).

#### C. Phân tích giới hạn kỹ thuật
* **Rào cản thời gian (Time Constraint):** Đội hình ra sân chính thức chỉ được nộp lên ban tổ chức trước giờ bóng lăn **60 - 75 phút**. Do đó, module cào đội hình phải được lập lịch tự động kích hoạt vào khung giờ $T-60$ phút cho mỗi trận.
* **Sofascore WAF (Cloudflare):** Nếu một ngày cuối tuần có 6 trận đá cùng lúc 15:00 (giờ Anh), gửi 6 request liên tục trong 1 giây có thể bị Cloudflare chặn. Phải dùng hàng đợi giãn cách $3 - 5$ giây giữa các trận.

---

### NHÓM 4: THỐNG KÊ DIỄN BIẾN & TRẬN ĐẤU (MATCH PERFORMANCE & EVENT DATA)

Nhóm dữ liệu cốt lõi phản ánh toàn bộ diễn biến chuyên môn của 90 phút thi đấu.

#### A. Danh mục đặc trưng có thể trích rút

##### 4.1. Kết Quả & Timeline
* `ht_home_goals`, `ht_away_goals`: Tỷ số hiệp 1.
* `ft_home_goals`, `ft_away_goals`: Tỷ số chung cuộc (Hiệp 2).
* `first_goal_minute`: Phút ghi bàn mở tỷ số.
* `first_goal_team`: Đội ghi bàn trước (`Home` / `Away` / `None`).
* `events_timeline`: Mảng JSON các sự kiện (bàn thắng, phản lưới, penalty, thẻ đỏ, thay người).

##### 4.2. Thống Kê Kỹ Thuật Cơ Bản (Cho cả Home & Away)
* `shots_total`: Tổng số cú sút.
* `shots_on_target`: Sút trúng đích.
* `shots_off_target`: Sút chệch khung thành.
* `shots_blocked`: Cú sút bị hậu vệ chặn lại.
* `hit_woodwork`: Số lần sút trúng xà ngang / cột dọc.
* `possession_pct`: Tỷ lệ kiểm soát bóng ($0.0 - 100.0\%$).
* `passes_total`, `passes_completed`, `passes_accuracy_pct`: Tổng số đường chuyền và độ chính xác.
* `passes_final_third`: Số đường chuyền ở 1/3 sân đối phương.
* `crosses_total`, `crosses_accuracy_pct`: Số pha tạt bóng và độ chuẩn xác.
* `corners`: Số quả phạt góc được hưởng.
* `offsides`: Số lần rơi vào thế việt vị.
* `fouls`: Số lỗi vi phạm bị thổi phạt.
* `yellow_cards`, `red_cards`: Số thẻ vàng, thẻ đỏ.
* `tackles_total`, `tackles_won`: Số pha tắc bóng và tắc bóng thành công.
* `interceptions`: Số pha đánh chặn cắt đường chuyền.
* `clearances`: Số pha phá bóng giải nguy.
* `goalkeeper_saves`: Số lần cứu thua của thủ môn.

##### 4.3. Chỉ Số Phân Tích Nâng Cao (Advanced Metrics)
* `xg`: Tổng bàn thắng kỳ vọng (Expected Goals).
* `npxg`: Bàn thắng kỳ vọng không tính phạt đền (Non-penalty xG).
* `xga`: Bàn thua kỳ vọng (Expected Goals Against).
* `psxg`: xG tính sau cú sút (Post-shot xG - phản ánh độ hiểm hóc của cú sút).
* `xg_diff`: Hiệu số chênh lệch ($xG - Goals$).
* `ppda`: Số đường chuyền cho phép đối phương thực hiện trước mỗi hành động phòng ngự (đo cường độ pressing).
* `field_tilt`: Tỷ lệ kiểm soát bóng riêng ở 1/3 sân cuối cùng (đo mức độ dồn ép thế trận).
* `progressive_passes`: Số đường chuyền tịnh tiến ($\ge 10$ yards về phía khung thành đối phương).
* `progressive_carries`: Số lần dẫn bóng tịnh tiến.
* `sca`: Số hành động tạo cơ hội dứt điểm (Shot-Creating Actions).
* `gca`: Số hành động dẫn trực tiếp tới bàn thắng (Goal-Creating Actions).

#### B. Ứng viên API phù hợp nhất
1. **Football-Data.co.uk (CSV tĩnh):**
   - Nguồn tải nhanh nhất cho toàn bộ thống kê cơ bản: sút, sút trúng đích, phạt góc, thẻ phạt, phạm lỗi từ mùa 1993/94 đến nay.
2. **Understat (`https://understat.com/league/EPL`):**
   - Bóc tách xG, xGA, npxG, xG theo từng tình huống bóng (Open Play, Corner, Set Piece, Penalty).
3. **FBref (Crawler qua BeautifulSoup / `worldfootballR`):**
   - Nguồn duy nhất có đầy đủ các chỉ số PPDA, Field Tilt, SCA, GCA, Progressive Passes.

#### C. Phân tích giới hạn kỹ thuật
* **FBref Rate Limit cực gắt:**
  - Quy định: **Không quá 20 requests / phút**.
  - Một mùa EPL có 380 trận. Nếu cào 380 trang chi tiết trận đấu, tối thiểu phải mất **$380 / 20 = 19$ phút** cho 1 mùa giải.
  - Nếu vi phạm: Server trả mã lỗi `429 Too Many Requests` và **khóa IP trong 1 giờ**.
  - **Khuyến nghị:** Phải cài đặt `time.sleep(3.5)` giữa các request và lưu file HTML cache cục bộ để không bao giờ tải lại lần thứ hai.
* **Understat:** Chỉ có dữ liệu từ mùa **2014/15**, thiếu toàn bộ các chỉ số tranh chấp, chuyền bóng và thẻ phạt.

---

### NHÓM 5: THỐNG KÊ CÁ NHÂN CẦU THỦ TRONG TRẬN (PLAYER MATCH-LEVEL STATS)

Bóc tách chi tiết từng hành vi của 22 - 28 cầu thủ xuất hiện trên sân trong một trận đấu.

#### A. Danh mục đặc trưng có thể trích rút

| Tên Đặc Trưng | Kiểu Dữ Liệu | Ý Nghĩa / Đơn Vị | Nguồn Tối Ưu |
|---|:---:|---|---|
| `minutes_played` | `INT` | Số phút thi đấu thực tế trên sân | Sofascore / FPL |
| `player_rating` | `FLOAT` | Điểm số đánh giá thuật toán ($1.0 - 10.0$) | Sofascore |
| `player_xg` | `FLOAT` | xG cá nhân tạo ra trong trận | Understat / Sofascore |
| `player_xa` | `FLOAT` | xA (kiến tạo kỳ vọng) trong trận | Understat / Sofascore |
| `shots_total`, `shots_on_target` | `INT` | Số cú sút và sút trúng đích của cầu thủ | Sofascore / Understat |
| `key_passes` | `INT` | Đường chuyền mở ra cú sút cho đồng đội | Understat / Sofascore |
| `touches_total` | `INT` | Tổng số lần chạm bóng | Sofascore |
| `touches_in_box` | `INT` | Chạm bóng trong vòng cấm đối phương | Sofascore / FBref |
| `dribbles_attempted`, `succeeded`| `INT` | Rê bóng qua người thành công/thất bại | Sofascore |
| `ground_duels_won_pct` | `FLOAT` | Tỷ lệ thắng tranh chấp tay đôi dưới đất | Sofascore |
| `aerial_duels_won_pct` | `FLOAT` | Tỷ lệ thắng không chiến trên không | Sofascore |
| `dispossessed` | `INT` | Số lần bị đối phương cướp bóng | Sofascore / FBref |
| `turnovers_bad_control` | `INT` | Số lần khống chế bóng hỏng | Sofascore / FBref |
| `big_chances_created` | `INT` | Số cơ hội mười mươi tạo ra | Sofascore |
| `big_chances_missed` | `INT` | Số cơ hội mười mươi bỏ lỡ | Sofascore |
| `shot_map_events` | `JSON` | Danh sách cú sút: tọa độ $(x, y)$, cự ly, góc sút, bộ phận chân/đầu | Sofascore (`/shotmap`) hoặc Understat |

#### B. Ứng viên API phù hợp nhất
1. **Sofascore API (`GET https://api.sofascore.com/api/v1/event/{event_id}/lineups` & `/shotmap`):**
   - Trả về toàn bộ chỉ số rating, tranh chấp duels, touches và tọa độ dứt điểm của từng cầu thủ trong 1 trận.
2. **Understat (`shotsData` payload):**
   - Trả về tọa độ chính xác từng cú sút của từng cầu thủ, loại đường chuyền dẫn tới cú sút, và xG tương ứng.

#### C. Phân tích giới hạn kỹ thuật (Điểm nghẽn bùng nổ dữ liệu)
* **Số lượng bản ghi khổng lồ:**
  - 1 mùa EPL: $380 \text{ trận} \times \approx 28 \text{ cầu thủ (đá chính + thay người)} = \mathbf{10.640 \text{ dòng dữ liệu}}$.
  - 10 mùa giải: hơn **$100.000$ dòng dữ liệu cá nhân**.
* **Nguy cơ nghẽn API:**
  - Nếu dùng gói API-Football Free (100 calls/ngày): Cần hơn **100 ngày** chỉ để cào xong 1 mùa giải cấp độ cầu thủ!
  - **Giải pháp tối ưu:** Chỉ trích xuất bảng này qua **Understat** (cào theo mùa trong 10 phút) hoặc **Sofascore** thông qua bộ điều phối có delay $3$ giây và lưu cache JSON cục bộ.

---

### NHÓM 6: MÙA GIẢI & BẢNG XẾP HẠNG (LEAGUE & SEASON DATA)

Nhóm dữ liệu phản ánh cấu trúc giải đấu, phong độ tích lũy và tương quan đối đầu lịch sử.

#### A. Danh mục đặc trưng có thể trích rút

| Tên Đặc Trưng | Kiểu Dữ Liệu | Ý Nghĩa / Đơn Vị |
|---|:---:|---|
| `season` | `VARCHAR(10)` | Mùa giải (ví dụ: `"2024/25"`) |
| `league_tier` | `INT` | Hạng đấu (EPL = `1`) |
| `table_rank_home`, `away` | `INT` | Thứ hạng hiện tại trên BXH trước trận |
| `points_home`, `away` | `INT` | Tổng điểm hiện tại của 2 đội |
| `matches_played_home`, `away` | `INT` | Số trận đã đá từ đầu mùa |
| `ppg_home`, `away` | `FLOAT` | Điểm trung bình mỗi trận (`season_ppg`) |
| `goal_difference_per_game` | `FLOAT` | Hiệu số bàn thắng trung bình mỗi trận |
| `home_venue_ppg` | `FLOAT` | Điểm trung bình trên sân nhà của Đội nhà |
| `away_venue_ppg` | `FLOAT` | Điểm trung bình trên sân khách của Đội khách |
| `form_points_last5_home`, `away` | `INT` | Tổng điểm giành được trong 5 trận gần nhất ($0 - 15$) |
| `form_gd_last5_home`, `away` | `INT` | Hiệu số bàn thắng trong 5 trận gần nhất |
| `form_xg_avg_last5_home`, `away` | `FLOAT` | xG trung bình 5 trận gần nhất |
| `form_xga_avg_last5_home`, `away` | `FLOAT` | xGA (bàn thua kỳ vọng) TB 5 trận gần nhất |
| `h2h_matches_played` | `INT` | Số lần gặp nhau trong 5 trận gần nhất |
| `h2h_home_wins` | `INT` | Số trận thắng của Đội nhà trong các lần đối đầu |
| `h2h_draws` | `INT` | Số trận hòa giữa 2 đội |
| `h2h_home_ppg` | `FLOAT` | Điểm TB Đội nhà kiếm được khi gặp đối thủ này |
| `home_elo_rating`, `away_elo_rating`| `FLOAT` | Điểm số sức mạnh Elo trước giờ bóng lăn |
| `elo_difference` | `FLOAT` | Chênh lệch điểm Elo (`home_elo - away_elo`) |

#### B. Ứng viên API phù hợp nhất
* **Không cần gọi API bên ngoài!**
* Toàn bộ nhóm đặc trưng này nên được **tính toán nội bộ (Derived Features)** từ file kết quả lịch sử `epl_final.csv` (như file [build_features.py](file:///z:/Desktop/Bet/build_features.py) của nhóm đang làm).
* Việc tự tính toán đảm bảo $100\%$ không bị rò rỉ dữ liệu (*no data leakage*) vì chỉ sử dụng hàm trượt `.shift(1).rolling()`.

#### C. Phân tích giới hạn kỹ thuật
* Không tốn quota API, tốc độ tính toán bằng `pandas` và `numpy` chỉ mất vài giây cho toàn bộ 25 mùa giải EPL.

---

### NHÓM 7: THỊ TRƯỜNG CÁ CƯỢC & TỶ LỆ KÈO (BETTING & MARKET ODDS)

Tỷ lệ cược phản ánh trí tuệ đám đông (*wisdom of the crowd*) và xác suất ngầm định (*implied probabilities*) của các nhà cái hàng đầu thế giới.

#### A. Danh mục đặc trưng có thể trích rút

| Tên Đặc Trưng | Kiểu Dữ Liệu | Ý Nghĩa / Nhà Cái Tiêu Biểu |
|---|:---:|---|
| `b365_home_win_odds` | `FLOAT` | Kèo Chủ nhà thắng của Bet365 |
| `b365_draw_odds` | `FLOAT` | Kèo Hòa của Bet365 |
| `b365_away_win_odds` | `FLOAT` | Kèo Khách thắng của Bet365 |
| `pinnacle_home_win_closing`| `FLOAT` | Kèo Chủ của Pinnacle sát giờ bóng lăn (Chuẩn sắc nhất thị trường) |
| `pinnacle_draw_closing` | `FLOAT` | Kèo Hòa của Pinnacle sát giờ bóng lăn |
| `pinnacle_away_closing` | `FLOAT` | Kèo Khách của Pinnacle sát giờ bóng lăn |
| `asian_handicap_line` | `FLOAT` | Mức chấp kèo châu Á (ví dụ: `-0.5`, `-1.25`, `+0.75`) |
| `asian_handicap_home_odds` | `FLOAT` | Tỷ lệ tiền cho đội chủ nhà theo kèo chấp |
| `asian_handicap_away_odds` | `FLOAT` | Tỷ lệ tiền cho đội khách theo kèo chấp |
| `over_2_5_goals_odds` | `FLOAT` | Tỷ lệ cược cửa Tài 2.5 bàn |
| `under_2_5_goals_odds` | `FLOAT` | Tỷ lệ cược cửa Xỉu 2.5 bàn |
| `btts_yes_odds` | `FLOAT` | Kèo hai đội cùng ghi bàn (Both Teams To Score: Có) |
| `btts_no_odds` | `FLOAT` | Kèo hai đội cùng ghi bàn: Không |
| `market_implied_prob_home`| `FLOAT` | Xác suất ngầm định thắng nhà sau khi trừ biên phế (*overround*) |
| `market_implied_prob_draw`| `FLOAT` | Xác suất ngầm định hòa sau khi trừ biên phế |
| `market_implied_prob_away`| `FLOAT` | Xác suất ngầm định khách sau khi trừ biên phế |
| `market_overround_pct` | `FLOAT` | Tỷ lệ "cắt phế" của nhà cái (thường $2.0\% - 5.5\%$) |

#### B. Ứng viên API phù hợp nhất
1. **Football-Data.co.uk:**
   - **Kho lưu trữ tỷ lệ cược lịch sử miễn phí số 1 thế giới**. Chứa sẵn tỷ lệ mở kèo và đóng kèo của Bet365, Pinnacle, William Hill, Ladbrokes từ năm 2000 đến nay.
2. **The Odds API (`https://the-odds-api.com/`):**
   - API chuyên biệt về tỷ lệ cược real-time. Gói miễn phí cấp **500 requests / tháng** (đủ để theo dõi các trận vòng đấu hiện tại).
3. **API-Football (`/odds` endpoint):**
   - Trả về tỷ lệ cược của nhiều nhà cái, nhưng tốn quota 1 request/trận.

#### C. Phân tích giới hạn kỹ thuật
* Dữ liệu lịch sử: Hoàn toàn miễn phí và có sẵn qua CSV.
* Dữ liệu trực tiếp (Live / Sát giờ bóng lăn): Các API cung cấp kèo real-time đều hạn chế số lượt gọi (The Odds API giới hạn 500 req/tháng, tức chỉ đủ quét ~12-15 trận/tháng nếu quét nhiều lần để cập nhật biến động kèo).

---

## 3. Đánh Giá Dung Lượng Request & Điểm Nghẽn Toàn Cục Cho 1 Mùa EPL (380 Trận)

Nếu nhóm muốn thu thập **toàn bộ 7 nhóm đặc trưng** cho 1 mùa giải EPL hoàn chỉnh (380 trận), bảng tính chi phí lượt gọi API và thời gian khả thi như sau:

| Nguồn Dữ Liệu | Số Request Cần Thiết Cho 1 Mùa | Giới Hạn Quota Áp Dụng | Thời Gian Hoàn Thành Khả Thi | Đánh Giá Rủi Ro |
|---|:---:|---|:---:|---|
| **Football-Data.co.uk** | 1 request (tải 1 file CSV) | Không giới hạn | **< 2 giây** | Rủi ro = 0% |
| **FPL API** | 1 - 2 requests (toàn bộ 600 cầu thủ) | Không giới hạn | **< 3 giây** | Rủi ro = 0% |
| **Understat** | 1 request mùa + 380 req trận | Giãn cách 1 - 2s | **~10 - 15 phút** | Rất thấp (hầu như không chặn) |
| **Open-Meteo (Thời tiết)**| 380 requests (tọa độ sân) | Miễn phí, 10.000 req/ngày | **~2 phút** | Rất thấp |
| **FBref (Chỉ số nâng cao)**| 380 requests | **Tối đa 20 req/phút** | **~25 - 30 phút** | **Cao (Dễ bị khóa IP 1 giờ nếu quá tốc độ)** |
| **Sofascore (Đội hình & Shotmap)**| 380 req lineups + 380 shotmap | Không công bố (Cloudflare WAF) | **~45 - 60 phút** (delay 4s) | **Trung bình - Cao (Cần dùng `curl_cffi` giả lập Chrome)** |
| **API-Football (Gói Free)** | $380 \text{ trận} \times 3 \text{ endpoints} = 1.140$ | **100 req / ngày** | **12 ngày** | **Không khả thi nếu dùng gói Free cho dữ liệu lịch sử** |

---

## 4. Kiến Trúc Thu Thập Đề Xuất Cho Nhóm Sinh Viên (Zero-Cost & Không Bị Chặn)

Để lấy được tập đặc trưng đồ sộ nhất phục vụ đồ án mà không tốn chi phí và không bị khóa IP, nhóm nên phân bổ nhiệm vụ theo mô hình **3 tầng phối hợp**:

```
                              ┌─────────────────────────────────────────────────────────┐
                              │            KIẾN TRÚC THU THẬP TỐI ƯU CHO EPL            │
                              └────────────────────────────┬────────────────────────────┘
                                                           │
          ┌────────────────────────────────────────────────┼────────────────────────────────────────────────┐
          ▼                                                ▼                                                ▼
┌─────────────────────────────────┐      ┌──────────────────────────────────┐     ┌─────────────────────────────────┐
│ TẦNG 1: TĨNH & LỊCH SỬ (Batch)  │      │ TẦNG 2: BỔ TRỢ CHUYÊN SÂU        │     │ TẦNG 3: TRƯỚC TRẬN T-60 PHÚT    │
├─────────────────────────────────┤      ├──────────────────────────────────┤     ├─────────────────────────────────┤
│ • Football-Data.co.uk: Tỷ số,   │      │ • Understat: Cào xG, xA, xGChain,│     │ • FPL API: Quét chấn thương &   │
│   sút, góc, thẻ, KÈO CƯỢC 25 năm│      │   tọa độ cú sút (10-15 phút/mùa).│     │   khả năng ra sân (1 request).  │
│ • Transfermarkt Mirror: Giá trị │      │ • Open-Meteo: Cào thời tiết theo │     │ • Sofascore API: Cào danh sách  │
│   đội hình, quỹ lương từng mùa. │      │   tọa độ SVĐ (2 phút/mùa).       │     │   11 cầu thủ đá chính & sơ đồ.  │
└─────────────────────────────────┘      └──────────────────────────────────┘     └─────────────────────────────────┘
```

### Quy tắc triển khai an toàn:
1. **Tuyệt đối không dùng API-Football Free để cào lịch sử** (vì mức 100 req/ngày sẽ làm tắc nghẽn tiến độ). Chỉ dùng API-Football làm kênh dự phòng lấy đội hình trận sắp đá.
2. **FBref chỉ dùng khi thực sự cần PPDA / Progressive Passes**: Nếu dùng FBref, bắt buộc bọc decorator retry với `time.sleep(random.uniform(3.5, 5.0))` và lưu cache HTML xuống ổ cứng.
3. **Ưu tiên bộ đôi Football-Data.co.uk + Understat + FPL API**: Bộ ba này cung cấp đến **85% tổng số đặc trưng** của cả 7 nhóm mà **hoàn toàn miễn phí, tốc độ thu thập chỉ mất dưới 15 phút cho toàn bộ mùa giải**.

