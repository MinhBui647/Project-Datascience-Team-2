# EPL Raw Storage V2 — Raw Data Access / Cleaning Handover

## Purpose

This is a **read-only, bounded access interface** for Team Member #2 (Cleaning). It does not clean, relabel, aggregate, or rewrite raw data. It does **not** automatically run the existing Cleaning pipeline. The team's already-cleaned data in the Hugging Face `clean` folder remains maintained by the Cleaning owner.

## Verified source data (2026-10-25)

- PostgreSQL database: `epl_raw_storage_v2`.
- Completed snapshot: `368`, HF revision `57d166f42099a561483f5d96aa0b4e93207fac6d`.
- Full manifest: 316 files; Raw scope: 298 unique file observations.
- Logical record references: 8,502,616 (includes reuse; **not** the number newly inserted by this update).
- Format breakdown: 86 CSV, 9 CSV.GZ, 201 JSON, 2 TAR.GZ.
- JSONB source column: `raw.raw_records.raw_data`.

## Prerequisites

Run from the repository root with Python venv, PostgreSQL connection config and existing `scripts.tools.incremental_completed_snapshot_audit.db_parameters(readonly=True)` from M4.

## Commands (Windows PowerShell)

Inspect up to five raw files, without reading record bodies:

```powershell
& .\venv\Scripts\python.exe -B -m scripts.access.raw_data_access --list-files --snapshot-id 368 --limit 5
```

Use the exact `relative_path` returned by that command to sample one or two records:

```powershell
& .\venv\Scripts\python.exe -B -m scripts.access.raw_data_access --records --snapshot-id 368 --path 'COPY_RELATIVE_PATH_HERE' --limit 2
```

Results include `raw_data` (original JSONB), record/file IDs, record locator, optional archive member ID, league/season, temporal and metadata evidence. Timestamps are formatted in ISO 8601. `next_after_path` and `next_after_record_id` are continuation cursors, not record counts. Reuse them in the next command (`--after-path`, `--after-record-id`). A `null` continuation cursor means no more rows on that query.

Example using pages in a consumer program:

```python
from scripts.access.raw_data_access import connect_readonly, verify_readonly, records_page

with connect_readonly() as conn:
    with conn.cursor() as cur:
        verify_readonly(cur)
        cursor = 0
        while True:
            page = records_page(cur, snapshot_id=368,
                relative_path='REPLACE_WITH_OBSERVED_PATH', limit=20,
                after_record_id=cursor)
            for record in page['records']:
                process_original_jsonb(record['raw_data'])  # Your Cleaning callback
            cursor = page['next_after_record_id']
            if cursor is None:
                break
```

The sample `process_original_jsonb` is a placeholder owned by the Cleaning team, not an implemented function. For production Cleaning, use a dedicated read-only database user with SELECT-only privileges and appropriate snapshot retention coordination.

## Guarantees and limits

- Snapshot must be `completed`; each requested path must belong to that exact snapshot.
- File membership is resolved by `raw.file_observations`, then versioned raw JSONB records are read via `raw.raw_records.file_id`. This supports reused immutable file versions.
- Strict PostgreSQL `transaction_read_only=on`; *only* parameterized SELECT statements; no raw export file is created by this CLI.
- Keyset pagination: maximum 50 rows per query and maximum 8 MiB JSON response; reduce `--limit` for unusually large JSON documents.
- No guarantee that per-file `record_id` values are contiguous or start at 1; use the returned continuation cursor.
- Large 8.5M reference count is metadata, not a full export. This is a bounded handover adapter and smoke-testable read interface.
- A shared PostgreSQL database or a provisioned read-only DB account is needed; a GitHub clone does not contain the database data.
- The M5 Windows scheduler (19:00 daily) is read-only revision monitoring, **not** scheduled ingestion or Cleaning.

## Team acceptance

1. Run `--list-files` against a completed snapshot.
2. Run `--records` for a returned `relative_path` and verify `raw_data` is JSONB-compatible.
3. Inspect output format and use keyset cursors in Cleaning integration.
4. Report any necessary field projections to the Raw Storage owner without changing immutable raw data.



---

## PostgreSQL Remote Connection — Team Member #2

