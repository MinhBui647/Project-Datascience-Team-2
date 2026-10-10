# Hướng Dẫn & Chiến Lược Crawl: Sofascore (Internal REST API v1)

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
Sofascore là nguồn dữ liệu sâu nhất về **đội hình xuất phát (Lineups)**, **tọa độ sơ đồ chiến thuật** và **thống kê cá nhân từng cầu thủ trong 90 phút**.

* **Nhóm 3: Bối Cảnh & Điều Kiện Trận Đấu**
  * Đội hình ra sân Starting XI: Danh sách 11 cầu thủ đá chính, số áo, vị trí, tọa độ $(x, y)$ trên sân.
  * Sơ đồ chiến thuật: `formation` (ví dụ: `4-3-3`, `3-4-2-1`, `4-2-3-1`).
  * Danh sách cầu thủ dự bị (Bench).
* **Nhóm 5: Thống Kê Cá Nhân Cầu Thủ Trong Trận**
  * `player_rating`: Điểm số thuật toán độc quyền từ 1.0 đến 10.0 cho mỗi cầu thủ sau trận.
  * Thống kê đối kháng: `groundDuelsWonPercentage`, `aerialDuelsWonPercentage`.
  * Khống chế & Tranh chấp: `touches`, `touchesInOppositionBox`, `dribblesSucceeded`, `tacklesWon`, `interceptions`.
  * Cơ hội: `bigChancesCreated`, `bigChancesMissed`.

---

## 2. Hạn Chế Kỹ Thuật & Rào Cản Anti-Bot
1. **Cloudflare WAF:** Sofascore kiểm tra gắt gao User-Agent và TLS Fingerprint. Sử dụng thư viện `requests` mặc định của Python thường bị chặn mã `403 Forbidden`.
2. **Khóa IP theo tần suất:** Nếu gửi quá 30 requests/phút từ 1 địa chỉ IP, server sẽ tạm khóa IP.
3. **Phụ thuộc vào `event_id`:** Muốn cào dữ liệu trận đấu, bắt buộc phải có `event_id` của Sofascore (chuỗi số nguyên duy nhất, ví dụ: `10385732`).

---

## 3. Chiến Lược Thu Thập & Vượt Rào Cản (Bypass Tactics)

### A. Công nghệ khuyến nghị:
* Sử dụng thư viện **`curl_cffi`** (giả lập hoàn hảo TLS fingerprint của trình duyệt Chrome thật):
  ```bash
  pip install curl_cffi
  ```
* Thiết lập Header chuẩn trình duyệt:
  ```python
  headers = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Referer": "https://www.sofascore.com/",
      "Origin": "https://www.sofascore.com",
      "Accept": "*/*",
  }
  ```

### B. Giãn cách an toàn (Adaptive Jitter):
* Đặt khoảng nghỉ ngẫu nhiên `time.sleep(random.uniform(3.0, 5.0))` giữa mỗi trận.
* Lưu snapshot JSON thô vào `raw/sofascore/events/{event_id}_lineups.json` để không bao giờ tải lại lần thứ 2.

---

## 4. Đoạn Mã Mẫu (Crawler Script Template)

```python
import json
import random
import time
from pathlib import Path
from curl_cffi import requests

RAW_DIR = Path("raw/sofascore/events")
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "*/*",
}

def fetch_sofascore_lineups(event_id: int):
    dest = RAW_DIR / f"{event_id}_lineups.json"
    if dest.exists():
        with open(dest, "r", encoding="utf-8") as f:
            return json.load(f)
            
    url = f"https://api.sofascore.com/api/v1/event/{event_id}/lineups"
    # Giả lập Chrome124 TLS fingerprint
    resp = requests.get(url, headers=HEADERS, impersonate="chrome124", timeout=15)
    
    if resp.status_code == 200:
        data = resp.json()
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Successfully fetched lineups for event {event_id}")
        time.sleep(random.uniform(3.0, 4.5))
        return data
    else:
        print(f"Failed to fetch {event_id}, status: {resp.status_code}")
        return None
```

