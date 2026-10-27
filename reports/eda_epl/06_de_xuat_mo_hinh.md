# 06 — Đề xuất mô hình và kế hoạch kiểm định

[Mục lục](00_muc_luc.md) · [Danh sách đặc trưng CSV](feature_candidates.csv)

## Bài toán và đặc trưng

**Mục tiêu chính:** Trước trận, dự đoán ba xác suất `P(H)`, `P(D)`, `P(A)` có tổng bằng 1. Chỉ dùng thông tin đã có trước giờ bóng lăn. Không đưa `home_goals`, `away_goals`, `result`, `status`, thứ hạng cuối mùa hoặc thống kê cập nhật sau trận vào X. `date`, `season`, đội nhà/khách dùng để kiểm soát thời gian và kiểm tra dữ liệu; chỉ mã hóa tên đội nếu có quy trình tránh rò rỉ và kiểm tra đội mới lên hạng.

| Nhóm | Biến nên thử | Lý do EDA | Lưu ý |
|---|---|---|---|
| Sức mạnh | `elo_diff` | Tỷ lệ thắng nhà 19,4% → 72,4% giữa hai nhóm cực trị | Kiểm tra cách Elo được cập nhật: chỉ dùng kết quả trước trận |
| Phong độ mùa | `home_season_ppg - away_season_ppg` | Chênh tỷ lệ thắng 43,7 điểm % | Có thể thiếu ở đầu mùa; không dùng thứ hạng cuối mùa |
| Phong độ 5 trận | `home_points_avg5 - away_points_avg5` | Chênh 35,5 điểm % | Định nghĩa 5 trận phải loại chính trận dự đoán |
| Khả năng ghi bàn | `home_goals_for_avg5 - away_goals_for_avg5` | Chênh 32,0 điểm % | Kiểm tra phần thông tin tăng thêm ngoài Elo và PPG |
| Chất lượng cơ hội | `home_xg_avg5 - away_xg_avg5` | Chênh 38,7 điểm % | Chỉ so sánh trên các mùa có xG, cùng số trận |
| Đối đầu | `h2h_home_ppg` | Chênh 32,3 điểm % | Thử sau Elo và phong độ; thiếu 8,1% toàn bộ dữ liệu |
| Sút trúng đích | Chênh lệch trung bình 5 trận | Có thể đo sức tấn công | Chưa có kết luận từ biểu đồ; cần kiểm định riêng |

`rest_diff` **tạm hoãn** vì khoảng cách giữa hai trận Premier League có thể kéo dài nhiều năm khi đội vắng giải. `squad_value_log_ratio` **tạm hoãn** vì thiếu 95,6% toàn file và cần xác nhận giá trị đội hình đã công bố trước trận. Có thể bắt đầu bằng biến chênh lệch để giảm số cột; không thêm đồng thời `elo_diff`, `home_elo`, `away_elo` vào hồi quy không điều chuẩn.

## Thứ tự mô hình đề xuất

1. **Mốc tần suất lớp:** Ước lượng tỷ lệ H/D/A chỉ từ tập train. Đây là chuẩn tối thiểu cho log loss; mốc “luôn H” chỉ dùng cho accuracy.
2. **Logistic đa lớp với `elo_diff`:** Mô hình đơn giản, dễ giải thích và cho xác suất. Có thể thêm intercept để hấp thụ lợi thế sân nhà; không diễn giải intercept là hiệu ứng nhân quả.
3. **Logistic đa lớp có điều chuẩn với Elo + phong độ:** Lần lượt thêm điểm/trận mùa, điểm 5 trận, bàn thắng 5 trận; điền thiếu và chuẩn hóa chỉ theo train. Đây là phép kiểm định xem phong độ có bổ sung thông tin.
4. **Logistic đa lớp hoặc mô hình cây tăng cường với xG:** Dùng cùng mẫu trận có xG; thêm xG, rồi đối đầu và sút trúng đích. Mô hình cây dùng để kiểm tra tương tác và quan hệ phi tuyến, nhưng cần giới hạn độ phức tạp vì mỗi mùa chỉ 380 trận.
5. **Poisson bàn thắng nhà/khách (nhánh phụ):** Ước lượng hai số bàn thắng kỳ vọng rồi suy ra H/D/A. Hữu ích nếu sản phẩm cần cả phân phối tỉ số. Kiểm tra riêng giả định Poisson/độc lập và so sánh log loss H/D/A với mô hình phân lớp trực tiếp.

Chưa có kết quả huấn luyện, nên đây là **đề xuất thứ tự thử**, không phải xếp hạng mô hình đã được chứng minh. PCA không cần cho vòng đầu; chỉ thử như một nhánh nếu mô hình tuyến tính gặp vấn đề và giữ toàn bộ phép biến đổi trong pipeline train.

## Chia tập và đánh giá

- Sắp trận theo `date`; các trận cùng ngày ở cùng một phía của ranh giới train/test. Dùng đánh giá cuốn chiếu theo mùa: huấn luyện trên các mùa trước, kiểm tra ở mùa tiếp theo. Giữ **2025/26 làm tập kiểm tra cuối** sau khi chọn mô hình bằng các mùa trước đó; không chọn biến hoặc siêu tham số từ điểm của tập này.
- Để so sánh hai bộ đặc trưng, dùng **cùng tập trận, cùng các mùa, cùng cách điền thiếu**. Mọi bộ biến đổi (điền thiếu, chuẩn hóa, PCA, hiệu chỉnh xác suất) phải fit trong tập train của mỗi lần chia.
- Báo cáo log loss đa lớp làm thước đo chính cho xác suất; kèm macro-F1, balanced accuracy, confusion matrix và đồ thị hiệu chỉnh xác suất theo lớp. Ghi cả trung bình và biến động qua mùa; xem riêng lớp hòa D vì tỷ lệ thấp nhất.
- So sánh tuần tự: Elo → + phong độ → + xG → + đối đầu/sút trúng đích. Chỉ giữ một nhóm biến khi cải thiện lặp lại ở các mùa kiểm tra và trên tập holdout cuối. Nếu dùng mô hình cây, kiểm tra tầm quan trọng bằng phép bỏ từng **nhóm** biến trên dữ liệu kiểm tra, không suy từ tương quan đơn biến.

## Giả thuyết có thể bác bỏ

1. Elo chênh lệch có khả năng dự đoán H/D/A tốt hơn mốc tần suất lớp ở các mùa tương lai.
2. Phong độ 5 trận và điểm/trận mùa giảm log loss thêm so với chỉ Elo trên cùng tập trận.
3. xG gần đây giảm log loss thêm so với Elo + phong độ và bàn thắng gần đây.
4. Đối đầu trực tiếp cung cấp thông tin mới sau khi đã xét Elo, phong độ và xG.
5. Mô hình cây cải thiện ổn định so với logistic đa lớp nhờ tương tác/phi tuyến; nếu không, ưu tiên mô hình đơn giản hơn.
6. Lợi thế sân nhà thay đổi theo mùa; mô hình cố định qua nhiều mùa có thể cần hiệu chỉnh xác suất trên dữ liệu gần thời điểm dự đoán.

Giả thuyết về **khác biệt giữa các giải đấu** chưa thể kiểm tra vì dataset hiện chỉ có Premier League.
