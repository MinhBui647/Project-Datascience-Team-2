# Thu thập và tiền xử lý dữ liệu – Premier League

Tài liệu này tóm tắt công dụng của 5 file Python dùng để thu thập (crawl) và tiền xử lý dữ liệu Premier League, phục vụ bài toán **dự đoán tỉ số và xác suất Thắng/Hòa/Thua** của các trận sắp diễn ra.

## Tổng quan

| File | Công dụng | Kết quả chính |
|---|---|---|
| `crawl_epl_season.py` | Crawl thống kê tổng hợp của cả mùa (cầu thủ, đội, top ghi bàn, giá trị đội hình) | `data/<mùa>/*.csv` (11 file mỗi mùa) |
| `fetch_fixtures.py` | Lấy lịch thi đấu của mùa và xác định vòng tiếp theo | `data/<mùa>/fixtures.csv`, `next_round.csv` |
| `update_epl_final.py` | Cập nhật kết quả từng trận vào `epl_final.csv` (không thêm trùng), rồi làm mới vòng tiếp theo | `data/epl_final.csv`, `data/features/*.csv` |
| `build_features.py` | Tính bộ thuộc tính đầy đủ (51 thuộc tính) cho từng trận | `data/features/match_features.csv`, `next_round_features.csv` |
| `build_dataset.py` | Chạy toàn bộ quy trình và gộp thành **một file dataset gọn** để huấn luyện và dự đoán | `data/epl_dataset.csv` |

### Luồng xử lý

```
 Nguồn dữ liệu                     Script                          Kết quả
 ─────────────                     ──────                          ───────
 Understat, ESPN,        ──►  crawl_epl_season.py   ──►  data/<mùa>/  (thống kê theo mùa)
 Transfermarkt (bản mở)                                       │
                                                              │ transfermarkt_clubs.csv
 football-data.co.uk     ──►  update_epl_final.py   ──►  data/epl_final.csv  (kết quả từng trận)
                                                              │
 Understat (lịch, xG)    ──►  fetch_fixtures.py     ──►  lịch thi đấu và vòng tiếp theo
                                                              │
                                                              ▼
                              build_features.py     ──►  data/features/  (51 thuộc tính)
                                                              │
                                                              ▼
                              build_dataset.py      ──►  data/epl_dataset.csv  (31 cột, file cuối)
```

### Cách chạy hằng tuần (sau mỗi vòng đấu)

```bash
python crawler_pipeline/build_dataset.py        # cập nhật kết quả mới, tính thuộc tính, cập nhật epl_dataset.csv
python crawler_pipeline/update_epl_final.py     # (tùy chọn) làm mới data/features/ nếu dùng bộ 51 thuộc tính
python crawler_pipeline/crawl_epl_season.py 2026   # (tùy chọn) cập nhật thống kê tổng hợp của mùa đang diễn ra
```

Cài thư viện (chỉ cần một lần): `pip install requests pandas lxml`

---

## 1. `crawl_epl_season.py` – Crawl thống kê theo mùa

**Công dụng:** crawl số liệu tổng hợp của một mùa giải và lưu vào `data/<YYYY_YY>/`. Cấu trúc 11 file giống hệt các mùa cũ (ví dụ `data/2024_25/`).

**Cách dùng:**
```bash
python crawl_epl_season.py 2025 2026   # tham số là năm bắt đầu mùa: 2025 = 2025/26
```

**Nguồn và file tạo ra:**

| Nguồn | File | Nội dung |
|---|---|---|
| Understat (API `getLeagueData`) | `understat.csv` | Thống kê từng cầu thủ: bàn thắng, kiến tạo, xG, xA, cú sút, key passes, thẻ phạt… |
| | `understat_table.csv` | Bảng xếp hạng kèm xG, xGA, xPTS |
| | `club_players.csv`, `club_squads.csv` | Cầu thủ theo từng CLB, sắp xếp theo số phút thi đấu |
| ESPN (trang thống kê) | `espn_scoring_0.csv`, `espn_scoring_1.csv` | Top 50 ghi bàn, top 50 kiến tạo |
| | `espn_performance_0/1/2.csv` | Kỷ lục trận đấu, chuỗi trận, số khán giả |
| Transfermarkt (bộ dữ liệu mở `dcaribou/transfermarkt-datasets`) | `transfermarkt_clubs.csv`, `transfermarkt_squads.csv` | Giá trị đội hình và từng cầu thủ |

