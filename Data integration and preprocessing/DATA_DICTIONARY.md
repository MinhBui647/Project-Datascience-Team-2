# Data Dictionary – Premier League 2000/01 → 2026/27

Bộ dữ liệu sạch và bảng đặc trưng cho bài toán dự đoán tỉ số và xác suất Thắng/Hòa/Thua.
Tạo bởi `Data integration and preprocessing/` chỉ từ `data/raw_data/`; file này được sinh tự động
bằng `data_dictionary.py` – các cột *Kiểu*, *Có từ mùa*, *% thiếu*, *Ví dụ* được đo trên dữ liệu hiện tại.

## Tổng quan

| File (`data/clean/`) | Một dòng là | Số dòng | Số cột | Dùng cho |
|---|---|---|---|---|
| `matches.csv` | một trận | 9,850 (9,850 đã đá, 0 sắp đá) | 106 | phân tích, bộ dữ liệu sạch |
| `features.csv` | một trận | 9,850 | 102 | huấn luyện / dự đoán |

- Khóa chung: `match_id` (một trận chỉ có một dòng trong mỗi file).
- Mùa: 2000/01 → 2026/27; 2003/04 và 2004/05 chỉ có 335/380 trận (thiếu trong nguồn gốc);
  mùa 2026/27 đang diễn ra.
- Cột **Thời điểm** cho biết khi nào giá trị được biết:

| Thời điểm | Ý nghĩa | Dùng làm đầu vào mô hình? |
|---|---|---|
| Định danh | khóa, tên | không (chỉ để ghép / tra cứu) |
| Trước trận | biết trước ngày thi đấu | **có** |
| Sát giờ | chỉ biết ngay trước giờ bóng lăn (kèo chốt, đội hình) | có, nếu dự đoán sát giờ |
| Sau trận | thống kê của chính trận đấu | **không** – chỉ dùng qua trung bình các trận trước (`features.csv`) |
| Mục tiêu | điều cần dự đoán | là nhãn |
| Kỹ thuật | cờ chất lượng / trạng thái | lọc dữ liệu |

\* % thiếu tính trên các trận **đã đá**. Trận sắp đá không có kết quả, thống kê và kèo.

## 1. `matches.csv` – bộ dữ liệu sạch

