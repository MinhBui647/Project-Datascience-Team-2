"""Read-only, independently reconstructed checks for the two EDA input tables.

The temporal checks establish agreement with prior *observed EPL* fixtures for
selected columns. They do not establish source availability at prediction time
or certify every predictor against leakage.
"""

from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd


CHECK_COLUMNS = ["check", "status", "count", "detail_vi"]
ISSUE_COLUMNS = [
    "match_id", "date", "home_team", "away_team", "category", "field",
    "value", "reason_vi",
]


def _truth(values: pd.Series) -> pd.Series:
    return values.astype(str).str.lower().isin(["true", "1", "yes"])


def _number(value):
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    return value


def _mean(records, field):
    values = [r[field] for r in records if pd.notna(r[field])]
    return float(np.mean(values)) if values else np.nan


def _prior_observed_features(matches: pd.DataFrame) -> pd.DataFrame:
    """Recompute selected columns; read state before adding the current result."""
    work = matches.copy()
    work["_dt"] = pd.to_datetime(work["date"], errors="coerce")
    work["_played"] = work["status"].eq("played")
    if "kickoff_time" not in work:
        work["kickoff_time"] = np.nan
    work = work.sort_values(
        ["_played", "_dt", "kickoff_time"],
        ascending=[False, True, True], na_position="last", kind="stable",
    )
    first_season = work["season"].min()
    recent = defaultdict(lambda: deque(maxlen=5))
    venue_recent = defaultdict(lambda: deque(maxlen=5))
    season_totals = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    last_date = {}
    head_to_head = defaultdict(lambda: deque(maxlen=5))
    ratings, rating_season = {}, None
    rows = []

    for record in work.to_dict("records"):
        home, away = record["home_club_id"], record["away_club_id"]
        season, date, played = record["season"], record["_dt"], record["_played"]
        if played and season != rating_season:
            if rating_season is not None:
                ratings = {club: .8 * rating + .2 * 1500 for club, rating in ratings.items()}
            rating_season = season
        default = 1500 if season == first_season else 1450
        rh, ra = ratings.get(home, default), ratings.get(away, default)
        expected_score = 1 / (1 + 10 ** ((ra - rh - 65) / 400))
        derived = {
            "match_id": record["match_id"], "home_elo": rh, "away_elo": ra,
            "elo_diff": rh - ra, "elo_home_win_prob": expected_score,
        }
        postponed = str(record.get("postponed", False)).lower() in {"true", "1"}
        for side, club in [("home", home), ("away", away)]:
            history = list(recent[club])
            vhistory = list(venue_recent[(club, side)])
            count, points, gf, ga = season_totals[(club, season)]
            for name, metric in [
                ("points5", "points"), ("goals_for5", "gf"),
                ("goals_against5", "ga"), ("shots_on_target5", "sot"),
                ("xg5", "xg"), ("xga5", "xga"),
            ]:
                derived[f"{side}_{name}"] = _mean(history, metric)
            derived[f"{side}_form"] = "".join(x["result"] for x in history) or np.nan
            for word, result in [("wins5", "W"), ("draws5", "D"), ("losses5", "L")]:
                derived[f"{side}_{word}"] = (
                    sum(x["result"] == result for x in history) if history else np.nan
                )
            for name, metric in [("points5", "points"), ("goals_for5", "gf"), ("goals_against5", "ga")]:
                derived[f"{side}_{side}_{name}"] = _mean(vhistory, metric)
            derived[f"{side}_played"] = count
            derived[f"{side}_ppg"] = points / count if count else np.nan
            derived[f"{side}_goals_for_pg"] = gf / count if count else np.nan
            derived[f"{side}_goals_against_pg"] = ga / count if count else np.nan
            derived[f"{side}_rest_days"] = (
                (date - last_date[club]).days
                if club in last_date and not postponed and pd.notna(date) else np.nan
            )
        previous_pair = list(head_to_head[frozenset((home, away))])
        oriented = [
            (hg, ag) if previous_home == home else (ag, hg)
            for previous_home, hg, ag in previous_pair
        ]
        derived["h2h_matches"] = len(oriented)
        derived["h2h_home_points"] = (
            float(np.mean([3 if gf > ga else 1 if gf == ga else 0 for gf, ga in oriented]))
            if oriented else np.nan
        )
        derived["h2h_home_goal_diff"] = (
            float(np.mean([gf - ga for gf, ga in oriented])) if oriented else np.nan
        )
        rows.append(derived)

        # No result of this fixture enters any predictor above.
        if not played or pd.isna(record["home_goals"]) or pd.isna(record["away_goals"]):
            continue
        hg, ag = float(record["home_goals"]), float(record["away_goals"])
        outcome = 1 if hg > ag else .5 if hg == ag else 0
        margin = abs(hg - ag)
        multiplier = 1 if margin <= 1 else 1.5 if margin == 2 else (11 + margin) / 8
        movement = 20 * multiplier * (outcome - expected_score)
        ratings[home], ratings[away] = rh + movement, ra - movement
        for side, opposite, club, gf, ga in [
            ("home", "away", home, hg, ag), ("away", "home", away, ag, hg),
        ]:
            points = 3 if gf > ga else 1 if gf == ga else 0
            stats = {
                "points": points, "gf": gf, "ga": ga,
                "sot": record.get(f"{side}_shots_on_target", np.nan),
                "xg": record.get(f"{side}_xg", np.nan),
                "xga": record.get(f"{opposite}_xg", np.nan),
                "result": "W" if gf > ga else "D" if gf == ga else "L",
            }
            recent[club].append(stats)
            venue_recent[(club, side)].append(stats)
            total = season_totals[(club, season)]
            total[0] += 1
            total[1] += points
            total[2] += gf
            total[3] += ga
            last_date[club] = date
        head_to_head[frozenset((home, away))].append((home, hg, ag))
    return pd.DataFrame(rows)


