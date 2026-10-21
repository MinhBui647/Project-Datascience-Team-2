# ÁNH XẠ 1 - 1 CHI TIẾT TOÀN BỘ FILE THU THẬP DỮ LIỆU (CRAWL DATA)
## DỰ ÁN DỰ ĐOÁN KẾT QUẢ & TỈ SỐ NGOẠI HẠNG ANH (PREMIER LEAGUE)
**Phần phụ trách: Web Crawling & Data Ingestion (Lê Trường Giang - MSSV: 202416187)**

---

## 1. PHẠM VI DỮ LIỆU THU THẬP

Hệ thống cào và lưu trữ dữ liệu thô của dự án chỉ nằm trong đúng **2 thư mục**:
1. `raw_data/`: Chứa toàn bộ dữ liệu nền tảng lịch sử (2000/01 đến 2025/26) và các bản sao lưu.
2. `2026_2027_new/`: Chứa toàn bộ dữ liệu cào cập nhật hàng ngày cho mùa giải đang diễn ra (2026/27).

Mọi tệp dữ liệu trong 2 thư mục này được ánh xạ 1 - 1 về nguồn cung cấp, phương thức thu thập và nội dung thông tin chứa bên trong.

---

## 2. ÁNH XẠ CHI TIẾT THƯ MỤC `2026_2027_new/` (MÙA GIẢI HIỆN TẠI 2026/27)

Thư mục này nhận dữ liệu từ quy trình tự động hóa GitHub Actions chạy hàng ngày lúc 03:00 UTC (`crawler_pipeline/daily_raw_crawler.py`).

