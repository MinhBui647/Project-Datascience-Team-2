# Operations Runbook — EPL Raw Storage V2

Luồng vận hành hiện tại là `auto_ingest --run`: phát hiện revision mới trên Hugging Face, ingest JSON/CSV và Controlled Auto-Recovery. Windows Task Scheduler gọi luồng này lúc **19:00 UTC+7 mỗi ngày**. Các module monitor và supervised vẫn phục vụ kiểm tra chuyên biệt.

## 1. Điều kiện triển khai

Chạy tại thư mục gốc dự án, dùng Python **3.11** trong `venv/`. Cần PostgreSQL `epl_raw_storage_v2`, schema V2, registry nguồn, ít nhất một snapshot completed làm baseline, và các archive của baseline có đủ byte đã xác minh.

`.env` chứa cấu hình kết nối, `.env.v2` phải đặt `DB_NAME=epl_raw_storage_v2`. Hai file này, `raw-archive-v2/` và checkpoint/journal trong `reports/` thuộc dữ liệu triển khai. Clone GitHub chỉ mang source/config mẫu/DDL; không mang dữ liệu PostgreSQL, archive hay secrets.

```powershell
# Chỉ cần khi tạo môi trường mới.
py -3.11 -m venv venv
& .\venv\Scripts\python.exe -m pip install -r requirements.txt
& .\venv\Scripts\python.exe -B -m pip check
```

Dựng schema từ `sql/schema/raw_schema_v2.sql` và `sql/m4_4_handover_schema_v2.sql` theo điều kiện guard của từng script. Fresh-DB bootstrap đầy đủ chưa được nghiệm thu; không lấy CLI incremental làm công cụ nạp baseline lần đầu. Khi chuyển thư mục mà giữ DB cũ, xem phần restore archive trong [AUTOMATED_INGEST.md](AUTOMATED_INGEST.md).

## 2. Lệnh thường dùng

```powershell
# Đọc kết quả lượt gần nhất trước khi tạo báo cáo check mới.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --status

# Xem kế hoạch/checkpoint/fence, không ghi PostgreSQL.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --check

# Chạy ingest hoặc tiếp tục revision đang dở bằng recovery đã kiểm chứng.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --run
```

| Kết quả | Diễn giải |
|---|---|
| `SKIPPED_CURRENT` | Revision đã có snapshot completed; không ingest trùng |
| `SKIPPED_NO_RAW_CHANGES` | Revision đổi nhưng file thuộc Raw không đổi |
| `READY_TO_INGEST` | Chế độ check có kế hoạch hoặc state đang chờ xử lý |
| `COMPLETED` | Ingest và independent audit đã hoàn tất |
| `BLOCKED` | Đọc `recovery_status`, `intervention_required`, `reason_code`, `failed_stage` và `next_action` |

`--status` in lại receipt gần nhất, nên trường `mode` vẫn là `run` hoặc `check` của lượt đã lưu. `write_authorized=true`, `write_executed=false` cùng `SKIPPED_CURRENT` là kết quả hợp lệ. `steps=[]`, `retry_events=[]`, `recovery_status=NOT_NEEDED` nghĩa là không cần ghi/retry trong lượt đó.

`--check` lưu receipt mới và thay `reports/auto_ingest/latest.json`; hãy đọc `--status` trước nếu cần kiểm tra kết quả lượt tự động. Khi task vẫn `Running`, receipt gần nhất có thể còn là lượt trước. Đối chiếu timestamp, `mode`, receipt từng lượt và log wrapper để xác nhận đúng lần chạy.

## 3. Task Scheduler 19:00

Task giữ tên `EPL Raw Storage - Safe Revision Check`, action hiện là `run_windows_task.ps1 -Mode AutoIngest`. Tên task được giữ để nâng cấp lịch cũ; chế độ thực thi xác định bởi action.

```powershell
# Xem trước, chưa đăng ký.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\scheduling\install_windows_task.ps1 `
    -Mode AutoIngest -At '19:00'

