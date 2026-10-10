# ⚽ Premier League Data Ingestion & Staging Pipeline

Automated multi-source crawler, cross-entity integration, and staging data storage for the **English Premier League** (Seasons 2021/22 – 2026/27).

---

## 📌 Phân định Ranh giới Dự án (Team Responsibility Boundary)

Dự án này là phân hệ **Khâu 1: Thu thập, Tích hợp Thực thể & Lưu trữ Thô/Staging (Data Ingestion & Master Staging)**.

```
z:\Desktop\Bet/
│
├── 📂 crawler_pipeline/          # [PHẠM VI CỦA BẠN] Toàn bộ mã nguồn cào, ánh xạ và upload
│   ├── configs.py               # Cấu hình đường dẫn, URLs, constants
│   ├── extractors/              # Cào từng nguồn (FotMob, Understat, Football-Data, FPL, Weather)
│   ├── integrators/             # Ánh xạ ID CLB, Cầu thủ & ghép nối 9 bảng chuẩn hóa
│   ├── uploader/                # Đồng bộ Hugging Face Hub & Bộ kiểm thử QA 278 bài test
│   └── run.py                   # File điều phối thực thi chính
│
├── 📂 data/                      # [ĐẦU RA BÀN GIAO] Kho dữ liệu chuẩn hóa (Data Contract)
│   ├── 00_master_entities/      # Từ điển 29 CLB và 1.761 cầu thủ liên kết chéo
│   ├── 2021_2022/ ... 2026_2027/# 9 bảng dữ liệu chuẩn hóa theo từng mùa giải
│   └── README.md                # Dataset card định dạng Hugging Face Datasets
│
├── 📂 docs/                      # Tài liệu kỹ thuật chi tiết
│   ├── DATA_DICTIONARY.md       # Từ điển thuộc tính 9 bảng dữ liệu
│   ├── GITHUB_ACTIONS_GUIDE.md  # Hướng dẫn vận hành tự động hóa GitHub Actions
│   └── references/              # Báo cáo nghiên cứu & tài liệu tham khảo
│
├── 📂 .github/workflows/         # Automation Workflow chạy hàng ngày 03:00 UTC
├── requirements.txt             # Danh mục thư viện Python
└── .gitignore                   # Loại trừ file rác, cache và tokens
```

* **Dành cho người phụ trách Làm sạch (Data Cleaning) & Database**:
  - Bạn chỉ cần đọc dữ liệu từ thư mục `data/` (hoặc kéo qua [Hugging Face Dataset](https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset)).
  - Toàn bộ ID của 29 CLB và 1.761 cầu thủ đã được ánh xạ khóa ngoại chuẩn xác 100%.

---

## 🚀 Hướng Dẫn Vận Hành Nhanh (Quick Start)

### 1. Cài đặt môi trường
```bash
pip install -r requirements.txt
```

### 2. Chạy cập nhật dữ liệu mùa giải 2026-2027
```bash
python crawler_pipeline/run.py --season 2026_2027
```

### 3. Chạy cập nhật và đồng bộ trực tiếp lên Hugging Face Hub
```bash
python crawler_pipeline/run.py --season 2026_2027 --sync-hf
```

### 4. Chạy độc lập bộ kiểm định chất lượng (QA Suite 278 tests)
```bash
python crawler_pipeline/uploader/qa_validator.py
```

---

## ☁️ Đồng Bộ Tự Động Hóa (GitHub Actions & Hugging Face)
Pipeline tự động chạy hàng ngày vào lúc **03:00 UTC** qua GitHub Actions.
- **GitHub Repository**: [toibinguz/EPL_Data_Crawler](https://github.com/toibinguz/EPL_Data_Crawler)
- **Hugging Face Dataset**: [Toibinguz/epl-premier-league-dataset](https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset)

