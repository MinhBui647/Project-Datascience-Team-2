# Khám phá dữ liệu Premier League

Báo cáo này tìm hiểu dữ liệu Premier League đã được làm sạch và gợi ý những thông tin có thể dùng để dự đoán trước trận. Các con số dưới đây mô tả bản dữ liệu hiện có đến ngày 12/10/2026; báo cáo chưa đối chiếu độc lập từng kết quả trận đấu với nguồn ngoài.

Báo cáo hoàn thành lúc 20:31 ngày 27/10/2026 (UTC+07). Hai bảng khớp nhau ở 9.850 trận theo match_id: matches có 106 cột, features có 102 cột, trong đó 87 cột là đặc trưng ứng viên.

## 1. Các phát hiện chính

- Dữ liệu hiện có chỉ gồm Premier League, với 46 đội thi đấu từ 19/08/2000 đến 12/10/2026. Để so sánh giữa các mùa, báo cáo dùng 24 mùa có đủ trận, tương ứng 9.120 trận.
- Tính trên toàn bộ dữ liệu, chủ nhà thắng 45,6%, hòa 24,8%, còn đội khách thắng 29,6%. Các tỷ lệ này cho thấy lợi thế sân nhà trong những trận đã quan sát, dù mức độ thay đổi theo mùa.
- Trong các mùa có đủ trận, trung bình mỗi trận có 1,54 bàn của chủ nhà và 1,19 bàn của đội khách. Chênh lệch bàn thắng trung bình nhà−khách là 0,35; khoảng bootstrap theo mùa 95% là [0,30; 0,39].
- Elo, odds, phong độ và lịch sử đối đầu đều có liên hệ riêng với kết quả. Khi chỉ xét những trận có hai đội gần nhau về Elo, chênh lệch theo điểm phong độ và đối đầu (H2H) thu hẹp. xG gần đây vẫn đáng để kiểm tra thêm, nhưng giá trị bổ sung của nó chưa được chứng minh bằng mô hình.
- Có 167 trận cần đối chiếu thống kê sút giữa các nguồn để kiểm tra tính nhất quán. Biến rest_days chỉ đo khoảng cách tới trận EPL quan sát trước đó, không phải thời gian nghỉ trên mọi đấu trường.
- Khi tính lại độc lập 43 cột lịch sử trên toàn bộ trận, báo cáo ghi nhận 0 sai khác. Kết quả này chưa xác nhận thời điểm công bố dữ liệu hoặc bao quát mọi tình huống của cả 87 biến.

## 2. Phạm vi, đơn vị quan sát và cách lấy mẫu

| Bảng / mẫu | Số dòng | Số cột / mùa | Mục đích |
|---|---|---|---|
| matches.csv | 9.850 | 106 | Một trận; tỷ số, thống kê cùng trận, nguồn và cờ chất lượng |
| features.csv | 9.850 | 102 | Một trận; đặc trưng trước trận theo thiết kế, định danh và nhãn |
| Mẫu so sánh mùa đủ | 9.120 | 24 mùa | 380 trận, 20 đội và 38 trận mỗi đội trong từng mùa |

| Mùa | Số trận | Số đội | Ghi chú |
|---|---|---|---|
| 2003/04 | 335 | 20 | Mùa lịch sử thiếu trận |
| 2004/05 | 335 | 20 | Mùa lịch sử thiếu trận |
| 2026/27 | 60 | 20 | Mùa đang diễn ra trong snapshot |

Bộ dữ liệu chỉ có Premier League nên không thể so sánh giữa các giải. Tất cả trận hiện đều mang trạng thái played; không có provisional, postponed hoặc upcoming. Thống kê trên toàn bộ dữ liệu vẫn bao gồm các mùa thiếu trận. Riêng những biểu đồ về xu hướng, nhóm đội và phân phối bàn thắng chính dùng mẫu mùa có đủ trận; phạm vi này được ghi rõ ở từng hình.

![Hình 01. Các mùa thiếu trận và độ phủ của các chỉ số theo thời gian. Màu cam không mặc nhiên có nghĩa dữ liệu sai.](figures/01_coverage.png)

Hình 01. Các mùa thiếu trận và độ phủ của các chỉ số theo thời gian. Màu cam không mặc nhiên có nghĩa dữ liệu sai.

## 3. Kiểm toán dữ liệu và dữ liệu thiếu

| Trạng thái | Số kiểm tra |
|---|---|
| Đạt | 243 |
| Cảnh báo cần diễn giải | 7 |
| Thông tin | 3 |
| Không đạt | 0 |

Mỗi bảng không có match_id trùng lặp. Khi ghép một–một theo mã này, ngày, đội, tỷ số và kết quả đều khớp. Phần kiểm tra dữ liệu cũng xem xét miền giá trị, quan hệ giữa tỷ số và nhãn, các thống kê cùng trận và lịch sử trước trận. Những cảnh báo về khác biệt giữa các nguồn, số khán giả được điền và lịch sử cũ được ghi lại riêng; dữ liệu nguồn không bị tự động xóa hay sửa.

[Chi tiết kiểm tra dữ liệu](data_quality_checks.csv)

[Các dòng được gắn cờ và lý do](data_issues.csv)

