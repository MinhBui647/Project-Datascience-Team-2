# Mục lục EDA — matches.csv và features.csv

Đây là bộ EDA hiện tại. Nguồn đầu vào là clean/matches.csv và clean/features.csv ở gốc dự án. Các thư mục reports/eda_epl và mã EDA trong đó là tài liệu của lần phân tích trước.

Thời điểm lập: 2026-10-26T22:39:46 (UTC+07). Hai bảng có 9.850 trận khớp theo match_id; matches có 106 cột, features có 102 cột, trong đó 87 đặc trưng ứng viên.

[Mở báo cáo HTML có đầy đủ biểu đồ](report.html) · [Báo cáo Markdown](report.md)

| Phần phân tích | Liên kết |
|---|---|
| 1. Các phát hiện chính | [Xem trong báo cáo](report.html#section-2) |
| 2. Phạm vi, đơn vị quan sát và cách lấy mẫu | [Xem trong báo cáo](report.html#section-3) |
| 3. Kiểm toán dữ liệu và dữ liệu thiếu | [Xem trong báo cáo](report.html#section-4) |
| 4. Bàn thắng, bàn thua, nhãn và tỷ số | [Xem trong báo cáo](report.html#section-5) |
| 5. Mùa giải và lợi thế sân nhà | [Xem trong báo cáo](report.html#section-6) |
| 6. Nhóm đội và khả năng ghi bàn/phòng ngự | [Xem trong báo cáo](report.html#section-7) |
| 7. Thống kê cùng trận và đối chiếu nguồn xG | [Xem trong báo cáo](report.html#section-8) |
| 8. Ý nghĩa và thời điểm có đặc trưng | [Xem trong báo cáo](report.html#section-9) |
| 9. Phong độ, bàn thắng trung bình, đối đầu và kết quả | [Xem trong báo cáo](report.html#section-10) |
| 10. Bất thường và quyết định xử lý | [Xem trong báo cáo](report.html#section-11) |
| 11. Đặc trưng tiềm năng, trùng thông tin và độ ổn định | [Xem trong báo cáo](report.html#section-12) |
| 12. Giả thuyết cần kiểm tra bằng mô hình | [Xem trong báo cáo](report.html#section-13) |
| 13. Kế hoạch đánh giá và giới hạn diễn giải | [Xem trong báo cáo](report.html#section-14) |
| 14. Bàn giao và tái lập | [Xem trong báo cáo](report.html#section-15) |

## Sản phẩm bàn giao

- [Bộ 16 biểu đồ PNG](figures/)
- [Danh mục 87 đặc trưng tiềm năng](feature_candidates.csv)
- [9 giả thuyết mô hình](hypotheses.csv)
- [Kết quả kiểm tra chất lượng](data_quality_checks.csv)
- [Các dòng cần xác minh](data_issues.csv)
- [Metadata và SHA256 nguồn](analysis_metadata.json)
- [Toàn bộ bộ bàn giao ZIP](../EDA_matches_features.zip)

## Chạy lại EDA

Từ gốc dự án:

    python reports/eda_matches_features/run_eda.py

Lệnh này chỉ đọc hai CSV trong clean/; không cần thư mục data/.
Phạm vi hiện chỉ có Premier League, nên chưa có so sánh giữa các giải đấu.
