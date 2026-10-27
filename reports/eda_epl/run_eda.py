"""Rebuild the EPL exploratory report and charts from data/epl_dataset.csv.

Run from any directory: python reports/eda_epl/run_eda.py
The script reads source CSVs and writes only inside reports/eda_epl/.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FIG = OUT / "figures"
FIG.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.family": "Segoe UI",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.labelsize": 10,
    "figure.facecolor": "white",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 170,
})

COLORS = {"H": "#1769aa", "D": "#82909e", "A": "#e28a34"}
NAMES = {"H": "Chủ nhà thắng", "D": "Hòa", "A": "Đội khách thắng"}
KEY = ["season", "date", "home_team", "away_team"]
TARGET = ["home_goals", "away_goals", "result"]
IDENTITY = KEY + ["status"]


def save(fig: plt.Figure, name: str) -> None:
    fig.savefig(FIG / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def md_table(rows: list[list[object]], headers: list[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def outcome_table(frame: pd.DataFrame) -> pd.Series:
    return frame.result.value_counts(normalize=True).reindex(["H", "D", "A"]).fillna(0)


def feature_bins(frame: pd.DataFrame, col: str) -> tuple[pd.DataFrame, pd.Series]:
    valid = frame[[col, "result"]].dropna().copy()
    valid["group"] = pd.qcut(valid[col], 5, duplicates="drop")
    rates = valid.groupby("group", observed=True).result.value_counts(normalize=True).unstack(fill_value=0)
    counts = valid.groupby("group", observed=True).size()
    return rates.reindex(columns=["H", "D", "A"], fill_value=0), counts


def main() -> None:
    source = ROOT / "data" / "epl_dataset.csv"
    d = pd.read_csv(source)
    required = set(IDENTITY + TARGET + ["home_elo", "away_elo", "elo_diff"])
    absent = required - set(d.columns)
    if absent:
        raise ValueError(f"Missing dataset columns: {sorted(absent)}")

    d["date"] = pd.to_datetime(d.date, errors="coerce")
    played = d[d.status.eq("played")].copy()
    counts = played.groupby("season").size().sort_index()
    full_seasons = counts[counts.eq(380)].index
    core = played[played.season.isin(full_seasons)].copy()
    core["total_goals"] = core.home_goals + core.away_goals
    core["goal_diff"] = core.home_goals - core.away_goals
    recent = core[core.season.str[:4].astype(int).ge(2015)].copy()
    recent["points_diff"] = recent.home_points_avg5 - recent.away_points_avg5
    recent["season_ppg_diff"] = recent.home_season_ppg - recent.away_season_ppg
    recent["goals_form_diff"] = recent.home_goals_for_avg5 - recent.away_goals_for_avg5
    recent["xg_diff"] = recent.home_xg_avg5 - recent.away_xg_avg5
    recent["shots_on_target_diff"] = recent.home_shots_on_target_avg5 - recent.away_shots_on_target_avg5
    recent["rest_diff"] = recent.home_rest_days - recent.away_rest_days

    # 01. Source coverage and missingness by season.
    missing_cols = ["squad_value_log_ratio", "home_xg_avg5", "away_xg_avg5", "h2h_home_ppg",
                    "home_season_ppg", "away_season_ppg"]
    miss = d.groupby("season")[missing_cols].apply(lambda x: x.isna().mean())
    fig, ax = plt.subplots(figsize=(11, 8))
    im = ax.imshow(miss.to_numpy(), aspect="auto", cmap="YlOrRd", vmin=0, vmax=1)
    ax.set_yticks(np.arange(len(miss)), miss.index)
    ax.set_xticks(np.arange(len(missing_cols)), ["Giá trị đội hình", "xG nhà", "xG khách", "Đối đầu",
                                                   "Điểm/match nhà", "Điểm/match khách"], rotation=30, ha="right")
    ax.set_title("Tỷ lệ giá trị thiếu theo mùa")
    fig.colorbar(im, ax=ax, label="Tỷ lệ thiếu", shrink=0.75)
    save(fig, "01_missingness_by_season.png")

    # 02. Score and class distributions.
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    outcomes = outcome_table(core)
    axes[0].bar([NAMES[k] for k in outcomes.index], outcomes.values * 100,
                color=[COLORS[k] for k in outcomes.index])
    axes[0].set_ylabel("Tỷ lệ (%)")
    axes[0].set_title("Nhãn kết quả")
    for i, v in enumerate(outcomes.values):
        axes[0].text(i, v * 100 + 0.8, pct(v), ha="center")
    axes[0].set_ylim(0, max(outcomes.values) * 115)
    for col, title, color in [("home_goals", "Bàn thắng chủ nhà", COLORS["H"]),
                              ("away_goals", "Bàn thắng đội khách", COLORS["A"])]:
        values = core[col].clip(upper=6).value_counts(normalize=True).reindex(range(7), fill_value=0)
        ax = axes[1 if col == "home_goals" else 2]
        ax.bar(range(7), values.values * 100, color=color)
        ax.set_xticks(range(7), ["0", "1", "2", "3", "4", "5", "6+"])
        ax.set_ylabel("Tỷ lệ trận (%)")
        ax.set_title(title)
    fig.tight_layout()
    save(fig, "02_results_and_goals.png")

    # 03. Season-by-season results and scoring.
    season = core.groupby("season", sort=True).agg(matches=("result", "size"),
                                                     home_goals=("home_goals", "mean"),
                                                     away_goals=("away_goals", "mean"))
    for key in ["H", "D", "A"]:
        season[key] = core.groupby("season").result.apply(lambda x, key=key: x.eq(key).mean())
    xs = np.arange(len(season))
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for key in ["H", "D", "A"]:
        axes[0].plot(xs, season[key] * 100, marker="o", markersize=3, label=NAMES[key], color=COLORS[key])
    axes[0].set_ylabel("Tỷ lệ (%)")
    axes[0].set_title("Kết quả theo mùa đủ 380 trận")
    axes[0].legend(ncol=3)
    axes[1].plot(xs, season.home_goals + season.away_goals, marker="o", color="#6a51a3")
    axes[1].set_ylabel("Bàn thắng / trận")
    axes[1].set_title("Trung bình bàn thắng mỗi trận")
    axes[1].set_xticks(xs, season.index, rotation=60, ha="right")
    fig.tight_layout()
    save(fig, "03_season_trends.png")

    # 04. Team groups are based on final standings; descriptive EDA only.
    home = core.assign(team=core.home_team, points=core.result.map({"H": 3, "D": 1, "A": 0}),
                       gf=core.home_goals, ga=core.away_goals)[["season", "team", "points", "gf", "ga"]]
    away = core.assign(team=core.away_team, points=core.result.map({"H": 0, "D": 1, "A": 3}),
                       gf=core.away_goals, ga=core.home_goals)[["season", "team", "points", "gf", "ga"]]
    standings = pd.concat([home, away]).groupby(["season", "team"], as_index=False).agg(
        points=("points", "sum"), gf=("gf", "sum"), ga=("ga", "sum"))
    standings["gd"] = standings.gf - standings.ga
    standings = standings.sort_values(["season", "points", "gd", "gf"], ascending=[True, False, False, False])
    standings["rank"] = standings.groupby("season").cumcount() + 1
    standings["tier"] = pd.cut(standings["rank"], [0, 6, 14, 20], labels=["Top 6", "Giữa 8", "Cuối 6"])
    by_tier = core.merge(standings[["season", "team", "tier"]].rename(columns={"team": "home_team"}),
                         on=["season", "home_team"])
    tier_rates = by_tier.groupby("tier", observed=True).result.value_counts(normalize=True).unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    base = np.zeros(len(tier_rates))
    for key in ["H", "D", "A"]:
        v = tier_rates[key].to_numpy() * 100
        ax.bar(tier_rates.index.astype(str), v, bottom=base, color=COLORS[key], label=NAMES[key])
        base += v
    ax.set_ylim(0, 100)
    ax.set_ylabel("Tỷ lệ kết quả khi đá sân nhà (%)")
    ax.set_title("Kết quả theo nhóm đội ở các mùa đủ dữ liệu")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.14))
    fig.tight_layout()
    save(fig, "04_team_groups.png")

    # 05. Pre-match feature associations. Quantile groups can merge when many values tie.
    feature_specs = [
        ("elo_diff", "Chênh lệch Elo", "Cao"),
        ("season_ppg_diff", "Chênh lệch điểm/trận từ đầu mùa", "Cao"),
        ("points_diff", "Chênh lệch điểm 5 trận", "Cao"),
        ("goals_form_diff", "Chênh lệch bàn thắng TB 5 trận", "Trung bình"),
        ("xg_diff", "Chênh lệch xG TB 5 trận", "Cao (từ 2015)"),
        ("h2h_home_ppg", "Điểm đối đầu của chủ nhà", "Cần kiểm định"),
    ]
    association = {}
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.8), sharey=True)
    for ax, (col, title, _) in zip(axes.flat, feature_specs):
        rates, n_by_bin = feature_bins(recent, col)
        association[col] = {"n": int(n_by_bin.sum()), "bins": len(rates),
                            "home_win_low": float(rates.H.iloc[0]),
                            "home_win_high": float(rates.H.iloc[-1])}
        xs = np.arange(1, len(rates) + 1)
        for key in ["H", "D", "A"]:
            ax.plot(xs, rates[key] * 100, marker="o", label=NAMES[key], color=COLORS[key])
        ax.set_title(f"{title} (n={n_by_bin.sum():,})")
        ax.set_xticks(xs)
        ax.set_xlabel("Nhóm giá trị thấp → cao")
        ax.set_ylim(0, 85)
        ax.set_ylabel("Tỷ lệ kết quả (%)")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.015))
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    save(fig, "05_feature_associations.png")

    # 06. Scatter and binned trend for xG form versus the observed goal difference.
    xg = recent[["xg_diff", "goal_diff"]].dropna()
    sampled = xg.sample(min(2500, len(xg)), random_state=42)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].scatter(sampled.xg_diff, sampled.goal_diff, s=12, alpha=0.18, color="#1769aa")
    axes[0].axhline(0, color="#777777", lw=0.8)
    axes[0].axvline(0, color="#777777", lw=0.8)
    axes[0].set_xlabel("xG trung bình 5 trận: nhà − khách")
    axes[0].set_ylabel("Chênh lệch bàn thắng trận hiện tại")
    axes[0].set_title("Từng trận (mẫu tối đa 2.500 trận)")
    xg = xg.copy()
    xg["decile"] = pd.qcut(xg.xg_diff, 10, duplicates="drop")
    binned = xg.groupby("decile", observed=True).agg(x=("xg_diff", "mean"), y=("goal_diff", "mean"),
                                                       sd=("goal_diff", "std"), n=("goal_diff", "size"))
    ci = 1.96 * binned.sd / np.sqrt(binned.n)
    axes[1].errorbar(binned.x, binned.y, yerr=ci, marker="o", capsize=3, color="#e28a34")
    axes[1].axhline(0, color="#777777", lw=0.8)
    axes[1].axvline(0, color="#777777", lw=0.8)
    axes[1].set_xlabel("xG trung bình 5 trận: nhà − khách")
    axes[1].set_ylabel("Chênh lệch bàn thắng TB ± khoảng 95%")
    axes[1].set_title("Trung bình theo 10 nhóm xG")
    fig.tight_layout()
    save(fig, "06_xg_vs_goals.png")

    # 07. Extreme values: inspect, do not remove automatically.
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    goals_hist = core.total_goals.value_counts().sort_index()
    axes[0].bar(goals_hist.index, goals_hist.values, color="#1769aa")
    axes[0].axvline(core.total_goals.quantile(0.99), color="#c43c39", ls="--", label="Phân vị 99%")
    axes[0].set_xlabel("Tổng bàn thắng / trận")
    axes[0].set_ylabel("Số trận")
    axes[0].set_title("Đuôi phân phối bàn thắng")
    axes[0].legend()
    rest_home = core.home_rest_days.dropna()
    rest_away = core.away_rest_days.dropna()
    bins = np.arange(0.5, 31.5, 1)
    axes[1].hist(rest_home[rest_home.le(30)], bins=bins, alpha=0.65, color=COLORS["H"], label="Chủ nhà")
    axes[1].hist(rest_away[rest_away.le(30)], bins=bins, alpha=0.55, color=COLORS["A"], label="Đội khách")
    axes[1].set_xlim(1, 30)
    axes[1].set_xlabel("Ngày từ trận Premier League trước (≤30)")
    axes[1].set_ylabel("Số lượt đội")
    axes[1].set_title("Phân phối ngày nghỉ thông thường")
    axes[1].text(0.98, 0.96,
                 f">30 ngày: nhà {rest_home.gt(30).sum()}, khách {rest_away.gt(30).sum()}\n"
                 f">365 ngày: nhà {rest_home.gt(365).sum()}, khách {rest_away.gt(365).sum()}",
                 transform=axes[1].transAxes, va="top", ha="right", fontsize=9,
                 bbox={"facecolor": "white", "edgecolor": "#dddddd", "alpha": 0.9})
    axes[1].legend(loc="upper left")
    fig.tight_layout()
    save(fig, "07_outliers.png")

    # 08. Correlations among a readable selection of pre-match features.
    corr_cols = ["home_elo", "away_elo", "elo_diff", "home_points_avg5", "away_points_avg5",
                 "home_season_ppg", "away_season_ppg", "home_goals_for_avg5", "away_goals_for_avg5",
                 "home_xg_avg5", "away_xg_avg5", "h2h_home_ppg"]
    corr = recent[corr_cols].corr()
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(np.arange(len(corr_cols)), corr_cols, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(corr_cols)), corr_cols, fontsize=8)
    ax.set_title("Tương quan giữa các đặc trưng trước trận")
    fig.colorbar(im, ax=ax, label="Pearson r", shrink=0.75)
    fig.tight_layout()
    save(fig, "08_feature_correlations.png")

    # 09. PCA diagnostic only; never include targets or identifiers.
    predictor_cols = [c for c in d.columns if c not in set(IDENTITY + TARGET)]
    pca_cols = [c for c in predictor_cols if c != "squad_value_log_ratio"]
    complete = recent[pca_cols].dropna()
    matrix = complete.to_numpy(dtype=float)
    matrix = (matrix - matrix.mean(axis=0)) / matrix.std(axis=0, ddof=0)
    singular = np.linalg.svd(matrix, full_matrices=False, compute_uv=False)
    explained = singular ** 2 / np.square(singular).sum()
    cumulative = np.cumsum(explained)
    pca90 = int(np.searchsorted(cumulative, 0.90) + 1)
    pca95 = int(np.searchsorted(cumulative, 0.95) + 1)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    xs = np.arange(1, len(cumulative) + 1)
    ax.bar(xs, explained * 100, color="#a6bddb", label="Từng thành phần")
    ax2 = ax.twinx()
    ax2.plot(xs, cumulative * 100, marker="o", markersize=3, color="#1769aa", label="Tích lũy")
    ax2.axhline(95, color="#c43c39", ls="--", lw=1)
    ax2.axvline(pca95, color="#c43c39", ls="--", lw=1)
    ax.set_xlabel("Số thành phần chính")
    ax.set_ylabel("Phương sai từng thành phần (%)")
    ax2.set_ylabel("Phương sai tích lũy (%)")
    ax.set_xticks(xs)
    ax.set_title(f"PCA: cần {pca95}/{len(pca_cols)} thành phần để giữ 95% phương sai")
    fig.tight_layout()
    save(fig, "09_pca_diagnostic.png")

    # Candidate features are hypotheses to test with chronological validation.
    candidate_specs = [
        ("elo_diff", "Sức mạnh trước trận", "Cao", "Có sẵn; cân nhắc trùng thông tin với home_elo/away_elo"),
        ("season_ppg_diff", "Phong độ từ đầu mùa", "Cao", "Tính từ home_season_ppg - away_season_ppg"),
        ("points_diff", "Phong độ 5 trận", "Cao", "Tính từ home_points_avg5 - away_points_avg5"),
        ("xg_diff", "Chất lượng cơ hội 5 trận", "Cao", "Chỉ phù hợp các mùa có xG"),
        ("goals_form_diff", "Ghi bàn gần đây", "Trung bình", "Tính từ trung bình bàn thắng 5 trận"),
        ("h2h_home_ppg", "Đối đầu", "Cần kiểm định", "Có thể trùng tín hiệu với Elo và phong độ"),
        ("shots_on_target_diff", "Tạo cơ hội gần đây", "Cần kiểm định", "Tính từ sút trúng đích trung bình 5 trận"),
        ("rest_diff", "Lịch thi đấu", "Tạm hoãn", "Giá trị kéo dài qua mùa/vắng giải; cần tính lại ngày nghỉ thực tế"),
        ("squad_value_log_ratio", "Giá trị đội hình", "Tạm hoãn", "Thiếu nhiều và cần kiểm tra thời điểm định giá"),
    ]
    candidate_rows = []
    for col, meaning, priority, caveat in candidate_specs:
        coverage = recent[col].notna().mean() if col in recent else np.nan
        effect = association.get(col)
        candidate_rows.append({
            "feature": col,
            "meaning": meaning,
            "priority": priority,
            "coverage_recent_pct": round(100 * coverage, 1),
            "home_win_high_minus_low_pp": round(100 * (effect["home_win_high"] - effect["home_win_low"]), 1)
            if effect else np.nan,
            "note": caveat,
        })
    candidates = pd.DataFrame(candidate_rows)
    candidates.to_csv(OUT / "feature_candidates.csv", index=False, encoding="utf-8-sig")

    # Current-season cache reconciliation.
    current = played.season.max()
    current_fd = len(played[played.season.eq(current)])
    understat_path = ROOT / "data" / "understat_matches.csv"
    current_understat = None
    if understat_path.exists():
        understat = pd.read_csv(understat_path, usecols=["Season", "IsPlayed", "HomeTeam", "AwayTeam"])
        cur = understat[understat.Season.eq(current)].copy()
        cur["IsPlayed"] = cur.IsPlayed.astype(str).str.lower().isin(["true", "1"])
        current_understat = int(cur.IsPlayed.sum())

    duplicates = int(d.duplicated(KEY).sum())
    expected_result = np.where(played.home_goals > played.away_goals, "H",
                               np.where(played.home_goals < played.away_goals, "A", "D"))
    inconsistent = int((played.result.to_numpy() != expected_result).sum())
    missing_rows = [[c, f"{int(d[c].isna().sum()):,}", pct(d[c].isna().mean())]
                    for c in ["squad_value_log_ratio", "home_xg_avg5", "away_xg_avg5", "h2h_home_ppg"]]
    incomplete = [[s, f"{n:,}", f"{380 - n:,}"] for s, n in counts.items() if n < 380]
    ci = 1.96 * core.goal_diff.std(ddof=1) / np.sqrt(len(core))
    goals_extreme = core[core.total_goals.ge(7)].sort_values(["total_goals", "date"], ascending=[False, True])
    extreme_rows = [[r.season, r.date.strftime("%Y-%m-%d"), f"{r.home_team} {int(r.home_goals)}–{int(r.away_goals)} {r.away_team}"]
                    for r in goals_extreme.head(5).itertuples()]
    association_rows = [[title, f"{association[col]['n']:,}",
                         pct(association[col]["home_win_low"]), pct(association[col]["home_win_high"]),
                         f"{100 * (association[col]['home_win_high'] - association[col]['home_win_low']):+.1f} điểm %"]
                        for col, title, _ in feature_specs]
    candidate_md = [[r.feature, r.priority, f"{r.coverage_recent_pct:.1f}%",
                     "—" if pd.isna(r.home_win_high_minus_low_pp) else f"{r.home_win_high_minus_low_pp:+.1f} điểm %",
                     r.note] for r in candidates.itertuples()]
    tier_md = [[str(t), f"{tier_rates.loc[t, 'H'] * 100:.1f}%", f"{tier_rates.loc[t, 'D'] * 100:.1f}%",
                f"{tier_rates.loc[t, 'A'] * 100:.1f}%"] for t in tier_rates.index]

    report = f"""# Exploratory Analysis: Vũ Hoàng Dương – 202416175