| STT | Tên File / Cụm File | Nguồn Cung Cấp | Phương Thức Thu Thập | Định Dạng & Dung Lượng | Thông Tin Chứa Bên Trong |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **1** | `football_data_raw_2627.csv` | Football-Data.co.uk<br>`https://www.football-data.co.uk/mmz4281/2627/E0.csv` | HTTP GET định kỳ (Tải file CSV tích lũy cả mùa) | CSV<br>(~30 KB, 60 dòng) | Kết quả 60 trận đã đấu mùa 2026/27: ngày giờ (`Date`, `Time`), đội nhà/khách (`HomeTeam`, `AwayTeam`), tỷ số FT/HT (`FTHG`, `FTAG`, `HTHG`, `HTAG`), kết quả (`FTR`, `HTR`), sút (`HS`, `AS`, `HST`, `AST`), phạt góc (`HC`, `AC`), phạm lỗi (`HF`, `AF`), thẻ vàng/đỏ (`HY`, `AY`, `HR`, `AR`), trọng tài (`Referee`), tỷ lệ cược mở/đóng 1X2 từ Bet365, Pinnacle, William Hill, Betfair, kèo chấp Châu Á (AH) và kèo Tài/Xỉu (O/U). |
| **2** | `fotmob_fixtures_2026_2027.json` | FotMob REST API<br>`https://www.fotmob.com/api/data/leagues?id=47&season=2026%2F2027` | FotMob API Call | JSON<br>(~902 KB) | Danh mục toàn bộ 380 trận đấu của mùa 2026/27: `id` (FotMob Match ID), thời gian thi đấu chuẩn UTC, ID câu lạc bộ, trạng thái hoàn thành (`status.finished`), tỷ số khi trận đấu kết thúc. |
| **3** | `fpl_bootstrap_latest.json` | Fantasy Premier League Official API<br>`https://fantasy.premierleague.com/api/bootstrap-static/` | Official REST API Call | JSON<br>(~1.9 MB) | Snapshot tổng thể mùa 2026/27: 20 CLB (`teams`: chỉ số sức mạnh công/thủ sân nhà/khách `strength_*`), danh sách toàn bộ cầu thủ (`elements`: vị trí, giá tiền, % sở hữu, chỉ số ICT `influence`, `creativity`, `threat`, tình trạng chấn thương `news`, `chance_of_playing_next_round`), và danh mục 38 vòng đấu (`events`). |
| **4** | `fpl_fixtures_latest.json` | Fantasy Premier League Official API<br>`https://fantasy.premierleague.com/api/fixtures/` | Official REST API Call | JSON<br>(~281 KB) | Lịch thi đấu 380 trận mùa 2026/27 kèm chỉ số độ khó lịch thi đấu FDR (`team_h_difficulty`, `team_a_difficulty` theo thang điểm 1-5), mã trận, bàn thắng, kiến tạo, điểm bonus FPL của các trận đã kết thúc. |
| **5** | `understat_raw_2026_2027.json` | Understat AJAX Endpoint<br>`https://understat.com/getLeagueData/EPL/2026` | Understat API Call (Header: `X-Requested-With: XMLHttpRequest`) | JSON<br>(~302 KB) | Toàn bộ lịch thi đấu và số liệu tổng kết mùa 2026/27 của Understat: mảng `dates` (gồm `id` trận Understat, ngày giờ, tỷ số, tổng $xG$ đội nhà, $xG$ đội khách, cờ hoàn thành `isResult`), thống kê CLB trong `teams`, thống kê cầu thủ trong `players`. |
| **6** | `weather_cache_2026_2027.json` | Open-Meteo Archive API<br>`https://archive-api.open-meteo.com/v1/archive` | Open-Meteo REST API Call (theo tọa độ GPS sân và giờ bóng lăn) | JSON<br>(~6 KB) | Bộ đệm thời tiết cho các trận mùa 2026/27 ánh xạ theo khóa `{lat}_{lon}_{date}_{hour}` gồm: nhiệt độ mặt sân (`temp` - °C), độ ẩm (`humidity` - %), lượng mưa (`precipitation` - mm), tốc độ gió (`wind` - km/h), mã hiện tượng thời tiết WMO (`weather_code`). |
| **7** | `matches/fotmob/{match_id}.json`<br>*(Tập hợp 60 file tương ứng 60 trận đã đấu)* | FotMob REST API<br>`https://www.fotmob.com/api/data/matchDetails?matchId={match_id}` | FotMob API Call (Cào tăng tiến theo Delta) | 60 file JSON<br>(~250-400 KB/file) | Biên bản chi tiết từng trận đấu: `content.lineup` (đội hình 11 cầu thủ đá chính, dự bị, vị trí sa bàn, sơ đồ chiến thuật formation `4-3-3`, `4-2-3-1`, HLV 2 đội), `content.shotmap.shots` (tọa độ vị trí dứt điểm mét thực tế $x, y$, tọa độ mặt phẳng khung thành $x, y$, $xG$, $xGOT$, cầu thủ sút), `content.stats` (54 chỉ số kiểm soát bóng, chạm bóng trong vòng cấm, tắc bóng), `content.momentum` (chuỗi áp lực từng phút 1 đến 90), `content.playerStats` (chấm điểm rating FotMob từng cầu thủ). |
| **8** | `matches/understat/{match_id}.json`<br>*(Tập hợp 60 file tương ứng 60 trận đã đấu)* | Understat AJAX Endpoint<br>`https://understat.com/getMatchData/{match_id}` | Understat API Call (Cào tăng tiến theo Delta) | 60 file JSON<br>(~30-45 KB/file) | Chi tiết thông số kỳ vọng từng trận: mảng `shots` (toàn bộ các cú sút kèm tọa độ chuẩn hóa $X, Y \in [0, 1]$, $xG$, tình huống bóng sống/cố định/phạt góc/penalty, chân sút, người kiến tạo `player_assisted`, hành động trước đó `lastAction`) và đối tượng `rosters` (danh sách cầu thủ ra sân kèm 2 chỉ số triển khai bóng `xGChain`, `xGBuildup`). |

---

## 3. ÁNH XẠ CHI TIẾT THƯ MỤC `raw_data/` (DỮ LIỆU NỀN TẢNG LỊCH SỬ)

---

### 3.1. Phân mục `raw_data/football_data_co_uk/` (27 tệp CSV - 2.98 MB)

* **Nguồn cung cấp:** Website dữ liệu bóng đá mở của Joseph Buchdahl: `https://www.football-data.co.uk/mmz4281/{season_code}/E0.csv`
* **Phương thức thu thập:** HTTP GET tải trực tiếp file CSV.

