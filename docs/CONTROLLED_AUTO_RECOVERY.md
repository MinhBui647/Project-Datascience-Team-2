# CSV/JSON và Controlled Auto-Recovery

## Trạng thái đã triển khai

`scripts.orchestration.auto_ingest` hỗ trợ candidate **JSON và CSV không nén** trong cùng một snapshot. CSV được parse thành `csv_row`, giữ giá trị gốc dưới dạng JSONB; header và record locator được kiểm tra trước khi ghi. File không đổi tiếp tục liên kết lại phiên bản cũ.

Kiểm tra PostgreSQL ngày 25/10/2026 xác nhận snapshot #368 chứa:

| Định dạng | File | Tham chiếu record |
|---|---:|---:|
| CSV | 86 | 993.784 |
| CSV.GZ | 9 | 7.497.120 |
| JSON | 201 | 959 |
| TAR.GZ | 2 | 10.753 |

Lượt HF mới tạo #368 trước đây có 6 candidate JSON; các CSV trong snapshot đó được reuse. Kiểm thử mới chạy toàn luồng **CSV + JSON** bằng manifest, cache validation, archive, parser, SQL writer và checkpoint/journal thật; transport và SQL storage được thay bằng fixture. Chưa có revision HF mới chứa CSV thay đổi được dùng để nghiệm thu trực tiếp; không suy diễn kiểm thử fixture thành nghiệm thu CSV update trên nguồn thật.

## Retry có kiểm soát

Mặc định có tối đa **3 lần thử**, tính cả lần đầu, với exponential backoff và jitter: khoảng chờ ngẫu nhiên từ 0 đến 2 giây, rồi 0 đến 4 giây. Khoảng chờ được chặn ở 30 giây. Có thể cấu hình 1–5 lần thử và tối đa 60 giây chờ qua CLI.

| Tình huống | Hành vi |
|---|---|
| Timeout/reset/refused kết nối tạm thời, HTTP 408/429/500/502/503/504 | Retry thao tác đọc/chuẩn bị an toàn trong budget |
| PostgreSQL serialization failure, deadlock, lock unavailable, quá nhiều kết nối, server đang khởi động | Có thể retry sau khi kiểm tra điều kiện của stage |
| Preflight chỉ đọc thất bại tạm thời | Retry trước khi dành claim cho writer; không tạo write claim chỉ vì preview lỗi |
| Writer đã thoát với lỗi tạm thời được nhận diện | Audit DB độc lập trước; stage đã đủ thì bỏ qua ghi, stage thiếu hợp lệ thì chạy lại với attempt mới |
| Writer thành công nhưng readback tạm thời không truy cập được | Retry readback; nếu hết lượt thì lưu bằng chứng process thành công để lần chạy sau chỉ xác minh |
| Snapshot registration đã commit, thiếu REGISTERED checkpoint | Khi có bằng chứng terminal đáng tin cậy, đọc lại đúng snapshot từ DB và publish checkpoint; không đăng ký snapshot lần hai |
| Process crash/claim thiếu kết quả, writer timeout hoặc kết quả commit không rõ | Chặn và báo cần can thiệp; không tự replay |
| Sai hash, hỏng CSV/JSON, thiếu archive, sai metadata, lỗi quyền/authentication hoặc constraint | Chặn và báo nguyên nhân; không retry mù |
| Worker khác đang giữ khóa | `DEFERRED_BUSY`; đợi worker hiện tại hoặc lịch tiếp theo |

Classifier dùng kiểu exception, HTTP status, SQLSTATE hoặc diagnostic exception được allowlist của child. Một chuỗi `RuntimeError` tùy ý chứa từ “timeout” không trở thành quyền chạy lại writer. Lỗi TLS/cấu hình/chứng thực cũng không tự chuyển thành lỗi tạm thời.

## Chống ghi trùng khi phục hồi

Luồng vẫn giữ nguyên revision SHA, baseline, fingerprint, manifest hash, số file/byte/record và argv của writer. `active.json` ưu tiên revision đang dở, kể cả khi HF `main` đã có commit mới.

Mỗi lần retry ghi cần bằng chứng child trước đã kết thúc, lỗi thuộc nhóm được phép và audit DB mới xác nhận stage chưa hoàn thành nhưng còn hợp lệ. Writer/preflight cũ tiếp tục kiểm tra transaction, archive và observation:

- Registration chỉ insert khi target còn vắng. Target đã có phải được đối chiếu ID, revision, baseline và metadata.
- REUSE và manifest registration dùng transaction riêng; observation đã hoàn tất được kiểm chứng trước khi tiếp tục.
- Candidate writer commit từng file. Khi file đã `LINKED_VERIFIED`, coordinator bỏ qua file đó; chỉ file còn thiếu được nạp. Record locator và file version không được nhân bản.
- Finalization chỉ chuyển đúng target từ `in_progress` sang `completed` sau khi kiểm đủ record/manifest/archive. Một exit code `0` không thay thế independent audit.