| Cột | Kiểu | Mô tả | Đơn vị / miền | Nguồn | Thời điểm | Có từ mùa | % thiếu* | Ví dụ |
|---|---|---|---|---|---|---|---|---|
| `match_id` | text | Mã trận duy nhất `EPL_<năm bắt đầu>_<năm kết thúc>_<NHÀ>_<KHÁCH>` | chuỗi | tạo ra | Định danh | 2000/01 | 0.0% | `EPL_2013_2014_STK_WBA` |
| `league` | text | Tên giải đấu | `Premier League` | tạo ra | Định danh | 2000/01 | 0.0% | `Premier League` |
| `league_code` | text | Mã giải của football-data | `E0` | football-data | Định danh | 2000/01 | 0.0% | `E0` |
| `season` | text | Mùa giải | `2000/01` … `2026/27` | tạo ra | Định danh | 2000/01 | 0.0% | `2013/14` |
| `season_start_year` | int | Năm bắt đầu mùa giải | 2000 … 2026 | tạo ra | Định danh | 2000/01 | 0.0% | `2013` |
| `date` | text | Ngày thi đấu | `YYYY-MM-DD` | football-data / FPL | Trước trận | 2000/01 | 0.0% | `2013-10-19` |
| `kickoff_time` | text | Giờ bóng lăn, giờ Anh | `HH:MM` | football-data / FPL | Trước trận | 2019/20 | 72.4% | `19:30` |
| `home_club_id` | text | Mã 3 chữ cái của đội nhà | `ARS`, `MCI`, … | club_mapping | Định danh | 2000/01 | 0.0% | `STK` |
| `home_team` | text | Tên chuẩn của đội nhà | chuỗi | club_mapping | Định danh | 2000/01 | 0.0% | `Stoke` |
| `away_club_id` | text | Mã 3 chữ cái của đội khách | `ARS`, `MCI`, … | club_mapping | Định danh | 2000/01 | 0.0% | `WBA` |
| `away_team` | text | Tên chuẩn của đội khách | chuỗi | club_mapping | Định danh | 2000/01 | 0.0% | `West Brom` |
| `score` | text | Tỉ số cả trận `nhà-khách` | `2-1` | football-data | Mục tiêu | 2000/01 | 0.0% | `0-0` |
| `home_goals` | int | Số bàn thắng cả trận của đội nhà | bàn | football-data | Mục tiêu | 2000/01 | 0.0% | `0` |
| `away_goals` | int | Số bàn thắng cả trận của đội khách | bàn | football-data | Mục tiêu | 2000/01 | 0.0% | `0` |
| `result` | text | Kết quả cả trận: H = đội nhà thắng, D = hòa, A = đội khách thắng | H / D / A | football-data | Mục tiêu | 2000/01 | 0.0% | `D` |
| `ht_score` | text | Tỉ số hiệp 1 | `1-0` | football-data | Sau trận | 2000/01 | 0.0% | `0-0` |
| `ht_home_goals` | int | Số bàn hiệp 1 của đội nhà | bàn | football-data | Sau trận | 2000/01 | 0.0% | `0` |
| `ht_away_goals` | int | Số bàn hiệp 1 của đội khách | bàn | football-data | Sau trận | 2000/01 | 0.0% | `0` |
| `ht_result` | text | Kết quả hiệp 1 | H / D / A | football-data | Sau trận | 2000/01 | 0.0% | `D` |
| `home_shots` | int | Tổng số cú sút của đội nhà | cú sút | football-data | Sau trận | 2000/01 | 0.0% | `14` |
| `away_shots` | int | Tổng số cú sút của đội khách | cú sút | football-data | Sau trận | 2000/01 | 0.0% | `12` |
| `home_shots_on_target` | int | Số cú sút trúng đích của đội nhà | cú sút | football-data | Sau trận | 2000/01 | 0.0% | `6` |
| `away_shots_on_target` | int | Số cú sút trúng đích của đội khách | cú sút | football-data | Sau trận | 2000/01 | 0.0% | `5` |
| `home_corners` | int | Số quả phạt góc của đội nhà | lần | football-data | Sau trận | 2000/01 | 0.0% | `6` |
| `away_corners` | int | Số quả phạt góc của đội khách | lần | football-data | Sau trận | 2000/01 | 0.0% | `6` |
| `home_fouls` | int | Số lỗi đội nhà phạm phải | lần | football-data | Sau trận | 2000/01 | 0.0% | `12` |
| `away_fouls` | int | Số lỗi đội khách phạm phải | lần | football-data | Sau trận | 2000/01 | 0.0% | `10` |
| `home_yellow_cards` | int | Số thẻ vàng của đội nhà | thẻ | football-data | Sau trận | 2000/01 | 0.0% | `1` |
| `away_yellow_cards` | int | Số thẻ vàng của đội khách | thẻ | football-data | Sau trận | 2000/01 | 0.0% | `0` |
| `home_red_cards` | int | Số thẻ đỏ của đội nhà | thẻ | football-data | Sau trận | 2000/01 | 0.0% | `0` |
| `away_red_cards` | int | Số thẻ đỏ của đội khách | thẻ | football-data | Sau trận | 2000/01 | 0.0% | `0` |
| `home_xg` | float | Bàn thắng kỳ vọng (xG) của đội nhà | xG ≥ 0 | Understat | Sau trận | 2014/15 | 53.1% | `2.567` |
| `away_xg` | float | Bàn thắng kỳ vọng (xG) của đội khách | xG ≥ 0 | Understat | Sau trận | 2014/15 | 53.1% | `1.415` |
| `attendance` | int | Số khán giả; 0 = đá sân không khán giả (COVID) | người | Transfermarkt / football-data | Sau trận | 2000/01 | 38.4% | `50752` |
| `stadium` | text | Sân thi đấu | chuỗi | Transfermarkt | Trước trận | 2012/13 | 46.0% | `Anfield` |
| `referee` | text | Trọng tài chính, chuẩn hóa `<chữ cái đầu> <họ>` | `M Oliver` | football-data | Trước trận | 2000/01 | 0.0% | `H Webb` |
| `home_manager` | text | Huấn luyện viên của đội nhà | chuỗi | Transfermarkt | Trước trận | 2012/13 | 46.0% | `Jürgen Klopp` |
| `away_manager` | text | Huấn luyện viên của đội khách | chuỗi | Transfermarkt | Trước trận | 2012/13 | 46.0% | `Daniel Farke` |
| `home_formation` | text | Sơ đồ chiến thuật xuất phát của đội nhà (nhãn gốc) | `4-3-3 Attacking` | Transfermarkt | Sát giờ | 2013/14 | 49.8% | `3-5-2 flat` |
| `home_formation_shape` | text | Sơ đồ của đội nhà, chỉ giữ phần số | `4-3-3` | tạo ra | Sát giờ | 2013/14 | 49.8% | `3-5-2` |
| `away_formation` | text | Sơ đồ chiến thuật xuất phát của đội khách (nhãn gốc) | `4-3-3 Attacking` | Transfermarkt | Sát giờ | 2013/14 | 49.8% | `4-4-2 double 6` |
| `away_formation_shape` | text | Sơ đồ của đội khách, chỉ giữ phần số | `4-3-3` | tạo ra | Sát giờ | 2013/14 | 49.8% | `4-4-2` |
| `home_possession` | int | Tỉ lệ kiểm soát bóng của đội nhà | % | FotMob | Sau trận | 2010/11 | 39.1% | `48` |
| `away_possession` | int | Tỉ lệ kiểm soát bóng của đội khách | % | FotMob | Sau trận | 2010/11 | 39.1% | `52` |
| `home_big_chances` | int | Số tình huống ngon ăn (big chance) đội nhà tạo ra | lần | FotMob | Sau trận | 2011/12 | 60.2% | `0` |
| `away_big_chances` | int | Số tình huống ngon ăn (big chance) đội khách tạo ra | lần | FotMob | Sau trận | 2011/12 | 60.2% | `0` |
| `home_big_chances_missed` | int | Số tình huống ngon ăn đội nhà bỏ lỡ | lần | FotMob | Sau trận | 2011/12 | 60.2% | `0` |
| `away_big_chances_missed` | int | Số tình huống ngon ăn đội khách bỏ lỡ | lần | FotMob | Sau trận | 2011/12 | 60.2% | `0` |
| `home_shots_inside_box` | int | Số cú sút trong vòng cấm của đội nhà | cú sút | FotMob | Sau trận | 2011/12 | 60.2% | `10` |
| `away_shots_inside_box` | int | Số cú sút trong vòng cấm của đội khách | cú sút | FotMob | Sau trận | 2011/12 | 60.2% | `6` |
| `home_shots_outside_box` | int | Số cú sút ngoài vòng cấm của đội nhà | cú sút | FotMob | Sau trận | 2011/12 | 60.2% | `4` |
| `away_shots_outside_box` | int | Số cú sút ngoài vòng cấm của đội khách | cú sút | FotMob | Sau trận | 2011/12 | 60.2% | `3` |
| `home_passes` | int | Tổng số đường chuyền của đội nhà | đường chuyền | FotMob | Sau trận | 2011/12 | 60.2% | `571` |
| `away_passes` | int | Tổng số đường chuyền của đội khách | đường chuyền | FotMob | Sau trận | 2011/12 | 60.2% | `440` |
| `home_accurate_passes` | int | Số đường chuyền chính xác của đội nhà | đường chuyền | FotMob | Sau trận | 2011/12 | 60.2% | `506` |
| `away_accurate_passes` | int | Số đường chuyền chính xác của đội khách | đường chuyền | FotMob | Sau trận | 2011/12 | 60.2% | `381` |
| `home_touches_opp_box` | int | Số lần chạm bóng trong vòng cấm đối phương của đội nhà | lần | FotMob | Sau trận | 2013/14 | 79.0% | `14` |
| `away_touches_opp_box` | int | Số lần chạm bóng trong vòng cấm đối phương của đội khách | lần | FotMob | Sau trận | 2013/14 | 79.0% | `29` |
| `home_tackles` | int | Số pha tắc bóng của đội nhà | lần | FotMob | Sau trận | 2011/12 | 60.2% | `19` |
| `away_tackles` | int | Số pha tắc bóng của đội khách | lần | FotMob | Sau trận | 2011/12 | 60.2% | `14` |
| `home_interceptions` | int | Số pha cắt bóng của đội nhà | lần | FotMob | Sau trận | 2011/12 | 60.2% | `10` |
| `away_interceptions` | int | Số pha cắt bóng của đội khách | lần | FotMob | Sau trận | 2011/12 | 60.2% | `14` |
| `home_clearances` | int | Số pha phá bóng của đội nhà | lần | FotMob | Sau trận | 2011/12 | 60.2% | `7` |
| `away_clearances` | int | Số pha phá bóng của đội khách | lần | FotMob | Sau trận | 2011/12 | 60.2% | `26` |
| `home_duels_won` | int | Số pha tranh chấp thắng của đội nhà | lần | FotMob | Sau trận | 2011/12 | 60.2% | `43` |
| `away_duels_won` | int | Số pha tranh chấp thắng của đội khách | lần | FotMob | Sau trận | 2011/12 | 60.2% | `47` |
| `home_saves` | int | Số pha cứu thua của thủ môn đội nhà | lần | FotMob | Sau trận | 2011/12 | 60.2% | `2` |
| `away_saves` | int | Số pha cứu thua của thủ môn đội khách | lần | FotMob | Sau trận | 2011/12 | 60.2% | `3` |
| `home_fm_xg` | float | xG của đội nhà theo FotMob (khác mô hình xG của Understat) | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `1.99` |
| `away_fm_xg` | float | xG của đội khách theo FotMob (khác mô hình xG của Understat) | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `0.88` |
| `home_fm_npxg` | float | xG không tính phạt đền của đội nhà | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `1.99` |
| `away_fm_npxg` | float | xG không tính phạt đền của đội khách | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `0.88` |
| `home_fm_xg_open_play` | float | xG từ bóng sống của đội nhà | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `1.62` |
| `away_fm_xg_open_play` | float | xG từ bóng sống của đội khách | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `0.72` |
| `home_fm_xg_set_play` | float | xG từ bóng chết của đội nhà | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `0.53` |
| `away_fm_xg_set_play` | float | xG từ bóng chết của đội khách | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.2% | `0.23` |
| `home_fm_xgot` | float | xG của các cú sút trúng đích (xGOT) của đội nhà: chất lượng dứt điểm | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.3% | `2.27` |
| `away_fm_xgot` | float | xG của các cú sút trúng đích (xGOT) của đội khách: chất lượng dứt điểm | xG ≥ 0 | FotMob | Sau trận | 2018/19 | 76.3% | `4.65` |
| `home_pass_accuracy` | float | `accurate_passes / passes × 100` của đội nhà | % | tạo ra (FotMob) | Sau trận | 2011/12 | 60.2% | `88.6` |
| `away_pass_accuracy` | float | `accurate_passes / passes × 100` của đội khách | % | tạo ra (FotMob) | Sau trận | 2011/12 | 60.2% | `86.6` |
| `b365_home_odds` | float | Kèo mở Bet365 cho đội nhà thắng | tỉ lệ thập phân > 1 | football-data | Trước trận | 2002/03 | 7.7% | `2.25` |
| `b365_draw_odds` | float | Kèo mở Bet365 cho hòa | tỉ lệ thập phân > 1 | football-data | Trước trận | 2002/03 | 7.7% | `3.6` |
| `b365_away_odds` | float | Kèo mở Bet365 cho đội khách thắng | tỉ lệ thập phân > 1 | football-data | Trước trận | 2002/03 | 7.7% | `3.3` |
| `pinnacle_home_odds` | float | Kèo mở Pinnacle cho đội nhà thắng | tỉ lệ thập phân > 1 | football-data | Trước trận | 2012/13 | 47.7% | `4.34` |
| `pinnacle_draw_odds` | float | Kèo mở Pinnacle cho hòa | tỉ lệ thập phân > 1 | football-data | Trước trận | 2012/13 | 47.7% | `3.78` |
| `pinnacle_away_odds` | float | Kèo mở Pinnacle cho đội khách thắng | tỉ lệ thập phân > 1 | football-data | Trước trận | 2012/13 | 47.7% | `1.89` |
| `avg_home_odds` | float | Kèo mở trung bình thị trường cho đội nhà thắng | tỉ lệ thập phân > 1 | football-data | Trước trận | 2005/06 | 18.4% | `3.48` |
| `avg_draw_odds` | float | Kèo mở trung bình thị trường cho hòa | tỉ lệ thập phân > 1 | football-data | Trước trận | 2005/06 | 18.4% | `3.3` |
| `avg_away_odds` | float | Kèo mở trung bình thị trường cho đội khách thắng | tỉ lệ thập phân > 1 | football-data | Trước trận | 2005/06 | 18.4% | `2.18` |
| `b365_close_home_odds` | float | Kèo chốt Bet365 cho đội nhà thắng | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2019/20 | 72.4% | `3.2` |
| `b365_close_draw_odds` | float | Kèo chốt Bet365 cho hòa | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2019/20 | 72.4% | `3.4` |
| `b365_close_away_odds` | float | Kèo chốt Bet365 cho đội khách thắng | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2019/20 | 72.4% | `2.25` |
| `pinnacle_close_home_odds` | float | Kèo chốt Pinnacle cho đội nhà thắng | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2012/13 | 47.7% | `4.5` |
| `pinnacle_close_draw_odds` | float | Kèo chốt Pinnacle cho hòa | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2012/13 | 47.7% | `3.87` |
| `pinnacle_close_away_odds` | float | Kèo chốt Pinnacle cho đội khách thắng | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2012/13 | 47.7% | `1.84` |
| `avg_close_home_odds` | float | Kèo chốt trung bình thị trường cho đội nhà thắng | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2019/20 | 72.4% | `3.23` |
| `avg_close_draw_odds` | float | Kèo chốt trung bình thị trường cho hòa | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2019/20 | 72.4% | `3.43` |
| `avg_close_away_odds` | float | Kèo chốt trung bình thị trường cho đội khách thắng | tỉ lệ thập phân > 1 | football-data | Sát giờ | 2019/20 | 72.4% | `2.29` |
| `fm_match_id` | int | Mã trận của FotMob (truy ngược file `raw_data/sofascore`) | số nguyên | FotMob | Định danh | 2010/11 | 38.8% | `2782041` |
| `source_flags` | text | Nguồn đã ghép: FD = football-data, US = Understat, TM = Transfermarkt, FM = FotMob, FPL = lịch FPL | `FD+US+TM+FM` | tạo ra | Kỹ thuật | 2000/01 | 0.0% | `FD+TM+FM` |
| `attendance_imputed` | bool | True nếu `attendance` = 0 được suy ra do đá sân không khán giả | bool | tạo ra | Kỹ thuật | 2000/01 | 0.0% | `False` |
| `season_complete` | bool | True nếu mùa có đủ 380 trận | bool | tạo ra | Kỹ thuật | 2000/01 | 0.0% | `True` |
| `status` | text | `played` = đã đá, `upcoming` = sắp đá / bị hoãn | played / upcoming | tạo ra | Kỹ thuật | 2000/01 | 0.0% | `played` |
| `provisional` | bool | True nếu kết quả tạm lấy từ Understat, chờ football-data | bool | tạo ra | Kỹ thuật | 2000/01 | 0.0% | `False` |
| `postponed` | bool | True nếu trận sắp đá đang bị hoãn | bool | tạo ra | Kỹ thuật | 2000/01 | 0.0% | `False` |
| `gameweek` | int | Vòng đấu chính thức (chỉ mùa đang diễn ra) | 1 … 38 | FPL | Trước trận | 2026/27 | 99.4% | `4` |