## 1. Phạm vi và câu hỏi

Nguồn: `data/epl_dataset.csv`, mỗi dòng là một trận Premier League. File có **{len(d):,} dòng × {d.shape[1]} cột**: {len(played):,} trận `played` và {len(d) - len(played):,} trận `upcoming`. EDA kết quả chỉ dùng `played`. Các so sánh theo mùa dùng **{len(full_seasons)} mùa đủ 380 trận, {len(core):,} trận**; phân tích thuộc tính xG và phong độ dùng các mùa đủ dữ liệu từ 2015/16, {len(recent):,} trận.

Dataset **chỉ chứa Premier League** và không có cột `league`; chưa thể so sánh giữa các giải đấu. Cần dữ liệu của giải khác với cùng định nghĩa cột để làm phần đó. Nhóm đội ở mục 4 được xác định bằng thứ hạng cuối mùa, chỉ phục vụ mô tả, không dùng làm biến dự đoán trước trận.

## 2. Kiểm tra chất lượng dữ liệu

- Khóa trận (`season`, `date`, `home_team`, `away_team`) trùng: **{duplicates}**. Kết quả H/D/A sai so với tỉ số: **{inconsistent}**. Ngày không đọc được: **{int(d.date.isna().sum())}**.
- Các mùa chưa đủ 380 trận:

{md_table(incomplete, ['Mùa', 'Trận đã có', 'Thiếu so với 380'])}

- Giá trị thiếu cần xử lý theo mục tiêu phân tích:

{md_table(missing_rows, ['Cột', 'Số dòng thiếu', 'Tỷ lệ toàn file'])}

![Tỷ lệ giá trị thiếu theo mùa](figures/01_missingness_by_season.png)

xG hầu như không có trước 2014/15. Các chỉ số phong độ ở những trận đầu của đội/mùa có thể thiếu vì chưa đủ lịch sử; giữ dấu thiếu và giải thích, không điền 0 tự động. `squad_value_log_ratio` chủ yếu chỉ có ở mùa 2025/26 và một phần mùa 2026/27.

Mùa {current}: `epl_final.csv` có **{current_fd}** trận; cache Understat đánh dấu **{current_understat if current_understat is not None else 'không có dữ liệu'}** trận đã đá. Chênh lệch nguồn có thể làm các thuộc tính của vòng tiếp theo chậm cập nhật. Khi phân tích cả mùa, dùng tỷ lệ/trung bình mỗi trận và tách mùa đang diễn ra.

## 3. Bàn thắng, kết quả và lợi thế sân nhà

Trên {len(core):,} trận thuộc các mùa đủ dữ liệu, chủ nhà thắng **{pct(outcomes['H'])}**, hòa **{pct(outcomes['D'])}**, đội khách thắng **{pct(outcomes['A'])}**. Trung bình chủ nhà ghi **{core.home_goals.mean():.2f}** bàn/trận, đội khách ghi **{core.away_goals.mean():.2f}**. Chênh lệch bàn thắng trung bình chủ nhà trừ đội khách là **{core.goal_diff.mean():+.2f}** (khoảng 95% theo sai số chuẩn: {core.goal_diff.mean() - ci:+.2f} đến {core.goal_diff.mean() + ci:+.2f}). Đây là bằng chứng mô tả cho lợi thế sân nhà; chưa tách sức mạnh hai đội hoặc lịch thi đấu.