Các stage đã ghi `VERIFIED_AFTER_WRITE` vẫn phải khớp evidence hiện tại. Nếu journal nói thành công nhưng DB chưa đủ, hệ thống báo `CONTRADICTORY_VERIFIED_EVIDENCE`; nó không tự ghi lại để che sai lệch.

## Journal bất biến và budget bền vững

Claim/result M5 gốc vẫn nằm tại:

```text
reports/m5_write_attempts/v1/<repo_hash>/<revision>/<STAGE>.claim.json
reports/m5_write_attempts/v1/<repo_hash>/<revision>/<STAGE>.result.json
```

Recovery bổ sung ledger create-only:

```text
reports/auto_ingest/recovery/<repo_hash>/<revision>/<STAGE>/
  0001.claim.json
  0001.result.json
  0001.verified.json
  0002.claim.json
  0002.result.json
  0002.verified.json
```

Claim mới gắn với hash claim M5 gốc, context SHA256, argv hash và hash kết quả attempt trước. Result phân biệt `CHILD_SUCCEEDED`, `TRANSIENT_FAILURE`, `OUTCOME_UNKNOWN`; marker verified gắn với đúng hash result. Hash/identity/timestamp/gap/orphan bị sai sẽ chặn phục hồi.

Chỉ `TRANSIENT_FAILURE` chưa được xác minh hoàn tất mới cho phép tạo attempt writer tiếp theo. `CHILD_SUCCEEDED` chỉ cho phép xác minh, không replay. Thiếu result hoặc `OUTCOME_UNKNOWN` tiếp tục là hàng rào cần can thiệp.

Budget writer được tính từ ledger trên ổ đĩa, **không đặt lại khi chạy lệnh lần nữa hoặc qua ngày mới**. Hết 3 attempt mặc định mà vẫn chưa hoàn thành sẽ báo `WRITER_RETRY_BUDGET_EXHAUSTED`. Không xóa/đổi journal để ép retry. Muốn thay policy cần đối soát nguyên nhân, bằng chứng DB và các attempt trước; CLI không có cờ bỏ qua fence.

Claim cũ từ workflow trước, không có terminal process evidence trong recovery ledger, vẫn cần đối soát thủ công. Việc thêm recovery không tự cấp quyền chạy lại claim lịch sử chưa rõ kết quả.

## Lệnh vận hành

Lịch 19:00 đã gọi `auto_ingest --run`; policy mặc định tự áp dụng khi task nạp source mới, không phải cài lại task.

```powershell
# Chạy toàn bộ ingest/recovery với policy mặc định.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --run

# Chỉ đọc nguồn/DB/checkpoint và kiểm tra fence; không ghi PostgreSQL.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --check

# Xem kết quả gần nhất từ file local, không cần mạng hay PostgreSQL.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --status

# Tùy chỉnh retry khi cần; max-attempts bao gồm lần thử đầu.
& .\venv\Scripts\python.exe -B -m scripts.orchestration.auto_ingest --run `
    --max-attempts 3 --retry-base-seconds 2 --retry-max-seconds 30