| Cột | Số thiếu | Tỷ lệ thiếu |
|---|---|---|
| home_points5 | 20 | 0,2% |
| home_ppg | 270 | 2,7% |
| h2h_home_points | 800 | 8,1% |
| diff_xg5 | 5.256 | 53,4% |
| diff_possession5 | 3.822 | 38,8% |
| diff_big_chances5 | 5.970 | 60,6% |
| diff_box_touches5 | 7.680 | 78,0% |
| diff_xgot5 | 7.531 | 76,5% |
| odds_home_prob | 760 | 7,7% |
| gameweek | 9.790 | 99,4% |

Nếu bỏ mọi trận có ô trống ở bất kỳ cột nào trong 102 cột, dữ liệu chỉ còn 47 trận. Ngay cả khi chỉ xét các biến dự báo và bỏ gameweek cùng nhãn, cũng chỉ còn 2.035 trận đầy đủ. Vì thế, không nên dùng dropna cho cả bảng hoặc điền 0 hàng loạt. xG chỉ bắt đầu từ 2014/15, còn các biến FotMob xuất hiện ở những thời điểm khác nhau; một ô thiếu do chưa có dữ liệu trong giai đoạn đó không đồng nghĩa với giá trị 0 thật.

![Hình 02. Mức thiếu dữ liệu ở một số cột của chủ nhà và các biến chênh lệch. feature_candidates.csv ghi mức thiếu của toàn bộ đặc trưng.](figures/02_missingness.png)

Hình 02. Mức thiếu dữ liệu ở một số cột của chủ nhà và các biến chênh lệch. feature_candidates.csv ghi mức thiếu của toàn bộ đặc trưng.

| Tổ hợp nguồn | Số trận | Tỷ lệ |
|---|---|---|
| FD+US+TM+FM | 4.560 | 46,3% |
| FD | 3.821 | 38,8% |
| FD+TM+FM | 760 | 7,7% |
| FD+FM | 649 | 6,6% |
| FD+US+FM | 60 | 0,6% |

FD = Football-Data; US = Understat; TM = Transfermarkt; FM = FotMob. Việc một trận có cờ ghép nguồn không có nghĩa mọi chỉ số từ nguồn đó đều sẵn có. gameweek chỉ xuất hiện ở mùa hiện tại, nên không thể dùng cột này để quyết định giữ các trận lịch sử.

## 4. Bàn thắng, bàn thua, nhãn và tỷ số

| Mẫu | Kết quả | Số trận | Tỷ lệ | Wilson 95% xấp xỉ |
|---|---|---|---|---|
| Toàn snapshot | Chủ nhà thắng | 4.495 | 45,6% | [44,7%; 46,6%] |
| Toàn snapshot | Hòa | 2.441 | 24,8% | [23,9%; 25,6%] |
| Toàn snapshot | Đội khách thắng | 2.914 | 29,6% | [28,7%; 30,5%] |
| Mùa đủ | Chủ nhà thắng | 4.172 | 45,7% | [44,7%; 46,8%] |
| Mùa đủ | Hòa | 2.233 | 24,5% | [23,6%; 25,4%] |
| Mùa đủ | Đội khách thắng | 2.715 | 29,8% | [28,8%; 30,7%] |

Nhóm kết quả phổ biến nhất có số trận gấp 1,84 lần nhóm ít gặp nhất. Nên theo dõi riêng khả năng nhận diện trận hòa, nhưng chênh lệch tỷ lệ này chưa phải lý do để tự động tăng mẫu cho lớp hòa. Các khoảng Wilson dựa trên xấp xỉ các trận độc lập; để suy luận chặt chẽ hơn, cần tính đến việc các đội xuất hiện lặp lại.

![Hình 03. Kết quả của toàn bộ trận trong dữ liệu; phân phối bàn thắng được tính trên 24 mùa có đủ trận.](figures/03_outcomes_goals.png)

Hình 03. Kết quả của toàn bộ trận trong dữ liệu; phân phối bàn thắng được tính trên 24 mùa có đủ trận.

| Đại lượng | Trung bình | Phương sai | Phương sai/TB | Tỷ lệ 0 |
|---|---|---|---|---|
| Bàn nhà | 1,538 | 1,699 | 1,104 | 23,2% |
| Bàn khách | 1,190 | 1,343 | 1,128 | 32,8% |
| Tổng bàn | 2,728 | 2,764 | 1,013 | 7,2% |

Hai đội cùng ghi bàn trong 51,2% số trận; chủ nhà giữ sạch lưới ở 32,8% số trận và đội khách ở 23,2%. Tương quan Pearson giữa bàn thắng của hai phía là -0,092. Các con số này giúp hình thành giả thuyết cho mô hình đếm, nhưng chưa đủ để khẳng định bàn thắng tuân theo phân phối Poisson hoặc hai phía độc lập với nhau.

Nếu gộp tất cả đội–trận trong giải, tổng bàn ghi luôn bằng tổng bàn thua và hai phân phối cũng đối ứng với nhau. Muốn tìm hiểu khác biệt về tấn công hay phòng ngự, cần xét theo đội, mùa và sân thay vì xem hai tổng của cả giải là hai nguồn thông tin riêng.

![Hình 04. Phân phối tỷ số và tổng bàn thắng so với phân phối Poisson có cùng trung bình. Hình này dùng để mô tả dữ liệu, chưa phải mô hình dự báo.](figures/04_score_counts.png)