| Tên File | Nguồn & Phương Thức | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- |
| `football_data_raw_0001.csv`<br>đến<br>`football_data_raw_2526.csv`<br>*(26 tệp lịch sử)* | HTTP GET tải trực tiếp từ Football-Data.co.uk | Dữ liệu kết quả và kèo cá cược trọn vẹn của 26 mùa giải (mỗi file 380 trận = 9,880 trận): ngày đấu (`Date`), đội nhà/khách (`HomeTeam`, `AwayTeam`), tỷ số FT/HT (`FTHG`, `FTAG`, `HTHG`, `HTAG`), kết quả (`FTR`, `HTR`), sút bóng (`HS`, `AS`), sút trúng đích (`HST`, `AST`), phạt góc (`HC`, `AC`), phạm lỗi (`HF`, `AF`), thẻ vàng (`HY`, `AY`), thẻ đỏ (`HR`, `AR`), trọng tài (`Referee`), lượng khán giả (`Attendance`), tỷ lệ cược 1X2 mở/đóng từ Bet365, Pinnacle, William Hill, Ladbrokes, Betfair Exchange, kèo chấp Châu Á (AH) và kèo Tài/Xỉu (O/U). |
| `football_data_raw_2627.csv`<br>*(1 tệp mùa hiện tại)* | HTTP GET định kỳ từ Football-Data.co.uk | Bản sao lưu (mirror) của file `2026_2027_new/football_data_raw_2627.csv` phục vụ kiểm toán đồng bộ. |

---

### 3.2. Phân mục `raw_data/understat/` (20 tệp + 1 thư mục chứa 4,620 tệp - 194.87 MB)

* **Nguồn cung cấp:** Understat.com (`https://understat.com`) và kho mở `vaastav/Fantasy-Premier-League`.
* **Phương thức thu thập:** Gọi AJAX Endpoint (`getLeagueData`, `getMatchData`) kết hợp nhập dữ liệu mở.

| Tên File / Cụm File | Nguồn Cung Cấp | Phương Thức Thu Thập | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- | :--- |
| `understat_raw_2014_2015.json`<br>đến<br>`understat_raw_2026_2027.json`<br>*(13 tệp JSON tổng quan mùa)* | Understat AJAX Endpoint<br>`https://understat.com/getLeagueData/EPL/{year}` | Understat API Call | Snapshot tổng quan từng mùa giải từ 2014/15 đến 2026/27: mảng `dates` (380 trận/mùa, `id` trận đấu, ngày giờ, tỷ số, $xG$ đội nhà, $xG$ đội khách, cờ hoàn thành `isResult`), thống kê CLB trong `teams`, thống kê cầu thủ trong `players`. |
| `cache_matches/{match_id}.json`<br>*(Tập hợp 4,620 file JSON)* | Understat AJAX Endpoint<br>`https://understat.com/getMatchData/{match_id}` | Understat API Call | Biên bản chi tiết 4,620 trận đấu từ 2014/15 đến 2025/26: mảng `shots` (tọa độ cú sút $X,Y \in [0, 1]$, $xG$, loại tình huống, bộ phận dứt điểm, cầu thủ kiến tạo `player_assisted`, hành động trước đó `lastAction`) và đối tượng `rosters` (danh sách cầu thủ thi đấu, phút ra sân, chỉ số `xGChain`, `xGBuildup`). |
| `understat_matches_cache.tar.gz`<br>*(Dung lượng 16.1 MB)* | Đóng gói nội bộ hệ thống | Nén tar.gz từ thư mục `cache_matches/` | Chứa toàn bộ 4,620 file JSON của `cache_matches/` phục vụ đồng bộ nhanh lên Hugging Face Hub. |
| `understat_matches_all_2014_2027.csv`<br>*(Dung lượng 500 KB, 4,940 dòng)* | Tổng hợp từ Understat Overview API | Bảng phẳng tổng hợp | Tổng hợp 4,940 trận từ 2014 đến 2027: ngày thi đấu, đội nhà, đội khách, tỷ số thực tế, $xG$ nhà, $xG$ khách, xác suất thắng/hòa/thua dự báo bởi Understat. |
| `understat_players_all_2014_2027.csv`<br>*(Dung lượng 1.0 MB, 6,845 dòng)* | Tổng hợp từ Understat Overview API | Bảng phẳng tổng hợp | Thống kê cầu thủ theo từng mùa: số trận (`games`), số phút (`time`), bàn thắng (`goals`), $xG$, kiến tạo (`assists`), $xA$, số cú sút (`shots`), đường chuyền quyết định (`key_passes`), `xGChain`, `xGBuildup`. |
| `vaastav_understat_player_{season}.csv`<br>*(4 tệp: 2021_2022 đến 2024_2025)* | Kho mở GitHub `vaastav/Fantasy-Premier-League` | External Open Dataset | Bảng đối chiếu chéo chỉ số xG, xA, key passes của cầu thủ kết hợp giữa FPL và Understat. |