def audit_inputs(matches: pd.DataFrame, features: pd.DataFrame):
    """Return granular checks, row-level flags, and JSON-compatible audit details.

    Input DataFrames are never edited. A warning is an investigation flag, not
    an instruction to delete or impute a row.
    """
    checks, issues = [], []

    def check(name, count, detail, severity="fail"):
        count = int(count)
        checks.append({
            "check": name, "status": severity if count else "pass",
            "count": count, "detail_vi": detail,
        })

    def info(name, count, detail):
        checks.append({"check": name, "status": "info", "count": int(count), "detail_vi": detail})

    def flag_rows(frame, mask, category, field, reason, values=None):
        selected = frame.loc[mask]
        for index, row in selected.iterrows():
            value = values.loc[index] if isinstance(values, pd.Series) else row.get(field)
            issues.append({
                **{c: _number(row.get(c)) for c in ISSUE_COLUMNS[:4]},
                "category": category, "field": field, "value": _number(value),
                "reason_vi": reason,
            })

    info("matches_shape", len(matches), f"matches.csv: {len(matches):,} dòng × {matches.shape[1]} cột.")
    info("features_shape", len(features), f"features.csv: {len(features):,} dòng × {features.shape[1]} cột.")
    check("row_count_alignment", abs(len(matches) - len(features)), "Chênh lệch số dòng giữa hai bảng.")
    for name, frame in [("matches", matches), ("features", features)]:
        for test, mask in [
            ("null_match_id", frame["match_id"].isna()),
            ("duplicate_match_id", frame["match_id"].duplicated(keep=False)),
        ]:
            check(f"{name}_{test}", mask.sum(), "Khóa trận phải duy nhất và không thiếu.")
            flag_rows(frame, mask, "key", "match_id", "Khóa trận bị thiếu hoặc trùng lặp.")
        duplicate = frame.duplicated(["date", "home_club_id", "away_club_id"], keep=False)
        check(f"{name}_duplicate_fixture", duplicate.sum(), "Trùng ngày, đội nhà và đội khách.")
        flag_rows(frame, duplicate, "duplicate_fixture", "match_id", "Trùng bộ ngày–đội nhà–đội khách.")
        date_invalid = pd.to_datetime(frame["date"], errors="coerce").isna()
        check(f"{name}_invalid_date", date_invalid.sum(), "Ngày trận phải đọc được.")
        flag_rows(frame, date_invalid, "date", "date", "Ngày trận không hợp lệ hoặc thiếu.")

    keys_m, keys_f = set(matches.match_id.dropna()), set(features.match_id.dropna())
    check("match_id_set_alignment", len(keys_m ^ keys_f), "Số khóa chỉ xuất hiện ở một trong hai bảng.")
    unique_keys = not matches.match_id.duplicated().any() and not features.match_id.duplicated().any()
    shared = [c for c in matches.columns if c in features.columns and c != "match_id"]
    shared_discrepancies = {}
    if unique_keys:
        joined = matches.merge(features, on="match_id", suffixes=("_m", "_f"), validate="one_to_one")
        for col in shared:
            left, right = joined[f"{col}_m"], joined[f"{col}_f"]
            equal = left.eq(right) | (left.isna() & right.isna())
            # CSV inference may represent logically identical numbers differently.
            if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
                equal = pd.Series(np.isclose(left, right, equal_nan=True, rtol=1e-9, atol=1e-9), index=joined.index)
            count = int((~equal).sum())
            shared_discrepancies[col] = count
            check(f"shared_{col}", count, f"Đối chiếu {col} giữa hai bảng theo match_id.")
            if count:
                bad = features[features.match_id.isin(joined.loc[~equal, "match_id"])]
                flag_rows(bad, pd.Series(True, index=bad.index), "shared_field_mismatch", col, "Hai bảng không đồng nhất.")

    expected_result = pd.Series(np.select(
        [matches.home_goals > matches.away_goals, matches.home_goals < matches.away_goals],
        ["H", "A"], default="D",
    ), index=matches.index)
    played = matches["status"].eq("played")
    goals_known = matches[["home_goals", "away_goals"]].notna().all(axis=1)
    result_bad = (played & ~goals_known) | (goals_known & ~matches.result.eq(expected_result))
    check("result_consistent_with_goals", result_bad.sum(), "Nhãn H/D/A phải khớp tỷ số; trận đã đá phải có đủ bàn thắng.")
    flag_rows(matches, result_bad, "label", "result", "Nhãn kết quả thiếu hoặc không khớp tỷ số.")
    same_team = matches.home_club_id.eq(matches.away_club_id)
    check("same_team_fixture", same_team.sum(), "Một trận cần hai đội khác nhau.")
    flag_rows(matches, same_team, "fixture", "home_club_id", "Đội nhà và đội khách trùng nhau.")

    for score, home, away in [
        ("score", "home_goals", "away_goals"),
        ("ht_score", "ht_home_goals", "ht_away_goals"),
    ]:
        if all(c in matches for c in [score, home, away]):
            known = matches[[home, away]].notna().all(axis=1)
            expected = matches[home].map(lambda x: f"{x:g}" if pd.notna(x) else "") + "-" + matches[away].map(lambda x: f"{x:g}" if pd.notna(x) else "")
            bad = known & ~matches[score].eq(expected)
            check(f"{score}_string", bad.sum(), "Chuỗi tỷ số phải khớp các cột số bàn.")
            flag_rows(matches, bad, "score", score, "Chuỗi tỷ số không khớp số bàn.")

    for col in matches.select_dtypes(include="number").columns:
        values = matches[col]
        negative = values.lt(0)
        check(f"nonnegative_{col}", negative.sum(), "Các cột số của matches.csv không được âm.")
        flag_rows(matches, negative, "negative_value", col, "Giá trị âm ngoài miền dữ liệu.")
        infinite = pd.Series(np.isinf(values.astype(float)), index=matches.index)
        check(f"finite_{col}", infinite.sum(), "Giá trị số phải hữu hạn.")
        flag_rows(matches, infinite, "nonfinite", col, "Giá trị vô hạn.")

    shotbox_match_ids = set()
    zero_shot_components_match_ids = set()
    zero_shot_components_side_counts = {}
    for side in ["home", "away"]:
        for name, first, second, reason in [
            ("sot_le_shots", f"{side}_shots_on_target", f"{side}_shots", "Sút trúng đích lớn hơn tổng số cú sút."),
            ("ht_le_ft", f"ht_{side}_goals", f"{side}_goals", "Bàn hiệp một lớn hơn bàn cả trận."),
            ("accurate_passes_le_passes", f"{side}_accurate_passes", f"{side}_passes", "Chuyền thành công lớn hơn tổng đường chuyền."),
            ("missed_le_big_chances", f"{side}_big_chances_missed", f"{side}_big_chances", "Cơ hội lớn bỏ lỡ lớn hơn tổng cơ hội lớn."),
        ]:
            if first in matches and second in matches:
                bad = matches[first].gt(matches[second])
                check(f"{side}_{name}", bad.sum(), reason)
                flag_rows(matches, bad, "stat_consistency", first, reason)
        for suffix in ["possession", "pass_accuracy"]:
            col = f"{side}_{suffix}"
            if col in matches:
                bad = matches[col].lt(0) | matches[col].gt(100)
                check(f"range_{col}", bad.sum(), "Tỷ lệ phần trăm phải nằm trong [0, 100].")
                flag_rows(matches, bad, "percentage_range", col, "Phần trăm nằm ngoài [0, 100].")
        col = f"{side}_possession"
        if col in matches:
            extreme = matches[col].lt(10)
            check(f"extreme_{col}_under10", extreme.sum(), "Kiểm tra nguồn cho kiểm soát bóng dưới 10%; chưa kết luận là lỗi.", "warn")
            flag_rows(matches, extreme, "suspect_possession", col, "Kiểm soát bóng dưới 10%; cần đối chiếu nguồn.")
        inside, outside, total = [f"{side}_{x}" for x in ["shots_inside_box", "shots_outside_box", "shots"]]
        if all(c in matches for c in [inside, outside, total]):
            bad = (matches[inside] + matches[outside]).gt(matches[total])
            shotbox_match_ids.update(matches.loc[bad, "match_id"])
            reason = "Tổng cú sút trong/ngoài vòng cấm vượt tổng sút; cần đối chiếu định nghĩa và nguồn FD/FotMob."
            check(f"{side}_shot_components_exceed_total", bad.sum(), reason, "warn")
            excess = matches[inside] + matches[outside] - matches[total]
            flag_rows(matches, bad, "cross_source_shots", total, reason, excess)
            zero_components = matches[inside].eq(0) & matches[outside].eq(0) & matches[total].gt(0)
            zero_shot_components_match_ids.update(matches.loc[zero_components, "match_id"])
            zero_shot_components_side_counts[side] = int(zero_components.sum())
            zero_reason = (
                "Cú sút trong và ngoài vòng cấm đều bằng 0 nhưng tổng sút FD dương; "
                "cần kiểm tra độ đầy đủ và định nghĩa dữ liệu FotMob/FD, chưa xác nhận là lỗi."
            )
            check(f"{side}_zero_shot_components_positive_total", zero_components.sum(), zero_reason, "warn")
            flag_rows(matches, zero_components, "cross_source_zero_shots", total, zero_reason)

    if all(c in matches for c in ["home_possession", "away_possession"]):
        bad = (matches.home_possession + matches.away_possession - 100).abs().gt(1)
        check("possession_sum_100", bad.sum(), "Tổng kiểm soát bóng hai đội sai khác quá 1 điểm phần trăm.")
        flag_rows(matches, bad, "possession_sum", "home_possession", "Tổng kiểm soát bóng hai đội không gần 100%.")
    if "attendance_imputed" in matches:
        imputed = _truth(matches.attendance_imputed)
        info("attendance_imputed", imputed.sum(), "Khán giả đã được điền bởi pipeline; không coi là phép đo độc lập.")
        flag_rows(matches, imputed, "attendance_imputed", "attendance", "Giá trị khán giả được điền; cần giữ cờ attendance_imputed.")

    temporal_details = {}
    if unique_keys and not pd.to_datetime(matches.date, errors="coerce").isna().any():
        reconstructed = _prior_observed_features(matches).set_index("match_id")
        actual = features.set_index("match_id").reindex(reconstructed.index)
        temporal_fields = [c for c in reconstructed if c in actual]
        for col in temporal_fields:
            left, right = reconstructed[col], actual[col]
            if col.endswith("_form"):
                equal = left.eq(right) | (left.isna() & right.isna())
            else:
                equal = pd.Series(np.isclose(
                    pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce"),
                    equal_nan=True, rtol=1e-8, atol=1e-8,
                ), index=actual.index)
            count = int((~equal).sum())
            temporal_details[col] = {
                "rows_compared": len(actual), "nonnull_expected": int(left.notna().sum()),
                "mismatch_count": count,
            }
            check(f"prior_observed_{col}", count, "Tính lại trước trận từ lịch sử EPL quan sát được; không dùng kết quả trận hiện tại.")
            if count:
                bad_ids = set(actual.index[~equal])
                bad = features[features.match_id.isin(bad_ids)]
                expected_map = left.to_dict()
                observed_map = right.to_dict()
                values = bad.match_id.map(lambda mid: f"observed={observed_map[mid]}; expected={expected_map[mid]}")
                flag_rows(bad, pd.Series(True, index=bad.index), "prior_feature_mismatch", col, "Không khớp phép tính độc lập từ các trận quan sát trước đó.", values)
    else:
        check("temporal_audit_skipped", 1, "Không tính lại lịch sử khi khóa trùng hoặc ngày không hợp lệ.", "warn")

    for side in ["home", "away"]:
        col = f"{side}_rest_days"
        if col in features:
            gap = features[col].gt(30)
            check(f"{col}_gt30", gap.sum(), "Khoảng cách tới trận EPL quan sát trước vượt 30 ngày; không đồng nhất với ngày nghỉ thực tế.", "warn")
            flag_rows(features, gap, "observed_epl_gap", col, "Bao gồm nghỉ mùa, gián đoạn và thời gian chơi ngoài EPL; không diễn giải là ngày nghỉ thực tế.")
    prob_cols = ["odds_home_prob", "odds_draw_prob", "odds_away_prob"]
    if all(c in features for c in prob_cols):
        prob = features[prob_cols]
        bad = ((prob < 0) | (prob > 1)).any(axis=1)
        check("odds_prob_range", bad.sum(), "Các xác suất odds phải nằm trong [0, 1].")
        bad_sum = prob.notna().all(axis=1) & prob.sum(axis=1).sub(1).abs().gt(1e-8)
        check("odds_prob_sum", bad_sum.sum(), "Ba xác suất odds đầy đủ phải cộng thành 1.")
        flag_rows(features, bad | bad_sum, "odds_probability", "odds_home_prob", "Xác suất odds ngoài miền hoặc tổng không bằng 1.")

    coverage = []
    for season, group in matches.groupby("season", sort=True):
        clubs = set(group.home_club_id) | set(group.away_club_id)
        coverage.append({
            "season": season, "matches": len(group), "clubs": len(clubs),
            "first_date": str(group.date.min()), "last_date": str(group.date.max()),
            "rows_season_complete": int(_truth(group.season_complete).sum()) if "season_complete" in group else None,
            "expected_matches_double_round_robin": len(clubs) * (len(clubs) - 1),
        })
    numeric_predictors = [
        c for c in features.select_dtypes(include="number")
        if c not in {"home_goals", "away_goals", "gameweek"}
    ]
    all_issue_frame = pd.DataFrame(issues, columns=ISSUE_COLUMNS)
    details = {
        "matches_shape": [int(x) for x in matches.shape],
        "features_shape": [int(x) for x in features.shape],
        "coverage_by_season": coverage,
        "league_counts": {str(k): int(v) for k, v in matches.league.value_counts(dropna=False).items()} if "league" in matches else {},
        "source_flags_counts": {str(k): int(v) for k, v in matches.source_flags.value_counts(dropna=False).items()} if "source_flags" in matches else {},
        "shared_field_mismatch_counts": shared_discrepancies,
        "missing_rate_matches": {c: float(v) for c, v in matches.isna().mean().items()},
        "missing_rate_features": {c: float(v) for c, v in features.isna().mean().items()},
        "numeric_predictor_complete_case_rows": int(features[numeric_predictors].notna().all(axis=1).sum()),
        "all_column_complete_case_rows": int(features.notna().all(axis=1).sum()),
        "shot_components_exceed_total_unique_matches": len(shotbox_match_ids),
        "zero_shot_components_positive_total_unique_matches": len(zero_shot_components_match_ids),
        "zero_shot_components_positive_total_side_counts": zero_shot_components_side_counts,
        "selected_temporal_features": temporal_details,
        "selected_temporal_feature_count": len(temporal_details),
        "selected_temporal_mismatch_count": sum(x["mismatch_count"] for x in temporal_details.values()),
        "issue_rows": len(all_issue_frame),
        "semantics_vi": {
            "scope": "Kiểm toán thời gian chỉ gồm các cột được liệt kê; không xác nhận toàn bộ 87 đặc trưng hoặc thời điểm nhà cung cấp công bố dữ liệu.",
            "rolling5": "Trung bình trong tối đa 5 trận EPL quan sát trước đó, bỏ qua NaN trong cửa sổ; có thể mang lịch sử qua mùa mới hoặc khoảng vắng EPL.",
            "rest_days": "Chênh lệch ngày với trận EPL quan sát trước, không phải thời gian nghỉ giữa mọi giải đấu.",
            "season_ppg": "Điểm và bàn trung bình từ các trận quan sát trước trong cùng mùa; mùa thiếu không tái tạo đầy đủ bảng xếp hạng thực tế.",
            "h2h": "Tối đa 5 trận đối đầu quan sát trước; điểm và hiệu số quy về đội đang làm chủ nhà, bất kể sân trong trận cũ.",
            "elo_home_win_prob": "Kỳ vọng điểm Elo với thắng=1, hòa=0.5, thua=0; không phải xác suất thắng sân nhà H đã hiệu chỉnh cho H/D/A.",
            "attendance": "Giữ cờ attendance_imputed; các số 0 được điền trong giai đoạn COVID không là phép đo độc lập.",
            "anomalies": "Các dòng cảnh báo cần kiểm tra nguồn; không tự động xóa hoặc sửa dữ liệu.",
            "odds": "Odds chuẩn hóa có thể hỗ trợ dự đoán; cần xác nhận thời điểm lấy odds so với thời điểm chốt dự đoán.",
        },
    }
    return pd.DataFrame(checks, columns=CHECK_COLUMNS), all_issue_frame, details