Hình 04. Phân phối tỷ số và tổng bàn thắng so với phân phối Poisson có cùng trung bình. Hình này dùng để mô tả dữ liệu, chưa phải mô hình dự báo.

## 5. Mùa giải và lợi thế sân nhà

![Hình 05. Xu hướng qua các mùa có đủ trận. Mỗi điểm được tính từ 380 trận, nên các tỷ lệ và trung bình có cùng mẫu số.](figures/05_season_trends.png)

Hình 05. Xu hướng qua các mùa có đủ trận. Mỗi điểm được tính từ 380 trận, nên các tỷ lệ và trung bình có cùng mẫu số.

Trong các mùa có đủ trận, số bàn trung bình mỗi trận thấp nhất ở mùa 2006/07 (2,45) và cao nhất ở mùa 2023/24 (3,28). Chênh lệch giữa tỷ lệ thắng của chủ nhà và đội khách thấp nhất ở 2020/21 (-2,4%), cao nhất ở 2009/10 (26,8%). Đây là những mốc cao và thấp trong mẫu đã quan sát, chưa đủ để kết luận có xu hướng tuyến tính hoặc khác biệt được xác nhận bằng kiểm định.

| Thước đo sân nhà ở mùa đủ | Ước lượng | Bootstrap theo mùa 95% |
|---|---|---|
| Tỷ lệ H trừ tỷ lệ A | 16,0% | [13,5%; 18,3%] |
| Bàn nhà trừ bàn khách | 0,348 | [0,299; 0,394] |

Khoảng trong bảng được tính bằng 4.000 lần lấy mẫu lại 24 mùa (seed 42), với các trận trong cùng mùa luôn đi cùng nhau. Nhờ vậy, khoảng này phản ánh biến động giữa những mùa đã quan sát. Tuy nhiên, chỉ có 24 cụm và các đội vẫn lặp lại qua nhiều mùa, nên không thể xem đây là bằng chứng về tác động nhân quả của sân nhà.

![Hình 06. Mùa 2019/20 và 2020/21 được tô cam. So sánh theo số khán giả chỉ mô tả các trận trong từng mùa.](figures/06_home_advantage.png)

Hình 06. Mùa 2019/20 và 2020/21 được tô cam. So sánh theo số khán giả chỉ mô tả các trận trong từng mùa.

Dữ liệu có 441 giá trị khán giả được điền; những giá trị 0 trong giai đoạn COVID được đánh dấu bằng cờ attendance_imputed. Khi so sánh nhóm có và không có khán giả, cần nhớ rằng lịch thi đấu, sức mạnh đội và thời điểm cũng có thể ảnh hưởng đến kết quả; không thể quy toàn bộ khác biệt cho khán giả.

## 6. Nhóm đội và khả năng ghi bàn/phòng ngự

![Hình 07. Các nhóm được chia theo Elo trước trận và ngưỡng phân vị của toàn bộ mẫu để phục vụ mô tả. Thứ hạng cuối mùa không được dùng để tạo nhóm dự báo.](figures/07_team_groups.png)

Hình 07. Các nhóm được chia theo Elo trước trận và ngưỡng phân vị của toàn bộ mẫu để phục vụ mô tả. Thứ hạng cuối mùa không được dùng để tạo nhóm dự báo.

| Nhóm Elo trước trận | Sân | Đội–trận | Điểm/trận | Bàn ghi/trận | Bàn thua/trận |
|---|---|---|---|---|---|
| Elo thấp | Khách | 3.038 | 0,79 | 0,92 | 1,80 |
| Elo thấp | Nhà | 3.042 | 1,27 | 1,21 | 1,40 |
| Elo giữa | Khách | 3.038 | 1,03 | 1,08 | 1,59 |
| Elo giữa | Nhà | 3.042 | 1,48 | 1,37 | 1,25 |
| Elo cao | Khách | 3.044 | 1,59 | 1,57 | 1,23 |
| Elo cao | Nhà | 3.036 | 2,10 | 2,03 | 0,92 |

Những đội vắng mặt ở EPL mùa trước được nhận diện từ lịch sử tham dự có trong dữ liệu; nhóm này gồm đội mới xuất hiện lần đầu và đội trở lại giải. Cách chia nhóm chỉ phục vụ mô tả vì chưa đối chiếu toàn bộ lịch thi đấu hạng dưới. Các đội thuộc mùa đầu tiên của dữ liệu được loại khỏi so sánh này.

| Đội (ít nhất 3 mùa đủ tương đương) | Đội–trận | Điểm/trận | Bàn ghi/trận | Bàn thua/trận |
|---|---|---|---|---|
| Manchester United | 912 | 1,97 | 1,83 | 1,02 |
| Liverpool | 912 | 1,94 | 1,88 | 1,00 |
| Arsenal | 912 | 1,93 | 1,89 | 1,04 |
| Manchester City | 874 | 1,92 | 1,96 | 1,03 |
| Chelsea | 912 | 1,88 | 1,79 | 1,01 |
| Tottenham | 912 | 1,62 | 1,63 | 1,27 |
| Everton | 912 | 1,39 | 1,29 | 1,27 |
| Newcastle | 836 | 1,34 | 1,34 | 1,44 |