**Lưu ý:**
- Trang Transfermarkt có CAPTCHA, nên script dùng bộ dữ liệu mở thay thế. Bộ này chỉ cập nhật đến 06/2026, vì vậy mùa 2026/27 **chưa có** 2 file Transfermarkt.
- Chạy lại sẽ ghi đè các file trong folder của mùa đó.

---

## 2. `fetch_fixtures.py` – Lịch thi đấu và vòng tiếp theo

**Công dụng:** lấy toàn bộ 380 trận của mùa từ Understat, gồm cả trận đã đá và trận sắp đá. Sau đó gán số vòng và xác định **vòng tiếp theo chưa đá**.

**Cách dùng:**
```bash
python fetch_fixtures.py          # mùa hiện tại (2026/27)
python fetch_fixtures.py 2025     # mùa khác
```

**File tạo ra:**
- `data/<mùa>/fixtures.csv`: 380 trận, gồm vòng, giờ đá, tỉ số và xG nếu đã đá, cùng dự báo Thắng/Hòa/Thua của Understat.
- `data/<mùa>/next_round.csv`: các trận của vòng tiếp theo.

**Xử lý chính:**
- Đổi tên đội của Understat về cách viết trong `epl_final.csv`, ví dụ "Manchester City" → "Man City" (bảng `UNDERSTAT_TO_FD`).
- Vòng tiếp theo = các trận chưa đá gần nhất, mỗi đội chỉ xuất hiện một lần, trong khoảng tối đa 6 ngày. Cách này không bị lệch khi có trận bị hoãn.
- Các script khác dùng lại hàm `fetch_season_matches()` và `next_round()` của file này.

---

## 3. `update_epl_final.py` – Cập nhật kết quả từng trận

**Công dụng:** cập nhật `data/epl_final.csv` (mỗi dòng một trận, từ mùa 2000/01) từ football-data.co.uk, cũng là nguồn gốc của file này. Sau đó làm mới dữ liệu của vòng tiếp theo.

**Cách dùng:**
```bash
python update_epl_final.py               # mùa hiện tại, rồi làm mới vòng tiếp theo
python update_epl_final.py 2024 2025     # các mùa chỉ định
python update_epl_final.py --all         # tất cả các mùa từ 2000/01
python update_epl_final.py --no-refresh  # chỉ cập nhật epl_final.csv
```

**Xử lý chính:**
1. **Chuẩn hóa:** đổi tên cột gốc sang tên cột của `epl_final.csv` (ví dụ `FTHG` → `FullTimeHomeGoals`, `HS` → `HomeShots`) và đổi ngày sang `YYYY-MM-DD`.
2. **Chống trùng lặp:** mỗi trận nhận diện bằng khóa `Season + MatchDate + HomeTeam + AwayTeam`. Trận đã có thì bỏ qua, chỉ thêm trận mới. Trước khi ghi, script sao lưu file cũ vào `epl_final_backup.csv`.
3. **Làm mới vòng tiếp theo:** xóa `next_round_features.csv` cũ rồi gọi `build_features.py` để tạo lại theo lịch mới nhất. Bước này luôn chạy, kể cả khi không có trận mới, vì lịch có thể thay đổi.

---

## 4. `build_features.py` – Tính thuộc tính cho từng trận

**Công dụng:** tính **51 thuộc tính** cho mỗi trận. Mọi thuộc tính chỉ dùng dữ liệu của **các trận trước đó**, nên không bị rò rỉ dữ liệu (data leakage).

**Cách dùng:**
```bash
python build_features.py
```

**Đầu vào:** `epl_final.csv`, xG từng trận của Understat, `data/*/transfermarkt_clubs.csv`.

**File tạo ra:**
- `data/features/match_features.csv`: các trận đã đá, gồm thuộc tính và kết quả. Dùng để huấn luyện.
- `data/features/next_round_features.csv`: các trận vòng tiếp theo, chỉ có thuộc tính. Dùng để dự đoán.
- `data/understat_matches.csv`: bộ nhớ đệm xG từng trận từ mùa 2014/15. Lần chạy sau chỉ tải lại mùa hiện tại.