## 2. `features.csv` – bảng đặc trưng

Mọi đặc trưng chỉ dùng các trận đá **trước** trận đang xét (đã tự kiểm tra, xem `features_report.json`).
Trận sắp đá dùng mọi kết quả đã biết tới lúc chạy. Tiền tố `home_` / `away_` = đội nhà / đội khách.

| Cột | Kiểu | Mô tả | Đơn vị / miền | Thời điểm | Có từ mùa | % thiếu* | Ví dụ |
|---|---|---|---|---|---|---|---|
| `match_id` | text | Mã trận duy nhất `EPL_<năm bắt đầu>_<năm kết thúc>_<NHÀ>_<KHÁCH>` | chuỗi | Định danh | 2000/01 | 0.0% | `EPL_2013_2014_STK_WBA` |
| `season` | text | Mùa giải | `2000/01` … `2026/27` | Định danh | 2000/01 | 0.0% | `2013/14` |
| `date` | text | Ngày thi đấu | `YYYY-MM-DD` | Trước trận | 2000/01 | 0.0% | `2013-10-19` |
| `kickoff_time` | text | Giờ bóng lăn, giờ Anh | `HH:MM` | Trước trận | 2019/20 | 72.4% | `19:30` |
| `gameweek` | int | Vòng đấu chính thức (chỉ mùa đang diễn ra) | 1 … 38 | Trước trận | 2026/27 | 99.4% | `4` |
| `home_club_id` | text | Mã 3 chữ cái của đội nhà | `ARS`, `MCI`, … | Định danh | 2000/01 | 0.0% | `STK` |
| `home_team` | text | Tên chuẩn của đội nhà | chuỗi | Định danh | 2000/01 | 0.0% | `Stoke` |
| `away_club_id` | text | Mã 3 chữ cái của đội khách | `ARS`, `MCI`, … | Định danh | 2000/01 | 0.0% | `WBA` |
| `away_team` | text | Tên chuẩn của đội khách | chuỗi | Định danh | 2000/01 | 0.0% | `West Brom` |
| `status` | text | `played` = đã đá, `upcoming` = sắp đá / bị hoãn | played / upcoming | Kỹ thuật | 2000/01 | 0.0% | `played` |
| `provisional` | bool | True nếu kết quả tạm lấy từ Understat, chờ football-data | bool | Kỹ thuật | 2000/01 | 0.0% | `False` |
| `postponed` | bool | True nếu trận sắp đá đang bị hoãn | bool | Kỹ thuật | 2000/01 | 0.0% | `False` |
| `home_elo` | float | Elo của đội nhà trước trận | điểm | Trước trận | 2000/01 | 0.0% | `1456.204` |
| `away_elo` | float | Elo của đội khách trước trận | điểm | Trước trận | 2000/01 | 0.0% | `1494.361` |
| `elo_diff` | float | `home_elo − away_elo` | điểm | Trước trận | 2000/01 | 0.0% | `-38.157` |
| `elo_home_win_prob` | float | Điểm kỳ vọng của đội nhà theo Elo (đã cộng 65 điểm sân nhà): thắng + ½ hòa | 0 … 1 | Trước trận | 2000/01 | 0.0% | `0.539` |
| `home_position` | int | Vị trí của đội nhà trên bảng xếp hạng trước trận (điểm → hiệu số → bàn thắng); trống trước trận đầu tiên của mùa | 1 … 20 | Trước trận | 2000/01 | 2.7% | `13` |
| `away_position` | int | Vị trí của đội khách trên bảng xếp hạng trước trận (điểm → hiệu số → bàn thắng); trống trước trận đầu tiên của mùa | 1 … 20 | Trước trận | 2000/01 | 2.7% | `7` |
| `home_form` | text | Kết quả 5 trận gần nhất của đội nhà, cũ → mới | `WWDLW` | Trước trận | 2000/01 | 0.2% | `DLWWD` |
| `home_wins5` | int | Số trận thắng trong 5 trận gần nhất của đội nhà | 0 … 5 | Trước trận | 2000/01 | 0.2% | `2` |
| `home_draws5` | int | Số trận hòa trong 5 trận gần nhất của đội nhà | 0 … 5 | Trước trận | 2000/01 | 0.2% | `2` |
| `home_losses5` | int | Số trận thua trong 5 trận gần nhất của đội nhà | 0 … 5 | Trước trận | 2000/01 | 0.2% | `1` |
| `home_points5` | float | Điểm trung bình/trận, 5 trận gần nhất của đội nhà | 0 … 3 | Trước trận | 2000/01 | 0.2% | `1.6` |
| `home_goals_for5` | float | Bàn thắng trung bình/trận, 5 trận gần nhất của đội nhà | bàn | Trước trận | 2000/01 | 0.2% | `2.0` |
| `home_goals_against5` | float | Bàn thua trung bình/trận, 5 trận gần nhất của đội nhà | bàn | Trước trận | 2000/01 | 0.2% | `1.4` |
| `home_shots_on_target5` | float | Sút trúng đích trung bình/trận, 5 trận gần nhất của đội nhà | cú sút | Trước trận | 2000/01 | 0.2% | `6.0` |
| `home_xg5` | float | xG trung bình/trận, 5 trận gần nhất của đội nhà | xG | Trước trận | 2014/15 | 53.3% | `1.183` |
| `home_xga5` | float | xG để thủng (xGA) trung bình/trận, 5 trận gần nhất của đội nhà | xG | Trước trận | 2014/15 | 53.3% | `0.769` |
| `home_opp_elo5` | float | Elo trung bình (trước trận) của 5 đối thủ gần nhất của đội nhà: lịch đấu khó hay dễ | điểm | Trước trận | 2000/01 | 0.2% | `1462.147` |
| `home_perf_vs_elo5` | float | Phong độ đã tính sức mạnh đối thủ: trung bình (kết quả − điểm kỳ vọng Elo) 5 trận gần nhất của đội nhà; > 0 = đá tốt hơn Elo dự đoán | −1 … 1 | Trước trận | 2000/01 | 0.2% | `-0.075` |
| `home_possession5` | float | Kiểm soát bóng trung bình, 5 trận gần nhất của đội nhà | % | Trước trận | 2010/11 | 38.3% | `59.6` |
| `home_pass_accuracy5` | float | Tỉ lệ chuyền chính xác trung bình, 5 trận gần nhất của đội nhà | % | Trước trận | 2011/12 | 59.5% | `82.72` |
| `home_big_chances5` | float | Tình huống ngon ăn tạo ra trung bình/trận, 5 trận gần nhất của đội nhà | lần | Trước trận | 2011/12 | 59.5% | `1.0` |
| `home_big_chances_against5` | float | Tình huống ngon ăn để đối thủ tạo ra trung bình/trận, 5 trận gần nhất của đội nhà | lần | Trước trận | 2011/12 | 59.5% | `2.0` |
| `home_box_shots5` | float | Sút trong vòng cấm trung bình/trận, 5 trận gần nhất của đội nhà | cú sút | Trước trận | 2011/12 | 59.5% | `12.0` |
| `home_box_shots_against5` | float | Cú sút trong vòng cấm phải nhận trung bình/trận, 5 trận gần nhất của đội nhà | cú sút | Trước trận | 2011/12 | 59.5% | `6.8` |
| `home_box_touches5` | float | Chạm bóng trong vòng cấm đối phương trung bình/trận, 5 trận gần nhất của đội nhà | lần | Trước trận | 2013/14 | 76.3% | `32.0` |
| `home_box_touches_against5` | float | Số lần đối thủ chạm bóng trong vòng cấm của đội nhà, trung bình 5 trận gần nhất | lần | Trước trận | 2013/14 | 76.3% | `14.667` |
| `home_xgot5` | float | xGOT (FotMob) trung bình/trận, 5 trận gần nhất của đội nhà | xG | Trước trận | 2018/19 | 76.2% | `0.836` |
| `home_xgot_against5` | float | xGOT phải nhận trung bình/trận, 5 trận gần nhất của đội nhà | xG | Trước trận | 2018/19 | 76.2% | `2.262` |
| `home_home_points5` | float | Điểm trung bình 5 trận **sân nhà** gần nhất của đội nhà | 0 … 3 | Trước trận | 2000/01 | 0.5% | `1.8` |
| `home_home_goals_for5` | float | Bàn thắng trung bình 5 trận sân nhà gần nhất của đội nhà | bàn | Trước trận | 2000/01 | 0.5% | `0.6` |
| `home_home_goals_against5` | float | Bàn thua trung bình 5 trận sân nhà gần nhất của đội nhà | bàn | Trước trận | 2000/01 | 0.5% | `0.2` |
| `home_played` | int | Số trận đội nhà đã đá trong mùa trước trận này | 0 … 37 | Trước trận | 2000/01 | 0.0% | `7` |
| `home_ppg` | float | Điểm/trận của đội nhà từ đầu mùa | 0 … 3 | Trước trận | 2000/01 | 2.7% | `1.25` |
| `home_goals_for_pg` | float | Bàn thắng/trận của đội nhà từ đầu mùa | bàn | Trước trận | 2000/01 | 2.7% | `1.125` |
| `home_goals_against_pg` | float | Bàn thua/trận của đội nhà từ đầu mùa | bàn | Trước trận | 2000/01 | 2.7% | `1.25` |
| `home_rest_days` | int | Số ngày nghỉ của đội nhà kể từ trận Premier League trước | ngày | Trước trận | 2000/01 | 0.2% | `7` |
| `away_form` | text | Kết quả 5 trận gần nhất của đội khách, cũ → mới | `WWDLW` | Trước trận | 2000/01 | 0.3% | `DLLWW` |
| `away_wins5` | int | Số trận thắng trong 5 trận gần nhất của đội khách | 0 … 5 | Trước trận | 2000/01 | 0.3% | `2` |
| `away_draws5` | int | Số trận hòa trong 5 trận gần nhất của đội khách | 0 … 5 | Trước trận | 2000/01 | 0.3% | `1` |
| `away_losses5` | int | Số trận thua trong 5 trận gần nhất của đội khách | 0 … 5 | Trước trận | 2000/01 | 0.3% | `2` |
| `away_points5` | float | Điểm trung bình/trận, 5 trận gần nhất của đội khách | 0 … 3 | Trước trận | 2000/01 | 0.3% | `1.4` |
| `away_goals_for5` | float | Bàn thắng trung bình/trận, 5 trận gần nhất của đội khách | bàn | Trước trận | 2000/01 | 0.3% | `1.4` |
| `away_goals_against5` | float | Bàn thua trung bình/trận, 5 trận gần nhất của đội khách | bàn | Trước trận | 2000/01 | 0.3% | `1.2` |
| `away_shots_on_target5` | float | Sút trúng đích trung bình/trận, 5 trận gần nhất của đội khách | cú sút | Trước trận | 2000/01 | 0.3% | `3.2` |
| `away_xg5` | float | xG trung bình/trận, 5 trận gần nhất của đội khách | xG | Trước trận | 2014/15 | 53.3% | `1.474` |
| `away_xga5` | float | xG để thủng (xGA) trung bình/trận, 5 trận gần nhất của đội khách | xG | Trước trận | 2014/15 | 53.3% | `1.335` |
| `away_opp_elo5` | float | Elo trung bình (trước trận) của 5 đối thủ gần nhất của đội khách: lịch đấu khó hay dễ | điểm | Trước trận | 2000/01 | 0.3% | `1491.746` |
| `away_perf_vs_elo5` | float | Phong độ đã tính sức mạnh đối thủ: trung bình (kết quả − điểm kỳ vọng Elo) 5 trận gần nhất của đội khách; > 0 = đá tốt hơn Elo dự đoán | −1 … 1 | Trước trận | 2000/01 | 0.3% | `0.025` |
| `away_possession5` | float | Kiểm soát bóng trung bình, 5 trận gần nhất của đội khách | % | Trước trận | 2010/11 | 38.4% | `40.4` |
| `away_pass_accuracy5` | float | Tỉ lệ chuyền chính xác trung bình, 5 trận gần nhất của đội khách | % | Trước trận | 2011/12 | 59.4% | `76.66` |
| `away_big_chances5` | float | Tình huống ngon ăn tạo ra trung bình/trận, 5 trận gần nhất của đội khách | lần | Trước trận | 2011/12 | 59.4% | `2.0` |
| `away_big_chances_against5` | float | Tình huống ngon ăn để đối thủ tạo ra trung bình/trận, 5 trận gần nhất của đội khách | lần | Trước trận | 2011/12 | 59.4% | `3.0` |
| `away_box_shots5` | float | Sút trong vòng cấm trung bình/trận, 5 trận gần nhất của đội khách | cú sút | Trước trận | 2011/12 | 59.4% | `8.6` |
| `away_box_shots_against5` | float | Cú sút trong vòng cấm phải nhận trung bình/trận, 5 trận gần nhất của đội khách | cú sút | Trước trận | 2011/12 | 59.4% | `11.4` |
| `away_box_touches5` | float | Chạm bóng trong vòng cấm đối phương trung bình/trận, 5 trận gần nhất của đội khách | lần | Trước trận | 2013/14 | 76.3% | `16.667` |
| `away_box_touches_against5` | float | Số lần đối thủ chạm bóng trong vòng cấm của đội khách, trung bình 5 trận gần nhất | lần | Trước trận | 2013/14 | 76.3% | `35.333` |
| `away_xgot5` | float | xGOT (FotMob) trung bình/trận, 5 trận gần nhất của đội khách | xG | Trước trận | 2018/19 | 76.2% | `1.662` |
| `away_xgot_against5` | float | xGOT phải nhận trung bình/trận, 5 trận gần nhất của đội khách | xG | Trước trận | 2018/19 | 76.2% | `1.634` |
| `away_away_points5` | float | Điểm trung bình 5 trận **sân khách** gần nhất của đội khách | 0 … 3 | Trước trận | 2000/01 | 0.5% | `0.2` |
| `away_away_goals_for5` | float | Bàn thắng trung bình 5 trận sân khách gần nhất của đội khách | bàn | Trước trận | 2000/01 | 0.5% | `0.4` |
| `away_away_goals_against5` | float | Bàn thua trung bình 5 trận sân khách gần nhất của đội khách | bàn | Trước trận | 2000/01 | 0.5% | `2.4` |
| `away_played` | int | Số trận đội khách đã đá trong mùa trước trận này | 0 … 37 | Trước trận | 2000/01 | 0.0% | `7` |
| `away_ppg` | float | Điểm/trận của đội khách từ đầu mùa | 0 … 3 | Trước trận | 2000/01 | 2.7% | `1.875` |
| `away_goals_for_pg` | float | Bàn thắng/trận của đội khách từ đầu mùa | bàn | Trước trận | 2000/01 | 2.7% | `1.5` |
| `away_goals_against_pg` | float | Bàn thua/trận của đội khách từ đầu mùa | bàn | Trước trận | 2000/01 | 2.7% | `1.25` |
| `away_rest_days` | int | Số ngày nghỉ của đội khách kể từ trận Premier League trước | ngày | Trước trận | 2000/01 | 0.3% | `5` |
| `h2h_matches` | int | Số lần hai đội gặp nhau được tính (tối đa 5 lần gần nhất) | 0 … 5 | Trước trận | 2000/01 | 0.0% | `5` |
| `h2h_home_points` | float | Điểm trung bình của đội nhà trong các lần gặp đó | 0 … 3 | Trước trận | 2000/01 | 8.1% | `0.8` |
| `h2h_home_goal_diff` | float | Hiệu số trung bình của đội nhà trong các lần gặp đó | bàn | Trước trận | 2000/01 | 8.1% | `-0.8` |
| `odds_home_prob` | float | Xác suất đội nhà thắng ngụ ý từ kèo mở (trung bình thị trường, thiếu thì Bet365), đã bỏ biên nhà cái | 0 … 1 | Trước trận | 2002/03 | 7.7% | `0.435` |
| `odds_draw_prob` | float | Xác suất hòa ngụ ý từ kèo mở (trung bình thị trường, thiếu thì Bet365), đã bỏ biên nhà cái | 0 … 1 | Trước trận | 2002/03 | 7.7% | `0.276` |
| `odds_away_prob` | float | Xác suất đội khách thắng ngụ ý từ kèo mở (trung bình thị trường, thiếu thì Bet365), đã bỏ biên nhà cái | 0 … 1 | Trước trận | 2002/03 | 7.7% | `0.288` |
| `position_diff` | int | `home_position − away_position` (âm = đội nhà xếp trên) | −19 … 19 | Trước trận | 2000/01 | 2.8% | `2` |
| `diff_points5` | float | `home_points5 − away_points5` |  | Trước trận | 2000/01 | 0.4% | `0.0` |
| `diff_goals_for5` | float | `home_goals_for5 − away_goals_for5` |  | Trước trận | 2000/01 | 0.4% | `-0.4` |
| `diff_goals_against5` | float | `home_goals_against5 − away_goals_against5` |  | Trước trận | 2000/01 | 0.4% | `-0.4` |
| `diff_shots_on_target5` | float | `home_shots_on_target5 − away_shots_on_target5` |  | Trước trận | 2000/01 | 0.4% | `1.0` |
| `diff_xg5` | float | `home_xg5 − away_xg5` |  | Trước trận | 2014/15 | 53.4% | `0.284` |
| `diff_xga5` | float | `home_xga5 − away_xga5` |  | Trước trận | 2014/15 | 53.4% | `-0.311` |
| `diff_ppg` | float | `home_ppg − away_ppg` |  | Trước trận | 2000/01 | 2.8% | `-0.571` |
| `diff_perf_vs_elo5` | float | `home_perf_vs_elo5 − away_perf_vs_elo5` |  | Trước trận | 2000/01 | 0.4% | `-0.012` |
| `diff_possession5` | float | `home_possession5 − away_possession5` |  | Trước trận | 2010/11 | 38.8% | `-21.6` |
| `diff_big_chances5` | float | `home_big_chances5 − away_big_chances5` |  | Trước trận | 2011/12 | 60.6% | `-0.6` |
| `diff_big_chances_against5` | float | `home_big_chances_against5 − away_big_chances_against5` |  | Trước trận | 2011/12 | 60.6% | `-0.4` |
| `diff_box_touches5` | float | `home_box_touches5 − away_box_touches5` |  | Trước trận | 2019/20 | 78.0% | `10.6` |
| `diff_xgot5` | float | `home_xgot5 − away_xgot5` |  | Trước trận | 2019/20 | 76.5% | `-0.122` |
| `diff_xgot_against5` | float | `home_xgot_against5 − away_xgot_against5` |  | Trước trận | 2019/20 | 76.5% | `-1.274` |
| `home_goals` | int | Số bàn thắng cả trận của đội nhà | bàn | Mục tiêu | 2000/01 | 0.0% | `0` |
| `away_goals` | int | Số bàn thắng cả trận của đội khách | bàn | Mục tiêu | 2000/01 | 0.0% | `0` |
| `result` | text | Kết quả cả trận: H = đội nhà thắng, D = hòa, A = đội khách thắng | H / D / A | Mục tiêu | 2000/01 | 0.0% | `D` |