Bảng gộp những lần mỗi đội xuất hiện trong các mùa có đủ trận, chứ không phải bảng xếp hạng của một mùa riêng lẻ. Vì số mùa góp mặt khác nhau giữa các đội, không thể quy khác biệt trong bảng cho một đặc trưng duy nhất.

## 7. Thống kê cùng trận và đối chiếu nguồn xG

![Hình 15. Ba đồ thị hộp cho thấy trung vị và khoảng tứ phân vị; các điểm ngoài râu vẫn nằm trong dữ liệu. Với thẻ đỏ, báo cáo dùng tỷ lệ trận có ít nhất một thẻ vì cả trung vị lẫn tứ phân vị đều bằng 0.](figures/15_match_statistics.png)

Hình 15. Ba đồ thị hộp cho thấy trung vị và khoảng tứ phân vị; các điểm ngoài râu vẫn nằm trong dữ liệu. Với thẻ đỏ, báo cáo dùng tỷ lệ trận có ít nhất một thẻ vì cả trung vị lẫn tứ phân vị đều bằng 0.

Trong chính trận đấu, số sút, xG, kiểm soát bóng và thẻ có liên hệ với kết quả quan sát được. Những thông tin này chưa có trước giờ bóng lăn, nên không thể đưa trực tiếp vào mô hình dự đoán trước trận. Riêng thẻ và kiểm soát bóng còn có thể chịu ảnh hưởng từ diễn biến hoặc tỷ số của trận đấu.

| Thước đo trên cùng mẫu | Giá trị |
|---|---|
| Đội–trận có cả hai xG | 4.688 |
| Spearman Understat–FotMob | 0,940 |
| Chênh trung bình FotMob−Understat | -0,091 |
| Dải TB ±1,96 SD của chênh | [-0,711; 0,529] |

![Hình 12. Hai nguồn xG có tương quan cao, nhưng không vì thế mà có thể thay thế hoàn toàn cho nhau; dải SD cũng không phải khoảng tin cậy của trung bình.](figures/12_xg_sources.png)

Hình 12. Hai nguồn xG có tương quan cao, nhưng không vì thế mà có thể thay thế hoàn toàn cho nhau; dải SD cũng không phải khoảng tin cậy của trung bình.

Khi tạo đặc trưng, nên dùng nhất quán một nguồn xG và kiểm tra độ nhạy nếu muốn đổi nguồn. Ở đây, hai nguồn được giữ riêng thay vì ghép thành một chỉ số mới.

## 8. Ý nghĩa và thời điểm có đặc trưng

| Nhóm biến | Định nghĩa đúng | Lưu ý sử dụng |
|---|---|---|
| points5, goals_for5, goals_against5 | Trung bình tối đa 5 trận EPL trước, không phải tổng 5 trận | Cửa sổ có thể ngắn hơn 5 và không reset qua mùa |
| xg5, xga5 và các trung bình FotMob | Trung bình các giá trị không thiếu trong cửa sổ tối đa 5 trận | Có thể chỉ dựa trên 1–4 quan sát; cần thêm số quan sát hợp lệ |
| ppg, played, goals_for_pg, position | Thống kê từ phần mùa đã quan sát trước trận | Mùa lịch sử thiếu không tái dựng đúng toàn bộ bảng xếp hạng |
| h2h_home_points / goal_diff | TB tối đa 5 lần gặp trước, quy về đội đang làm chủ nhà | Có thể rất cũ; kiểm tra n đối đầu |
| elo_home_win_prob | Điểm kết quả kỳ vọng Elo với H=1, D=0,5, A=0 | Không phải P(H) ba lớp đã hiệu chuẩn; biến đổi đơn điệu của Elo |
| rest_days | Ngày từ trận EPL quan sát gần nhất | Không phải thời gian nghỉ mọi đấu trường |
| odds_*_prob | Nghịch đảo odds Avg, B365 dự phòng, chuẩn hóa tổng bằng 1 | Chưa có timestamp thu thập; đóng khung thời điểm dự đoán trước khi dùng |

Ở 143 trận, ít nhất một đội đã có lịch sử kết quả nhưng cửa sổ vẫn chưa đủ 5 trận; 800 trận chưa ghi nhận lần đối đầu nào trước đó. Nếu yêu cầu H2H có ít nhất 3 lần gặp, còn 7.632 trận. Với đội trở lại EPL, cửa sổ phong độ đôi khi vẫn chứa kết quả từ nhiều năm trước.

Để kiểm tra cách tính các biến lịch sử, kiểm toán đã tính lại độc lập 43 cột trên toàn bộ 9.850 trận. Các cột này gồm Elo, phong độ kết quả/bàn/xG, phong độ sân, thống kê mùa, ngày EPL và H2H; ghi nhận 0 ô sai khác. Phép kiểm tra dùng trạng thái trước khi cập nhật kết quả trận hiện tại. Kết quả này chưa thay thế việc xác minh timestamp dữ liệu, thời gian kickoff còn thiếu, toàn bộ biến FotMob hay dữ liệu nguồn thực tế.

## 9. Phong độ, bàn thắng trung bình, đối đầu và kết quả

![Hình 08. Các phân vị giúp nhìn dữ liệu theo từng nhóm trên toàn mẫu; chúng không phải ngưỡng ra quyết định của mô hình. Do một số giá trị trùng nhau, kích thước các nhóm không hoàn toàn bằng nhau.](figures/08_feature_associations.png)