**Các nhóm thuộc tính:**

| Nhóm | Mô tả |
|---|---|
| Elo (`add_elo`) | Điểm sức mạnh, cập nhật sau mỗi trận. K = 20, lợi thế sân nhà 65 điểm, có tính hệ số cách biệt bàn thắng. Đầu mùa Elo được kéo 20% về 1500. Đội mới lên hạng bắt đầu với 1450 |
| Phong độ 5 trận (`add_team_form`) | Trung bình bàn thắng, bàn thua, điểm, cú sút, sút trúng đích, phạt góc, xG, xGA |
| Phong độ theo sân | 5 trận sân nhà gần nhất của đội nhà, 5 trận sân khách gần nhất của đội khách |
| Từ đầu mùa | Số trận đã đá, điểm/trận, hiệu số/trận, số ngày nghỉ |
| Đối đầu (`add_h2h`) | Điểm và hiệu số trung bình của đội nhà trong 5 lần gặp gần nhất |
| Giá trị đội hình (`add_squad_value`) | Giá trị so với trung vị của giải. Nếu mùa hiện tại chưa có thì lấy mùa trước |
| Hiệu số `diff_*` | Chỉ số của đội nhà trừ chỉ số của đội khách |

Tên đội của Transfermarkt được đổi về cách viết của `epl_final.csv` qua bảng `TM_TO_FD`.

---

## 5. `build_dataset.py` – Dataset cuối cùng (file chính)

**Công dụng:** chạy toàn bộ quy trình bằng **một lệnh** (thu thập → chuẩn hóa → tính thuộc tính), rồi xuất ra **một file duy nhất**, chỉ giữ các cột cần cho bài toán dự đoán.

**Cách dùng:**
```bash
python build_dataset.py              # cập nhật sau mỗi vòng
python build_dataset.py --rebuild    # tính lại toàn bộ (khi sửa cách tính thuộc tính)
```

**File tạo ra:** `data/epl_dataset.csv`, mỗi dòng một trận, 31 cột viết dạng `snake_case`.

| Nhóm | Cột |
|---|---|
| Định danh | `season`, `date`, `home_team`, `away_team`, `status` |
| Biến mục tiêu | `home_goals`, `away_goals` (dự đoán tỉ số), `result` = H/D/A (dự đoán Thắng/Hòa/Thua) |
| Sức mạnh đội | `home_elo`, `away_elo`, `elo_diff`, `squad_value_log_ratio` |
| Phong độ (cho cả `home_` và `away_`) | `goals_for_avg5`, `goals_against_avg5`, `points_avg5`, `shots_on_target_avg5`, `xg_avg5`, `xga_avg5`, phong độ theo sân, `season_ppg`, `rest_days` |
| Đối đầu | `h2h_home_ppg` |

**Cột `status`:**
- `played`: trận đã đá, có kết quả. Dùng để **huấn luyện**.
- `upcoming`: trận của vòng tiếp theo, chưa có kết quả. Dùng để **dự đoán**.

**Chống trùng lặp:** trận đã đá có trong file thì giữ nguyên, chỉ nối thêm trận mới. Các dòng `upcoming` được thay mới mỗi lần chạy, nên trận nào vừa đá xong sẽ chuyển sang `played`.

---

## Lưu ý khi dùng dữ liệu để huấn luyện

- **Chia train/test theo thời gian**, ví dụ train đến mùa 2023/24, test từ 2024/25. Không chia ngẫu nhiên.
- **Bỏ mùa 2000/01** vì Elo chưa ổn định: mọi đội bắt đầu 1500 điểm. Các thuộc tính xG chỉ có từ mùa 2014/15.
- **Giá trị đội hình bị trống** ở một số đội mới lên hạng (mùa 2026/27: Ipswich, Hull, Coventry). Nên dùng mô hình tự xử lý được ô trống (XGBoost, LightGBM) hoặc điền giá trị trước.
- Số ngày nghỉ chỉ tính theo trận Premier League, chưa tính cúp.
- Kết quả thử nghiệm ban đầu với logistic regression (train 2015/16–2023/24, test 2024/25 đến nay): độ chính xác **49,6%**, log loss 1,009. Mốc so sánh "luôn đoán đội nhà thắng" đạt 41,4%.