![Phân phối kết quả và bàn thắng](figures/02_results_and_goals.png)

![Xu hướng theo mùa](figures/03_season_trends.png)

Biểu đồ theo mùa dùng tỷ lệ H/D/A và bàn thắng mỗi trận để so sánh công bằng giữa các mùa. Tỷ lệ nhãn cao nhất là H ({pct(outcomes['H'])}); dùng mức này làm mốc phân lớp đơn giản khi đánh giá mô hình, đồng thời báo cáo cả log loss và chất lượng dự đoán từng lớp.

## 4. Khác biệt giữa mùa giải và nhóm đội bóng

Nhóm đội theo thứ hạng cuối mỗi mùa đủ dữ liệu: Top 6, Giữa 8, Cuối 6. Khi đá sân nhà:

{md_table(tier_md, ['Nhóm đội', 'Thắng', 'Hòa', 'Thua'])}

![Kết quả theo nhóm đội](figures/04_team_groups.png)

Nhóm này sử dụng kết quả cả mùa nên có **rò rỉ tương lai nếu đưa vào mô hình trước trận**. Đối với phân tích dự đoán, thay bằng Elo hoặc điểm/trận được tính từ các trận trước thời điểm dự đoán.

## 5. Phong độ, đối đầu và kết quả

Các biểu đồ dưới đây chia từng đặc trưng trước trận thành tối đa 5 nhóm giá trị tăng dần. Bảng ghi tỷ lệ chủ nhà thắng ở nhóm thấp nhất và cao nhất; đây là liên hệ mô tả, chưa chứng minh biến tạo thêm giá trị sau khi đã xét Elo và các biến khác.