---

### 3.3. Phân mục `raw_data/sofascore/` (Lõi FotMob: 15 tệp + 2 thư mục chứa 6,192 tệp - 1.09 GB)

* **Nguồn cung cấp:** FotMob Internal REST API (`https://www.fotmob.com/api/data/...`).
* **Phương thức thu thập:** Gọi trực tiếp REST API nội bộ của FotMob bằng User-Agent trình duyệt web chuẩn.

| Tên File / Cụm File | Nguồn Cung Cấp | Phương Thức Thu Thập | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- | :--- |
| `fotmob_league_2010_2011.json`<br>đến<br>`fotmob_league_2020_2021.json`<br>*(11 tệp JSON tổng quan mùa)* | FotMob REST API<br>`https://www.fotmob.com/api/data/leagues?id=47&season=...` | FotMob API Call | Lịch thi đấu và kết quả cả mùa của 11 mùa giải (380 trận/mùa, `id` trận đấu FotMob, ngày giờ UTC, mã CLB, tỷ số kết thúc). |
| `fotmob_fixtures_2026_2027.json` | FotMob REST API | FotMob API Call | Bản sao lưu của file lịch đấu mùa 2026/27. |
| `cache_matches/{match_id}.json`<br>*(Tập hợp 6,141 file JSON lịch sử)* | FotMob REST API<br>`https://www.fotmob.com/api/data/matchDetails?matchId={match_id}` | FotMob API Call | Biên bản chi tiết 6,141 trận đấu từ mùa 2010/11 đến 2025/26: `content.lineup` (đội hình xuất phát, danh sách dự bị, vị trí sa bàn, sơ đồ chiến thuật formation `4-2-3-1`, `3-4-2-1`), `content.shotmap.shots` (tọa độ mét $x,y$, tọa độ khung thành, $xG$, $xGOT$, cầu thủ dứt điểm), `content.stats` (54 chỉ số kiểm soát bóng, tắc bóng, chạm bóng trong vòng cấm), `content.momentum` (chuỗi xung lực ép sân 90 phút), `content.playerStats` (rating cầu thủ). |
| `cache_2026_2027/{match_id}.json`<br>*(Tập hợp 51 file JSON mùa mới)* | FotMob REST API | FotMob API Call | Các trận đấu đầu mùa giải 2026/27 được cào trong giai đoạn khởi tạo hệ thống. |
| `sofascore_matches_cache.tar.gz`<br>*(Dung lượng 91.7 MB)* | Đóng gói nội bộ hệ thống | Nén tar.gz từ thư mục `cache_matches/` | Chứa toàn bộ 6,141 file JSON của `cache_matches/` phục vụ đồng bộ nhanh lên Hugging Face Hub. |
| `fotmob_epl_47_details.csv`<br>*(Dung lượng 23.2 MB)* | Bóc tách từ FotMob API | Bảng phẳng tổng hợp | Bảng phẳng tổng hợp toàn bộ các thông số sút bóng, đội hình và sự kiện từ các file JSON. |
| `fotmob_team_match_stats.csv`<br>*(Dung lượng 912 KB)* | Bóc tách từ FotMob API | Bảng phẳng tổng hợp | Bảng phẳng tổng hợp 54 thông số cấp câu lạc bộ trong từng trận đấu. |