Hình 08. Các phân vị giúp nhìn dữ liệu theo từng nhóm trên toàn mẫu; chúng không phải ngưỡng ra quyết định của mô hình. Do một số giá trị trùng nhau, kích thước các nhóm không hoàn toàn bằng nhau.

| Biến | Số trận hợp lệ | Spearman với H | Spearman với hiệu số bàn |
|---|---|---|---|
| odds_home_prob | 9.090 | 0,383 | 0,460 |
| elo_diff | 9.850 | 0,354 | 0,426 |
| diff_ppg | 9.577 | 0,292 | 0,355 |
| diff_xg5 | 4.594 | 0,276 | 0,333 |
| diff_shots_on_target5 | 9.815 | 0,246 | 0,305 |
| diff_points5 | 9.815 | 0,225 | 0,274 |
| diff_goals_for5 | 9.815 | 0,207 | 0,256 |
| diff_goals_against5 | 9.815 | -0,186 | -0,217 |
| h2h_home_points | 9.050 | 0,202 | 0,247 |
| h2h_home_goal_diff | 9.050 | 0,235 | 0,287 |

Để tính tương quan, báo cáo không gán nhãn H/D/A vào một thang số tùy ý. Thay vào đó, kết quả được xét dưới dạng H=1, còn lại=0, hoặc qua hiệu số bàn, vốn có thứ tự tự nhiên. Mỗi hệ số dựa trên tập trận hợp lệ riêng, nên chưa thể dùng chúng để xếp hạng trực tiếp khả năng dự đoán của các biến.

![Hình 10. Sáu biến được so sánh thêm trên cùng 4.408 trận có đủ dữ liệu; mẫu này tập trung nhiều hơn ở giai đoạn có xG, nên không đại diện đều cho toàn bộ lịch sử.](figures/10_common_sample.png)

Hình 10. Sáu biến được so sánh thêm trên cùng 4.408 trận có đủ dữ liệu; mẫu này tập trung nhiều hơn ở giai đoạn có xG, nên không đại diện đều cho toàn bộ lịch sử.

![Hình 09. So sánh nhóm thấp và cao theo tercile toàn mẫu ở các trận có Elo gần nhau; đây mới là bước điều kiện hóa sơ bộ, chưa loại hết khác biệt về sức mạnh và thời kỳ.](figures/09_strength_conditioned.png)

Hình 09. So sánh nhóm thấp và cao theo tercile toàn mẫu ở các trận có Elo gần nhau; đây mới là bước điều kiện hóa sơ bộ, chưa loại hết khác biệt về sức mạnh và thời kỳ.

| Biến | Ngưỡng thấp / cao | Tỷ lệ H thấp | Tỷ lệ H cao | Chênh cao−thấp | Elo TB thấp / cao |
|---|---|---|---|---|---|
| Chênh điểm TB 5 trận | ≤-0,400 / ≥0,400 | 43,7% (n=900) | 45,9% (n=924) | 2,22 điểm % | -8,5 / 7,9 |
| Chênh bàn ghi TB 5 trận | ≤-0,400 / ≥0,400 | 44,4% (n=1.051) | 44,5% (n=916) | 0,11 điểm % | -6,5 / 5,0 |
| Điểm TB đối đầu của đội nhà | ≤1,000 / ≥1,600 | 45,2% (n=1.079) | 45,3% (n=897) | 0,03 điểm % | -4,4 / 4,0 |
| Chênh xG TB 5 trận | ≤-0,324 / ≥0,257 | 38,4% (n=349) | 45,2% (n=343) | 6,79 điểm % | -7,8 / 6,1 |

Khi chỉ nhìn các trận có Elo gần nhau, chênh lệch gắn với phong độ điểm, bàn ghi và đối đầu nhỏ đi nhiều so với liên hệ ban đầu. Chênh lệch theo xG vẫn lớn hơn, dù hai nhóm còn khác nhau về Elo trung bình và giai đoạn thi đấu. Vì vậy, giá trị tăng thêm của xG cần được kiểm định bằng mô hình theo thời gian; kết quả mô tả này chưa cho thấy quan hệ nhân quả hay mức cải thiện dự báo.

## 10. Bất thường và quyết định xử lý

| Nhóm cờ | Số cờ | Trận duy nhất trong nhóm | Diễn giải |
|---|---|---|---|
| attendance_imputed | 441 | 441 | Khán giả được điền: giữ cờ khi phân tích |
| cross_source_shots | 176 | 167 | Trong + ngoài vòng cấm (FotMob) vượt tổng sút (Football-Data): xác minh định nghĩa/nguồn |
| cross_source_zero_shots | 6 | 3 | Sút trong và ngoài vòng cấm bằng 0 nhưng Football-Data có sút: xác minh giá trị 0 và độ phủ nguồn |
| observed_epl_gap | 534 | 284 | Khoảng cách EPL >30 ngày: gồm nghỉ mùa, vắng giải; không tự coi là lỗi |
| suspect_possession | 1 | 1 | Xem reason_vi trong bảng chi tiết |

Ở 167 trận, tổng số sút trong và ngoài vòng cấm lớn hơn tổng sút được ghi nhận. Vì các chỉ số đến từ hai nguồn khác nhau, đây là dấu hiệu cần đối chiếu, chưa đủ để xác định bên nào sai. Trận Newcastle–Tottenham ngày 15/05/2016 cũng ghi kiểm soát bóng 3%–97% dù tỷ số là 5–1; trường hợp này cần kiểm tra lại nguồn.