{md_table(association_rows, ['Đặc trưng', 'Số trận hợp lệ', 'Nhóm thấp', 'Nhóm cao', 'Chênh lệch'])}

![Mối liên hệ đặc trưng với H/D/A](figures/05_feature_associations.png)

![xG phong độ và chênh lệch bàn thắng](figures/06_xg_vs_goals.png)

Các kiểm định ở bước mô hình cần so sánh: Elo đơn lẻ; Elo + phong độ; thêm xG; cuối cùng thêm đối đầu/ngày nghỉ. Chia train/test theo thời gian để đo phần thông tin bổ sung thật sự.

## 6. Ngoại lệ và mất cân bằng nhãn

Tổng bàn thắng phân vị 99 là **{core.total_goals.quantile(0.99):.0f}**; có **{len(goals_extreme):,}** trận đạt ít nhất 7 bàn, lớn nhất **{int(core.total_goals.max())}**. Năm ví dụ cao nhất:

{md_table(extreme_rows, ['Mùa', 'Ngày', 'Tỉ số'])}

![Ngoại lệ bàn thắng và ngày nghỉ](figures/07_outliers.png)

`home_rest_days` có **{int(rest_home.gt(30).sum())}** lượt đội trên 30 ngày (trong đó **{int(rest_home.gt(365).sum())}** trên 365 ngày; lớn nhất **{int(rest_home.max())}**). Với `away_rest_days`, các số tương ứng là **{int(rest_away.gt(30).sum())}**, **{int(rest_away.gt(365).sum())}** và **{int(rest_away.max())}**. Mã tạo thuộc tính lấy khoảng cách giữa hai trận Premier League liên tiếp của cùng đội, kể cả qua các mùa hoặc nhiều năm đội vắng giải. Vì vậy số này **không phản ánh ngày nghỉ thực tế** của đội; tạm chưa đưa `rest_diff` vào mô hình. Nếu cần dùng, hãy tính lại từ lịch thi đấu đầy đủ, hoặc định nghĩa lại khoảng nghỉ trong phạm vi cùng mùa và xử lý riêng trận đầu mùa.