---

### 3.4. Phân mục `raw_data/fpl_api/` (35 tệp - 49.87 MB)

* **Nguồn cung cấp:** Fantasy Premier League Official API (`fantasy.premierleague.com`) và kho mở `vaastav/Fantasy-Premier-League`.
* **Phương thức thu thập:** Official REST API cho mùa mới; Nhập tệp CSV mở cho 10 mùa lịch sử (2016/17 đến 2025/26).

| Tên File / Cụm File | Nguồn Cung Cấp | Phương Thức Thu Thập | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- | :--- |
| `fpl_bootstrap_latest.json`<br>& `fpl_fixtures_latest.json` | Premier League Fantasy Official API | Official REST API Call | Bản sao lưu snapshot mùa 2026/27: danh mục 20 đội, chỉ số sức mạnh CLB, danh sách cầu thủ, chỉ số ICT, tình trạng chấn thương, lịch thi đấu và FDR. |
| `fpl_cleaned_players_{season}.csv`<br>*(10 tệp: 2016/17 đến 2025/26)* | Kho mở GitHub `vaastav/Fantasy-Premier-League` | External Open Dataset | Thống kê tổng hợp mùa của từng cầu thủ: `first_name`, `second_name`, `goals_scored`, `assists`, `total_points`, `minutes`, `clean_sheets`, giá tiền, tỷ lệ người chơi sở hữu. |
| `fpl_fixtures_{season}.csv`<br>*(8 tệp: 2018/19 đến 2025/26)* | Kho mở GitHub `vaastav/Fantasy-Premier-League` | External Open Dataset | Lịch đấu và kết quả lịch sử: mã trận `code`, vòng đấu `event`, `kickoff_time`, tỷ số và chỉ số FDR hai đội (`team_h_difficulty`, `team_a_difficulty`). |
| `fpl_merged_gw_{season}.csv`<br>*(10 tệp lớn: 2016/17 đến 2025/26, 3.0 – 5.4 MB/file)* | Kho mở GitHub `vaastav/Fantasy-Premier-League` | External Open Dataset | Dữ liệu từng vòng đấu (Gameweek) của từng cầu thủ (33 đến 56 cột): số phút ra sân, bàn thắng, kiến tạo, điểm thưởng (`bonus`, `bps`), điểm kỳ vọng (`xP`), và bộ ba chỉ số ICT Index (`influence`, `creativity`, `threat`). |
| `fpl_teams_{season}.csv`<br>*(5 tệp: 2021/22 đến 2025/26)* | Kho mở GitHub `vaastav/Fantasy-Premier-League` | External Open Dataset | Chỉ số sức mạnh của 20 CLB mỗi mùa: số trận thắng/hòa/thua, điểm phong độ, hệ số sức mạnh tấn công/phòng ngự sân nhà/khách. |

---

### 3.5. Phân mục `raw_data/open_meteo/` (1 tệp CSV + 1 thư mục chứa 27 tệp JSON - 51.20 MB)

* **Nguồn cung cấp:** Open-Meteo Historical Archive API dựa trên mô hình khí quyển tái phân tích ECMWF ERA5 Reanalysis.
* **Phương thức thu thập:** Gọi Open-Meteo REST API theo tọa độ GPS và ngày giờ thi đấu.

| Tên File / Cụm File | Nguồn Cung Cấp | Phương Thức Thu Thập | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- | :--- |
| `epl_weather_archive_2000_2021.csv`<br>*(Dung lượng 3.85 MB, 68,481 dòng)* | Mô hình khí tượng ERA5 Reanalysis qua Open-Meteo API | External Historical Climate Dataset | Dữ liệu thời tiết theo ngày từ 2000 đến 2021 tại 9 vùng địa lý trọng điểm của Vương quốc Anh: `region`, `date`, `latitude`, `longitude`, `temperature_c` (nhiệt độ), `precipitation_mm` (lượng mưa), `wind_speed_kmh` (tốc độ gió). |
| `stadiums/{stadium_name}.json`<br>*(Tập hợp 27 tệp JSON)* | Open-Meteo Archive API<br>`https://archive-api.open-meteo.com/v1/archive` | Open-Meteo REST API Call | Dữ liệu thời tiết theo từng giờ của 27 sân vận động tương ứng 27 CLB Ngoại hạng Anh dựa trên tọa độ GPS thực tế: nhiệt độ không khí (`temperature_2m` - °C), nhiệt độ cảm nhận (`apparent_temperature`), độ ẩm tương đối (`relative_humidity_2m` - %), lượng mưa (`precipitation` - mm), tốc độ gió (`wind_speed_10m` - km/h), mã hiện tượng thời tiết WMO (`weather_code`). |