# Đăng ký mới; nếu task đã có thì thêm -Replace.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\scheduling\install_windows_task.ps1 `
    -Mode AutoIngest -At '19:00' -Replace -Install

# Kích hoạt một lượt ngay.
Start-ScheduledTask -TaskName 'EPL Raw Storage - Safe Revision Check'

# Xem action, kết quả và lần chạy tiếp theo.
$taskName = 'EPL Raw Storage - Safe Revision Check'
(Get-ScheduledTask -TaskName $taskName).Actions
Get-ScheduledTaskInfo -TaskName $taskName |
    Select-Object LastRunTime,LastTaskResult,NextRunTime
```

Windows dùng SE Asia Standard Time, UTC+07:00. Task cần phiên Windows đã đăng nhập, máy và PostgreSQL hoạt động, có mạng. `StartWhenAvailable` hỗ trợ chạy bù, `IgnoreNew` tránh chồng task, giới hạn task 12 giờ. Task Scheduler không restart mù; runner quyết định retry bằng evidence. Giữ thư mục triển khai ổn định hoặc cập nhật lại action khi di chuyển dự án.

Installer mặc định `-Mode Check` để giữ tương thích lời gọi cũ. Muốn ingest định kỳ phải chọn rõ `-Mode AutoIngest`. Lịch đang cài không cần đăng ký lại khi chỉ cập nhật source recovery ở cùng đường dẫn.

### Sau khi đổi tên hoặc di chuyển thư mục

Task Scheduler lưu đường dẫn tuyệt đối trong action và working directory. Đổi tên thư mục không tự cập nhật task; lỗi `0x8007010B` có thể xuất hiện khi working directory cũ không còn tồn tại. Khi task đã dừng, mở PowerShell tại thư mục dự án mới và đăng ký lại:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\scheduling\install_windows_task.ps1 `
    -Mode AutoIngest -At '19:00' -Replace -Install

$taskName = 'EPL Raw Storage - Safe Revision Check'
(Get-ScheduledTask -TaskName $taskName).Actions |
    Select-Object Arguments,WorkingDirectory
Get-ScheduledTaskInfo -TaskName $taskName |
    Select-Object LastRunTime,LastTaskResult,NextRunTime