### 1. Connection Overview

Raw Storage được lưu trữ trong PostgreSQL trên máy của Team Member #3.

Team Member #2 truy cập từ xa thông qua **Tailscale VPN** và tài khoản PostgreSQL chỉ có quyền đọc (read-only).

| Parameter | Value |
|---|---|
| Database | `epl_raw_storage_v2` |
| Schema | `raw` |
| Host | Tailscale IP của Team Member #3 |
| Port | `5432` |
| Username | `epl_cleaner` |
| Password | 1234 |
| Permission | Read-only (SELECT) |

**Prerequisites:**
- Team Member #3 đã tạo tài khoản và cấp quyền truy cập.
- PostgreSQL và Windows Firewall đã cho phép kết nối qua Tailscale.
- Máy Team Member #3 đang bật và PostgreSQL đang hoạt động.
- Hai thành viên đã kết nối qua Tailscale.

### 2. Establish Network Connection

**Step 1 — Install Tailscale**

Tải Tailscale tại: https://tailscale.com/download

Đăng nhập bằng tài khoản riêng và chấp nhận lời mời chia sẻ thiết bị từ Team Member #3.

**Step 2 — Verify Connection**

Mở PowerShell và chạy:

```powershell
Test-NetConnection <MEMBER_3_TAILSCALE_IP> -Port 5432
```

Kết quả mong muốn:

```text
TcpTestSucceeded : True
```

Nếu kết nối thất bại, liên hệ Team Member #3 để kiểm tra PostgreSQL, Tailscale và Firewall.

### 3. Configure Database Connection

Tại thư mục gốc dự án, tạo môi trường Python:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Tạo các file cấu hình nếu chưa tồn tại:

```powershell
Copy-Item .env.example .env
Copy-Item .env.v2.example .env.v2
```

Cập nhật file `.env`:

```dotenv
DB_HOST=<MEMBER_3_TAILSCALE_IP>
DB_PORT=5432
DB_NAME=epl_raw_storage_v2
DB_USER=epl_cleaner
DB_PASSWORD=<PASSWORD_PROVIDED_PRIVATELY>
```

Đảm bảo file `.env.v2` có cấu hình:

```dotenv
DB_NAME=epl_raw_storage_v2
```

**Security:** Không commit `.env`, `.env.v2`, mật khẩu hoặc thông tin xác thực lên GitHub.

### 4. Verify Raw Data Access

Sau khi cấu hình thành công, thực hiện kiểm tra truy xuất dữ liệu.

**List raw files:**

```powershell
python -B -m scripts.access.raw_data_access `
    --list-files `
    --snapshot-id 368 `
    --limit 5
```

**Read sample records:**

```powershell
python -B -m scripts.access.raw_data_access `
    --records `
    --snapshot-id 368 `
    --path "RELATIVE_PATH" `
    --limit 2
```

Trong đó:
- `snapshot-id`: ID của snapshot đã hoàn thành (`completed`).
- `RELATIVE_PATH`: Đường dẫn file lấy từ kết quả `--list-files`.
- `limit`: Số bản ghi đọc trong một lần.

Snapshot `367` chỉ là ví dụ. Sử dụng snapshot mới nhất đã được Team Member #3 xác nhận.

Kết quả thành công cần có trạng thái `VERIFIED_READONLY` và trả về dữ liệu thô cùng metadata.

### 5. Data Access Rules

- Chỉ sử dụng tài khoản `epl_cleaner` để truy xuất dữ liệu.
- Không chỉnh sửa hoặc xóa dữ liệu trong schema `raw`.
- Dữ liệu truy xuất được sử dụng cho Cleaning & Integration.
- Ghi nhận `snapshot_id`, `file_id` và phiên bản nguồn đã xử lý để hỗ trợ incremental cleaning.
- Liên hệ Team Member #3 khi cần quyền truy cập hoặc gặp lỗi kết nối.

**Important:** PostgreSQL hiện được lưu trên máy cá nhân của Team Member #3. Khi máy chủ tắt, Sleep hoặc PostgreSQL ngừng hoạt động, Team Member #2 không thể truy xuất dữ liệu từ xa.

---