---

### 3.6. Phân mục `raw_data/transfermarkt/` (9 tệp nén `.csv.gz` - 229.95 MB)

* **Nguồn cung cấp:** Kho dữ liệu mở `dcaribou/transfermarkt-datasets` (Dmitry Fischer) trên GitHub/Kaggle (Cấp phép CC0 1.0 Universal Public Domain).
* **Phương thức thu thập:** External Open Dataset (Bộ dữ liệu mở đồng bộ từ Transfermarkt).

| Tên File | Nguồn Cung Cấp & Phương Thức | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- |
| `tfm_player_valuations_full.csv.gz`<br>*(Dung lượng 6.86 MB, > 650,000 dòng)* | Transfermarkt Open Dataset | Lịch sử biến động giá trị chuyển nhượng của mọi cầu thủ từ năm 2000 đến nay: mã cầu thủ (`player_id`), ngày ghi nhận (`date`), giá trị thị trường quy đổi Euro (`market_value_in_eur`), CLB chủ quản (`current_club_name`, `current_club_id`). Dùng thuật toán `merge_asof` lấy giá trị ngay trước giờ bóng lăn, không rò rỉ dữ liệu tương lai. |
| `tfm_games_full.csv.gz`<br>*(Dung lượng 4.76 MB, 5,320 trận)* | Transfermarkt Open Dataset | Danh mục trận đấu từ 2012 đến 2026: mã trận (`game_id`), mùa giải (`season`), vòng đấu (`round`), ngày đấu (`date`), ID đội nhà/khách, bàn thắng, sân vận động (`stadium`), số lượng khán giả (`attendance`), trọng tài (`referee`). |
| `tfm_game_events_full.csv.gz`<br>*(Dung lượng 43.36 MB)* | Transfermarkt Open Dataset | Sự kiện từng phút trong trận: mã sự kiện (`game_event_id`), ngày, mã trận, phút diễn ra (`minute`), loại sự kiện (`type`: Bàn thắng, Thẻ phạt, Thay người), ID đội bóng, ID cầu thủ liên quan. |
| `tfm_game_lineups_full.csv.gz`<br>*(Dung lượng 119.99 MB)* | Transfermarkt Open Dataset | Đội hình thi đấu từ 2012 đến 2026: mã biên bản (`game_lineups_id`), ngày, mã trận, ID cầu thủ, tên cầu thủ, phân loại (`type`: đá chính `starting_lineup` hoặc dự bị `substitutes`), vị trí thi đấu (`position`), số áo (`number`), băng đội trưởng (`team_captain`). |
| `tfm_transfers_full.csv.gz`<br>*(Dung lượng 5.54 MB)* | Transfermarkt Open Dataset | Lịch sử chuyển nhượng: ID cầu thủ, ngày chuyển nhượng (`transfer_date`), mùa giải (`transfer_season`), CLB chuyển nhượng (`from_club_id`), CLB tiếp nhận (`to_club_id`), mức phí chuyển nhượng (`transfer_fee`). |
| `tfm_appearances_full.csv.gz`<br>*(Dung lượng 43.16 MB)* | Transfermarkt Open Dataset | Thống kê ra sân của từng cầu thủ trong từng trận: mã bản ghi (`appearance_id`), mã trận, mã cầu thủ, CLB, số phút thi đấu thực tế (`minutes_played`), bàn thắng, kiến tạo, thẻ vàng, thẻ đỏ. |
| `tfm_players_full.csv.gz`<br>*(Dung lượng 4.19 MB)* | Transfermarkt Open Dataset | Hồ sơ nhân khẩu học tĩnh của cầu thủ: ID cầu thủ, họ tên, quốc tịch (`country_of_citizenship`), ngày sinh (`date_of_birth`), vị trí sở trường (`sub_position`), chân thuận (`foot`), chiều cao tính bằng cm (`height_in_cm`). |
| `tfm_clubs_full.csv.gz`<br>*(Dung lượng 51 KB)* | Transfermarkt Open Dataset | Danh mục 29 CLB Ngoại hạng Anh: ID CLB, tên CLB, tổng giá trị đội hình (`total_market_value`), quy mô đội hình (`squad_size`), tuổi trung bình (`average_age`), số ngoại binh (`foreigners_number`), tên sân nhà, sức chứa sân. |
| `tfm_club_games_full.csv.gz`<br>*(Dung lượng 2.04 MB)* | Transfermarkt Open Dataset | Thông số đối đầu và bối cảnh bảng xếp hạng tại thời điểm đá: mã trận, mã CLB, bàn thắng ghi được, vị trí trên BXH trước trận (`own_position`), tên HLV trưởng (`own_manager_name`), ID đối thủ, vị trí BXH của đối thủ (`opponent_position`). |