Thêm 3 trận có cả hai thành phần sút của FotMob bằng 0 trong khi tổng sút từ Football-Data vẫn dương. Nên kiểm tra cách ghi nhận và độ phủ của nguồn trước khi hiểu các số 0 này là đội không có cú sút nào.

Khoảng cách giữa hai lần xuất hiện ở EPL dài nhất là 9.225 ngày; 52 đội–trận có khoảng cách trên một năm. Không nên cắt ngắn những khoảng này rồi gọi kết quả là thời gian nghỉ thực tế. Khi nghiên cứu mệt mỏi, có thể đánh dấu lịch sử quá cũ, tính khoảng cách trong mùa hoặc bổ sung lịch thi đấu ở mọi đấu trường.

![Hình 13. Báo cáo giữ nguyên các dòng dữ liệu nguồn. Phân phối trên thang log cho thấy chỉ nhìn trung bình và độ lệch chuẩn chưa đủ để hiểu rest_days.](figures/13_anomalies.png)

Hình 13. Báo cáo giữ nguyên các dòng dữ liệu nguồn. Phân phối trên thang log cho thấy chỉ nhìn trung bình và độ lệch chuẩn chưa đủ để hiểu rest_days.

## 11. Đặc trưng tiềm năng, trùng thông tin và độ ổn định

![Hình 11. Mỗi cặp tương quan có thể dựa trên một tập trận khác nhau; các quan hệ xác định về mặt đại số được kiểm tra thêm bằng công thức.](figures/11_redundancy.png)

Hình 11. Mỗi cặp tương quan có thể dựa trên một tập trận khác nhau; các quan hệ xác định về mặt đại số được kiểm tra thêm bằng công thức.

Cả 14 cột diff_* có sẵn đều bằng giá trị home−away của chỉ số tương ứng. Vì vậy, không nên đưa đồng thời cả ba biến vào hồi quy tuyến tính không điều chuẩn. elo_home_win_prob là biến đổi đơn điệu của elo_diff; position_diff và diff_ppg, cũng như hai biến H2H, có nhiều thông tin trùng nhau. Ở bước EDA, chưa cần áp dụng PCA như một yêu cầu bắt buộc; có thể ưu tiên các nhóm biến dễ giải thích.

| Biến | Thiếu | Quyết định đề xuất |
|---|---|---|
| elo_diff | 0,0% | Ưu tiên elo_diff làm biến sức mạnh đại diện. |
| diff_ppg | 2,8% | Ưu tiên thử làm đại diện chênh lệch; xác nhận giá trị thêm vào Elo bằng chia tập theo thời gian. |
| diff_points5 | 0,4% | Ưu tiên thử làm đại diện chênh lệch; xác nhận giá trị thêm vào Elo bằng chia tập theo thời gian. |
| diff_goals_for5 | 0,4% | Ưu tiên thử làm đại diện chênh lệch; xác nhận giá trị thêm vào Elo bằng chia tập theo thời gian. |
| diff_goals_against5 | 0,4% | Ưu tiên thử làm đại diện chênh lệch; xác nhận giá trị thêm vào Elo bằng chia tập theo thời gian. |
| diff_shots_on_target5 | 0,4% | Ưu tiên thử làm đại diện chênh lệch; xác nhận giá trị thêm vào Elo bằng chia tập theo thời gian. |
| diff_xg5 | 53,4% | Ưu tiên thử trên tập con có xG; so sánh cùng trận và cùng giai đoạn với mô hình không xG. |
| diff_xga5 | 53,4% | Ưu tiên thử trên tập con có xG; so sánh cùng trận và cùng giai đoạn với mô hình không xG. |
| h2h_home_points | 8,1% | Thử bổ sung sau Elo; ưu tiên một đại diện H2H và kiểm tra giá trị tăng thêm. |
| h2h_home_goal_diff | 8,1% | Thử bổ sung sau Elo; ưu tiên một đại diện H2H và kiểm tra giá trị tăng thêm. |
| diff_possession5 | 38,8% | Thử trên tập con FotMob đủ độ phủ; hoãn cho mô hình toàn lịch sử. |
| diff_big_chances5 | 60,6% | Thử trên tập con FotMob đủ độ phủ; hoãn cho mô hình toàn lịch sử. |
| diff_box_touches5 | 78,0% | Thử trên tập con FotMob đủ độ phủ; hoãn cho mô hình toàn lịch sử. |
| diff_xgot5 | 76,5% | Thử trên tập con FotMob đủ độ phủ; hoãn cho mô hình toàn lịch sử. |
| odds_home_prob | 7,7% | Tách kịch bản có odds; hoãn dùng nếu chưa xác minh thời điểm ghi nhận và quyền tiếp cận lúc dự đoán. |
| home_rest_days | 0,2% | Hoãn dùng như biến mệt mỏi; cần bổ sung lịch đầy đủ hoặc đổi rõ ý nghĩa thành khoảng cách EPL. |

[Danh mục đầy đủ 87 đặc trưng: định nghĩa, nguồn, thời điểm có, thiếu, tương quan và quyết định](feature_candidates.csv)

