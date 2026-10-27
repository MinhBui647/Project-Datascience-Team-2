"""Vietnamese catalogue for the supplied pre-match EPL feature table.

This module describes and summarizes existing columns. It does not modify the
input tables, rebuild features, select a fitted model, or write any artifacts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


ID_COLUMNS = {
    "match_id", "season", "date", "kickoff_time", "gameweek",
    "home_club_id", "home_team", "away_club_id", "away_team",
    "status", "provisional", "postponed",
}
TARGET_COLUMNS = {"home_goals", "away_goals", "result"}
CATALOG_COLUMNS = [
    "feature", "group", "definition_vi", "source_vi", "availability_vi",
    "missing_pct", "n_valid", "first_season_valid", "last_season_valid",
    "spearman_H", "spearman_goal_diff", "decision_vi", "caution_vi",
]

HISTORY_AVAILABILITY = (
    "Theo thiết kế: có trước trận từ lịch sử EPL đã quan sát; cần xác minh "
    "ngày/giờ thực tế và thời điểm nguồn công bố dữ liệu."
)
WINDOW_CAUTION = (
    "Cửa sổ tối đa 5 trận EPL đã quan sát, có thể ít hơn 5; không reset khi đổi "
    "mùa hoặc xuống hạng. Các trung bình bỏ qua NaN, nên số quan sát hợp lệ "
    "có thể khác nhau giữa biến; lịch sử có thể cũ khi đội trở lại EPL."
)
SEASON_CAUTION = (
    "Chỉ tính các trận EPL đã quan sát trong mùa trước trận hiện tại. Mùa thiếu "
    "trận làm sai lệch thống kê; đầu mùa mẫu nhỏ hoặc chưa có lịch sử."
)
RESULT_SOURCE = "Kết quả/tỷ số lịch sử trong matches.csv, nguồn chính Football-Data."
SHOT_SOURCE = (
    "Sút trúng đích lịch sử Football-Data (HST/AST) trong matches.csv, sau bước làm sạch; "
    "pipeline tích hợp không bổ sung chỉ số này từ FotMob."
)
XG_SOURCE = "xG lịch sử Understat: home_xg/away_xg trong matches.csv."
FM_SOURCE = "Thống kê lịch sử FotMob trong matches.csv."
CORE_DIFFS = {
    "points5", "goals_for5", "goals_against5", "shots_on_target5", "ppg",
}
XG_DIFFS = {"xg5", "xga5"}
FM_DIFFS = {
    "possession5", "big_chances5", "big_chances_against5", "box_touches5",
    "xgot5", "xgot_against5",
}

# Suffix -> group, definition for one team, source. All rolling numerical
# statistics below are means except the explicitly documented W/D/L counts.
TEAM_SPECS = {
    "form": (
        "Phong độ kết quả", "Chuỗi W/D/L của tối đa 5 trận EPL trước, cũ đến mới",
        RESULT_SOURCE,
    ),
    "wins5": ("Phong độ kết quả", "Số trận thắng trong tối đa 5 trận EPL trước", RESULT_SOURCE),
    "draws5": ("Phong độ kết quả", "Số trận hòa trong tối đa 5 trận EPL trước", RESULT_SOURCE),
    "losses5": ("Phong độ kết quả", "Số trận thua trong tối đa 5 trận EPL trước", RESULT_SOURCE),
    "points5": (
        "Phong độ kết quả", "Điểm trung bình/trận trong tối đa 5 trận EPL trước (0–3, không phải tổng 0–15)",
        RESULT_SOURCE,
    ),
    "goals_for5": (
        "Phong độ bàn thắng", "Bàn ghi trung bình/trận trong tối đa 5 trận EPL trước", RESULT_SOURCE,
    ),
    "goals_against5": (
        "Phong độ bàn thắng", "Bàn thua trung bình/trận trong tối đa 5 trận EPL trước", RESULT_SOURCE,
    ),
    "shots_on_target5": (
        "Phong độ tạo cơ hội", "Số cú sút trúng đích trung bình/trận trong tối đa 5 trận EPL trước",
        SHOT_SOURCE,
    ),
    "xg5": (
        "Phong độ xG", "xG tạo ra trung bình/trận hợp lệ trong tối đa 5 trận EPL trước", XG_SOURCE,
    ),
    "xga5": (
        "Phong độ xG", "xG đối phương tạo ra trung bình/trận hợp lệ trong tối đa 5 trận EPL trước", XG_SOURCE,
    ),
    "opp_elo5": (
        "Sức mạnh lịch thi đấu", "Elo trước trận trung bình của các đối thủ trong tối đa 5 trận EPL trước",
        "Elo tự tính tuần tự từ tỷ số EPL lịch sử, lấy Elo đối thủ trước mỗi trận.",
    ),
    "perf_vs_elo5": (
        "Phong độ sau điều chỉnh Elo",
        "Trung bình [điểm kết quả thực tế (1 thắng, 0,5 hòa, 0 thua) trừ điểm kết quả kỳ vọng Elo] trong tối đa 5 trận trước",
        "Kết quả EPL lịch sử và Elo trước trận có lợi thế sân nhà.",
    ),
    "possession5": (
        "Phong cách thi đấu lịch sử", "Tỷ lệ kiểm soát bóng (%) trung bình trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "pass_accuracy5": (
        "Phong cách thi đấu lịch sử", "Tỷ lệ chuyền chính xác (%) trung bình trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "big_chances5": (
        "Phong độ tạo cơ hội", "Số cơ hội lớn tạo ra trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "big_chances_against5": (
        "Phong độ phòng ngự", "Số cơ hội lớn đối phương tạo ra trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "box_shots5": (
        "Phong độ tạo cơ hội", "Số cú sút trong vòng cấm trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "box_shots_against5": (
        "Phong độ phòng ngự", "Số cú sút trong vòng cấm của đối phương trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "box_touches5": (
        "Phong độ tạo cơ hội", "Số lần chạm bóng trong vòng cấm đối phương trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "box_touches_against5": (
        "Phong độ phòng ngự", "Số lần đối phương chạm bóng trong vòng cấm trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "xgot5": (
        "Phong độ xGOT", "xGOT của cú sút trúng đích trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "xgot_against5": (
        "Phong độ xGOT", "xGOT đối phương trung bình/trận trong tối đa 5 trận EPL trước", FM_SOURCE,
    ),
    "played": (
        "Thành tích từ đầu mùa", "Số trận EPL đã quan sát của đội trong mùa trước trận hiện tại", RESULT_SOURCE,
    ),
    "ppg": (
        "Thành tích từ đầu mùa", "Điểm trung bình/trận trên các trận EPL đã quan sát từ đầu mùa", RESULT_SOURCE,
    ),
    "goals_for_pg": (
        "Thành tích từ đầu mùa", "Bàn ghi trung bình/trận trên các trận EPL đã quan sát từ đầu mùa", RESULT_SOURCE,
    ),
    "goals_against_pg": (
        "Thành tích từ đầu mùa", "Bàn thua trung bình/trận trên các trận EPL đã quan sát từ đầu mùa", RESULT_SOURCE,
    ),
    "rest_days": (
        "Khoảng cách lịch quan sát", "Số ngày từ trận EPL gần nhất đã quan sát của đội đến ngày trận hiện tại",
        "Ngày thi đấu EPL trong matches.csv; không bao phủ các giải khác.",
    ),
}


def _team_spec(suffix: str) -> dict[str, str]:
    group, definition, source = TEAM_SPECS[suffix]
    caution = WINDOW_CAUTION
    decision = "Giữ như cách biểu diễn home/away thay thế; so sánh với biến chênh lệch."
    if suffix in {"played", "ppg", "goals_for_pg", "goals_against_pg"}:
        caution = SEASON_CAUTION
        if suffix == "played":
            decision = "Giữ để kiểm soát độ dài lịch sử; không diễn giải là sức mạnh đội."
        elif suffix in {"goals_for_pg", "goals_against_pg"}:
            decision = "Ưu tiên thử trong mô hình tỷ số; cân nhắc tạo chênh lệch chỉ ở bước mô hình."
    if suffix == "rest_days":
        caution = (
            "Không đo số ngày nghỉ thực: thiếu trận cúp/giải châu Âu/hạng dưới. Có "
            "khoảng cách nhiều năm do xuống hạng; không điền hoặc cắt ngưỡng tùy ý. "
            "Nếu trận bị đánh dấu postponed, mã tạo đặc trưng trả NaN."
        )
        decision = "Hoãn dùng như biến mệt mỏi; cần bổ sung lịch đầy đủ hoặc đổi rõ ý nghĩa thành khoảng cách EPL."
    elif suffix == "form":
        decision = "Dùng mô tả; nếu mô hình dùng số đếm/điểm5 thì không thêm chuỗi thô mặc định."
        caution += " Chuỗi không phải biến số; cần mã hóa có chủ đích nếu dùng trong mô hình."
    elif suffix in {"wins5", "draws5", "losses5"}:
        decision = "Biểu diễn thay thế cho points5; kiểm tra dư thừa trước khi dùng đồng thời."
        caution += " Số đếm phụ thuộc độ dài lịch sử; tổng W+D+L có thể nhỏ hơn 5."
    elif suffix in {"opp_elo5", "perf_vs_elo5"}:
        decision = "Thử trong kiểm tra loại bỏ từng nhóm biến; chưa có bằng chứng tăng chất lượng dự báo."
    elif suffix in {"xg5", "xga5"}:
        caution += " Understat khác định nghĩa/nguồn với xG FotMob; không tự nối hai thang đo."
    elif source == FM_SOURCE:
        caution += " Độ phủ FotMob thay đổi theo mùa; đánh giá trên cùng tập trận."
    return {
        "group": group, "definition_vi": definition, "source_vi": source,
        "availability_vi": HISTORY_AVAILABILITY, "decision_vi": decision,
        "caution_vi": caution,
    }


def _describe(feature: str) -> dict[str, str]:
    if feature in {"home_elo", "away_elo", "elo_diff", "elo_home_win_prob"}:
        definitions = {
            "home_elo": "Elo đội chủ nhà trước trận.",
            "away_elo": "Elo đội khách trước trận.",
            "elo_diff": "Chênh lệch Elo trước trận: home_elo trừ away_elo.",
            "elo_home_win_prob": (
                "Điểm kết quả kỳ vọng Elo: 1/[1+10^((away_elo-home_elo-65)/400)]. "
                "Thang kết quả thắng=1, hòa=0,5, thua=0; không phải P(H) được hiệu chuẩn."
            ),
        }
        caution = (
            "Elo tự tính: bắt đầu 1500, đội mới 1450 sau mùa đầu, K=20, lợi thế nhà "
            "65, hồi quy 20% về 1500 qua mùa; cập nhật có hệ số hiệu số bàn thắng. "
            "Thiếu trận lịch sử ảnh hưởng Elo; Elo đội trở lại có thể dựa trên lịch sử cũ."
        )
        decision = "Ưu tiên elo_diff làm biến sức mạnh đại diện."
        if feature in {"home_elo", "away_elo"}:
            decision = "Giữ như biểu diễn sức mạnh home/away thay thế; tránh thêm cả ba cột Elo mặc định."
        elif feature == "elo_home_win_prob":
            decision = "Không chọn đồng thời với elo_diff; không dùng trực tiếp làm xác suất thắng H."
            caution += " Biến đổi đơn điệu xác định của elo_diff; không có xác suất hòa riêng."
        return {
            "group": "Sức mạnh Elo", "definition_vi": definitions[feature],
            "source_vi": "Tự tính tuần tự từ kết quả/tỷ số EPL trước trận trong matches.csv.",
            "availability_vi": HISTORY_AVAILABILITY,
            "decision_vi": decision, "caution_vi": caution,
        }
    if feature in {"home_position", "away_position", "position_diff"}:
        definition = {
            "home_position": "Thứ hạng đội chủ nhà trên bảng tự dựng từ các trận đã quan sát trong mùa.",
            "away_position": "Thứ hạng đội khách trên bảng tự dựng từ các trận đã quan sát trong mùa.",
            "position_diff": "Thứ hạng chủ nhà trừ thứ hạng đội khách; âm nghĩa là chủ nhà đứng cao hơn.",
        }[feature]
        return {
            "group": "Vị trí trong mùa", "definition_vi": definition,
            "source_vi": "Tự xếp bảng theo điểm, hiệu số, bàn ghi rồi mã đội; không phải BXH chính thức.",
            "availability_vi": HISTORY_AVAILABILITY,
            "decision_vi": "Biến thay thế cho ppg/diff_ppg; thử riêng vì tương quan rất cao.",
            "caution_vi": SEASON_CAUTION + " Mã đội dùng phá hòa hoàn toàn; lịch đá lệch số trận ảnh hưởng thứ hạng.",
        }
    if feature.startswith("odds_"):
        side = {"home": "chủ nhà thắng", "draw": "hòa", "away": "đội khách thắng"}[feature.split("_")[1]]
        return {
            "group": "Thị trường odds", "definition_vi": (
                f"Xác suất ngụ ý {side}: nghịch đảo odds chia tổng nghịch đảo odds H/D/A để bỏ biên nhà cái."
            ),
            "source_vi": "avg_home/draw/away_odds của Football-Data; thiếu từng phía thì dùng b365 tương ứng; không dùng cột close.",
            "availability_vi": "Chỉ dùng khi chứng minh ảnh chụp odds có trước thời điểm dự đoán đã chọn; timestamp hiện chưa xác minh.",
            "decision_vi": "Tách kịch bản có odds; hoãn dùng nếu chưa xác minh thời điểm ghi nhận và quyền tiếp cận lúc dự đoán.",
            "caution_vi": (
                "Ba xác suất cộng 1 nên phụ thuộc tuyến tính. Fallback có thể trộn avg và B365 giữa "
                "các phía; không mặc định là xác suất đúng hoặc odds tại lúc mở kèo. "
                "So sánh với Elo trên cùng tập trận và báo cáo thêm kịch bản không odds."
            ),
        }
    if feature.startswith("h2h_"):
        definitions = {
            "h2h_matches": "Số cuộc gặp EPL đã quan sát trước trận giữa cặp đội, tối đa 5, không phân biệt sân.",
            "h2h_home_points": "Điểm trung bình/trận của đội hiện là chủ nhà trong tối đa 5 cuộc gặp trước (0–3), bất kể sân lúc đó.",
            "h2h_home_goal_diff": "Hiệu số bàn thắng trung bình từ góc nhìn đội hiện là chủ nhà trong tối đa 5 cuộc gặp EPL trước, bất kể sân.",
        }
        return {
            "group": "Thành tích đối đầu", "definition_vi": definitions[feature],
            "source_vi": RESULT_SOURCE, "availability_vi": HISTORY_AVAILABILITY,
            "decision_vi": (
                "Giữ làm thước đo độ tin cậy H2H; 0 nghĩa là chưa có cuộc gặp quan sát."
                if feature == "h2h_matches" else
                "Thử bổ sung sau Elo; ưu tiên một đại diện H2H và kiểm tra giá trị tăng thêm."
            ),
            "caution_vi": (
                "Không reset qua mùa; có thể là đối đầu từ nhiều năm trước. Số trận nhỏ làm "
                "ước lượng dao động; sức mạnh đội gây nhiễu quan hệ với kết quả. "
                "Điểm H2H và hiệu số H2H tương quan cao; cần xét cùng số cuộc gặp."
            ),
        }
    if feature.startswith("diff_"):
        suffix = feature.removeprefix("diff_")
        spec = _team_spec(suffix)
        spec["definition_vi"] = "Chủ nhà trừ đội khách của biến: " + spec["definition_vi"] + "."
        spec["caution_vi"] += (
            f" Đẳng thức xác định: {feature}=home_{suffix}-away_{suffix}; "
            "không mặc định dùng đồng thời cả ba cột trong mô hình."
        )
        if suffix in CORE_DIFFS:
            spec["decision_vi"] = "Ưu tiên thử làm đại diện chênh lệch; xác nhận giá trị thêm vào Elo bằng chia tập theo thời gian."
        elif suffix in XG_DIFFS:
            spec["decision_vi"] = "Ưu tiên thử trên tập con có xG; so sánh cùng trận và cùng giai đoạn với mô hình không xG."
        elif suffix in FM_DIFFS:
            spec["decision_vi"] = "Thử trên tập con FotMob đủ độ phủ; hoãn cho mô hình toàn lịch sử."
        else:
            spec["decision_vi"] = "Thử trong kiểm tra loại bỏ nhóm biến; chưa xác nhận đóng góp ngoài Elo."
        return spec
    for side, side_vi, venue in (("home", "đội chủ nhà", "home"), ("away", "đội khách", "away")):
        prefix = side + "_"
        if not feature.startswith(prefix):
            continue
        suffix = feature.removeprefix(prefix)
        if suffix.startswith(venue + "_"):
            metric = suffix.removeprefix(venue + "_")
            meaning = {
                "points5": "Điểm trung bình/trận (0–3)",
                "goals_for5": "Bàn ghi trung bình/trận",
                "goals_against5": "Bàn thua trung bình/trận",
            }[metric]
            venue_vi = "sân nhà" if venue == "home" else "sân khách"
            return {
                "group": "Phong độ riêng theo sân", "definition_vi": (
                    f"{meaning} của {side_vi} trong tối đa 5 trận EPL trước trên {venue_vi}; "
                    "đây là cửa sổ riêng, không phải lọc riêng sân từ 5 trận gần nhất chung."
                ),
                "source_vi": RESULT_SOURCE, "availability_vi": HISTORY_AVAILABILITY,
                "decision_vi": "Thử như bổ sung/biểu diễn thay thế cho phong độ chung; kiểm tra dư thừa và độ cũ lịch sử.",
                "caution_vi": WINDOW_CAUTION + " Năm trận cùng sân có thể trải dài hơn năm trận chung.",
            }
        spec = _team_spec(suffix)
        spec["definition_vi"] = spec["definition_vi"] + f"; đội được xét: {side_vi}."
        return spec
    raise ValueError(f"Chưa có định nghĩa đặc trưng: {feature}")


def _spearman(values: pd.Series, target: pd.Series) -> float:
    pair = pd.DataFrame({"feature": values, "target": target}).dropna()
    if len(pair) < 3 or pair["feature"].nunique() < 2 or pair["target"].nunique() < 2:
        return np.nan
    return round(float(pair["feature"].corr(pair["target"], method="spearman")), 6)


def build_feature_catalog(features: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Describe all non-ID/non-target columns and summarize their observed data.

    Missingness and season coverage use all rows in ``features``. Correlations
    use available valid labels from ``matches``, aligned explicitly by match_id,
    and pairwise complete cases. They are exploratory associations, not feature
    importance or evidence of incremental predictive value. H means the binary
    indicator of a home win; goal difference means home_goals minus away_goals.
    """
    required_match = {"match_id", "result", "home_goals", "away_goals"}
    if not required_match.issubset(matches.columns):
        raise ValueError(f"matches thiếu cột: {sorted(required_match - set(matches.columns))}")
    if not {"match_id", "season"}.issubset(features.columns):
        raise ValueError("features phải có match_id và season.")
    if features["match_id"].duplicated().any() or matches["match_id"].duplicated().any():
        raise ValueError("match_id phải duy nhất ở cả hai bảng để tính tương quan đúng.")

    labels = matches.set_index("match_id").reindex(features["match_id"])
    labels.index = features.index
    valid_result = labels["result"].isin(["H", "D", "A"])
    if "status" in labels:
        valid_result &= labels["status"].eq("played")
    home_win = labels["result"].eq("H").astype(float).where(valid_result)
    goal_diff = (
        pd.to_numeric(labels["home_goals"], errors="coerce")
        - pd.to_numeric(labels["away_goals"], errors="coerce")
    ).where(valid_result)

    rows = []
    for feature in features.columns:
        if feature in ID_COLUMNS | TARGET_COLUMNS:
            continue
        values = features[feature]
        spec = _describe(feature)
        seasons = features.loc[values.notna(), "season"].dropna().astype(str)
        numeric = pd.api.types.is_numeric_dtype(values.dtype)
        rows.append({
            "feature": feature, **spec,
            "missing_pct": round(float(values.isna().mean() * 100), 3) if len(values) else np.nan,
            "n_valid": int(values.notna().sum()),
            "first_season_valid": seasons.min() if len(seasons) else pd.NA,
            "last_season_valid": seasons.max() if len(seasons) else pd.NA,
            "spearman_H": _spearman(values, home_win) if numeric else np.nan,
            "spearman_goal_diff": _spearman(values, goal_diff) if numeric else np.nan,
        })
    return pd.DataFrame(rows, columns=CATALOG_COLUMNS)
