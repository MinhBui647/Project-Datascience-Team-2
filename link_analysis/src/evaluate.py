"""Bước 3 – Đặc trưng đồ thị có cải thiện dự đoán không? (một script cho toàn bộ phần đánh giá)

  A. H10: 85 đặc trưng số của features.csv, CÓ và KHÔNG CÓ 5 đặc trưng đồ thị (có / không có kèo nhà cái).
     Đối chứng dùng cột của khâu Tích hợp dữ liệu: elo_diff (chỉ Elo), odds_*_prob (chỉ kèo).
  B. H4 (giả thuyết của báo cáo EDA): đối đầu có bổ sung thông tin sau Elo + phong độ không.
  C. Tương quan của đặc trưng đồ thị với elo_diff và h2h_home_points (giải thích kết quả).
  D. Danh mục 9 đặc trưng gr_* theo khuôn feature_candidates.csv của khâu EDA.
  E. Bảng tóm tắt kết quả chính (results/evaluation_summary.csv) và hình figures/4_danh_gia.png.

Chia theo thời gian, không xáo trộn: rolling (huấn luyện mọi mùa trước, kiểm thử từng mùa từ 2010/11) và
split của nhóm (huấn luyện đến 2023/24, kiểm thử từ 2024/25). Hai mô hình: Gradient Boosting và Logistic Regression
(cắt mỗi cột về phân vị 1–99% của tập huấn luyện rồi chuẩn hóa). Chỉ số: log loss, Brier, accuracy, và khoảng tin cậy
95% của chênh lệch log loss so với cấu hình tham chiếu (bootstrap theo cặp, 2.000 lần).

Chạy:  python src/evaluate.py --features features.csv --graph results/graph_features_matches.csv
"""
import argparse
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
CLASSES = ["A", "D", "H"]
GRAPH = ["gr_pagerank_log_ratio", "gr_common_opp_diff", "gr_trans2_diff", "gr_h2h_share", "gr_h2h_gd"]
IDS = ["match_id", "season", "date", "kickoff_time", "gameweek", "home_club_id", "home_team", "away_club_id",
       "away_team", "status", "provisional", "postponed", "home_goals", "away_goals", "result"]
FORM = ["elo_diff", "diff_points5", "diff_goals_for5", "diff_goals_against5"]


class ClipPercentile(BaseEstimator, TransformerMixin):
    """Cắt mỗi cột về [phân vị 1, phân vị 99] học từ tập huấn luyện (chống ngoại lai đuôi nặng)."""

    def fit(self, X, y=None):
        X = np.asarray(X, float)
        self.lo_, self.hi_ = np.nanpercentile(X, 1, 0), np.nanpercentile(X, 99, 0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X, float), self.lo_, self.hi_)


def make_model(kind):
    if kind == "logreg":
        return make_pipeline(SimpleImputer(strategy="median"), ClipPercentile(), StandardScaler(),
                             LogisticRegression(C=0.3, max_iter=3000))
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.04, max_iter=150,
                                          min_samples_leaf=60, l2_regularization=5.0, random_state=0)


def per_match_logloss(y, P):
    return -np.log(np.clip(P[np.arange(len(y)), pd.Categorical(y, CLASSES).codes], 1e-12, 1))


def brier(y, P):
    return float(((P - np.eye(3)[pd.Categorical(y, CLASSES).codes]) ** 2).sum(1).mean())


def predict(df, sets, kind, splits):
    """splits: danh sách (mask huấn luyện, mask kiểm thử). Trả về xác suất gộp của từng bộ đặc trưng, nhãn, mùa."""
    probs, ys, seasons = {k: [] for k in sets}, [], []
    for tr, te in splits:
        train, test = df[tr], df[te]
        if test.empty:
            continue
        for name, cols in sets.items():
            cols = [c for c in cols if train[c].notna().any()]  # xG chỉ có từ 2014/15: bỏ cột rỗng ở các mùa đầu
            m = make_model(kind).fit(train[cols], train.result)
            assert list(m.classes_) == CLASSES
            probs[name].append(m.predict_proba(test[cols]))
        ys.append(test.result.values)
        seasons.append(test.season.values)
    return {k: np.vstack(v) for k, v in probs.items()}, np.concatenate(ys), np.concatenate(seasons)


def report(title, probs, y, seasons, ref):
    print(f"\n=== {title}  (n = {len(y)} trận) ===")
    print(f"{'bộ đặc trưng':34s} {'log loss':>8s} {'Brier':>7s} {'acc':>6s}   chênh lệch vs tham chiếu, KTC 95%      mùa tốt hơn")
    ll_ref = per_match_logloss(y, probs[ref])
    stats = {}
    for name, P in probs.items():
        ll = per_match_logloss(y, P)
        line = f"{name:34s} {ll.mean():8.4f} {brier(y, P):7.4f} {accuracy_score(y, np.array(CLASSES)[P.argmax(1)]):6.3f}"
        if name == ref:
            print(line + "   (tham chiếu)")
            continue
        d = ll - ll_ref
        rng = np.random.default_rng(0)  # cùng một chuỗi mẫu cho mọi so sánh: kết quả không phụ thuộc thứ tự
        lo, hi = np.percentile([d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)], [2.5, 97.5])
        wins = sum(ll[seasons == s].mean() < ll_ref[seasons == s].mean() for s in np.unique(seasons))
        print(line + f"   {d.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]          {wins}/{len(np.unique(seasons))}")
        stats[name] = {"log_loss": ll.mean(), "delta": d.mean(), "lo": lo, "hi": hi}
    return stats


