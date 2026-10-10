# Hướng Dẫn & Chiến Lược Thu Thập: Transfermarkt

---

## 1. Vai Trò & Các Nhóm Đặc Trưng Phụ Trách
Transfermarkt là kho thông tin số 1 toàn cầu về tài chính bóng đá, giá trị thị trường và hồ sơ pháp lý/hợp đồng của cầu thủ và CLB.

* **Nhóm 1: Thực Thể Đội Bóng**
  * `market_value_total_eur`: Tổng giá trị định giá của 20 CLB Ngoại Hạng Anh theo từng mùa.
  * `avg_squad_age`: Độ tuổi trung bình của đội hình.
  * `foreign_players_ratio`: Tỷ lệ cầu thủ ngoại binh.
* **Nhóm 2: Thực Thể Cầu Thủ**
  * `market_value_current_eur`: Giá trị thị trường ước tính của từng cầu thủ (€).
  * `highest_market_value_historic`: Giá trị đỉnh cao nhất trong sự nghiệp.
  * `contract_expires`: Ngày đáo hạn hợp đồng hiện tại.
  * `height_cm`, `preferred_foot`, `citizenship`.

---

## 2. Hạn Chế Kỹ Thuật & Rào Cản Web
1. **Cloudflare Turnstile & CAPTCHA cực kỳ khắt khe:** Trang web Transfermarkt cấm triệt để bot tự động. Việc viết scraper cào HTML trực tiếp từng trang cầu thủ rất dễ bị kích hoạt trang thử thách CAPTCHA.
2. **Cập nhật theo kỳ (Periodic Updates):** Giá trị chuyển nhượng không biến động theo từng phút mà chỉ được điều chỉnh lớn sau 2 kỳ chuyển nhượng (Mùa hè tháng 8 và Mùa đông tháng 1).

---

## 3. Chiến Lược Thu Thập Tối Ưu (Zero-Scraping / Open Mirror)

Thay vì cào trực tiếp gây rủi ro khóa IP, ta sử dụng **bộ dữ liệu mở chuẩn quốc tế `dcaribou/transfermarkt-datasets`**:
* Kho dữ liệu này được cập nhật tự động hàng tuần lên GitHub và Kaggle dưới dạng file CSV nén sạch sẽ.
* Đã bao gồm dữ liệu lịch sử đầy đủ từ năm 2012 đến tháng **06/2026** (bao trọn vẹn 5 mùa giải chúng ta cần).

### File chính cần tải:
* `clubs.csv`: Bảng danh mục CLB, giá trị đội hình, SVĐ, HLV.
* `players.csv`: Toàn bộ cầu thủ, vị trí, chiều cao, chân thuận, ngày sinh.
* `player_evaluations.csv`: Lịch sử biến động giá trị thị trường theo thời gian.

---

## 4. Đoạn Mã Mẫu (Automated Downloader Template)

```python
import urllib.request
import pandas as pd
from pathlib import Path

RAW_DIR = Path("raw/transfermarkt")
RAW_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://raw.githubusercontent.com/dcaribou/transfermarkt-datasets/master/data/unprep"

FILES = ["clubs.csv", "players.csv", "player_evaluations.csv"]

def download_transfermarkt_assets():
    for f in FILES:
        url = f"{BASE_URL}/{f}"
        dest = RAW_DIR / f
        print(f"Downloading {f} from open mirror...")
        urllib.request.urlretrieve(url, dest)
        df = pd.read_csv(dest)
        print(f"  -> Saved {len(df)} rows to {dest}")

# Lọc riêng các CLB Ngoại Hạng Anh
def filter_epl_clubs():
    clubs = pd.read_csv(RAW_DIR / "clubs.csv")
    epl_clubs = clubs[clubs["domestic_competition_id"] == "GB1"]
    epl_clubs.to_csv(RAW_DIR / "epl_clubs_filtered.csv", index=False)
    print(f"Found {len(epl_clubs)} EPL club records.")
```

