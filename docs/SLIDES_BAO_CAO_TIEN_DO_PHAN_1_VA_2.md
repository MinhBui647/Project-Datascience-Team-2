# BÁO CÁO TIẾN ĐỘ BÀI TẬP LỚN: DỰ ĐOÁN KẾT QUẢ NGOẠI HẠNG ANH (PREMIER LEAGUE)
## NỘI DUNG TRÌNH BÀY: PHẦN 1 & PHẦN 2 (WEB CRAWLING & DATA PREPARATION)

---

### MỤC LỤC BỘ SLIDES

* **Slide 1:** Trang bìa (Tiêu đề đề tài & Thành viên báo cáo)
* **Slide 2:** Tổng quan bài toán & Mục tiêu của giai đoạn 1 & 2
* **Slide 3:** Kiến trúc tổng thể Pipeline dữ liệu (Từ Raw đến Features)
* **[PHẦN 1] Slide 4:** Bức tranh tổng quan 7 nguồn dữ liệu thô (27 mùa giải)
* **[PHẦN 1] Slide 5:** Thách thức kỹ thuật trong Web Crawling & Thu thập đa nguồn
* **[PHẦN 1] Slide 6:** Giải pháp kiến trúc: Chiến thuật A (Archive Bundle) & Season Isolation
* **[PHẦN 1] Slide 7:** Cơ chế Delta Crawl tự động lấy Hugging Face làm Source of Truth
* **[PHẦN 1] Slide 8:** Tự động hóa CI/CD với GitHub Actions (Daily Incremental Crawl)
* **[PHẦN 1] Slide 9:** Tổng kết sản phẩm bàn giao Phần 1 (Lê Trường Giang)
* **[PHẦN 2] Slide 10:** Kiến trúc 6 giai đoạn Tích hợp & Tiền xử lý dữ liệu
* **[PHẦN 2] Slide 11:** Giai đoạn 1 – Tích hợp đa nguồn & Chuẩn hóa thực thể CLB
* **[PHẦN 2] Slide 12:** Giai đoạn 2 – Chuẩn hóa thời gian, trọng tài và sơ đồ chiến thuật
* **[PHẦN 2] Slide 13:** Giai đoạn 3 – Bộ quy tắc làm sạch dữ liệu nghiêm ngặt (Rules R1 – R11)
* **[PHẦN 2] Slide 14:** Giai đoạn 4 – Dựng bảng trận đấu hợp nhất (`matches.csv`)
* **[PHẦN 2] Slide 15:** Giai đoạn 5 – Kỹ thuật tạo 87 đặc trưng trước trận (Feature Engineering)
* **[PHẦN 2] Slide 16:** Cơ chế kiểm định chống rò rỉ dữ liệu tương lai (Data Leakage Protection)
* **[PHẦN 2] Slide 17:** Giai đoạn 6 – Kiểm định chất lượng tự động (108/108 QA Checks)
* **[TỔNG KẾT] Slide 18:** Bàn giao sản phẩm sạch cho các khâu EDA, Link Analysis & Modeling

---

<!-- slide -->
## SLIDE 1: TRANG BÌA (TITLE SLIDE)

### ĐỀ TÀI: THIẾT KẾ HỆ THỐNG DỰ ĐOÁN KẾT QUẢ & TỈ SỐ BÓNG ĐÁ NGOẠI HẠNG ANH (PREMIER LEAGUE)
#### BÁO CÁO TIẾN ĐỘ GIAI ĐOẠN 1: THU THẬP, TÍCH HỢP & TIỀN XỬ LÝ DỮ LIỆU ĐA NGUỒN

* **Đơn vị thực hiện:** Nhóm 5 – Project Data Science
* **Thành viên trình bày:**
  1. **Lê Trường Giang (MSSV: 202416187)** — Người 1: Web Crawling & Thu thập dữ liệu
  2. **Bùi Anh Minh (MSSV: 202400059)** — Người 2: Tích hợp, Chuẩn hóa & Tiền xử lý dữ liệu (Data Integration & Preparation)