### Elo
Bắt đầu 1500 (mùa 2000/01), đội mới lên hạng 1450; sau mỗi trận đội nhà nhận
Δ = 20 × hệ số cách biệt × (kết quả − E), đội khách nhận −Δ, với kết quả = 1 / 0,5 / 0 và
E = 1 / (1 + 10^((Elo_khách − Elo_nhà − 65) / 400)); hệ số cách biệt = 1 (≤1 bàn), 1,5 (2 bàn),
(11 + cách biệt) / 8 (≥3 bàn). Đầu mỗi mùa Elo được kéo 20 % về 1500.

## 3. Lưu ý khi sử dụng
- Chia train/test **theo thời gian**, không chia ngẫu nhiên.
- Nên bỏ mùa 2000/01 khi huấn luyện (Elo, phong độ chưa có lịch sử).
- xG chỉ có từ 2014/15; kèo chốt và giờ đá từ 2019/20; sân, HLV, sơ đồ từ 2012/13 đến 2025/26.
- FotMob (thư mục `raw_data/sofascore`) có từ 2010/11 (mùa này thiếu 111 trận): đến 2015/16 chỉ có kiểm soát bóng;
  tình huống ngon ăn, sút trong vòng cấm, chuyền, tắc bóng… từ 2016/17; xG, xGOT FotMob từ 2020/21;
  chạm bóng trong vòng cấm đủ từ 2024/25 (khoảng 70–90 % các mùa 2020/21–2023/24). Các đặc trưng `*5` từ FotMob
  trống khi 5 trận trước không có số liệu – nên huấn luyện mô hình dùng chúng trên dữ liệu từ 2016/17.
- `provisional = True`: kết quả tạm (chỉ có tỉ số + xG), sẽ được thay khi football-data cập nhật.
- Quy tắc làm sạch và mọi ô đã sửa: `data/clean/cleaning_log.csv`, `clean_report.json`.
