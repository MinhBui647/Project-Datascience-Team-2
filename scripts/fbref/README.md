# Hướng Dẫn & Chiến Lược Crawl: FBref (Dữ Liệu Opta Nâng Cao)

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
FBref (thuộc hệ thống Sports-Reference, đối tác của Stats Perform / Opta) là nguồn duy nhất cung cấp miễn phí các chỉ số bóng đá hiện đại dạng bảng chi tiết nhất.

* **Nhóm 4: Thống Kê Diễn Biến & Trận Đấu (Chỉ số nâng cao)**
  * `PPDA` (Passes Allowed Per Defensive Action): Đo lường cường độ pressing của 2 đội.
  * `SCA` & `GCA` (Shot-Creating Actions & Goal-Creating Actions): Số hành động tạo cú sút và bàn thắng.
  * `Progressive Passes` & `Progressive Carries`: Đường chuyền và rê bóng tịnh tiến vào 1/3 sân cuối cùng.
  * `PSxG` (Post-Shot Expected Goals): xG theo quỹ đạo bóng (đo lường khả năng cứu thua của thủ môn).
  * `Passes Under Pressure`: Số đường chuyền thực hiện khi đang bị đối phương áp sát.

---

## 2. Hạn Chế Kỹ Thuật & Cảnh Báo Khóa IP
> [!CAUTION]
> **Giới hạn tốc độ cực kỳ nghiêm ngặt:** FBref áp đặt hạn mức tối đa **20 requests / phút** trên mỗi địa chỉ IP.
> Nếu bạn gửi request thứ 21 trong vòng 60 giây, hệ thống sẽ trả về mã lỗi `429 Too Many Requests` và **khóa IP tạm thời trong vòng đúng 1 giờ (60 phút)**.

* Không nên cào đa luồng (`multithreading` / `asyncio`) thiếu kiểm soát.
* Một mùa EPL có 380 trận, do đó cào 380 trang chi tiết trận đấu sẽ mất tối thiểu $380 / 18 \approx 22$ phút.

---

## 3. Chiến Lược Thu Thập An Toàn (Safe Crawling Strategy)

1. **Kiểm Soát Tốc Độ Bằng Jitter:**
   * Cài đặt khoảng dừng bắt buộc `time.sleep(random.uniform(3.5, 5.0))` sau mỗi request. Tốc độ này tương đương khoảng 12 - 15 req/phút (nằm sâu trong vùng an toàn < 20 req/phút).
2. **Bộ Nhớ Đệm Cục Bộ (Local HTML Cache):**
   * Mỗi trang HTML sau khi tải về phải được lưu ngay thành file `.html` vào `raw/fbref/html_cache/{match_id}.html`.
   * Trước khi gửi bất kỳ request nào, luôn kiểm tra xem file HTML đã có trên đĩa chưa. Nếu có rồi, tuyệt đối không tải lại.
3. **Thư Viện Hỗ Trợ:**
   * Có thể dùng thư viện Python chuyên dụng `soccerdata` hoặc `ScraperFC` (đã tích hợp sẵn bộ kiểm soát rate-limit cho FBref).

---

## 4. Đoạn Mã Mẫu (Crawler Script Template)

```python
import random
import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

RAW_DIR = Path("raw/fbref/html_cache")
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch_fbref_match_page(match_id: str, url: str):
    cache_path = RAW_DIR / f"{match_id}.html"
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    print(f"Requesting FBref: {url}...")
    resp = requests.get(url, headers=HEADERS, timeout=15)
    
    if resp.status_code == 200:
        html = resp.text
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(html)
        
        # Giãn cách ngẫu nhiên 3.5 - 5.0 giây để tuyệt đối không bị khóa IP
        delay = random.uniform(3.5, 5.0)
        time.sleep(delay)
        return html
    elif resp.status_code == 429:
        print("[CRITICAL] Hit FBref Rate Limit! IP may be banned for 1 hour. Stopping immediately.")
        raise ConnectionRefusedError("FBref rate limit exceeded.")
    else:
        print(f"Failed with status: {resp.status_code}")
        return None
```