class Tee:
    def __init__(self, path):
        self.f, self.out = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.f.write(s)
        self.out.write(s)

    def flush(self):
        self.f.flush()
        self.out.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--graph", required=True)
    ap.add_argument("--out", default="results/evaluation.txt")
    ap.add_argument("--catalog", default="results/feature_candidates_link_analysis.csv")
    ap.add_argument("--summary", default="results/evaluation_summary.csv")
    ap.add_argument("--figure", default="figures/4_danh_gia.png")
    a = ap.parse_args()
    sys.stdout = Tee(a.out)

    f, g = pd.read_csv(a.features), pd.read_csv(a.graph)
    full = f.merge(g[["match_id"] + [c for c in g.columns if c.startswith("gr_")]], on="match_id", how="left", validate="1:1")
    full = full[full.status == "played"]
    df = full[full.season != "2000/01"].sort_values("date", kind="stable").reset_index(drop=True)
    num = [c for c in f.columns if c not in IDS and pd.api.types.is_numeric_dtype(f[c])]
    odds = [c for c in num if c.startswith("odds_")]
    no_odds = [c for c in num if c not in odds]
    seasons = sorted(df.season.unique())
    rolling = [(df.season < s, df.season == s) for s in seasons if s >= "2010/11"]
    team_split = [(df.season < "2024/25", df.season >= "2024/25")]
    print(f"{len(df)} trận đã đá | {len(num)} đặc trưng số của nhóm (trong đó {len(odds)} cột kèo) | đặc trưng đồ thị: {GRAPH}")

    print("\n######## A. H10 – đặc trưng đồ thị có bổ sung cho 85 đặc trưng của nhóm không")
    sets = {
        "chỉ Elo (elo_diff)": ["elo_diff"],
        "chỉ đồ thị (5 cột gr_*)": GRAPH,
        "chỉ kèo (odds_*_prob)": odds,
        "85 đặc trưng, không kèo": no_odds,
        "85 đặc trưng, không kèo + đồ thị": no_odds + GRAPH,
        "85 đặc trưng": num,
        "85 đặc trưng + đồ thị": num + GRAPH,
    }
    main_stats = {}
    for kind, label in [("hgb", "Gradient Boosting"), ("logreg", "Logistic Regression (cắt phân vị)")]:
        for tag, splits in [("rolling 2010/11 →", rolling), ("split nhóm: train < 2024/25, test ≥ 2024/25", team_split)]:
            probs, y, s = predict(df, sets, kind, splits)
            st = report(f"{label} | {tag}", probs, y, s, "85 đặc trưng")
            if tag.startswith("rolling"):
                main_stats[kind] = st
            if tag.startswith("rolling"):
                # so sánh riêng kịch bản không kèo
                sub = {k: probs[k] for k in ["85 đặc trưng, không kèo", "85 đặc trưng, không kèo + đồ thị"]}
                report(f"{label} | {tag} | kịch bản không kèo", sub, y, s, "85 đặc trưng, không kèo")

    print("\n######## B. H4 (EDA) – đối đầu có bổ sung sau Elo + phong độ không")
    h4 = {
        "Elo + phong độ": FORM,
        "+ H2H của nhóm": FORM + ["h2h_home_points", "h2h_home_goal_diff", "h2h_matches"],
        "+ H2H có trọng số (gr_h2h_*)": FORM + ["gr_h2h_share", "gr_h2h_gd"],
    }
    many = df.h2h_matches.fillna(0) >= 3
    for kind, label in [("hgb", "Gradient Boosting"), ("logreg", "Logistic Regression (cắt phân vị)")]:
        for tag, cond in [("mọi trận", None), ("h2h_matches ≥ 3", many)]:
            splits = [(tr, te & cond) if cond is not None else (tr, te) for tr, te in rolling]
            probs, y, s = predict(df, h4, kind, splits)
            report(f"{label} | rolling 2010/11 → | {tag}", probs, y, s, "Elo + phong độ")

    print("\n######## C. Tương quan Pearson của đặc trưng đồ thị với đặc trưng sẵn có (mọi trận đã đá)")
    for c in GRAPH:
        print(f"  {c:24s} elo_diff {full[c].corr(full.elo_diff):+.2f}   h2h_home_points {full[c].corr(full.h2h_home_points):+.2f}")

    sys.stdout = sys.stdout.out
    catalog(full, a.catalog)
    summary_figure(main_stats, a.summary, a.figure)