```

Installer lấy đường dẫn mới từ vị trí script. Kiểm tra cả `Arguments` và `WorkingDirectory` cùng trỏ tới thư mục hiện tại. Có thể dùng `Start-ScheduledTask` như trên để chạy bù; `COMPLETED` và audit của receipt mới xác nhận ingest hoàn tất. `LastTaskResult=0` cũng có thể ứng với lượt bỏ qua hợp lệ khi không có dữ liệu mới.

Môi trường `venv/` cũng có thể còn đường dẫn cũ trong activation script và launcher như `pip.exe`, dù `venv\Scripts\python.exe` vẫn chạy. Kiểm tra cả `python.exe -m pip --version`, `pip.exe --version` và activation; sửa launcher hoặc tạo lại môi trường tại vị trí mới nếu cần. Cấu hình task và môi trường Python là thiết lập local; GitHub lưu script cài đặt và hướng dẫn, không mang theo các thiết lập này.

Lần khôi phục ngày 25/10/2026 đã được kiểm chứng bằng lượt chạy bù tạo snapshot **#369**; xem [bằng chứng ingest](AUTOMATED_INGEST.md#91-khôi-phục-sau-đổi-tên-thư-mục).

## 4. Recovery và báo cáo

Policy mặc định tối đa 3 lần thử, backoff+jitter cơ sở 2 giây, trần 30 giây. Đọc/chuẩn bị/preflight an toàn có thể retry. Writer chỉ được replay khi child đã thoát với lỗi tạm thời được allowlist và audit mới xác nhận stage thiếu nhưng hợp lệ. Stage đã commit được bỏ qua sau xác minh; registration đã commit có thể dựng lại REGISTERED checkpoint khi có terminal evidence.

| Recovery status | Hành động |
|---|---|
| `NOT_NEEDED`, `RECOVERED` | Không cần can thiệp |
| `RESUME_AVAILABLE` | Dùng `--run` để tiếp tục revision đã pin |
| `RETRY_EXHAUSTED` | Lỗi tạm thời ở thao tác an toàn hết lượt hiện tại; lần chạy tiếp theo có thể tiếp tục |
| `DEFERRED_BUSY` | Đợi worker giữ khóa kết thúc |
| `MANUAL_INTERVENTION_REQUIRED` | Đối soát lỗi dữ liệu/quyền/config/journal hoặc writer không rõ kết quả |

Giữ nguyên `active.json`, PLANNED/PREPARED/REGISTERED, claim/result M5 và recovery ledger. Budget writer không reset giữa các lượt. Không xóa journal, đổi hash pin hoặc gọi writer M4 trực tiếp để vượt fence. Xem [CONTROLLED_AUTO_RECOVERY.md](CONTROLLED_AUTO_RECOVERY.md) cho reason code, cấu trúc attempt và quy tắc xử lý.

| Vị trí local | Nội dung |
|---|---|
| `reports/auto_ingest/latest.json` | Kết quả gần nhất cho `--status` |
| `reports/auto_ingest/runs/<run_id>/` | Receipt và log từng DB stage/preflight/attempt |
| `reports/auto_ingest/recovery/` | Ledger recovery create-only |
| `reports/m4_6_runs/<revision>/` | Checkpoint M4 bất biến |
| `reports/m5_write_attempts/` | M5 claim/result gốc |
| `reports/scheduled_tasks/` | stdout/stderr/status của wrapper Windows |

Các artifact này không có sẵn trong GitHub checkout. Timestamp receipt dùng UTC; lịch kích hoạt dùng giờ Windows local.

## 5. Monitor và supervised chuyên biệt

`unified_incremental_runner --check` chỉ phát hiện revision và trả `REVIEW_*`, không ingest. `supervised_pipeline_runner --plan`/`--execute-one-stage` xử lý một stage với digest và xác nhận tương ứng; không dùng CLI này làm action lịch 19:00.

Trong điều tra chuyên sâu, đọc `--help`, dùng target SHA40 và baseline/evidence đã xác minh. Các công cụ cũ không tự hiểu mọi recovery attempt bổ sung; đối chiếu cả M5 journal gốc và ledger mới trước khi kết luận kết quả.

## 6. Bàn giao cho Cleaning

```powershell
# Snapshot #368 là ví dụ đã nghiệm thu, không phải ID cố định trên mọi máy.
& .\venv\Scripts\python.exe -B -m scripts.access.raw_data_access --list-files --snapshot-id 368 --limit 5
& .\venv\Scripts\python.exe -B -m scripts.access.raw_data_access --records --snapshot-id 368 `
    --path '2026_2027_new/football_data_raw_2627.csv' --limit 2
```

Chỉ đọc completed snapshot, dùng đúng observed path và cursor để phân trang. Mỗi trang tối đa 50 record/8 MiB. Cleaning sở hữu phần chuyển đổi dữ liệu; Raw không chạy Cleaning hay huấn luyện model. Chi tiết: [raw_data_handover.md](raw_data_handover.md).

## 7. Kiểm thử, CI và source pins

```powershell
& .\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\venv\Scripts\python.exe -B -m scripts.verification.check_repository
& .\venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py'
```

`check_repository` kiểm cú pháp Python, JSON config, byte SHA256 của 6 source M4, link Markdown và YAML/action pins. `.gitattributes` giữ LF cho sáu file đó. Nếu hash sai, đối chiếu source; không sửa hằng số pin để vượt kiểm chứng.

`tests/` được giữ local và bỏ qua bởi Git; lệnh unittest trên cần thư mục test có sẵn. [CI GitHub](../.github/workflows/ci.yml) chạy kiểm tra source/docs/workflow và CLI imports trên Windows/Linux, bỏ qua bước unittest khi checkout không có test. [Workflow ingest thủ công](../.github/workflows/raw-ingest.yml) cần self-hosted runner nối tới deployment có dữ liệu.
