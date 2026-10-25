# M1 — Khảo sát nguồn dữ liệu & chốt yêu cầu (Source & Requirements)

**Vai trò:** xác định dữ liệu nào cần lưu, ai tạo/cập nhật, phạm vi lịch sử và nguyên tắc bất biến trước khi thiết kế PostgreSQL. Đây là tài liệu **tổng hợp theo trách nhiệm chức năng** từ lịch sử dự án, không khẳng định chính xác số hiệu của mọi sub-milestone M1 ở source local.

## 1. Bối cảnh và mục tiêu

Dự án nhóm 9 thành viên môn Nhập môn Khoa học dữ liệu hướng tới dự đoán kết quả trận đấu EPL tiếp theo. Raw Storage do thành viên #3 phụ trách đóng vai trò nền tảng dữ liệu, **không gắn chặt với tập trường của riêng một mô hình dự đoán**. Người #1 chịu trách nhiệm nguồn/crawl; người #2 Cleaning; các thành viên downstream tiếp tục tạo feature/model.

- **Nguồn trung tâm:** Hugging Face dataset `Toibinguz/epl-premier-league-dataset`.
- **Phạm vi giải đấu:** EPL. Raw Storage bảo toàn mọi file raw hiện có liên quan đến EPL, không tự cắt tập lưu trữ vào khoảng mùa 2010/11–2024/25 (khoảng đó từng là phạm vi phân tích mô hình, không phải điều kiện xóa raw).
- **Nguồn/định dạng đã thấy:** dữ liệu lịch sử dưới dạng CSV từ các nhà cung cấp bóng đá; mùa cập nhật 2026–2027 gồm các file CSV, JSON và TAR.GZ như FotMob, FPL, weather cache, football-data và Understat.
- **Cách cập nhật từ thành viên 1:** cập nhật dữ liệu mùa 2026–2027; commit/crawl theo lịch phía upstream, có thể một số file thay thế toàn bộ và một số file được bổ sung nội dung. Không giả định mọi lần cập nhật là append-only.

## 2. Data requirements cần chốt liên nhóm

| Nhóm yêu cầu | Thông tin cần nhận / quyết định | Ý nghĩa với Raw Storage |
|---|---|---|
| Định danh nguồn | HF repo + revision/commit SHA | Pin dữ liệu, tái lập và so sánh phiên bản |
| Danh mục file | Relative path, format, season, loại dữ liệu | Discovery và source mapping |
| Tần suất thay đổi | Giờ crawler, giờ commit, file thay đổi theo mùa | Chạy monitor sau upstream |
| Hành vi cập nhật | Overwrite/append/new file; file có thể chưa đồng bộ | Không suy đoán số record hay dùng mốc thời gian làm khóa duy nhất |
| Nguyên tắc dữ liệu thô | Bảo toàn raw bytes/record và không làm sạch trước lưu | Reproducibility và separation of concerns |
| Khả năng làm sạch | Dữ liệu downstream cần truy xuất theo file/snapshot | Xây API/CLI read-only, pagination, lineage |
| Quản trị phiên bản | Phân biệt RAW scope với `clean/` và non-raw files | Tránh lưu nhầm cleaned vào RAW candidate set |
| Chất lượng & provenance | Record source, locator, event time (nếu biết) | Metadata evidence và kiểm tra nguồn |

**Kiểu dữ liệu:** ở giai đoạn contract, nên thu thập tên trường, kiểu dữ liệu dự kiến, độ nullable và ví dụ — nhưng **không biến các schema đã làm sạch thành schema cứng cho toàn bộ raw file**. `raw_data JSONB` cho phép lưu payload gốc đa cấu trúc; metadata được chuẩn hóa tối thiểu ở các cột riêng.

## 3. Quyết định thiết kế đã chốt

1. Lưu trữ gốc trong **PostgreSQL JSONB**; giữ bản sao bất biến của file raw dưới archive riêng.
2. **Không đổi định dạng mọi file về CSV**. CSV, CSV.GZ, JSON, TAR.GZ giữ đặc trưng định dạng khi ingest.
3. Mỗi target HF revision là phiên bản có thể kiểm toán; dùng path, fingerprint/hash và snapshot membership để phát hiện thay đổi.
4. Không dựa hoàn toàn vào giờ cập nhật, vì có ngày không có trận đấu/crawl không thêm row; cần xử lý **no-change**.
5. Dữ liệu đã được người #2 clean trên HF `clean/`; Raw Storage chỉ hỗ trợ truy xuất và handover, không tái chạy quá trình Cleaning của họ.

## 4. Đầu vào, đầu ra và điều kiện bàn giao

**Đầu vào:** HF dataset, danh mục nguồn/dữ liệu upstream, lịch cập nhật, yêu cầu Cleaning/Model.

**Đầu ra M1:** phạm vi EPL và RAW scope, quy tắc nhận diện file, metadata cần lưu, danh sách định dạng, giả thiết thay đổi qua ngày, nguyên tắc bảo toàn và contract giao cho M2/M3.

**Gate để chuyển M2:** mọi file được nhóm xem là raw cần có cơ chế nhận diện; phải phân biệt thay đổi dữ liệu và thay đổi chỉ metadata; không có yêu cầu ép tất cả trường record vào một bảng cột cố định.

## 5. File liên quan để đối chiếu trong repository

- `config/discovery_config.json` — file Discovery đúng tên đã chốt với nhóm.
- `config/source_mapping.json` — ánh xạ pattern/file sang nhóm nguồn.
- `config/csv_format_config.json` — biến thể định dạng CSV.
- `scripts/discovery/hf_manifest_v2.py` và `scripts/tools/hf_manifest_v2.py` — các module đã hiện diện trong inventory; **đối chiếu CLI thực tế** nếu cần chạy riêng.

## 6. Rủi ro và ghi chú nghiệm thu

- Một commit HF có thể cập nhật nhiều file khác nhau; không dựa duy nhất vào số lượng file mới để quyết định ingest.
- Bảng cleaned ngoài HF không đồng nghĩa mọi raw đã được đảm bảo đầy đủ/tương thích Cleaning; contract bàn giao cần chỉ rõ `snapshot_id`.
- Thời điểm upstream theo xác nhận gần nhất: crawler khoảng 16:00–16:30; AutoIngest đặt 19:00 giờ Windows UTC+7 để phát hiện và ingest revision sau upstream, **không phải cam kết upstream luôn hoàn tất trước 19:00**. GitHub CI kiểm source; lịch vận hành ở Windows.

**Bước kế tiếp:** [M2 — Storage Design](../M2/README.md).