def summary_figure(st, csv_path, fig_path):
    """Bảng tóm tắt + hình kết quả chính (rolling 2010/11, so với "85 đặc trưng")."""
    import figures as fg
    pick = [("hgb", "85 đặc trưng + đồ thị", "+ 5 đặc trưng đồ thị · Gradient Boosting", "graph"),
            ("logreg", "85 đặc trưng + đồ thị", "+ 5 đặc trưng đồ thị · Logistic Regression", "graph"),
            ("hgb", "chỉ Elo (elo_diff)", "Chỉ Elo thay cho 85 đặc trưng · Gradient Boosting", "ref"),
            ("hgb", "chỉ đồ thị (5 cột gr_*)", "Chỉ 5 đặc trưng đồ thị · Gradient Boosting", "ref"),
            ("hgb", "chỉ kèo (odds_*_prob)", "Chỉ kèo nhà cái · Gradient Boosting", "ref")]
    rows = pd.DataFrame([{"model": k, "config": c, "label": lab, "kind": kind, **st[k][c]} for k, c, lab, kind in pick])
    rows.round(4).to_csv(csv_path, index=False, encoding="utf-8-sig")
    fg.evaluation(rows, "85 đặc trưng", path=fig_path)
    print(f"Đã ghi {csv_path} và {fig_path}")


GROUP = "Mạng lưới đối đầu (Link Analysis)"
SOURCE = "Tự tính từ kết quả/tỷ số EPL trước trận trong matches.csv: đồ thị đối đầu, cửa sổ 3 năm, nửa đời 365 ngày."
AVAIL = ("Theo thiết kế: có trước trận, chỉ dùng các trận có ngày nhỏ hơn ngày thi đấu; đã kiểm chứng bằng --selftest "
         "và kiểm thử đảo kết quả tương lai.")
NOT_USED = "Không bắt buộc đưa vào mô hình cuối: thêm vào 85 đặc trưng không cải thiện log loss (xem results/evaluation.txt)."
REF = "Cột tham chiếu/độ tin cậy; không đưa trực tiếp vào mô hình."
SPEC = {
    "gr_pagerank_home": ("PageRank của đội nhà trên đồ thị ưu thế (bên thua/hòa bầu cho bên thắng theo phần điểm), "
                         "trung bình các đội trong cửa sổ = 1.", REF,
                         "Thiếu khi đội chưa đá trận nào trong 3 năm gần nhất; trung bình ~1,17 vì cửa sổ gồm cả đội đã xuống hạng."),
    "gr_pagerank_away": ("PageRank của đội khách, cùng cách tính.", REF, "Như gr_pagerank_home."),
    "gr_pagerank_log_ratio": ("ln(PageRank nhà / PageRank khách).", NOT_USED, "Trùng thông tin sức mạnh với elo_diff."),
    "gr_common_opp_diff": ("Trung bình có trọng số của (phần điểm đội nhà − phần điểm đội khách) trước cùng một đối thủ chung.",
                           NOT_USED, "Gần như trùng elo_diff."),
    "gr_common_opp_n": ("Số đối thủ chung dùng để tính gr_common_opp_diff.", REF, "Độ tin cậy của gr_common_opp_diff."),
    "gr_trans2_diff": ("Ưu thế bắc cầu 2 bước: Σ ưu thế(nhà→C)·ưu thế(C→khách) trừ chiều ngược lại.", NOT_USED,
                       "Trùng thông tin sức mạnh với elo_diff."),
    "gr_h2h_share": ("Phần điểm trung bình của đội nhà trong các lần gặp trực tiếp 10 năm gần nhất (nửa đời 3 năm), "
                     "co về 0,5 bằng 1 trận ảo, trừ 0,5.", NOT_USED, "Tương quan cao với h2h_home_points; xem giả thuyết H4."),
    "gr_h2h_gd": ("Hiệu số bàn thắng trung bình có trọng số thời gian trong các lần gặp trực tiếp, co về 0.", NOT_USED,
                  "Tương quan cao với gr_h2h_share và h2h_home_goal_diff."),
    "gr_h2h_weight": ("Tổng trọng số thời gian của các lần gặp trực tiếp.", REF,
                      "Bằng 0 khi chưa gặp trong 10 năm; dùng để lọc trường hợp đối đầu thưa."),
}


def catalog(full, path):
    """Danh mục đặc trưng gr_* theo đúng khuôn feature_candidates.csv của khâu EDA (cùng cách tính Spearman)."""
    from scipy.stats import spearmanr
    home_win = (full.home_goals > full.away_goals).astype(int)
    goal_diff = full.home_goals - full.away_goals
    rows = []
    for c, (definition, decision, caution) in SPEC.items():
        ok = full[c].notna()
        rows.append({"feature": c, "group": GROUP, "definition_vi": definition, "source_vi": SOURCE, "availability_vi": AVAIL,
                     "missing_pct": round(100 * (1 - ok.mean()), 3), "n_valid": int(ok.sum()),
                     "first_season_valid": full.season[ok].min(), "last_season_valid": full.season[ok].max(),
                     "spearman_H": spearmanr(full[c][ok], home_win[ok]).statistic,
                     "spearman_goal_diff": spearmanr(full[c][ok], goal_diff[ok]).statistic,
                     "decision_vi": decision, "caution_vi": caution})
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"Đã ghi danh mục đặc trưng: {path}")


if __name__ == "__main__":
    main()