---

### 3.7. Phân mục `raw_data/fbref/` (16 tệp CSV - 283.50 MB)

* **Nguồn cung cấp:** Dữ liệu StatsBomb/Opta lưu trữ trên FBref (Sports Reference LLC).
* **Phương thức thu thập:** External Open Data Mirror (Trích xuất từ FBref qua các công cụ học thuật `worldfootballR` và `soccerdata`, gồm 8 mùa giải từ 2017/18 đến 2024/25).

| Tên File | Nguồn Cung Cấp & Phương Thức | Thông Tin Chứa Bên Trong |
| :--- | :--- | :--- |
| `ENG_M_1st_defense_player_advanced_match_stats.csv`<br>(43.58 MB, 49 cột)<br>& `ENG_M_1st_defense_team_advanced_match_stats.csv`<br>(2.75 MB, 44 cột) | FBref / StatsBomb Mirror | Thống kê phòng ngự và áp sát: Áp sát theo 3 khu vực sân (`Pressures_Def_3rd`, `Pressures_Mid_3rd`, `Pressures_Att_3rd`), tắc bóng (`Tkl`), cản cú sút (`Blocks`), cắt bóng (`Int`), phá bóng (`Clr`), lỗi cá nhân dẫn đến cú dứt điểm của đối phương (`Err`). |
| `ENG_M_1st_passing_player_advanced_match_stats.csv`<br>(45.90 MB, 55 cột)<br>& `ENG_M_1st_passing_team_advanced_match_stats.csv`<br>(0.28 MB, 50 cột) | FBref / StatsBomb Mirror | Thống kê chuyền bóng: Tỷ lệ chuyền ngắn/trung bình/dài, đường chuyền tịnh tiến hướng lên ít nhất 10 yards (`PrgP`), đường chuyền mở ra cơ hội sút (`KP`), đường chuyền vào vòng cấm đối phương (`PPA`, `CrsPA`). |
| `ENG_M_1st_passing_types_player_advanced_match_stats.csv`<br>(43.66 MB, 48 cột)<br>& `ENG_M_1st_passing_types_team_advanced_match_stats.csv`<br>(2.74 MB, 43 cột) | FBref / StatsBomb Mirror | Phân loại đường chuyền: Bóng sống (`Live`), bóng chết cố định (`Dead`), đá phạt (`FK`), ném biên, đường chuyền bị bắt việt vị. |
| `ENG_M_1st_possession_player_advanced_match_stats.csv`<br>(44.16 MB, 55 cột)<br>& `ENG_M_1st_possession_team_advanced_match_stats.csv`<br>(2.94 MB, 50 cột) | FBref / StatsBomb Mirror | Thống kê kiểm soát và tịnh tiến: Chạm bóng theo 3 phần sân (`Touches_Def_3rd`, `Touches_Mid_3rd`, `Touches_Att_3rd`), dẫn bóng tịnh tiến (`PrgC`), qua người thành công (`Succ`), bị cướp bóng (`Dis`). |
| `ENG_M_1st_misc_player_advanced_match_stats.csv`<br>(42.49 MB, 49 cột)<br>& `ENG_M_1st_misc_team_advanced_match_stats.csv`<br>(2.75 MB, 44 cột) | FBref / StatsBomb Mirror | Thống kê đối kháng: Tranh chấp trên không thắng/thua (`Won`, `Lost`, `Won%`), phạm lỗi (`Fls`), bị phạm lỗi (`Fld`), thu hồi bóng hai (`Recov`). |
| `ENG_M_1st_match_shooting.csv`<br>(20.30 MB, 23 cột) | FBref / StatsBomb Mirror | Toàn bộ các cú sút trong trận: Cầu thủ, phút, khoảng cách dứt điểm tính bằng mét (`Distance`), bộ phận chạm bóng, chỉ số $PSxG$ (Post-Shot Expected Goals). |
| `ENG_M_1st_summary_player_advanced_match_stats.csv`<br>(4.57 MB, 58 cột) | FBref / StatsBomb Mirror | Tổng hợp các chỉ số tạo cơ hội: Bàn thắng kỳ vọng (`xG`), bàn thắng kỳ vọng không tính phạt đền (`npxG`), kiến tạo kỳ vọng (`xAG`), hành động dẫn đến cú sút (`SCA`), hành động dẫn đến bàn thắng (`GCA`). |
| `ENG_M_1st_keeper_player_advanced_match_stats.csv`<br>(3.15 MB, 52 cột)<br>& `ENG_M_1st_keeper_team_advanced_match_stats.csv`<br>(3.15 MB, 51 cột) | FBref / StatsBomb Mirror | Thống kê thủ môn nâng cao: Bàn thua ngăn chặn kỳ vọng (`PSxG+/-`), tỷ lệ cắt các pha tạt bóng bổng (`Stp%`), số đường phát bóng dài. |
| `ENG_M_1st_match_summary.csv`<br>(18.25 MB, 33 cột) | FBref / StatsBomb Mirror | Tóm tắt trận đấu và dòng thời gian các sự kiện chính. |
| `fbref_epl_team_advanced_match_stats.csv`<br>(2.83 MB, 53 cột) | FBref / StatsBomb Mirror | Bảng tổng hợp toàn bộ các chỉ số vi mô nâng cao ở cấp độ câu lạc bộ cho từng trận đấu. |

