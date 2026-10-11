# ⚽ Premier League Master Raw Data Ingestion & Cloud Sync

Hệ thống tự động hóa thu thập dữ liệu bóng đá Ngoại Hạng Anh (English Premier League) đa nguồn từ mùa giải **2000/01 đến 2026/27** (27 mùa giải), phục vụ trực tiếp cho bài toán **Trích rút tri thức (Knowledge Extraction)**, **Kỹ nghệ đặc trưng (Feature Engineering)** và **Huấn luyện mô hình Học máy (Machine Learning)**.

---

## 📌 Phân Định Phạm Vi Dự Án (Team Responsibilities)

* **Nhiệm vụ của phân hệ này**: Thu thập toàn diện dữ liệu thô (**Raw Data**) từ 7 nguồn dữ liệu uy tín nhất với độ chi tiết vi mô (tọa độ sút, chỉ số áp sát, đội hình, định giá, kèo cược, thời tiết) và lưu trữ tập trung trên **Hugging Face Dataset Hub**.
* **Phân hệ tiếp theo (Các thành viên khác)**: Kéo dữ liệu từ Hugging Face về để làm sạch, trích rút tri thức và nạp vào cơ sở dữ liệu.

```text
z:\Desktop\Bet/
│
├── 📂 raw_data/                  # [KHO DỮ LIỆU THÔ] Đầy đủ 7 nguồn (Đã gitignore, lưu trên HF)
│   ├── fbref/                   # 16 files  | StatsBomb: Pressures, Progressive Passes, PSxG
│   ├── transfermarkt/           # 15 files  | Định giá 2000-2026, events từng phút, lineups, transfers
│   ├── sofascore/               # 6,146 files| FotMob: Shot map (x,y), rating, formations
│   ├── understat/               # 4,633 files| Understat: Shot map (X,Y), xGChain, xGBuildup
│   ├── fpl_api/                 # 37 files  | Fantasy EPL: ICT Index, Threat, Bonus từng Gameweek
│   ├── open_meteo/              # 30 files  | Thời tiết từng sân & 68k bản ghi khí tượng UK
│   └── football_data_co_uk/     # 32 files  | Kết quả 27 mùa, thẻ phạt, góc, odds nhà cái
│
├── 📂 crawler_pipeline/          # [MÃ NGUỒN CÀO DỮ LIỆU]
│   ├── daily_raw_crawler.py     # Cào gia tăng hàng ngày cho mùa hiện tại (2026/27)
│   ├── crawl_fotmob_historical.py # Crawler lịch sử FotMob
│   ├── crawl_understat_historical.py # Crawler lịch sử Understat
│   └── uploader/                # Module đẩy lên Hugging Face Hub
│
├── 📜 RAW_DATA_CATALOG.md        # [TỪ ĐIỂN DỮ LIỆU] Mô tả chi tiết schema, ý nghĩa cột & cách đọc
├── 📜 crawl_daily.bat            # [CLICK & RUN] Chạy cào dữ liệu mới nhất mùa 2026/27
├── 📜 sync_local_to_cloud.bat    # [CLICK & RUN] Đẩy dữ liệu local raw_data/ lên Hugging Face Hub
├── 📜 sync_cloud_to_local.bat    # [CLICK & RUN] Kéo toàn bộ dữ liệu từ Hugging Face về máy local
├── 📂 .github/workflows/         # GitHub Actions chạy tự động định kỳ 03:00 UTC
└── requirements.txt             # Thư viện Python cần thiết
```

---

## 🚀 3 Luồng Công Việc Chính (Clean Workflows)

### 1. Luồng Cào dữ liệu hàng ngày (Daily Ingestion)
Mỗi ngày sau khi các trận đấu kết thúc, chạy script để cập nhật tỷ số mới nhất, shot map mới, điểm rating và kèo cược của mùa 2026/27:
* **Trên Windows**: Nhấp đúp vào `crawl_daily.bat`
* **Lệnh Terminal**:
  ```bash
  python crawler_pipeline/daily_raw_crawler.py
  ```

### 2. Luồng Đồng bộ Local lên Cloud (Sync Local -> Cloud)
Đẩy toàn bộ các cập nhật từ máy bạn lên kho lưu trữ đám mây Hugging Face:
* **Trên Windows**: Nhấp đúp vào `sync_local_to_cloud.bat`
* **Lệnh Terminal**:
  ```bash
  python sync_local_to_cloud.py
  ```

### 3. Luồng Đồng bộ Cloud về Local (Sync Cloud -> Local)
Dành cho bất kỳ thành viên nào trong nhóm muốn kéo toàn bộ kho dữ liệu thô (~1.8 GB) về máy cá nhân mà không cần chạy lại crawler:
* **Trên Windows**: Nhấp đúp vào `sync_cloud_to_local.bat`
* **Lệnh Terminal**:
  ```bash
  python sync_cloud_to_local.py
  ```

---

## 🔗 Liên Kết & Tài Nguyên
* **Hugging Face Dataset Hub**: [Toibinguz/epl-premier-league-dataset](https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset)
* **Từ điển dữ liệu thô chi tiết**: Xem file [`RAW_DATA_CATALOG.md`](RAW_DATA_CATALOG.md)