* **Kho lưu trữ mã nguồn:** [GitHub Repository](https://github.com/MinhBui647/Project-Datascience-Team-5) (Branch: `toibingu`)
* **Kho dữ liệu đám mây:** [Hugging Face Dataset Hub](https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset)

> **Speaker Notes:**
> *"Kính chào thầy cô và các bạn. Hôm nay, nhóm chúng em xin đại diện trình bày tiến độ 2 phần đầu tiên trong chuỗi bài tập lớn: Phần 1 - Thu thập dữ liệu đa nguồn từ năm 2000 đến nay do bạn Lê Trường Giang phụ trách, và Phần 2 - Quy trình Tích hợp, Làm sạch và Xây dựng bộ đặc trưng huấn luyện Machine Learning do bạn Bùi Anh Minh phụ trách. Đây là nền tảng cốt lõi quyết định tính chính xác và độ tin cậy của toàn bộ các khâu EDA, Link Analysis và Xây dựng mô hình dự đoán phía sau."*

---

<!-- slide -->
## SLIDE 2: TỔNG QUAN BÀI TOÁN & MỤC TIÊU DỰ ÁN

### 1. Mục tiêu bài toán
* Dự đoán kết quả trận đấu Ngoại hạng Anh: **Thắng - Hòa - Thua (1X2)** và **Tỉ số chính xác**.
* Ứng dụng trong phân tích chiến thuật, đánh giá phong độ và dự báo thị trường thể thao.

### 2. Yêu cầu tiên quyết đối với dữ liệu
* **Phạm vi lịch sử dài hạn:** Tối thiểu từ mùa 2000/01 đến mùa giải hiện tại 2026/27 (27 mùa liên tục).
* **Độ sâu thông tin:** Không chỉ dừng lại ở tỉ số cuối trận mà phải bao quát: chỉ số vi mô trận đấu (xG, shotmap, momentum), đội hình, trọng tài, thời tiết và tỷ lệ cược nhà cái.
* **Nguyên tắc "Zero Data Leakage":** Tuyệt đối không để thông tin phát sinh sau khi bóng lăn rò rỉ vào các biến đặc trưng trước trận.

### 3. Mục tiêu hoàn thành của 2 phần đầu
* **Phần 1:** Thu thập trọn vẹn 27 mùa giải từ 7 nguồn, tự động hóa cập nhật hàng ngày bằng CI/CD GitHub Actions trong < 20 giây.
* **Phần 2:** Chuẩn hóa, giải quyết xung đột, xử lý 11 luật làm sạch và sinh ra 2 bảng dữ liệu vàng: `matches.csv` (106 cột) và `features.csv` (102 cột, 87 đặc trưng) đạt 108/108 bài kiểm tra QA.

> **Speaker Notes:**
> *"Mục tiêu của bài toán là dự đoán kết quả và tỉ số các trận đấu bóng đá. Để mô hình máy học không bị thiên kiến, chúng ta không thể chỉ dùng vài mùa giải gần đây hay chỉ dùng tỷ số đơn thuần. Chúng em đặt ra bài toán thu thập liên tục 27 mùa giải từ năm 2000 đến nay với độ sâu dữ liệu cực lớn: từ chỉ số xG từng cú sút, đội hình, chỉ số kiểm soát bóng cho đến tỷ lệ cược nhà cái, đồng thời thiết lập cơ chế kỹ thuật chống rò rỉ thông tin tương lai một cách triệt để."*

---

<!-- slide -->
## SLIDE 3: KIẾN TRÚC TỔNG THỂ PIPELINE DỮ LIỆU

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                      PHẦN 1: WEB CRAWLING & STORAGE (Lê Trường Giang)                            │
│  7 Nguồn Dữ Liệu Raw ──► Strategy A (.tar.gz) ──► Delta Crawler ──► Hugging Face Hub (Cloud SOT) │
└────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                 │
                                                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                   PHẦN 2: DATA INTEGRATION & PREPARATION (Bùi Anh Minh)                          │
│                                                                                                  │
│   [GĐ 1: Integrate]    [GĐ 2: Normalize]    [GĐ 3: Clean]      [GĐ 4: Build]     [GĐ 5: Feature] │
│   Ghép 4 nguồn theo ──► Chuẩn hóa mã ──► 11 Luật làm sạch ──► Dựng bảng      ──► 87 Đặc trưng    │
│   Key (Season,H,A)      CLB, Referee,        (R1 - R11)       `matches.csv`      trước trận      │
│                         sơ đồ chiến thuật    Sân kín COVID    (9.850 x 106)      `features.csv`  │
│                                                                                   (9.850 x 102)  │
└────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                 │
                                                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│               SẢN PHẨM SẠCH BÀN GIAO CHO CÁC KHÂU TIẾP THEO (GĐ 6: Validate)                     │
│  • Bàn giao cho EDA (Người 4) & Link Analysis (Người 5)                                          │
│  • Bàn giao cho Modeling (Người 7), Evaluation (Người 8) & Visualization (Người 6)               │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

> **Speaker Notes:**
> *"Sơ đồ này mô tả bức tranh toàn cảnh của nhóm: Phần 1 thu thập dữ liệu thô từ 7 nguồn, đóng gói tối ưu và đồng bộ lên Hugging Face Hub đóng vai trò làm trung tâm lưu trữ. Phần 2 kéo dữ liệu thô này vào một quy trình 6 giai đoạn khép kín: Tích hợp đa nguồn, Chuẩn hóa mã nhận diện, Làm sạch theo 11 luật định, Xây dựng bảng trận đấu chuẩn `matches.csv` và sinh ra 87 đặc trưng quá khứ trong `features.csv`. Cuối cùng, bộ dữ liệu sạch đạt 108 bài kiểm tra chất lượng sẽ được bàn giao cho các khâu EDA, Link Analysis và Xây dựng mô hình."*

---

<!-- slide -->
## SLIDE 4: [PHẦN 1] TỔNG QUAN 7 NGUỒN DỮ LIỆU THÔ ĐÃ CRAWL (27 MÙA GIẢI)

| STT | Nguồn dữ liệu | Phạm vi thời gian | Kích thước / Định dạng | Nội dung dữ liệu cốt lõi |
| :---: | :--- | :---: | :---: | :--- |
| **1** | **Football-Data.co.uk** | 2000/01 – 2026/27 (27 mùa) | 27 CSVs (3.0 MB) | Tỷ số H1/cả trận, thẻ phạt, góc, trọng tài, tỷ lệ cược nhà cái (Bet365, Pinnacle, AH, O/U). **Nguồn xương sống.** |
| **2** | **Understat** | 2014/15 – 2026/27 (13 mùa) | Archive `.tar.gz` (16.1 MB) + Overview CSV/JSON | 4,620 trận xG, xGA, xác suất thắng/hòa/thua, tọa độ cú sút $(X, Y)$, `xGChain`, `xGBuildup` từng cầu thủ. |
| **3** | **FotMob / Sofascore** | 2010/11 – 2026/27 (17 mùa) | Archive `.tar.gz` (91.7 MB) + Fixtures JSON | 6,029 trận: Shotmap chi tiết (xGOT, góc sút), đội hình (lineup), chấm điểm rating cầu thủ, kiểm soát bóng, momentum. |
| **4** | **Transfermarkt** | 2012/13 – 2025/26 (14 mùa) | 9 gói `.csv.gz` (229.9 MB) | 5,320 trận: Khán giả, sân vận động, trọng tài, HLV, sơ đồ chiến thuật (formation), giá trị đội hình. |
| **5** | **FPL API (Premier League)** | 2016/17 – 2026/27 (11 mùa) | 35 CSV/JSON (49.8 MB) | Điểm Gameweek cầu thủ, chỉ số ICT (Influence, Creativity, Threat), giá thị trường, độ khó lịch thi đấu (FDR). |
| **6** | **FBref (Sports Reference)** | 2010/11 – 2025/26 (16 mùa) | 16 CSVs (283.5 MB) | Thống kê nâng cao: Pressing, đường chuyền tịnh tiến (progressive passes), tạo cơ hội dứt điểm (SCA/GCA). |
| **7** | **Open-Meteo Weather** | 2000 – 2026 (27 năm) | 29 Files (51.2 MB) | Khí tượng học lịch sử (ERA5 Reanalysis 68,481 dòng) và thời tiết tại 25 sân bóng lúc bóng lăn (nhiệt độ, mưa, gió). |

> **Speaker Notes:**
> *"Để phục vụ cho mô hình dự đoán mạnh mẽ nhất, chúng em không dựa vào một nguồn đơn lẻ. Bạn Lê Trường Giang đã xây dựng hệ thống thu thập toàn diện từ 7 nguồn dữ liệu uy tín nhất hiện nay: Football-Data đóng vai trò xương sống cung cấp kết quả và tỷ lệ cược; Understat và FotMob cung cấp số liệu xG và thống kê vi mô chuyên sâu; Transfermarkt cung cấp thông tin chiến thuật và giá trị cầu thủ; FPL API mang lại dữ liệu phong độ theo tuần; cùng dữ liệu nâng cao từ FBref và thời tiết lúc bóng lăn từ Open-Meteo."*

---

<!-- slide -->
## SLIDE 5: [PHẦN 1] CÁC THÁCH THỨC KỸ THUẬT KHI CRAWL ĐA NGUỒN

### 1. Bùng nổ số lượng file nhỏ (Small-file HTTP Bottleneck)
* Cào riêng lẻ từng trận từ Understat và FotMob tạo ra hơn **10,700 file JSON nhỏ** (~50–250 KB mỗi file).
* **Vấn đề phát sinh:** Đẩy 10,700 file rời rạc lên Hugging Face Hub qua giao thức Git LFS gây nghẽn hàng đợi HTTP, mất hơn 4 giờ đồng hồ và thường xuyên bị đứt kết nối mạng (`WinError 10053`).

### 2. Sự bất đối xứng về cấu trúc dữ liệu giữa các nguồn
* **Nguồn tích lũy cả mùa (Cumulative):** Football-Data (`E0.csv`) gom toàn bộ trận từ vòng 1 đến vòng mới nhất vào 1 file duy nhất.
* **Nguồn nguyên tử (Atomic API):** FotMob và Understat chỉ cung cấp API tra cứu theo từng trận đơn lẻ (`match_id`).

### 3. Môi trường thực thi tạm thời trên CI/CD (Ephemeral Runner)
* GitHub Actions chạy trên máy ảo Ubuntu tạm thời: Khi khởi động, đĩa cứng hoàn toàn trống trơn (do dữ liệu thô bị `.gitignore`).
* Nếu kiểm tra trạng thái bằng đĩa local (`if not os.path.exists`), runner sẽ hiểu nhầm là chưa có dữ liệu và tiến hành cào lại toàn bộ từ đầu, gây lãng phí băng thông và nguy cơ bị ban IP.

> **Speaker Notes:**
> *"Khi tiến hành cào dữ liệu thực tế, nhóm đối mặt với 3 thách thức kỹ thuật lớn: Thứ nhất là vấn đề hơn 10.000 file JSON nhỏ khiến tốc độ đồng bộ đám mây cực kỳ chậm và dễ gãy kết nối. Thứ hai là sự không đồng nhất giữa nguồn trả về cả mùa như Football-Data và nguồn chỉ trả về từng trận như FotMob. Thứ ba là làm sao để hệ thống CI/CD tự động hóa của GitHub Actions biết được trận nào đã cào, trận nào chưa cào khi ổ đĩa máy ảo mỗi ngày mở lên đều trống trơn."*

---

<!-- slide -->
## SLIDE 6: [PHẦN 1] GIẢI PHÁP: CHIẾN THUẬT A (ARCHIVE BUNDLE) & SEASON ISOLATION

### 1. Chiến thuật A (Archive Bundling cho dữ liệu lịch sử)
* Đóng gói toàn bộ ~10,700 file JSON trận đấu lịch sử (2010–2026) thành 2 file nén `.tar.gz` nguyên khối:
  * `raw_data/understat/understat_matches_cache.tar.gz` (16.14 MB – nén 4,611 trận).
  * `raw_data/sofascore/sofascore_matches_cache.tar.gz` (91.72 MB – nén 6,081 trận).
* **Hiệu quả:**
  * Giảm dung lượng lưu trữ từ ~1.2 GB xuống **107.8 MB** (giảm hơn 90%).
  * Đồng bộ lên Hugging Face Hub chỉ mất **60 giây** thay vì 4 tiếng. Loại bỏ hoàn toàn lỗi ngắt kết nối socket.

### 2. Cách ly tuyệt đối mùa giải hiện tại (`2026_2027_new/`)
* Mùa giải 2026/27 đang diễn ra được tách thành thư mục độc lập cùng cấp với `raw_data/`.
* Từng trận đấu mới diễn ra được lưu thành từng file JSON riêng biệt:
  * `2026_2027_new/matches/fotmob/{match_id}.json`
  * `2026_2027_new/matches/understat/{match_id}.json`
* **Lợi ích:** Các mùa lịch sử đã chốt sổ được đóng băng an toàn; crawler hàng ngày chỉ tác động vào đúng thư mục mùa 2026/27.

> **Speaker Notes:**
> *"Để giải quyết triệt để vấn đề hàng nghìn file nhỏ, bạn Giang đã triển khai 'Chiến thuật A': Đóng gói toàn bộ các trận đấu lịch sử từ 2010 đến 2026 thành 2 gói nén tar.gz chuẩn mực, giảm dung lượng từ hơn 1 GB xuống đúng 108 MB, giúp upload lên Hugging Face chỉ trong 1 phút. Đồng thời, mùa giải 2026/27 đang thi đấu được cách ly riêng vào thư mục `2026_2027_new`. Trận nào mới diễn ra thì chỉ thêm đúng file JSON của trận đó, tuyệt đối không làm xáo trộn dữ liệu cũ."*

---

<!-- slide -->
## SLIDE 7: [PHẦN 1] CƠ CHẾ DELTA CRAWL LẤY HUGGING FACE LÀM SOURCE OF TRUTH

```
[Mỗi ngày Runner GitHub Actions thức dậy]
                    │
                    ▼
[BƯỚC 1: ĐỐI SOÁT METADATA TRÊN HUGGING FACE] (Thời gian: 0.4s)
• Gọi HF API: `api.list_repo_tree("2026_2027_new/matches/fotmob")`
• Thu được tập hợp ID các trận ĐÃ CÓ TRÊN CLOUD: {ID_1, ID_2, ..., ID_59}
                    │
                    ▼
[BƯỚC 2: QUÉT LỊCH ĐẤU HÔM NAY TỪ NGUỒN] (Thời gian: 1.0s)
• Tải danh sách fixtures FotMob & Understat mùa 2026/27
• Thu được tập hợp các trận ĐÃ HOÀN THÀNH (Finished): {ID_1, ..., ID_60}
                    │
                    ▼
[BƯỚC 3: TÍNH TOÁN HIỆU SỐ DELTA]
• DELTA = Finished_Matches - HF_Matches
• Ví dụ vòng 6: Ra đúng 1 trận vừa đá xong (Coventry 4-1 Newcastle, ID: 5795468)
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
  [Nếu DELTA = 0]       [Nếu CÓ DELTA]
  • Giữa tuần không     • CHỈ CRAWL ĐÚNG 1 FILE JSON CỦA TRẬN ĐÓ:
    có trận mới           `matches/fotmob/5795468.json`
  • Bỏ qua bước cào       `matches/understat/31239.json`
  • Pipeline mất ~7s    • Cập nhật file snapshot: `football_data_raw_2627.csv`
                        • Đẩy Delta lên Hugging Face Hub (Thời gian: ~6s)
```

> **Speaker Notes:**
> *"Đây là điểm sáng kỹ thuật quan trọng nhất của phần Crawling: Thay vì phụ thuộc vào ổ đĩa local, crawler sử dụng Hugging Face Hub làm 'Single Source of Truth'. Khi khởi động, crawler chỉ tốn 0.4 giây đọc metadata trên cloud để biết được những trận nào đã có. Sau đó đối chiếu với kết quả thi đấu thực tế để tính ra hiệu số Delta. Nếu giữa tuần không có trận nào, Delta = 0 và crawler bỏ qua việc tải lẻ. Nếu cuối tuần có trận mới, crawler chỉ tải đúng các file JSON phát sinh và đẩy lên cloud. Toàn bộ quá trình hoàn toàn tự động, chính xác và không bao giờ bị trùng lặp."*

---

<!-- slide -->
## SLIDE 8: [PHẦN 1] TỰ ĐỘNG HÓA CI/CD VỚI GITHUB ACTIONS DAILY WORKFLOW

### 1. Thiết kế Workflow tự động (`.github/workflows/daily_epl_crawl.yml`)
* **Lịch trình tự động (Cron Trigger):** Chạy vào lúc **03:00 UTC (10:00 sáng VN)** mỗi ngày — thời điểm tất cả các trận đấu đêm hôm trước và bảng điểm nhà cái đã được chốt sổ chính thức.
* **Hỗ trợ kích hoạt thủ công (Manual Dispatch):** Cho phép chạy bù hoặc kiểm tra tức thì qua giao diện GitHub.
* **Bảo mật tuyệt đối:** Token Hugging Face được quản lý qua GitHub Repository Secrets (`${{ secrets.HF_TOKEN }}`). Không bao giờ hardcode token trong mã nguồn.

### 2. Hiệu năng thực thi ấn tượng
* Thời gian thiết lập môi trường Python & dependencies: **~10 giây**.
* Thời gian kiểm tra đối soát Delta & tải trận mới: **~3–5 giây**.
* Thời gian đồng bộ lên Hugging Face Hub: **~6 giây**.
* **Tổng thời gian chạy trung bình:** **14 – 21 giây/ngày** (Tiết kiệm 99% tài nguyên runner miễn phí của GitHub).

> **Speaker Notes:**
> *"Quy trình cào dữ liệu được tự động hóa 100% bằng GitHub Actions. Cứ 10 giờ sáng hàng ngày, hệ thống tự động thức dậy, thực thi kiểm tra delta và cập nhật dữ liệu. Tổng thời gian thực thi chỉ mất khoảng 14 đến 21 giây mỗi ngày. Hệ thống chạy hoàn toàn độc lập, không cần bất kỳ sự can thiệp thủ công nào của con người trong suốt mùa giải."*

---

<!-- slide -->
## SLIDE 9: [PHẦN 1] TỔNG KẾT SẢN PHẨM BÀN GIAO PHẦN 1 (LÊ TRƯỜNG GIANG)

### 1. Kho dữ liệu thô (Raw Data Lake) trên Hugging Face
* **Đường dẫn:** `https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset`
* **Cấu trúc lưu trữ chuẩn mực:**
  * `raw_data/`: Đầy đủ 7 nguồn dữ liệu lịch sử (FBref, Transfermarkt, Football-Data, Understat, FotMob, FPL, Open-Meteo).
  * `2026_2027_new/`: Đầy đủ 60/60 trận đấu đã diễn ra của mùa 2026/27 (đầy đủ shotmap, đội hình, rating và kèo cược).
  * `README.md`: Bảng Master Data Catalog mô tả chi tiết từng cột, schema và mã Python đọc mẫu.

### 2. Bộ công cụ 1-Click dành cho các thành viên trong nhóm
* `crawl_daily.bat` / `daily_raw_crawler.py`: Kích hoạt crawler hàng ngày.
* `sync_cloud_to_local.bat` / `sync_cloud_to_local.py`: Tải toàn bộ dataset từ Hugging Face về máy cá nhân với 1 cú click (tự động bung nén `.tar.gz`).
* `sync_local_to_cloud.bat` / `sync_local_to_cloud.py`: Đẩy dữ liệu từ máy cá nhân lên cloud an toàn theo cơ chế Strategy A.

> **Speaker Notes:**
> *"Kết thúc Phần 1, bạn Lê Trường Giang đã bàn giao một kho dữ liệu thô toàn vẹn trên Hugging Face gồm 27 mùa giải và trọn vẹn 60 trận của mùa 2026/27, đi kèm bộ script 1-click giúp bất kỳ thành viên nào trong nhóm cũng có thể kéo dữ liệu về máy local để làm việc chỉ trong vài giây."*

---

<!-- slide -->
## SLIDE 10: [PHẦN 2] KIẾN TRÚC 6 GIAI ĐOẠN TÍCH HỢP & TIỀN XỬ LÝ (BÙI ANH MINH)

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│              QUY TRÌNH 6 GIAI ĐOẠN KHÉP KÍN (Data Integration and Preprocessing)                 │
└────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                 │
  ┌──────────────────────────────────────────────┴─────────────────────────────────────────────┐
  ▼                                                                                            ▼
[GIAI ĐOẠN 1: TÍCH HỢP] (integrate.py)               [GIAI ĐOẠN 2: CHUẨN HÓA] (normalize.py)
• Đọc 4 nguồn trận đấu: FD, US, TM, FM               • Tạo mã định danh duy nhất `match_id`
• Ánh xạ tên CLB về mã chuẩn 3 chữ cái                • Quy đổi ngày giờ về giờ Anh (Europe/London)
• Ghép theo khóa: KEY = (season, home, away)         • Chuẩn hóa 165 tên trọng tài -> 88 tên chuẩn
• Xuất ra: `integrated.csv` (9.850 x 91)             • Rút gọn 26 sơ đồ chiến thuật -> 17 chuẩn
                                                     • Xuất ra: `normalized.csv` (9.850 x 100)
  ▲                                                                                            ▲
  │                                                                                            │
  ├────────────────────────────────────────────────────────────────────────────────────────────┤
  │                                                                                            │
  ▼                                                                                            ▼
[GIAI ĐOẠN 3: LÀM SẠCH] (clean.py)                   [GIAI ĐOẠN 4: DỰNG MATCHES] (update_matches)
• Áp dụng bộ 11 luật định chất lượng (R1 -> R11)     • Kết hợp trận đã chốt + trận tạm thời + sắp đá
• Xử lý 441 trận đóng cửa sân thời COVID             • Kiểm soát chống trùng tuyệt đối theo `match_id`
• Ghi vết mọi sửa đổi vào `cleaning_log.csv`         • Xuất ra BẢNG VÀNG SẠCH:
• Xuất ra: `cleaned.csv` (9.850 x 103)                 `matches.csv` (9.850 dòng x 106 cột)
  ▲                                                                                            ▲
  │                                                                                            │
  ├────────────────────────────────────────────────────────────────────────────────────────────┤
  │                                                                                            │
  ▼                                                                                            ▼
[GIAI ĐOẠN 5: TẠO ĐẶC TRƯNG] (features.py)           [GIAI ĐOẠN 6: KIỂM ĐỊNH QA] (validate.py)
• Cơ chế "Đọc Trước - Cập Nhật Sau" cho TeamState    • Chạy 108 bài kiểm tra tự động (QA Suite)
• Xây dựng 87 đặc trưng quá khứ (Phong độ, Elo, xG)  • Kiểm định gián đoạn thời gian (Time-cutoff)
• Xuất ra BẢNG ĐẶC TRƯNG HUẤN LUYỆN:                 • Tự động sinh `DATA_DICTIONARY.md`
  `features.csv` (9.850 dòng x 102 cột)              • Xuất ra: `qa_report.json` (108/108 PASS)
```

> **Speaker Notes:**
> *"Bây giờ, chúng ta bước sang Phần 2 do bạn Bùi Anh Minh phụ trách: Quy trình Tích hợp và Tiền xử lý dữ liệu. Để chuyển hóa dữ liệu thô hỗn tạp thành dữ liệu sạch đạt tiêu chuẩn Machine Learning, bạn Minh đã thiết kế một pipeline tuần tự gồm 6 giai đoạn rõ ràng: Tích hợp, Chuẩn hóa, Làm sạch theo luật, Dựng bảng trận đấu hoàn chỉnh, Tạo 87 đặc trưng trước trận và Kiểm định chất lượng QA với 108 tiêu chí."*

---

<!-- slide -->
## SLIDE 11: [PHẦN 2] GIAI ĐOẠN 1 – TÍCH HỢP ĐA NGUỒN & ÁNH XẠ CLB

### 1. Khóa ghép trận đấu duy nhất: `KEY = (season, home_club_id, away_club_id)`
* **Nguyên lý:** Trong mỗi mùa giải Ngoại hạng Anh, hai đội bóng chỉ đối đầu trên sân của một đội **đúng một lần duy nhất**.
* **Nguyên tắc vàng:** **Tuyệt đối không ghép nối theo ngày thi đấu!** Vì giữa các nguồn có sự lệch múi giờ (UTC vs GMT/BST) hoặc lịch thi đấu bị hoãn/dời. Ngày thi đấu chỉ dùng để đối chiếu mâu thuẫn (nếu lệch > 1 ngày thì ghi nhận vào `mismatches.csv`).

### 2. Hệ thống chuẩn hóa tên Câu Lạc Bộ 2 tầng
* Tên đội bóng giữa các nguồn viết rất khác nhau (ví dụ: `Man United`, `Manchester United`, `Man Utd`, `Manchester United FC`).
* Nhóm xây dựng bảng tra cứu kép quy về mã 3 chữ cái cố định:
  * `club_mapping.csv`: Ánh xạ 29 CLB lớn hiện đại kèm ID nguồn (`tm_club_id`, `fotmob_team_id`).
  * `club_mapping_extra.csv`: Bổ sung 17 CLB lịch sử trong 27 năm qua (Blackburn, Bolton, Wigan, Oldham...).
* **Cơ chế Fail-Fast:** Nếu xuất hiện tên đội lạ chưa được khai báo, chương trình sẽ lập tức dừng lại và cảnh báo thay vì âm thầm bỏ qua dữ liệu.

### 3. Kết quả tích hợp xuất sắc ([`report.json`](file:///z:/Desktop/Bet/clean/report.json))
* **Understat:** Khớp **4,620 / 4,620 trận** (100% độ phủ các mùa có Understat).
* **Transfermarkt:** Khớp **5,320 / 5,320 trận** (100% độ phủ các mùa có Transfermarkt).
* **FotMob:** Khớp **6,029 / 6,140 trận** (100% các mùa từ 2011 đến nay).
* File `mismatches.csv`: **0 dòng (Hoàn toàn rỗng – Không có bất kỳ mâu thuẫn nào về ngày hay tỉ số)**.

> **Speaker Notes:**
> *"Ở Giai đoạn 1, thách thức lớn nhất là làm sao ghép 4 nguồn dữ liệu lại với nhau mà không bị lệch trận. Bạn Minh đã áp dụng nguyên tắc cốt lõi: Ghép nối bằng bộ ba (Mùa giải, Đội nhà, Đội khách) thay vì ghép bằng ngày thi đấu. Nhờ hệ thống ánh xạ 46 câu lạc bộ chuẩn xác, độ phủ ghép nối đạt 100% trên toàn bộ các mùa giải tương ứng và bảng cảnh báo sai lệch `mismatches.csv` đạt con số 0 tuyệt đối."*

---

<!-- slide -->
## SLIDE 12: [PHẦN 2] GIAI ĐOẠN 2 – CHUẨN HÓA DỮ LIỆU (NORMALIZE)

### 1. Khởi tạo mã trận đấu duy nhất (`match_id`)
* Cấu trúc chuẩn hóa: `EPL_<NămBắtĐầu>_<NămKếtThúc>_<MãNhà>_<MãKhách>`
* Ví dụ: `EPL_2026_2027_ARS_LEE` (Trận Arsenal vs Leeds mùa 2026/27).
* Đảm bảo tính bất biến và ổn định qua nhiều lần chạy, phục vụ việc kiểm tra chống trùng lặp.

### 2. Chuẩn hóa tên Trọng tài (Referee Normalization)
* Tên trọng tài trong dữ liệu gốc có đến **165 biến thể viết khác nhau** (ví dụ: `Mike Dean`, `M Dean`, `Dean, M. L.`, `M. Dean`).
* Thuật toán đưa về định dạng chuẩn duy nhất: `<Chữ_cái_đầu_Tên> <Họ>` $\rightarrow$ Rút gọn từ 165 biến thể xuống **88 trọng tài chuẩn**.
* Sửa các lỗi gõ sai chính tả nghiêm trọng của nguồn gốc (ví dụ: `"D Gallagh"` $\rightarrow$ `"D Gallagher"`).

### 3. Chuẩn hóa Sơ đồ chiến thuật & Kiểu dữ liệu
* **Sơ đồ chiến thuật (Formations):** Trích xuất phần số từ mô tả phức tạp của Transfermarkt (ví dụ: `"4-3-3 Attacking"` $\rightarrow$ `"4-3-3"`), rút gọn từ 26 nhãn gốc về **17 sơ đồ chuẩn**.
* **Kiểu dữ liệu:** Ép kiểu nguyên `Int64` cho các biến đếm (hỗ trợ giá trị null), ép kiểu `float` cho tỷ lệ kèo và xG, chuẩn hóa giờ thi đấu theo giờ nước Anh (`Europe/London`).
* **Sản phẩm:** `normalized.csv` (**9,850 dòng × 100 cột**).

> **Speaker Notes:**
> *"Bước sang Giai đoạn 2, dữ liệu được chuẩn hóa hình thức toàn diện. Tên trọng tài từ 165 cách viết lộn xộn được quy về đúng 88 tên chuẩn; sơ đồ chiến thuật từ 26 biến thể được rút gọn về 17 sơ đồ thi đấu cơ bản; và mỗi trận đấu được cấp một mã `match_id` bất biến duy nhất. File kết quả sau giai đoạn này mở rộng lên 100 cột dữ liệu chuẩn hóa."*

---

<!-- slide -->
## SLIDE 13: [PHẦN 2] GIAI ĐOẠN 3 – BỘ 11 QUY TẮC LÀM SẠCH NGHIÊM NGẶT (CLEAN)

Mọi thao tác làm sạch đều tuân theo các nguyên tắc được định nghĩa chặt chẽ và ghi log minh bạch vào [`cleaning_log.csv`](file:///z:/Desktop/Bet/clean/cleaning_log.csv) (**459 bản ghi sửa đổi**):

| Mã Luật | Tên quy tắc làm sạch | Phát hiện lỗi trong dữ liệu gốc | Hành động xử lý của hệ thống | Số trường hợp xử lý |
| :---: | :--- | :--- | :--- | :---: |
| **R1** | `drop_duplicates` | Trùng lặp `match_id` hoặc trùng khóa ghép | Giữ bản ghi đầy đủ dữ liệu nhất, xóa dòng thừa | 0 (Dữ liệu không trùng) |
| **R2-R3**| `fix_formats_and_ranges` | Giá trị ngoài miền cho phép; tỷ lệ cược nhà cái $\le 1$ | Xóa ô lỗi, đưa về giá trị trống (NaN) | 0 |
| **R4-R5**| `fix_consistency` | Kết quả không khớp tỉ số; Bàn thắng H1 > Cả trận | Tính lại kết quả từ tỉ số; Xóa dữ liệu H1 mâu thuẫn | 0 |
| **R6** | `fix_consistency` | Sút trúng đích lớn hơn tổng số cú sút (`shots_on_target > shots`) | Xóa cả hai ô dữ liệu sút của đội đó để tránh nhiễu | **10 ô dữ liệu** |
| **R7** | `fix_referees` | Tên trọng tài chỉ xuất hiện 1 lần do viết sai tên đệm | Sửa lại theo tên vị trọng tài chính cầm còi trong mùa | **6 trọng tài** |
| **R8** | `check_season_window` | Ngày thi đấu nằm ngoài khung thời gian mùa giải | Đưa ra cảnh báo kiểm tra thủ công | 0 |
| **R9** | `fill_closed_doors` | Trống số lượng khán giả đợt dịch COVID-19 (17/06/2020 – 23/05/2021) | **Điền số khán giả = 0**, đồng thời gán cờ `attendance_imputed = True` | **441 trận đấu** |
| **R10** | `fix_consistency` | Tổng kiểm soát bóng 2 đội không bằng $100 \pm 2\%$ | Xóa ô kiểm soát bóng cả 2 đội | 0 |
| **R11** | `fix_consistency` | Bỏ lỡ cơ hội lớn nhiều hơn tổng số cơ hội tạo ra | Xóa các ô chỉ số mâu thuẫn nội tại | **2 ô dữ liệu** |

> **Speaker Notes:**
> *"Giai đoạn 3 là khâu làm sạch chuyên sâu với 11 quy tắc từ R1 đến R11. Một ví dụ tiêu biểu là Luật R9: Trong giai đoạn dịch COVID-19, các trận đấu diễn ra trên sân không khán giả nhưng dữ liệu gốc lại để trống. Hệ thống tự động nhận diện mốc thời gian này, điền giá trị khán giả bằng 0 và đánh dấu cờ truy vết cho đúng 441 trận đấu. Tất cả 459 thao tác sửa đổi đều được lưu lại từng vết trong file `cleaning_log.csv` để phục vụ audit."*

---

<!-- slide -->
## SLIDE 14: [PHẦN 2] GIAI ĐOẠN 4 – DỰNG BẢNG TRẬN ĐẤU HỢP NHẤT (`matches.csv`)

### 1. Cơ chế hợp nhất 3 nhóm trận đấu
* **Nhóm 1 (Trận đấu đã chốt chính thức):** Toàn bộ các trận đấu từ mùa 2000/01 đến mùa 2025/26 và các vòng đã chốt của mùa 2026/27.
* **Nhóm 2 (Trận đấu có kết quả tạm thời):** Các trận vừa đá xong trong ngày nhưng nguồn Football-Data chưa kịp cập nhật $\rightarrow$ Tạm thời lấy kết quả từ Understat/FotMob.
* **Nhóm 3 (Trận đấu sắp diễn ra - Upcoming Matches):**
  * Tự động lọc các trận thuộc vòng hiện tại chưa đá hoặc bị hoãn.
  * Chỉ nạp thêm vòng tiếp theo khi thời gian đến giờ bóng lăn còn $\le 1$ ngày.

### 2. Thông số kỹ thuật của sản phẩm [`matches.csv`](file:///z:/Desktop/Bet/clean/matches.csv)
* **Quy mô dữ liệu:** Đúng chuẩn **9,850 dòng × 106 cột**.
* **Độ bao phủ mùa giải:** 27 mùa giải liên tục (2000/01 – 2026/27).
  * 24 mùa giải đủ 380 trận/mùa.
  * 2 mùa 2003/04 và 2004/05 có 335 trận (khoảng trống lịch sử từ nguồn gốc).
  * Mùa 2026/27: Đã cập nhật đủ **60/60 trận đã đá** (trọn vẹn Vòng 1 đến Vòng 6).
* **Công dụng:** Đóng vai trò là bảng dữ liệu sự thật (Ground Truth) lưu trữ toàn bộ chỉ số thực tế của trận đấu phục vụ phân tích hồi quy và kiểm chứng.

> **Speaker Notes:**
> *"Bảng sản phẩm chính thứ nhất là `matches.csv` gồm 9.850 dòng và 106 cột. Bảng này tổng hợp toàn bộ các trận đấu từ năm 2000 đến nay. Điểm đặc biệt của bảng này là nó được thiết kế mở: vừa lưu các trận đã đấu, vừa có khả năng nạp các trận sắp đá trong vòng 24 giờ tới để chuẩn bị sẵn đầu vào cho mô hình dự đoán kết quả."*

---

<!-- slide -->
## SLIDE 15: [PHẦN 2] GIAI ĐOẠN 5 – KỸ THUẬT TẠO 87 ĐẶC TRƯNG TRƯỚC TRẬN (FEATURE ENGINEERING)

Mỗi trận đấu trong [`features.csv`](file:///z:/Desktop/Bet/clean/features.csv) (**9,850 dòng × 102 cột**) được trang bị **87 đặc trưng trước trận** chia thành 8 nhóm chỉ số:

| STT | Nhóm đặc trưng | Số lượng | Danh sách các biến tiêu biểu | Ý nghĩa dự báo |
| :---: | :--- | :---: | :--- | :--- |
| **1** | **Phong độ kết quả (5 trận)** | 14 | `form`, `wins5`, `draws5`, `losses5`, `points5`, `goals_for5`, `goals_against5` (Home & Away) | Đo lường điểm số và hiệu số bàn thắng trong ngắn hạn. |
| **2** | **Phong độ cơ hội (xG 5 trận)** | 6 | `shots_on_target5`, `xg5`, `xga5` (từ Understat) | Đo lường chất lượng cơ hội tạo ra và số bàn thua kỳ vọng. |
| **3** | **Sức mạnh đối thủ & Elo** | 6 | `elo_diff`, `opp_elo5` (Elo trung bình 5 đối thủ gần nhất), `perf_vs_elo5` | Đánh giá phong độ có tính đến độ khó của đối thủ đã gặp. |
| **4** | **Lối chơi nâng cao (FotMob)** | 12 | `possession5`, `pass_accuracy5`, `big_chances5`, `box_touches5`, `xgot5` | Phản ánh khả năng kiểm soát thế trận và chạm bóng vùng cấm. |
| **5** | **Sân nhà / Sân khách** | 6 | `home_points5`, `home_goals_for5`, `away_points5`, `away_goals_for5` | Tách biệt phong độ khi đá sân nhà và khi phải làm khách. |
| **6** | **Toàn mùa & Thể lực** | 8 | `played`, `ppg` (điểm trung bình/trận), `rest_days` (số ngày nghỉ) | Đo lường độ ổn định dài hạn và mức độ suy giảm thể lực. |
| **7** | **BXH & Lịch sử đối đầu** | 5 | `position`, `position_diff`, `h2h_matches`, `h2h_home_points`, `h2h_home_goal_diff` | Yếu tố tâm lý và cái dớp đối đầu giữa hai câu lạc bộ. |
| **8** | **Tỷ lệ cược thị trường** | 3 | `odds_home_prob`, `odds_draw_prob`, `odds_away_prob` | Xác suất ngầm định từ nhà cái quốc tế (đã bóc tách margin). |

> **Speaker Notes:**
> *"Từ bảng trận đấu, bạn Minh đã xây dựng 87 đặc trưng toán học phục vụ huấn luyện mô hình. Không chỉ dừng lại ở số trận thắng thua thông thường, bộ đặc trưng bao gồm các chỉ số hiện đại như xG 5 trận gần nhất, chỉ số Elo có trọng số theo độ mạnh đối thủ, số lần chạm bóng trong vòng cấm đối phương từ FotMob và số ngày nghỉ của cầu thủ. Tất cả đều là thông tin được tính trước khi tiếng còi khai cuộc vang lên."*

---

<!-- slide -->
## SLIDE 16: [PHẦN 2] CƠ CHẾ BẢO VỆ CHỐNG RÒ RỈ DỮ LIỆU TƯƠNG LAI (ZERO DATA LEAKAGE)

### 1. Cơ chế "Đọc Trước – Cập Nhật Sau" (Read-First, Update-Later)
* Để loại bỏ hoàn toàn hiện tượng Look-ahead Bias (dữ liệu tương lai lọt vào quá khứ):
  1. **Bước 1 (Đọc trạng thái - READ):** Khi xét trận đấu tại thời điểm $T$, hệ thống chỉ đọc trạng thái tích lũy của hai đội từ các trận diễn ra TRƯỚC thời điểm $T$ để tạo đặc trưng.
  2. **Bước 2 (Cập nhật - UPDATE):** Sau khi đã ghi nhận xong bộ đặc trưng, kết quả của trận đấu tại thời điểm $T$ mới được nạp vào để cập nhật lại Elo và phong độ cho các trận tương lai.
* Các trận sắp đá (Upcoming) chỉ thực hiện bước "Đọc", đảm bảo chỉ số hoàn toàn khách quan.

### 2. Thuật toán kiểm định rò rỉ tự động (Time-cutoff Leakage Test)
* Hệ thống chọn ngẫu nhiên **5 ngày mốc lịch sử** (`2007-09-29`, `2014-08-18`, `2016-10-02`, `2022-03-17`, `2022-12-26`).
* Cắt toàn bộ dữ liệu tương lai sau ngày đó và tính toán lại bộ đặc trưng.
* **Kết quả đối chiếu:** Khớp chính xác **100% (Match 100%)** giữa việc tính toán từng bước và tính toán gián đoạn $\rightarrow$ Cờ `leakage_check_passed = True`.

### 3. Phân tích tương quan Spearman với Hiệu số bàn thắng
* Đánh giá trên **3,480 trận đấu** chứng minh tính hiệu quả của các đặc trưng nâng cao:
  * `odds_home_prob` (Tỷ lệ kèo): **0.47**
  * `elo_diff` (Chênh lệch Elo): **0.44**
  * `diff_xg5` (Chênh lệch xG tạo ra): **0.34** (Cao hơn nhiều so với chênh lệch bàn thắng thông thường: 0.29)
  * `diff_box_touches5` (Chạm bóng vòng cấm): **0.33**

> **Speaker Notes:**
> *"Rò rỉ dữ liệu tương lai là cạm bẫy lớn nhất trong bài toán dự đoán chuỗi thời gian thể thao. Nhóm đã áp dụng cơ chế 'Đọc Trước - Cập Nhật Sau': Dữ liệu trận đấu chỉ được cập nhật vào hồ sơ đội bóng sau khi đặc trưng của trận đó đã được chốt. Hệ thống còn tự động chạy thuật toán Time-cutoff tại 5 thời điểm ngẫu nhiên trong 20 năm qua để đối soát và chứng minh 100% không có dữ liệu tương lai bị rò rỉ. Phân tích tương quan cũng chứng minh các chỉ số xG và chạm bóng vòng cấm có độ tương quan dự báo bàn thắng thực tế vượt trội so với số bàn thắng đơn thuần."*

---

<!-- slide -->
## SLIDE 17: [PHẦN 2] GIAI ĐOẠN 6 – KIỂM ĐỊNH CHẤT LƯỢNG TỰ ĐỘNG (108/108 QA CHECKS)

Hệ thống kiểm định [`validate.py`](file:///z:/Desktop/Bet/clean/qa_report.json) tự động thực thi **108 bài kiểm tra chất lượng**:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             KẾT QUẢ KIỂM ĐỊNH TỰ ĐỘNG (qa_report.json)                            │
│                  TỔNG SỐ BÀI KIỂM TRA: 108  |  PASSED: 108  |  FAILED: 0                        │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

* **Nhóm 1: Tính toàn vẹn & Khóa chính (Unique & Keys):**
  * Duy nhất `match_id` 100% (0 trùng lặp).
  * Duy nhất bộ `(season, home, away)` 100% (0 trùng lặp).
  * 100% CLB nằm trong danh mục ánh xạ chuẩn. Đội nhà $\ne$ Đội khách.
* **Nhóm 2: Định dạng & Tính nhất quán (Formats & Consistency):**
  * Ngày đúng chuẩn `YYYY-MM-DD`, giờ thi đấu `HH:MM`.
  * Tỉ số cả trận và H1 khớp tuyệt đối với kết quả Thắng/Hòa/Thua (`FTR`). Bàn thắng H1 $\le$ Cả trận.
* **Nhóm 3: Miền giá trị nghiệp vụ (Business Logic Ranges):**
  * Tỷ lệ cược nhà cái $> 1$, tổng xác suất quy đổi loại biên lợi nhuận $= 1.0$.
  * Kiểm soát bóng FotMob: $\text{Home} + \text{Away} = 100 \pm 2\%$.
  * Chỉ số xG, cú sút, phạt góc, thẻ vàng, thẻ đỏ đều nằm trong biên độ vật lý cho phép.
* **Nhóm 4: Cấu trúc mùa giải & Chống rò rỉ:**
  * Toàn bộ 24 mùa giải đủ 20 đội, 380 trận.
  * `features.csv` khớp chính xác số dòng với `matches.csv`. Cờ `leakage_check_passed = True`.
  * Trận sắp đá (upcoming) bắt buộc chưa có kết quả (Target = Null) và đã sẵn sàng chỉ số Elo.

> **Speaker Notes:**
> *"Để đảm bảo dữ liệu khi đưa sang cho các bạn làm mô hình là sạch tuyệt đối, bạn Minh đã lập trình 108 bài kiểm tra tự động chạy qua file `validate.py`. Kết quả kiểm định đạt tỷ lệ hoàn hảo 108/108 PASS, không có bất kỳ bài kiểm tra nào bị FAIL. Từ tính duy nhất của khóa, sự nhất quán giữa tỉ số và kết quả, miền giá trị xác suất cược đến kiểm tra chống rò rỉ đều đã được đóng dấu kiểm định."*

---

<!-- slide -->
## SLIDE 18: [TỔNG KẾT] SẢN PHẨM HOÀN THÀNH & SẴN SÀNG CHO CÁC KHÂU SAU

### 1. Bảng đối chiếu tiến độ với đề bài yêu cầu
| Hạng mục bàn giao | Người thực hiện | Yêu cầu theo đề bài | Kết quả thực tế của nhóm | Đánh giá |
| :--- | :---: | :--- | :--- | :---: |
| **Crawler & Dữ liệu thô** | **Lê Trường Giang** (Người 1) | Thu thập đa nguồn, xử lý trùng lặp, lưu CSV/JSON, log | • 7 nguồn, 27 mùa giải (2000–2027)<br>• Chiến thuật A nén 10,700 trận (.tar.gz)<br>• Tự động hóa CI/CD GitHub Actions | **Vượt tiến độ** |
| **Tích hợp & Làm sạch** | **Bùi Anh Minh** (Người 2) | Ghép nguồn, chuẩn hóa tên/ngày, xử lý thiếu, bảng sạch | • Pipeline 6 giai đoạn, 11 quy tắc sạch<br>• Ánh xạ 46 CLB, 88 trọng tài chuẩn<br>• `matches.csv` (9,850 dòng x 106 cột) | **Đạt 100% chuẩn** |
| **Kỹ thuật Đặc trưng** | **Bùi Anh Minh** (Người 2) | Phong độ 5 trận, bàn thắng TB, sân nhà/khách, Data Dictionary | • 87 đặc trưng trước trận (Elo, xG, lối chơi)<br>• Cơ chế chống rò rỉ dữ liệu (100% Pass)<br>• `features.csv` (9,850 dòng x 102 cột) | **Vượt tiến độ** |

### 2. Sẵn sàng tích hợp cho các thành viên giai đoạn tiếp theo
* **Bàn giao cho EDA (Người 4 - Vũ Hoàng Dương):** Bảng `features.csv` sạch sẽ với 87 biến để vẽ phân phối bàn thắng, tương quan phong độ và kiểm định giả thuyết.
* **Bàn giao cho Link Analysis (Người 5 - Đoàn Quốc Khánh):** Danh sách 9,850 cặp đấu đối đầu lịch sử với mã CLB chuẩn để dựng đồ thị mạng lưới đối đầu.
* **Bàn giao cho Modeling (Người 7 - Mỵ Duy Hải Anh) & Evaluation (Người 8 - Lưu Hoàng Phương):** Bộ nhãn `target` chuẩn mực kèm tập đặc trưng quá khứ phân tách theo thời gian, sẵn sàng đưa vào XGBoost, Random Forest và Poisson Regression.

---

### PHẦN KẾT THÚC & HỎI ĐÁP (Q&A)
* **Toàn bộ dữ liệu sạch đã được đồng bộ tại:** `z:\Desktop\Bet\clean\` và [Hugging Face Hub](https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset).
* Xin chân thành cảm ơn Thầy Cô và các bạn đã lắng nghe. Nhóm 5 xin sẵn sàng trả lời các câu hỏi!