---

## 4. TỔNG HỢP PHÂN LOẠI THU THẬP BẰNG SỐ LIỆU

| Phân Loại Thu Thập | Nguồn Cung Cấp | Thư Mục / Tệp Đại Diện | Số Lượng Tệp | Tổng Dung Lượng |
| :--- | :--- | :--- | :---: | :---: |
| **Cào Động Qua API Calls** | • Football-Data.co.uk (HTTP GET)<br>• Understat AJAX Endpoint<br>• FotMob REST API<br>• FPL Official REST API<br>• Open-Meteo Archive REST API | `2026_2027_new/`<br>`raw_data/football_data_co_uk/`<br>`raw_data/understat/cache_matches/`<br>`raw_data/sofascore/cache_matches/`<br>`raw_data/open_meteo/stadiums/` | **10,845 files** | **~1.33 GB** |
| **Nhập Từ Open Datasets & Mirrors** | • Transfermarkt Open Dataset (DCaribou)<br>• FPL Historical (Vaastav Repo)<br>• FBref StatsBomb Historical Mirror<br>• ECMWF ERA5 Climate Reanalysis | `raw_data/transfermarkt/` (9 file gz)<br>`raw_data/fpl_api/` (33 file csv)<br>`raw_data/fbref/` (16 file csv)<br>`raw_data/open_meteo/epl_weather_*.csv` | **64 files** | **~520 MB** |
| **TỔNG CỘNG** | **7 Nguồn Độc Lập** | **`raw_data/` + `2026_2027_new/`** | **10,909 files** | **~1.85 GB** |