Tương quan giúp nhận ra hướng đáng tìm hiểu, nhưng chưa chứng minh biến nào thực sự có ích cho dự báo. Các quyết định trong bảng vì thế là thứ tự đề xuất để kiểm định. Nên xét riêng kịch bản chỉ có dữ liệu bóng đá và kịch bản thêm odds đúng thời điểm, đồng thời tránh để mẫu thiếu dữ liệu của một giai đoạn trở thành dấu hiệu nhận diện nhãn.

![Hình 16. Hướng liên hệ ổn định qua nhiều mùa là tín hiệu để tìm hiểu thêm, chưa chứng minh khả năng dự báo ngoài mẫu. Mốc ≥100 quan sát giúp tránh diễn giải hệ số tính từ quá ít trận.](figures/16_temporal_stability.png)

Hình 16. Hướng liên hệ ổn định qua nhiều mùa là tín hiệu để tìm hiểu thêm, chưa chứng minh khả năng dự báo ngoài mẫu. Mốc ≥100 quan sát giúp tránh diễn giải hệ số tính từ quá ít trận.

| Biến | Mùa đủ điều kiện | Số mùa tương quan dương | Khoảng Spearman |
|---|---|---|---|
| diff_goals_for5 | 24 | 24 | [0,066; 0,345] |
| diff_points5 | 24 | 24 | [0,076; 0,313] |
| diff_xg5 | 12 | 12 | [0,190; 0,358] |
| elo_diff | 24 | 24 | [0,226; 0,426] |
| h2h_home_points | 24 | 24 | [0,022; 0,332] |
| odds_home_prob | 22 | 22 | [0,287; 0,467] |

![Hình 14. Biểu đồ mô tả xác suất suy từ odds trong bộ dữ liệu hiện có; đây chưa phải kết quả backtest hay đánh giá hiệu quả tài chính. Elo expectation cũng không được xem như P(H).](figures/14_odds_reliability.png)

Hình 14. Biểu đồ mô tả xác suất suy từ odds trong bộ dữ liệu hiện có; đây chưa phải kết quả backtest hay đánh giá hiệu quả tài chính. Elo expectation cũng không được xem như P(H).

Đặc trưng odds được tạo từ Avg, dùng B365 khi cần dự phòng, rồi chuẩn hóa các giá trị nghịch đảo. Chúng chưa chắc là closing odds. Vì chưa có timestamp thu thập, báo cáo cũng chưa thể xác nhận liệu odds đã sẵn có trước trận một ngày hay chưa.

## 12. Giả thuyết cần kiểm tra bằng mô hình

| Mã | Giả thuyết | So sánh đề xuất |
|---|---|---|
| H1 | Thêm chênh lệch điểm sức mạnh Elo có giúp dự đoán thắng, hòa, thua tốt hơn cách chỉ dựa vào tỷ lệ kết quả trước đây không? | So cách dự đoán theo tỷ lệ thắng, hòa, thua trong tập huấn luyện với mô hình logistic đa lớp có thêm Elo. |
| H2 | Thêm phong độ điểm và bàn ghi, bàn thua gần đây có giúp dự đoán tốt hơn mô hình chỉ dùng Elo không? | So mô hình chỉ có Elo với mô hình có thêm phong độ điểm và bàn ghi, bàn thua. |
| H3 | Thêm bàn thắng kỳ vọng (xG) tạo ra và phải chịu gần đây có giúp dự đoán tốt hơn khi đã có Elo và phong độ bàn ghi, bàn thua không? | So mô hình có Elo và phong độ bàn ghi, bàn thua với mô hình thêm xG tạo ra và phải chịu. |
| H4 | Lịch sử đối đầu của hai đội có giúp dự đoán tốt hơn sau khi đã xét Elo và phong độ gần đây không? | So mô hình có Elo và phong độ gần đây với mô hình thêm lịch sử đối đầu. |
| H5 | Lợi thế sân nhà có thay đổi theo giai đoạn, và việc tính riêng từng giai đoạn có giúp dự đoán tốt hơn không? | So mô hình dùng lợi thế sân nhà cố định với mô hình ước lượng mức này từ dữ liệu quá khứ theo từng giai đoạn. |
| H6 | Phong độ sân nhà của đội chủ nhà và sân khách của đội khách có giúp dự đoán tốt hơn phong độ chung không? | So mô hình dùng phong độ chung với mô hình thêm phong độ theo sân của từng đội. |
| H7 | Biết lịch sử thi đấu của một đội đã cũ có giúp dự đoán tốt hơn sau khi đã xét Elo và phong độ không? | So mô hình có Elo và phong độ với mô hình thêm dấu hiệu lịch sử đã cũ; thử riêng số ngày nghỉ thực nếu có lịch mọi giải. |
| H8 | Thống kê các trận trước từ FotMob có giúp dự đoán tốt hơn khi mô hình đã có Elo, phong độ và xG không? | So mô hình có Elo, phong độ và xG với mô hình thêm lần lượt từng nhóm thống kê FotMob. |
| H9 | Nếu tỷ lệ cược (odds) đã có trước lúc dự đoán, xác suất suy ra từ đó có giúp dự đoán tốt hơn không? | Trên cùng các trận, so mô hình chỉ dùng dữ liệu bóng đá với mô hình thêm xác suất từ odds; đánh giá riêng các xác suất này làm mốc. |