Không xóa các trận nhiều bàn hoặc nghỉ lâu chỉ vì chúng nằm ngoài IQR. Đối chiếu nguồn, trận hoãn và lịch thi đấu trước khi quyết định. Nhãn H chiếm {pct(outcomes['H'])}, cao hơn D và A; khi huấn luyện nên xem confusion matrix, macro-F1 và log loss thay vì chỉ accuracy.

## 7. Tương quan và giảm chiều

![Tương quan đặc trưng](figures/08_feature_correlations.png)

File có **{len(predictor_cols)} cột đặc trưng** sau khi bỏ định danh, trạng thái và 3 nhãn. `elo_diff` được tính từ `home_elo - away_elo`; cần tránh giữ cả ba trong mô hình tuyến tính không điều chuẩn nếu gây đa cộng tuyến. Một số cặp khác có tương quan, nhưng câu hỏi chính là mỗi biến có bổ sung tín hiệu dự đoán ngoài Elo hay không.

Để kiểm tra PCA, dùng **{len(complete):,} trận đủ dữ liệu** trong giai đoạn 2015/16 trở đi, bỏ `squad_value_log_ratio`, chuẩn hóa **{len(pca_cols)} biến số trước trận** và không đưa nhãn vào. Hai thành phần đầu giữ **{pct(cumulative[1])}** phương sai; ba thành phần giữ **{pct(cumulative[2])}**; cần **{pca90}** thành phần cho 90% và **{pca95}** thành phần cho 95%. Vì vậy **chưa cần PCA cho EDA chính**. Có thể dùng PCA 2D để xem cấu trúc điểm dữ liệu, nhưng hình đó bỏ nhiều thông tin và không thay các biểu đồ theo biến gốc.