```

`--max-attempts 1` tắt việc thử lại trong lượt hiện tại. Lượt vẫn xác minh checkpoint và giữ hàng rào ghi; tham số này không xóa lịch sử attempt.

## Đọc báo cáo và quyết định can thiệp

Receipt từng lượt vẫn ở `reports/auto_ingest/runs/<run_id>/receipt.json`. File `reports/auto_ingest/latest.json` lưu kết quả gần nhất để phục vụ `--status`.

| Trường | Ý nghĩa |
|---|---|
| `state` | `COMPLETED`, `SKIPPED_CURRENT`, `READY_TO_INGEST` hoặc `BLOCKED` |
| `recovery_status` | Kết quả recovery cụ thể theo bảng bên dưới |
| `intervention_required` | Có cần người vận hành đối soát/sửa nguyên nhân hay không |
| `failed_stage`, `reason_code`, `error_category`, `detail` | Bước lỗi và nguyên nhân đã lọc thông tin bí mật |
| `retry_policy`, `retry_events` | Budget, attempt, thời gian chờ và nhóm lỗi của các retry thao tác an toàn |
| `recoveries` | Bước đã đối soát không ghi lại hoặc retry sau audit thiếu hợp lệ |
| `candidate_formats` | Số candidate JSON/CSV khi có kế hoạch |
| `receipt_path`, `next_action` | Đường dẫn bằng chứng và hành động tiếp theo |

| Recovery status | Cách xử lý |
|---|---|
| `NOT_NEEDED` | Không phát sinh lỗi cần recovery |
| `RECOVERED` | Lượt đã xử lý thành công lỗi tạm thời/đối soát |
| `RESUME_AVAILABLE` | Check tìm thấy lượt active có evidence cho phép `--run` tiếp tục |
| `RETRY_EXHAUSTED` | Lỗi tạm thời hết budget của thao tác an toàn trong lượt này; lần chạy tiếp theo có thể thử lại từ state đã pin |
| `DEFERRED_BUSY` | Worker khác còn chạy; không cần khởi động thêm |
| `MANUAL_INTERVENTION_REQUIRED` | Sửa lỗi dữ liệu/quyền/cấu hình hoặc đối soát DB/checkpoint/journal trước khi tiếp tục |

Với `MANUAL_INTERVENTION_REQUIRED`, giữ nguyên claim/result/checkpoint và đọc đúng `<STAGE>.write.log` của receipt, cùng các thư mục `<STAGE>_attempt_0002/` nếu có. Không chạy lại từng writer CLI, không đổi revision và không xóa journal để vượt chặn. Với `RETRY_EXHAUSTED` sau lỗi readback, lần `--run` sau dùng terminal success receipt để xác minh mà không ghi lại.

Exit code `2` báo lượt chưa hoàn tất. `intervention_required` và `recovery_status` phân biệt lỗi tạm thời còn có thể tự tiếp tục với lỗi cần người vận hành; Task Scheduler giữ chính sách không restart mù toàn pipeline.

## Bằng chứng nghiệm thu 25/10/2026

- Toàn bộ repository tại nghiệm thu recovery: **1.968 test chạy, 17 skip, không lỗi**. Bao gồm `--status`, kiểm fence khi `--check`, context/argv drift, journal hash/timestamp, budget bền vững và phục hồi primary result sau khi có terminal success proof. Log local: `reports/auto_ingest/final_controlled_recovery_regression.log`. Nhóm kiểm thử phân loại lỗi/backend được chạy lại sau khi siết kiểm HTTP status, SQLSTATE và DNS cấu hình. CI tiếp tục chạy cả các test kiểm repository/workflow bổ sung sau mốc này.
- Integration CSV+JSON dùng writer SQL thật trên SQL storage fixture; có kiểm tra nạp lại file version không tăng record và CSV lỗi dừng trước registration.
- Recovery integration mô phỏng preflight tạm thời, writer lỗi trước/sau commit, lỗi readback, thiếu REGISTERED sau commit, timeout chưa rõ kết quả và budget xuyên lượt.
- Nghiệm thu đọc nguồn thật sau **một lỗi kết nối giả lập** lúc **12:12:29 UTC+7**: retry 1 lần, `recovery_status=RECOVERED`, `SKIPPED_CURRENT`, snapshot #368, `write_executed=false`. Đây là fault injection để xác minh cơ chế, không phải tuyên bố HF thực tế đã gián đoạn.
- Báo cáo local có thống kê PostgreSQL thật: `reports/auto_ingest/controlled_recovery_acceptance.json`.
- Task AutoIngest được kích hoạt thử lúc **12:13:04 UTC+7**, wrapper exit `0`; receipt `SKIPPED_CURRENT`, `write_executed=false`, policy mặc định `3 / 2s / 30s`. Bằng chứng local: `reports/scheduled_tasks/20261025T051304056Z-AutoIngest-31272.status.json`.

Các artifact `reports/` chỉ nằm trên máy vận hành và bị ignore khi đưa mã nguồn lên Git.

[CI GitHub](../.github/workflows/ci.yml) chạy regression recovery bằng fixture, không truy cập hoặc upload journal production. [Workflow self-hosted dispatch](../.github/workflows/raw-ingest.yml) sử dụng cùng runner/recovery policy trong deployment hiện có, không tự xóa fence hay reset budget.

Các module chính: [policy retry](../scripts/orchestration/auto_recovery.py), [ledger recovery](../scripts/orchestration/auto_recovery_journal.py), [điều phối DB có kiểm chứng](../scripts/orchestration/controlled_ingest_dispatch.py), [entrypoint tự động](../scripts/orchestration/auto_ingest.py), [backend M4](../scripts/orchestration/auto_ingest_backend.py).

## Giới hạn còn giữ

JSON tối đa 16 MiB/file; CSV không nén tối đa 64 MiB/file; tối đa 250.000 record/candidate và tổng candidate tối đa 64 MiB/plan. Candidate CSV.GZ/TAR.GZ mới/thay đổi và path bị xóa vẫn bị chặn theo hợp đồng incremental hiện tại. CSV.GZ/TAR.GZ không đổi được reuse. Recovery không mở rộng format/budget và không bỏ qua validation.

Thiết kế phân biệt SQLSTATE theo [PostgreSQL Error Codes](https://www.postgresql.org/docs/15/errcodes-appendix.html); HTTP status/response từ [Hugging Face error utilities](https://huggingface.co/docs/huggingface_hub/package_reference/utilities). Các quy tắc cho phép replay trong dự án còn yêu cầu audit độc lập và ledger như mô tả ở trên.
