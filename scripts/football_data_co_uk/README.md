# Hướng Dẫn & Chiến Lược Crawl: Football-Data.co.uk

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
`Football-Data.co.uk` là nguồn dữ liệu nền tảng (Baseline) cung cấp kết quả trận đấu, thống kê kỹ thuật cơ bản và toàn bộ dữ liệu thị trường cá cược lịch sử qua file CSV tĩnh từ năm 1993 đến nay.

* **Nhóm 4: Thống Kê Diễn Biến & Trận Đấu**
  * Tỷ số hiệp 1 và cả trận: `FTHG`, `FTAG`, `FTR`, `HTHG`, `HTAG`, `HTR`.
  * Thống kê sút: `HS`, `AS` (Tổng sút), `HST`, `AST` (Sút trúng đích).
  * Kỷ luật & Tranh chấp: `HF`, `AF` (Lỗi), `HY`, `AY` (Thẻ vàng), `HR`, `AR` (Thẻ đỏ), `HC`, `AC` (Phạt góc).
  * Trọng tài: `Referee`.
* **Nhóm 7: Thị Trường Cá Cược & Tỷ Lệ Kèo (100% Phụ trách)**
  * Kèo 1X2 Mở & Đóng: Bet365 (`B365H, B365D, B365A, B365CH, B365CD, B365CA`), Pinnacle (`PSH, PSD, PSA, PSCH, PSCD, PSCA`), Max, Avg.
  * Kèo Tài/Xỉu 2.5: `B365>2.5`, `B365<2.5`, `P>2.5`, `P<2.5`, `Max>2.5`, `Avg>2.5` (Opening & Closing).
  * Kèo Chấp Châu Á: `AHh`, `AHCh`, `B365AHH`, `B365AHA`, `PAHH`, `PAHA`.

---

## 2. Hạn Chế Kỹ Thuật
1. **Thiếu dữ liệu cầu thủ & sự kiện:** Hoàn toàn không có danh sách đội hình ra sân, cầu thủ ghi bàn, kiến tạo hay thẻ phạt theo từng cá nhân.
2. **Thiếu chỉ số nâng cao:** Không có bàn thắng kỳ vọng (xG), không có tỷ lệ kiểm soát bóng (%) hay số đường chuyền.
3. **Quy ước tên đội bóng:** Dùng tên rút gọn truyền thống của Anh (ví dụ: `Man City`, `Man United`, `Spurs`, `Wolves`, `Nott'm Forest`). Cần ánh xạ qua từ điển chuẩn hóa khi nối với Understat/Transfermarkt.

---

## 3. Chiến Lược Thu Thập & Xử Lý

### Cấu trúc URL 5 mùa gần nhất:
```
Mùa 2021/22: https://www.football-data.co.uk/mmz4281/2122/E0.csv
Mùa 2022/23: https://www.football-data.co.uk/mmz4281/2223/E0.csv
Mùa 2023/24: https://www.football-data.co.uk/mmz4281/2324/E0.csv
Mùa 2024/25: https://www.football-data.co.uk/mmz4281/2425/E0.csv
Mùa 2025/26: https://www.football-data.co.uk/mmz4281/2526/E0.csv
```

### Chiến thuật:
* Tải trực tiếp qua HTTP GET bằng `urllib` hoặc `requests`.
* Xử lý lỗi encoding: File có thể chứa ký tự đặc biệt (`utf-8-sig` hoặc `cp1252`).
* Chuẩn hóa định dạng ngày từ `DD/MM/YYYY` về chuẩn ISO `YYYY-MM-DD`.
* Lưu bản thô vào `raw/football_data_co_uk/` trước khi tách thành các nhóm trong `data/organized/`.

---

## 4. Đoạn Mã Mẫu (Crawler Script Template)

```python
import urllib.request
import pandas as pd
from pathlib import Path

SEASONS = {
    "2021_2022": "2122",
    "2022_2023": "2223",
    "2023_2024": "2324",
    "2024_2025": "2425",
    "2025_2026": "2526",
}
RAW_DIR = Path("raw/football_data_co_uk")
RAW_DIR.mkdir(parents=True, exist_ok=True)

for season_name, code in SEASONS.items():
    url = f"https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
    dest = RAW_DIR / f"epl_{season_name}.csv"
    print(f"Downloading {season_name} from {url}...")
    urllib.request.urlretrieve(url, dest)
    
    # Kiểm tra số dòng
    df = pd.read_csv(dest, encoding="utf-8-sig")
    print(f"  -> Saved {len(df)} matches, {len(df.columns)} columns.")
```