![Chẩn đoán PCA](figures/09_pca_diagnostic.png)

## 8. Đặc trưng tiềm năng và giả thuyết để kiểm tra

{md_table(candidate_md, ['Đặc trưng', 'Ưu tiên', 'Có dữ liệu gần đây', 'Chênh thắng nhà: nhóm cao − thấp', 'Ghi chú'])}

Danh sách dạng CSV: [feature_candidates.csv](feature_candidates.csv). Mức ưu tiên dựa trên EDA, chưa phải kết quả đánh giá mô hình.

1. **Elo:** Elo chênh lệch lớn hơn đi cùng xác suất chủ nhà thắng cao hơn; kiểm tra bằng mô hình H/D/A với chia tập theo mùa.
2. **Phong độ:** điểm/trận, điểm 5 trận và xG 5 trận có thể thêm thông tin ngoài Elo; so sánh mô hình lồng nhau trên cùng tập thời gian.
3. **Đối đầu:** `h2h_home_ppg` có liên hệ đơn biến với kết quả, nhưng có thể trùng tín hiệu sức mạnh đội; kiểm tra mức cải thiện ngoài Elo và phong độ.
4. **Lợi thế sân nhà:** ảnh hưởng có thể thay đổi theo mùa và nhóm đội; thử tương tác giữa Elo chênh lệch và sân nhà.
5. **Nghỉ giữa trận:** số ngày nghỉ có thể phản ánh lịch đấu, nhưng dữ liệu chỉ tính Premier League; kiểm tra sau khi kiểm soát Elo và mùa.
6. **Giá trị đội hình:** hiện thiếu nhiều. Mã thu thập chọn giá trị gần cuối mùa; phải kiểm tra mốc thời gian định giá trước khi dùng để dự đoán trước trận.

## 9. Giới hạn và cách cập nhật

- Nguồn hiện chỉ là Premier League; so sánh các giải đấu cần dữ liệu bổ sung.
- Hai mùa thiếu trận và mùa đang diễn ra phải được tách khi so sánh tổng số hoặc thứ hạng.
- Thiếu dữ liệu xG và giá trị đội hình cần xử lý theo mục đích. Việc điền thiếu cho mô hình phải học từ tập train, rồi áp dụng cho test theo thời gian.
- Báo cáo và hình có thể tạo lại sau lần cập nhật CSV bằng `python reports/eda_epl/run_eda.py`.
"""
    (OUT / "report.md").write_text(report, encoding="utf-8")
    print(f"Wrote {OUT / 'report.md'}; {len(list(FIG.glob('*.png')))} figures; {OUT / 'feature_candidates.csv'}")


if __name__ == "__main__":
    main()