[Chi tiết giả thuyết, tập dữ liệu, tiêu chí và lưu ý](hypotheses.csv)

Những liên hệ trên gợi ra hướng thử nghiệm, nhưng EDA chưa đủ để khẳng định giả thuyết nào đúng. Nên bắt đầu từ tỷ lệ H/D/A tính trên tập huấn luyện, rồi so với một mô hình đơn giản có Elo. Nếu dự đoán tỷ số, cần đánh giá riêng Poisson và các biến thể đếm trên dữ liệu quá khứ, đồng thời kiểm tra độ phân tán và mối liên hệ giữa bàn thắng nhà–khách. Biểu đồ đối chiếu Poisson ở trên mới là bước khám phá phân phối.

## 13. Kế hoạch đánh giá và giới hạn diễn giải

- Trước tiên, xác định rõ mục tiêu là dự đoán H/D/A hay tỷ số, và dự đoán ngay trước giờ bóng lăn hay trước một ngày. Chỉ dùng thông tin đã có ở thời điểm đó.
- Chia dữ liệu theo thời gian: học từ các mùa trước rồi đánh giá trên mùa tiếp theo. Các trận cùng ngày nên nằm cùng một phía của ranh giới chia tập.
- Khi so sánh các nhóm biến cơ bản, xG, FotMob, H2H và odds, hãy dùng cùng tập trận và cùng lần chia dữ liệu. Trình bày riêng kết quả trên mẫu dài hạn và mẫu giàu thống kê gần đây.
- Ở mỗi lần chia tập, chỉ học cách điền thiếu, chuẩn hóa, chọn biến, PCA (nếu dùng) và hiệu chỉnh xác suất từ tập huấn luyện. Không dùng tập kiểm tra để chọn biến.
- Dùng log loss đa lớp làm thước đo chính cho H/D/A; xem thêm Brier, macro-F1, recall lớp hòa, ma trận nhầm lẫn và độ hiệu chỉnh xác suất. Nên trình bày cả mức biến động qua mùa.
- Sau khi chốt đặc trưng và quy trình, khóa một tập đánh giá cuối. Vì EDA này đã xem toàn bộ dữ liệu hiện có, một mùa trong đó không còn hoàn toàn mới; tốt nhất là kiểm chứng thêm bằng dữ liệu tương lai chưa xem.
- Lặp lại đánh giá khi thay đổi cách xử lý hai mùa thiếu, đội trở lại EPL, cửa sổ dưới 5 trận, H2H ít trận, nguồn xG và khoảng trống lịch sử.
- Các mối liên hệ trong báo cáo chỉ mang tính mô tả. Nhiều trận có cùng đội và nhiều phép so sánh được thực hiện, nên cần phương pháp phù hợp hơn trước khi đưa ra kết luận xác nhận hay nhân quả.

Dữ liệu nguồn đã được xử lý và có 108 kiểm tra QA, nhưng từng bài toán vẫn cần xem lại những giới hạn liên quan. Lần kiểm toán này tập trung thêm vào EDA và thời điểm thông tin có thể được dùng để dự đoán. Báo cáo chưa huấn luyện mô hình dự đoán.

[Tham khảo phương pháp: tránh rò rỉ dữ liệu (scikit-learn)](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)

[Tham khảo phương pháp: đánh giá theo thời gian (scikit-learn)](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-of-time-series-data)

## 14. Bàn giao và tái lập

- Mở report.html để đọc báo cáo cùng toàn bộ 16 biểu đồ ngay trong trình duyệt, kể cả khi ngoại tuyến.
- Dùng report.md và thư mục figures/ nếu muốn đưa nội dung, ảnh PNG vào tài liệu hoặc slide.
- feature_candidates.csv ghi lại 87 biến cùng hướng kiểm tra được đề xuất.
- hypotheses.csv tập hợp 9 giả thuyết, phép so sánh và tiêu chí đánh giá.
- data_quality_checks.csv và data_issues.csv lưu kết quả kiểm tra và các dòng cần xem lại; dữ liệu nguồn chưa bị sửa.
- analysis_metadata.json lưu thời điểm tạo, phiên bản thư viện, SHA256 của hai nguồn và chi tiết kiểm toán.

Báo cáo dùng clean/matches.csv và clean/features.csv, lấy từ thư mục clean của kho Toibinguz/epl-premier-league-dataset trên Hugging Face. Để hiểu cách tạo các biến lịch sử, nhóm đã đối chiếu với mã ở origin/Data_itegration, commit eaef4cd, thư mục Data integration and preprocessing. Mã EDA chỉ đọc dữ liệu nguồn và ghi kết quả vào reports/eda_matches_features; dấu vân tay của hai tệp được kiểm tra trước và sau khi chạy.

| Nguồn | Đường dẫn | SHA256 |
|---|---|---|
| matches | clean\matches.csv | 3c122c5f6d5f7ff5aed498ae28499ca26e6a6e8d26639d52172876b2a668b81a |
| features | clean\features.csv | 671192ed692d4a609ebf530dc9f161101f8c803271c4ad15dac27087bdb1ecdf |

Có thể tạo lại báo cáo từ gốc dự án bằng lệnh dưới đây, sau khi cài pandas, numpy, matplotlib và scipy:

    python reports/eda_matches_features/run_eda.py
