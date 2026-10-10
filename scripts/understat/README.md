# Hướng Dẫn & Chiến Lược Crawl: Understat

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
Understat là nguồn số một về dữ liệu giải thuật Bàn thắng kỳ vọng (xG) và chuỗi tấn công (attacking chains) cho 6 giải VĐQG hàng đầu châu Âu từ năm 2014.

* **Nhóm 4: Thống Kê Diễn Biến & Trận Đấu**
  * `HomeXG`, `AwayXG`: xG tổng của đội nhà và đội khách.
  * `npxG`: xG không tính phạt đền.
  * `xGA`: Bàn thua kỳ vọng.
  * `xGChain`: Tổng xG các chuỗi kiểm soát bóng có đóng góp lối chơi.
  * `xGBuildup`: xG chuỗi triển khai bóng từ tuyến dưới (loại trừ cú sút & kiến tạo).
* **Nhóm 5: Thống Kê Cá Nhân Cầu Thủ Trong Trận**
  * `shotsData`: Tọa độ chính xác $(x, y)$ của từng cú sút, cự ly (mét), góc sút.
  * Loại tình huống (`situation`): Bóng sống (`OpenPlay`), Phạt góc (`FromCorner`), Đá phạt (`SetPiece`), Phạt đền (`Penalty`).
  * Bộ phận dứt điểm (`shotType`): Chân trái, chân phải, đánh đầu.
  * Người kiến tạo (`player_assisted`) và kết quả (`result`: Goal, Saved, Missed, Blocked, HitWoodwork).

---

## 2. Hạn Chế Kỹ Thuật
1. **Dữ liệu nhúng trong Javascript:** Không có REST endpoint chính thức trả về JSON thuần túy. Dữ liệu được mã hóa chuỗi JSON trong thẻ `<script>` (`var shotsData = JSON.parse('...')`). Cần dùng Regular Expressions (Regex) để trích xuất.
2. **Thiếu số liệu phòng ngự & sự kiện:** Không có số liệu tắc bóng, đánh chặn, thẻ phạt, việt vị hay phạt góc.
3. **Phạm vi thời gian:** Chỉ có dữ liệu từ mùa **2014/15 trở lại đây** (5 mùa 2021-2026 hoàn toàn được phủ kín).

---

## 3. Chiến Lược Thu Thập & Giải Mã Payload

### URL mẫu:
* Danh sách trận của mùa: `https://understat.com/league/EPL/<Năm bắt đầu>` (ví dụ: `2021` = mùa 2021/22).
* Chi tiết từng trận đấu: `https://understat.com/match/<match_id>` (ví dụ: `https://understat.com/match/16376`).

### Cơ chế bóc tách an toàn:
1. Gửi request HTTP GET với `headers={"User-Agent": "Mozilla/5.0..."}`.
2. Quét thẻ `<script>` tìm chuỗi `var datesData = JSON.parse('...')` hoặc `var shotsData = JSON.parse('...')`.
3. Giải mã chuỗi unicode escape bằng `bytes(raw, 'utf-8').decode('unicode_escape')`.
4. Parse thành Python dictionary bằng `json.loads()`.
5. Giãn cách request từ $1.0 - 1.5$ giây. Lưu toàn bộ JSON thô vào `raw/understat/`.

---

## 4. Đoạn Mã Mẫu (Crawler Script Template)

```python
import json
import re
import time
import requests
from pathlib import Path

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}
RAW_DIR = Path("raw/understat")
RAW_DIR.mkdir(parents=True, exist_ok=True)

def fetch_season_matches(year: int):
    url = f"https://understat.com/league/EPL/{year}"
    resp = requests.get(url, headers=HEADERS)
    match = re.search(r"datesData\s+=\s+JSON\.parse\('([^']+)'\)", resp.text)
    if not match:
        raise ValueError(f"Cannot parse datesData for year {year}")
    raw = match.group(1).encode("utf-8").decode("unicode_escape")
    matches = json.loads(raw)
    
    out_file = RAW_DIR / f"understat_matches_{year}_{year+1}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(matches)} matches for season {year}/{year+1} to {out_file}")
    return matches

def fetch_match_shots(match_id: str):
    url = f"https://understat.com/match/{match_id}"
    resp = requests.get(url, headers=HEADERS)
    match = re.search(r"shotsData\s+=\s+JSON\.parse\('([^']+)'\)", resp.text)
    if match:
        raw = match.group(1).encode("utf-8").decode("unicode_escape")
        return json.loads(raw)
    return {}
```

