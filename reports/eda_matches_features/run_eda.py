"""EDA of the downloaded clean matches/features snapshot; never edits input data.

Run: python reports/eda_matches_features/run_eda.py
Outputs are local to this directory. No network access or model training.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd
from scipy.stats import poisson

from audit import audit_inputs
from feature_catalog import build_feature_catalog

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FIG = OUT / "figures"
LABELS = ["H", "D", "A"]
COLORS = {"H": "#176b9b", "D": "#84929e", "A": "#db863e"}
NAMES = {"H": "Chủ nhà thắng", "D": "Hòa", "A": "Đội khách thắng"}
ID = ["match_id", "season", "date", "kickoff_time", "gameweek", "home_club_id",
      "home_team", "away_club_id", "away_team", "status", "provisional", "postponed"]
TARGET = ["home_goals", "away_goals", "result"]
FEATURE_NAMES = {
    "elo_diff": "Chênh Elo",
    "diff_points5": "Chênh điểm TB 5 trận",
    "diff_goals_for5": "Chênh bàn ghi TB 5 trận",
    "diff_goals_against5": "Chênh bàn thua TB 5 trận",
    "diff_xg5": "Chênh xG TB 5 trận",
    "diff_xga5": "Chênh xGA TB 5 trận",
    "diff_ppg": "Chênh điểm/trận trong mùa",
    "diff_shots_on_target5": "Chênh sút trúng đích TB",
    "h2h_home_points": "Điểm TB đối đầu của đội nhà",
    "h2h_home_goal_diff": "Hiệu số đối đầu của đội nhà",
    "odds_home_prob": "Xác suất H suy từ odds",
    "position_diff": "Chênh vị trí trước trận",
    "diff_possession5": "Chênh kiểm soát bóng TB",
    "diff_perf_vs_elo5": "Chênh kết quả vượt kỳ vọng Elo",
}
plt.rcParams.update({
    "font.family": "Segoe UI", "font.size": 10, "axes.titlesize": 12,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.dpi": 160,
})


def nfmt(value):
    return f"{int(value):,}".replace(",", ".")


def vfmt(value, digits=2):
    if pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}".replace(".", ",")


def pct(value, digits=1):
    return vfmt(100 * value, digits) + "%"


def wilson(k, n):
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    z = 1.95996398454
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return center - half, center + half


def save(fig, name):
    fig.savefig(FIG / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)


class Report:
    """Author the same report in Markdown and standalone HTML without a parser."""
    def __init__(self):
        self.md, self.html, self.toc = [], [], []
        self.counter = 0

    def section(self, title, level=2):
        self.counter += 1
        ident = "section-" + str(self.counter)
        if level == 2:
            self.toc.append((ident, title))
        self.md.append("#" * level + " " + title + "\n")
        self.html.append(f'<h{level} id="{ident}">{html.escape(title)}</h{level}>')

    def p(self, text):
        self.md.append(text + "\n")
        self.html.append("<p>" + html.escape(text) + "</p>")

    def bullets(self, lines):
        self.md.append("\n".join("- " + line for line in lines) + "\n")
        self.html.append("<ul>" + "".join("<li>" + html.escape(x) + "</li>" for x in lines) + "</ul>")

    def table(self, frame):
        frame = frame.apply(lambda s: s.astype(object).map(lambda value: "—" if pd.isna(value) else str(value)))
        esc = lambda x: x.replace("|", "/").replace("\n", " ")
        self.md.append("| " + " | ".join(map(esc, frame.columns)) + " |")
        self.md.append("|" + "|".join(["---"] * len(frame.columns)) + "|")
        for row in frame.itertuples(index=False, name=None):
            self.md.append("| " + " | ".join(esc(x) for x in row) + " |")
        self.md.append("")
        self.html.append('<div class="table-wrap">' + frame.to_html(index=False, escape=True, border=0) + "</div>")

    def image(self, name, caption):
        self.md.append(f"![{caption}](figures/{name})\n\n{caption}\n")
        encoded = base64.b64encode((FIG / name).read_bytes()).decode()
        self.html.append(f'<figure><img src="data:image/png;base64,{encoded}" alt="{html.escape(caption)}">'
                         f'<figcaption>{html.escape(caption)}</figcaption></figure>')

    def link(self, label, target):
        self.md.append(f"[{label}]({target})\n")
        self.html.append(f'<p><a href="{html.escape(target)}">{html.escape(label)}</a></p>')

    def code(self, value):
        self.md.append("    " + value + "\n")
        self.html.append("<pre>" + html.escape(value) + "</pre>")

    def write(self):
        (OUT / "report.md").write_text("\n".join(self.md), encoding="utf-8")
        toc = "".join(f'<a href="#{ident}">{html.escape(title)}</a>' for ident, title in self.toc)
        css = """
        :root{--ink:#19384a;--muted:#506575;--accent:#176b9b}
        *{box-sizing:border-box}body{margin:0;background:#edf2f5;color:#233746;
        font:16px/1.65 "Segoe UI",Arial,sans-serif}main{max-width:1160px;margin:30px auto;
        background:white;padding:42px 50px;border-radius:12px;box-shadow:0 6px 28px #18374714}
        h1{font-size:34px;line-height:1.25;color:var(--ink);margin-top:0}
        h2{font-size:25px;margin-top:44px;border-top:1px solid #dbe5ec;padding-top:23px;color:var(--ink)}
        h3{font-size:20px;color:var(--ink)}p,li{max-width:100ch}li{margin-bottom:7px}
        nav{padding:18px 23px;background:#f0f6fa;border-left:4px solid var(--accent)}
        nav a{display:block;margin:5px 0;break-inside:avoid}.nav-links{columns:2;column-gap:30px}a{color:var(--accent)}
        figure{margin:24px 0 36px}img{width:100%;height:auto;display:block}
        figcaption{font-size:14px;color:var(--muted);margin-top:8px}
        .table-wrap{overflow-x:auto;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px}
        th{background:#eaf3f8;color:var(--ink);text-align:left}
        td,th{padding:9px 11px;border-bottom:1px solid #dce5ea;vertical-align:top}
        tr:nth-child(even) td{background:#f8fafb}pre{background:#f1f5f8;padding:14px;overflow:auto}
        @media(max-width:760px){main{margin:0;padding:24px 18px;border-radius:0}h1{font-size:27px}.nav-links{columns:1}}
        @media print{body{background:white}main{box-shadow:none;margin:0;padding:0}nav{display:none}
        figure{break-inside:avoid}h2{break-after:avoid}}
        """
        document = '<!doctype html><html lang="vi"><head><meta charset="utf-8">' \
                   '<meta name="viewport" content="width=device-width, initial-scale=1">' \
                   '<title>EDA Premier League — matches và features</title><style>' + css + \
                   '</style></head><body><main>' + "\n".join(self.html[:3]) + \
                   '<nav><strong>Mục lục</strong><div class="nav-links">' + toc + \
                   "</div></nav>" + "\n".join(self.html[3:]) + "</main></body></html>"
        (OUT / "report.html").write_text(document, encoding="utf-8")
        write_index(self.toc, self.md[2].strip())


def write_index(toc, summary):
    lines=[
        "# Mục lục EDA — matches.csv và features.csv", "",
        "Đây là bộ EDA hiện tại. Nguồn đầu vào là clean/matches.csv và clean/features.csv ở gốc dự án. "
        "Các thư mục reports/eda_epl và mã EDA trong đó là tài liệu của lần phân tích trước.", "",
        summary, "",
        "[Mở báo cáo HTML có đầy đủ biểu đồ](report.html) · [Báo cáo Markdown](report.md)", "",
        "| Phần phân tích | Liên kết |", "|---|---|",
    ]
    lines += [f"| {title} | [Xem trong báo cáo](report.html#{ident}) |" for ident,title in toc]
    lines += [
        "", "## Sản phẩm bàn giao", "",
        "- [Bộ 16 biểu đồ PNG](figures/)",
        "- [Danh mục 87 đặc trưng tiềm năng](feature_candidates.csv)",
        "- [9 giả thuyết mô hình](hypotheses.csv)",
        "- [Kết quả kiểm tra chất lượng](data_quality_checks.csv)",
        "- [Các dòng cần xác minh](data_issues.csv)",
        "- [Metadata và SHA256 nguồn](analysis_metadata.json)",
        "- [Toàn bộ bộ bàn giao ZIP](../EDA_matches_features.zip)", "",
        "## Chạy lại EDA", "",
        "Từ gốc dự án:", "", "    python reports/eda_matches_features/run_eda.py", "",
        "Lệnh này chỉ đọc hai CSV trong clean/; không cần thư mục data/.",
        "Phạm vi hiện chỉ có Premier League, nên chưa có so sánh giữa các giải đấu.", "",
    ]
    (OUT/"00_muc_luc.md").write_text("\n".join(lines),encoding="utf-8")


def team_rows(matches, features):
    elo = features.set_index("match_id")
    parts = []
    for side, other, venue in [("home", "away", "Nhà"), ("away", "home", "Khách")]:
        part = pd.DataFrame({
            "match_id": matches.match_id, "season": matches.season, "date": matches.date,
            "club_id": matches[f"{side}_club_id"], "team": matches[f"{side}_team"],
            "venue": venue, "gf": matches[f"{side}_goals"], "ga": matches[f"{other}_goals"],
            "elo": matches.match_id.map(elo[f"{side}_elo"]),
        })
        part["pts"] = np.select([part.gf.gt(part.ga), part.gf.eq(part.ga)], [3, 1], default=0)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def associations(frame, columns):
    rows = []
    for col in columns:
        valid = frame[[col, "result", "goal_diff"]].dropna()
        rows.append({
            "feature": col, "n": len(valid),
            "rho_H": valid[col].corr(valid.result.eq("H").astype(int), method="spearman"),
            "rho_GD": valid[col].corr(valid.goal_diff, method="spearman"),
        })
    return pd.DataFrame(rows).set_index("feature")


def binned(frame, col, q=5):
    data = frame[[col, "result"]].dropna().copy()
    data["bin"] = pd.qcut(data[col], q, duplicates="drop")
    counts = pd.crosstab(data["bin"], data.result).reindex(columns=LABELS, fill_value=0)
    counts["n"] = counts.sum(axis=1)
    return counts


def make_figures(m, f, seasons, core, teams, common_cols, overall, common, issues):
    # 01: coverage and source availability, including incomplete seasons.
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), layout="constrained")
    x = np.arange(len(seasons))
    axes[0].bar(x, seasons.matches, color=np.where(seasons.complete, "#176b9b", "#db863e"))
    axes[0].axhline(380, color="#536571", linestyle="--", linewidth=1)
    for i, row in enumerate(seasons.itertuples()):
        if not row.complete:
            axes[0].text(i, row.matches + 8, str(row.matches), ha="center", fontsize=10)
    axes[0].set(title="Số trận theo mùa; cam = mùa chưa đủ cấu trúc 380 trận", ylabel="Số trận")
    axes[0].set_xticks(x, seasons.index, rotation=60, ha="right")
    source_cols = {"home_xg": "xG Understat", "home_fm_xg": "xG FotMob",
                   "home_possession": "Kiểm soát bóng", "home_touches_opp_box": "Chạm bóng trong vòng cấm"}
    coverage = m.groupby("season")[list(source_cols)].agg(lambda s: s.notna().mean())
    for col, label in source_cols.items():
        axes[1].plot(x, coverage[col], marker="o", markersize=3, label=label)
    axes[1].set_xticks(x, coverage.index, rotation=60, ha="right")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].set(title="Độ phủ của chỉ số, không phải tỷ lệ ghép ID nguồn", ylabel="Tỷ lệ không thiếu", ylim=(-.03, 1.08))
    axes[1].legend(ncols=2, loc="upper left")
    fig.suptitle(f"01 · Phạm vi dữ liệu | {nfmt(len(m))} trận, {len(seasons)} mùa", fontsize=16)
    save(fig, "01_coverage.png")

    # 02: missingness is structural in time; no blanket imputation.
    cols = ["home_elo", "home_points5", "home_ppg", "home_position", "h2h_home_points",
            "home_xg5", "home_possession5", "home_pass_accuracy5", "home_big_chances5",
            "home_box_touches5", "home_xgot5", "odds_home_prob", "gameweek"]
    miss = f.groupby("season")[cols].agg(lambda s: s.isna().mean())
    fig, ax = plt.subplots(figsize=(13, 10), layout="constrained")
    image = ax.imshow(miss.to_numpy(), vmin=0, vmax=1, cmap="YlOrRd", aspect="auto")
    ax.set_yticks(range(len(miss)), miss.index)
    ax.set_xticks(range(len(cols)), cols, rotation=60, ha="right")
    fig.colorbar(image, ax=ax, format=PercentFormatter(1), label="Tỷ lệ thiếu")
    ax.set_title("02 · Thiếu dữ liệu theo mùa | toàn bộ 9.850 trận")
    save(fig, "02_missingness.png")

    # 03: all-file labels, plus complete-season scoring distributions.
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), layout="constrained")
    counts = m.result.value_counts().reindex(LABELS)
    bars = axes[0].bar(LABELS, counts / len(m), color=[COLORS[k] for k in LABELS])
    for bar, k in zip(bars, LABELS):
        axes[0].text(bar.get_x()+bar.get_width()/2, bar.get_height()+.012,
                     f"{pct(counts[k]/len(m))}\nn={nfmt(counts[k])}", ha="center", fontsize=10)
    axes[0].set(ylim=(0, .57), title=f"H/D/A · toàn bộ n={nfmt(len(m))}")
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    for side, label, color in [("home", "Nhà", COLORS["H"]), ("away", "Khách", COLORS["A"])]:
        bucket = core[f"{side}_goals"].clip(upper=6)
        dist = bucket.value_counts(normalize=True).reindex(range(7), fill_value=0)
        axes[1].plot(range(7), dist, marker="o", label=label, color=color)
    axes[1].set_xticks(range(7), ["0","1","2","3","4","5","6+"])
    axes[1].set(title="Bàn ghi mỗi đội theo sân", xlabel="Bàn thắng", ylabel="Tỷ lệ đội–trận")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].legend()
    ts = core.home_goals + core.away_goals
    dist = ts.clip(upper=7).value_counts(normalize=True).reindex(range(8), fill_value=0)
    axes[2].bar(range(8), dist, color="#37858b")
    axes[2].set_xticks(range(8), ["0","1","2","3","4","5","6","7+"])
    axes[2].set(title="Tổng bàn mỗi trận", xlabel="Tổng bàn")
    axes[2].yaxis.set_major_formatter(PercentFormatter(1))
    fig.suptitle(f"03 · Kết quả và bàn thắng | hai đồ thị bàn thắng dùng {nfmt(len(core))} trận mùa đủ", fontsize=15)
    save(fig, "03_outcomes_goals.png")

    # 04: exploratory count diagnostics, not a fitted forecasting model.
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained")
    score = pd.crosstab(core.home_goals.clip(upper=6), core.away_goals.clip(upper=6))
    score = score.reindex(index=range(7), columns=range(7), fill_value=0)
    im = axes[0].imshow(score, cmap="Blues", origin="lower")
    for i in range(7):
        for j in range(7):
            if score.iloc[i,j]:
                axes[0].text(j, i, nfmt(score.iloc[i,j]), ha="center", va="center",
                             color="white" if score.iloc[i,j] > score.to_numpy().max()*.55 else "#19384a", fontsize=8)
    axes[0].set_xticks(range(7), ["0","1","2","3","4","5","6+"])
    axes[0].set_yticks(range(7), ["0","1","2","3","4","5","6+"])
    axes[0].set(xlabel="Bàn khách", ylabel="Bàn nhà", title="Tần suất tỷ số")
    fig.colorbar(im, ax=axes[0], shrink=.7, label="Số trận")
    observed = ts.clip(upper=7).value_counts(normalize=True).reindex(range(8), fill_value=0)
    expected = np.r_[poisson.pmf(range(7), ts.mean()), poisson.sf(6, ts.mean())]
    axes[1].bar(np.arange(8)-.18, observed, .36, label="Quan sát", color="#37858b")
    axes[1].bar(np.arange(8)+.18, expected, .36, label="Poisson có cùng trung bình", color="#c7b58a")
    axes[1].set_xticks(range(8), ["0","1","2","3","4","5","6","7+"])
    axes[1].set(xlabel="Tổng bàn", ylabel="Tỷ lệ trận", title="Đối chiếu phân phối đếm (mô tả)")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].legend(fontsize=9)
    fig.suptitle(f"04 · Tỷ số và giả định phân phối | {nfmt(len(core))} trận mùa đủ", fontsize=15)
    save(fig, "04_score_counts.png")

    # 05: season comparisons on complete seasons only.
    trend = core.groupby("season").agg(home=("home_goals","mean"), away=("away_goals","mean"))
    rates = pd.crosstab(core.season, core.result, normalize="index").reindex(columns=LABELS)
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), layout="constrained")
    for col, label, color in [("home","Bàn nhà",COLORS["H"]),("away","Bàn khách",COLORS["A"])]:
        axes[0].plot(trend.index, trend[col], marker="o", label=label, color=color)
    axes[0].set(title="Bàn thắng trung bình", ylabel="Bàn / trận")
    axes[0].legend()
    for k in LABELS:
        axes[1].plot(rates.index, rates[k], marker="o", label=NAMES[k], color=COLORS[k])
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].set(title="Tỷ lệ kết quả", ylabel="Tỷ lệ")
    axes[1].legend(ncols=3)
    for ax in axes:
        ax.tick_params(axis="x", rotation=60)
    fig.suptitle(f"05 · Xu hướng theo mùa | {len(trend)} mùa đủ, mỗi mùa 380 trận", fontsize=15)
    save(fig, "05_season_trends.png")

    # 06: home advantage and crowd-period association, no causal interpretation.
    advantage = core.assign(adv=core.result.map({"H":1, "D":0, "A":-1})).groupby("season").adv.mean()
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), layout="constrained")
    axes[0].bar(advantage.index, advantage, color=["#db863e" if s in ["2019/20","2020/21"] else "#176b9b" for s in advantage.index])
    axes[0].axhline(0, color="black", linewidth=.7)
    axes[0].tick_params(axis="x", rotation=70)
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].set(title="Tỷ lệ H trừ tỷ lệ A theo mùa", ylabel="Chênh lệch điểm phần trăm")
    covid = core[core.season.isin(["2019/20","2020/21"]) & core.attendance.notna()].copy()
    covid["crowd"] = np.where(covid.attendance.eq(0), "Khán giả = 0 (được điền)", "Khán giả > 0")
    cg = covid.groupby(["season","crowd"]).result.agg(n="size", advantage=lambda s:(s.eq("H").sum()-s.eq("A").sum())/len(s))
    categories = list(cg.index.get_level_values("crowd").unique())
    for i, cat in enumerate(categories):
        part = cg.xs(cat, level="crowd").reindex(["2019/20","2020/21"])
        bars = axes[1].bar(np.arange(2)+(i-.5)*.36, part.advantage, .36, label=cat)
        for bar, count in zip(bars, part.n):
            axes[1].text(bar.get_x()+bar.get_width()/2, bar.get_height()+.012, f"n={int(count)}", ha="center", fontsize=9)
    axes[1].set_xticks(range(2), ["2019/20","2020/21"])
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].set(title="So sánh mô tả trong hai mùa COVID", ylabel="Tỷ lệ H trừ A")
    axes[1].legend(fontsize=8, loc="upper left")
    fig.suptitle("06 · Lợi thế sân nhà thay đổi theo mùa và giai đoạn", fontsize=15)
    save(fig, "06_home_advantage.png")

    # 07: strength tiers use pre-match Elo, not end-of-season rank.
    t = teams.copy()
    t["strength"] = pd.qcut(t.elo, 3, labels=["Elo thấp","Elo giữa","Elo cao"])
    group = t.groupby(["strength","venue"], observed=True).agg(n=("pts","size"), pts=("pts","mean"), gf=("gf","mean"), ga=("ga","mean"))
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), layout="constrained")
    for i, venue in enumerate(["Nhà","Khách"]):
        part = group.xs(venue, level="venue")
        axes[0].bar(np.arange(3)+(i-.5)*.36, part.pts, .36, label=venue, color=[COLORS["H"],COLORS["A"]][i])
        axes[1].plot(range(3), part.gf, "o-", label=f"Ghi bàn · {venue}")
        axes[1].plot(range(3), part.ga, "x--", label=f"Thủng lưới · {venue}")
    for ax in axes[:2]:
        ax.set_xticks(range(3), ["Elo thấp","Elo giữa","Elo cao"])
        ax.legend(fontsize=8)
    axes[0].set(title="Điểm trung bình", ylabel="Điểm / đội–trận")
    axes[1].set(title="Bàn ghi và bàn thua", ylabel="Bàn / đội–trận")
    prior = {s:set(g.club_id) for s,g in team_rows(m, f).groupby("season")}
    t["new"] = [cid not in prior.get(f"{int(s[:4])-1}/{str(int(s[:4]))[-2:]}", set())
                if int(s[:4]) > int(m.season.min()[:4]) else np.nan for s,cid in zip(t.season,t.club_id)]
    ng = t.dropna(subset=["new"]).groupby("new").pts.agg(["mean","size"]).reindex([False,True])
    bars = axes[2].bar([0,1], ng["mean"], color=["#37858b","#b69562"])
    axes[2].set_xticks([0,1], ["Có ở mùa trước","Không có ở mùa trước"])
    axes[2].tick_params(axis="x", rotation=12)
    axes[2].set(title="Lịch sử tham dự EPL", ylabel="Điểm / đội–trận")
    for bar, count in zip(bars, ng["size"]):
        axes[2].text(bar.get_x()+bar.get_width()/2, bar.get_height()+.025, f"n={nfmt(count)}", ha="center", fontsize=9)
    fig.suptitle(f"07 · Nhóm đội | {nfmt(len(t))} đội–trận thuộc 24 mùa đủ", fontsize=15)
    save(fig, "07_team_groups.png")

    # 08: unadjusted associations with actual GF/GA rolling averages explicitly included.
    acols = ["elo_diff","diff_points5","diff_goals_for5","diff_goals_against5","h2h_home_points","diff_xg5"]
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), layout="constrained")
    for ax, col in zip(axes.flat, acols):
        bins = binned(f, col)
        bottom = np.zeros(len(bins))
        for k in LABELS:
            values = bins[k] / bins.n
            ax.bar(range(len(bins)), values, bottom=bottom, color=COLORS[k], label=NAMES[k])
            bottom += values
        ax.set_xticks(range(len(bins)), [f"Q{i+1}\nn={nfmt(v)}" for i,v in enumerate(bins.n)])
        ax.set(title=f"{FEATURE_NAMES[col]} · n={nfmt(bins.n.sum())}", ylim=(0,1.05))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        for i, row in enumerate(bins.itertuples()):
            ax.text(i, (row.H/row.n)/2, pct(row.H/row.n), ha="center", color="white", fontsize=9)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncols=3)
    fig.suptitle("08 · Tỷ lệ H/D/A theo phân vị đặc trưng; Q1 thấp → Q5 cao", fontsize=15)
    save(fig, "08_feature_associations.png")

    # 09: partial strength control; no causal or incremental-prediction claim.
    conditional = []
    for col in ["diff_points5","diff_goals_for5","h2h_home_points","diff_xg5"]:
        low, high = f[col].quantile([1/3,2/3])
        near = f[f.elo_diff.abs().le(50) & f[col].notna()]
        for name, mask in [("Thấp", near[col].le(low)), ("Cao", near[col].ge(high))]:
            part = near[mask]
            k, count = part.result.eq("H").sum(), len(part)
            lo, hi = wilson(k, count)
            conditional.append(dict(feature=col, group=name, n=count, rate=k/count, lo=float(lo), hi=float(hi),
                                    mean_elo=part.elo_diff.mean(), low=low, high=high))
    cond = pd.DataFrame(conditional)
    fig, axes = plt.subplots(1, 4, figsize=(15, 5), layout="constrained")
    for ax, col in zip(axes, cond.feature.unique()):
        part = cond[cond.feature.eq(col)]
        ax.bar([0,1], part.rate, color=["#95afb9","#176b9b"])
        ax.errorbar([0,1], part.rate, yerr=np.vstack([part.rate-part.lo, part.hi-part.rate]), fmt="none", color="#273f50", capsize=4)
        for i, row in enumerate(part.itertuples()):
            ax.text(i, row.hi+.02, f"{pct(row.rate)}\nn={nfmt(row.n)}", ha="center", fontsize=9)
        ax.set_xticks([0,1], ["Nhóm thấp","Nhóm cao"])
        ax.set(title=FEATURE_NAMES[col].replace("Chênh ",""), ylim=(0,.67))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
    fig.suptitle("09 · Chỉ xét |chênh Elo| ≤ 50; thanh sai số Wilson 95% mang tính mô tả", fontsize=14)
    save(fig, "09_strength_conditioned.png")

    # 10: compare features on identical rows as well as on available rows.
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    y = np.arange(len(common_cols))
    ax.barh(y-.16, overall.loc[common_cols,"rho_H"], .30, label="Mỗi biến dùng các trận có dữ liệu", color="#93b3c4")
    ax.barh(y+.16, common.loc[common_cols,"rho_H"], .30, label=f"Cùng {nfmt(common.n.iloc[0])} trận", color="#176b9b")
    ax.set_yticks(y, [FEATURE_NAMES[c] for c in common_cols])
    ax.set(xlabel="Spearman với H=1, D/A=0", title="10 · Liên hệ đơn biến; không phải tầm quan trọng dự báo")
    ax.legend(fontsize=9)
    ax.invert_yaxis()
    save(fig, "10_common_sample.png")

    # 11: deterministic and empirical redundancy.
    corr_cols = ["home_elo","away_elo","elo_diff","elo_home_win_prob","diff_ppg","position_diff",
                 "diff_points5","diff_goals_for5","diff_xg5","h2h_home_points","h2h_home_goal_diff","odds_home_prob"]
    corr = f[corr_cols].corr(method="spearman")
    fig, ax = plt.subplots(figsize=(12, 10), layout="constrained")
    im = ax.imshow(corr, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(corr)), corr_cols, rotation=65, ha="right", fontsize=9)
    ax.set_yticks(range(len(corr)), corr_cols, fontsize=9)
    for i in range(len(corr)):
        for j in range(len(corr)):
            value = corr.iloc[i,j]
            ax.text(j,i,f"{value:.2f}",ha="center",va="center",fontsize=7,color="white" if abs(value)>.7 else "#243d4d")
    ax.set_title("11 · Tương quan Spearman giữa các biến; từng cặp dùng mẫu hợp lệ riêng")
    fig.colorbar(im, ax=ax, shrink=.75)
    save(fig, "11_redundancy.png")

    # 12: compare both xG definitions on the very same team-match rows.
    xgparts = [m[[f"{s}_xg",f"{s}_fm_xg"]].set_axis(["understat","fotmob"],axis=1) for s in ["home","away"]]
    xg = pd.concat(xgparts, ignore_index=True).dropna()
    diff = xg.fotmob-xg.understat
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), layout="constrained")
    hb = axes[0].hexbin(xg.understat, xg.fotmob, gridsize=40, mincnt=1, cmap="Blues", bins="log")
    lim = max(xg.max())+.1
    axes[0].plot([0,lim],[0,lim],"--",color="#db863e")
    axes[0].set(xlabel="xG Understat",ylabel="xG FotMob",title=f"Cùng {nfmt(len(xg))} đội–trận")
    fig.colorbar(hb, ax=axes[0], label="Mật độ (log)")
    axes[1].hexbin((xg.fotmob+xg.understat)/2, diff, gridsize=40, mincnt=1, cmap="Blues", bins="log")
    for value, label in [(diff.mean(),"Chênh trung bình"),(diff.mean()+1.96*diff.std(),"TB + 1,96 SD"),(diff.mean()-1.96*diff.std(),"TB − 1,96 SD")]:
        axes[1].axhline(value,linestyle="--",label=f"{label}: {vfmt(value)}")
    axes[1].set(xlabel="Trung bình hai xG",ylabel="FotMob − Understat",title="Độ đồng thuận, không mặc định cùng định nghĩa")
    axes[1].legend(fontsize=8)
    fig.suptitle("12 · Kiểm tra tính nhất quán giữa hai nguồn xG",fontsize=15)
    save(fig, "12_xg_sources.png")

    # 13: semantic and cross-source issues remain in the input; no automatic cleaning.
    gaps = pd.concat([f.home_rest_days,f.away_rest_days]).dropna()
    shotparts=[]
    for side in ["home","away"]:
        ss=m[[f"{side}_shots",f"{side}_shots_inside_box",f"{side}_shots_outside_box"]].dropna()
        shotparts.append(pd.DataFrame({"total":ss.iloc[:,0],"parts":ss.iloc[:,1]+ss.iloc[:,2]}))
    shots=pd.concat(shotparts,ignore_index=True)
    fig, axes=plt.subplots(1,3,figsize=(15,5),layout="constrained")
    axes[0].hist(gaps[gaps.le(60)],bins=range(0,63,3),color="#176b9b")
    axes[0].set(xlabel="Ngày từ trận EPL trước",ylabel="Đội–trận",title=f"Khoảng cách ≤60 ngày · n={nfmt(gaps.le(60).sum())}")
    axes[1].hist(np.log10(gaps),bins=30,color="#db863e")
    axes[1].set(xlabel="log10(số ngày)",title=f"Toàn bộ khoảng cách · max={nfmt(gaps.max())} ngày")
    axes[2].scatter(shots.total,shots.parts,s=8,alpha=.2,color="#176b9b")
    bad=shots.parts.gt(shots.total)
    axes[2].scatter(shots.loc[bad,"total"],shots.loc[bad,"parts"],s=14,color="#bd4b43",alpha=.6,label=f"Vượt tổng: {nfmt(bad.sum())} đội–trận")
    zero=shots.parts.eq(0)&shots.total.gt(0)
    axes[2].scatter(shots.loc[zero,"total"],shots.loc[zero,"parts"],s=26,color="#b47716",
                    label=f"Thành phần = 0, tổng > 0: {nfmt(zero.sum())}")
    cap=max(shots.max())
    axes[2].plot([0,cap],[0,cap],"--",color="#536571")
    axes[2].set(xlabel="Tổng sút (Football-Data)",ylabel="Trong + ngoài vòng cấm (FotMob)",title="Thống kê khác nguồn")
    axes[2].legend(fontsize=8)
    fig.suptitle("13 · Bất thường cần diễn giải hoặc xác minh",fontsize=15)
    save(fig,"13_anomalies.png")

    # 14: probabilities from odds, with matched labels. Elo expectation is intentionally excluded.
    odds=f[["odds_home_prob","odds_draw_prob","odds_away_prob","result"]].dropna().copy()
    fig,axes=plt.subplots(1,3,figsize=(15,5),layout="constrained")
    for ax,k,col in zip(axes,LABELS,["odds_home_prob","odds_draw_prob","odds_away_prob"]):
        odds["bin"]=pd.qcut(odds[col],10,duplicates="drop")
        cal=odds.groupby("bin",observed=True).agg(pred=(col,"mean"),n=("result","size"),k=("result",lambda s:s.eq(k).sum()))
        lo,hi=wilson(cal.k,cal.n)
        observed=cal.k/cal.n
        ax.plot([0,1],[0,1],"--",color="#80909b")
        ax.errorbar(cal.pred,observed,yerr=np.vstack([observed-lo,hi-observed]),fmt="o-",color=COLORS[k],capsize=3)
        ax.set(title=NAMES[k],xlabel="Xác suất từ odds (TB trong nhóm)",ylabel="Tỷ lệ kết quả quan sát",xlim=(0,1),ylim=(0,1))
    fig.suptitle(f"14 · Độ phù hợp mô tả của odds | cùng {nfmt(len(odds))} trận, Wilson 95% xấp xỉ",fontsize=14)
    save(fig,"14_odds_reliability.png")

    # 15: relationships for describing the finished match, not prediction inputs.
    actual=[("xg","xG nhà − khách"),("shots_on_target","Sút trúng đích nhà − khách"),
            ("possession","Kiểm soát bóng nhà − khách"),("red_cards","Thẻ đỏ nhà − khách")]
    fig,axes=plt.subplots(2,2,figsize=(13,9),layout="constrained")
    for ax,(base,label) in zip(axes.flat,actual):
        data=core[[f"home_{base}",f"away_{base}","result"]].dropna().copy()
        if base=="red_cards":
            sizes=data.groupby("result").size().reindex(LABELS)
            for i,side in enumerate(["home","away"]):
                rate=data.assign(event=data[f"{side}_red_cards"].gt(0)).groupby("result").event.mean().reindex(LABELS)
                ax.bar(np.arange(3)+(i-.5)*.36,rate,.36,color=COLORS["H" if side=="home" else "A"],
                       label="Đội nhà có thẻ đỏ" if side=="home" else "Đội khách có thẻ đỏ")
            ax.set_xticks(range(3),[f"{k}\nn={nfmt(sizes[k])}" for k in LABELS])
            ax.yaxis.set_major_formatter(PercentFormatter(1))
            ax.set(title="Tỷ lệ có ít nhất một thẻ đỏ",ylabel="Tỷ lệ trận")
            ax.legend(fontsize=9)
            continue
        data["diff"]=data.iloc[:,0]-data.iloc[:,1]
        groups=[data.loc[data.result.eq(k),"diff"] for k in LABELS]
        boxes=ax.boxplot(groups,tick_labels=[f"{k}\nn={nfmt(len(g))}" for k,g in zip(LABELS,groups)],patch_artist=True,showfliers=False)
        for box,k in zip(boxes["boxes"],LABELS):
            box.set_facecolor(COLORS[k]);box.set_alpha(.65)
        ax.axhline(0,color="#7c8c96",linestyle="--",linewidth=.8)
        ax.set(title=label,ylabel="Chênh lệch trong chính trận")
    fig.suptitle("15 · Thống kê cùng trận và kết quả | chỉ dùng mô tả, không đưa trực tiếp vào X trước trận",fontsize=14)
    save(fig,"15_match_statistics.png")

    # 16: stability of direction across complete seasons, not validation scores.
    stability=[]
    scols=["elo_diff","diff_points5","diff_goals_for5","h2h_home_points","diff_xg5","odds_home_prob"]
    for season,g in f[f.season.isin(core.season.unique())].groupby("season"):
        for col in scols:
            v=g[[col,"result"]].dropna()
            if len(v)>=100 and v[col].nunique()>1:
                stability.append({"season":season,"feature":col,"n":len(v),"rho_H":v[col].corr(v.result.eq("H").astype(int),method="spearman")})
    stable=pd.DataFrame(stability)
    fig,ax=plt.subplots(figsize=(14,6),layout="constrained")
    for col,g in stable.groupby("feature"):
        ax.plot(g.season,g.rho_H,"o-",markersize=3,label=FEATURE_NAMES[col])
    ax.axhline(0,color="#80909b",linewidth=.8)
    ax.tick_params(axis="x",rotation=60)
    ax.set(ylabel="Spearman với H=1",title="16 · Độ ổn định hướng liên hệ qua mùa đủ; mỗi điểm có ≥100 trận hợp lệ")
    ax.legend(ncols=2,fontsize=9)
    save(fig,"16_temporal_stability.png")
    return cond, xg, stable, group, shots


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    FIG.mkdir(parents=True,exist_ok=True)
    paths={"matches":ROOT/"clean"/"matches.csv","features":ROOT/"clean"/"features.csv"}
    before={key:hashlib.sha256(path.read_bytes()).hexdigest() for key,path in paths.items()}
    raw_m=pd.read_csv(paths["matches"],low_memory=False)
    raw_f=pd.read_csv(paths["features"],low_memory=False)
    checks,issues,details=audit_inputs(raw_m,raw_f)
    checks.to_csv(OUT/"data_quality_checks.csv",index=False,encoding="utf-8-sig")
    issues.to_csv(OUT/"data_issues.csv",index=False,encoding="utf-8-sig")
    if checks.status.eq("fail").any():
        raise ValueError("Input integrity audit failed; inspect data_quality_checks.csv before reporting.")
    catalog=build_feature_catalog(raw_f,raw_m)
    catalog.to_csv(OUT/"feature_candidates.csv",index=False,encoding="utf-8-sig")
    m=raw_m[raw_m.status.eq("played") & ~raw_m.provisional.fillna(False).astype(bool)].copy()
    m["date"]=pd.to_datetime(m.date)
    f=raw_f[raw_f.match_id.isin(m.match_id)].copy()
    f["date"]=pd.to_datetime(f.date)
    f["goal_diff"]=f.home_goals-f.away_goals
    f["diff_goals_for_pg"]=f.home_goals_for_pg-f.away_goals_for_pg
    f["diff_rest_days"]=f.home_rest_days-f.away_rest_days
    seasons=m.groupby("season").agg(matches=("match_id","size"),latest=("date","max"))
    team_all=team_rows(m,f)
    team_counts=team_all.groupby(["season","club_id"]).size()
    seasons["teams"]=team_all.groupby("season").club_id.nunique()
    seasons["complete"]=[r.matches==380 and r.teams==20 and team_counts.loc[s].eq(38).all()
                         for s,r in seasons.iterrows()]
    core=m[m.season.isin(seasons.index[seasons.complete])].copy()
    teams=team_all[team_all.season.isin(core.season.unique())].copy()
    acols=list(FEATURE_NAMES)
    overall=associations(f,acols)
    common_cols=["odds_home_prob","elo_diff","diff_xg5","diff_points5","diff_goals_for5","h2h_home_points"]
    common_frame=f.dropna(subset=common_cols)
    common=associations(common_frame,common_cols)
    cond,xg,stable,group,shots=make_figures(m,f,seasons,core,teams,common_cols,overall,common,issues)
    print("Generated 16 exploratory figures.",flush=True)
    hypotheses=make_hypotheses()
    hypotheses.to_csv(OUT/"hypotheses.csv",index=False,encoding="utf-8-sig")
    meta={
        "created_at":datetime.now(timezone(timedelta(hours=7))).isoformat(),
        "inputs":{k:{"path":str(p.relative_to(ROOT)),"sha256":before[k],"bytes":p.stat().st_size}
                  for k,p in paths.items()},
        "rows_matches":len(raw_m),"columns_matches":len(raw_m.columns),
        "rows_features":len(raw_f),"columns_features":len(raw_f.columns),
        "predictors_catalogued":len(catalog),"complete_seasons":seasons.index[seasons.complete].tolist(),
        "complete_season_matches":len(core),"common_association_sample":len(common_frame),
        "libraries":{"python":platform.python_version(),"pandas":pd.__version__,"numpy":np.__version__,"matplotlib":matplotlib.__version__},
        "source_semantics":"origin/Data_itegration at eaef4cd: Data integration and preprocessing/features.py",
        "audit_details":details,
        "method":"Descriptive EDA only; no predictive models trained; inputs unchanged; seed 42 for season bootstrap.",
    }
    build_report(raw_m,raw_f,m,f,seasons,core,teams,checks,issues,catalog,overall,common,cond,xg,stable,group,shots,hypotheses,meta)
    after={key:hashlib.sha256(path.read_bytes()).hexdigest() for key,path in paths.items()}
    if after!=before:
        raise RuntimeError("Input file fingerprints changed during EDA.")
    (OUT/"analysis_metadata.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(f"Completed: {OUT/'report.html'}; {len(catalog)} feature candidates; {len(hypotheses)} hypotheses.",flush=True)


def make_hypotheses():
    criterion = "Giảm log loss/Brier ngoài thời gian trên cùng các trận qua nhiều fold; báo cáo cả macro-F1 và recall từng lớp."
    rows = [
        ("H1","Elo bổ sung khả năng dự đoán H/D/A so với tần suất nhãn lịch sử.",
         "elo_diff","Các trận có nhãn hợp lệ; phân tích nhạy cảm với mùa thiếu.",
         "Mốc tần suất chỉ học từ train → logistic đa lớp có Elo.", "Elo expected score không phải P(H)."),
        ("H2","Phong độ điểm và bàn ghi/bàn thua gần đây bổ sung thông tin ngoài Elo.",
         "diff_points5, diff_goals_for5, diff_goals_against5","Cùng các trận có đủ lịch sử, xem riêng cửa sổ chưa đủ 5 trận.",
         "Elo → Elo + nhóm phong độ.", "Liên hệ sau điều kiện Elo đang yếu; không chọn chỉ theo tương quan."),
        ("H3","Phong độ xG/xGA bổ sung thông tin ngoài Elo và phong độ bàn thắng.",
         "diff_xg5, diff_xga5","Cùng mẫu có xG, ưu tiên mùa đủ từ 2015/16; không so hai tập thời kỳ khác nhau.",
         "Elo + phong độ bàn → thêm xG/xGA.", "Trung bình hiện bỏ NaN; cần thêm số trận xG hợp lệ khi dựng lại."),
        ("H4","Đối đầu bổ sung thông tin sau khi đã xét Elo và phong độ.",
         "h2h_home_points, h2h_home_goal_diff, h2h_matches","Giữ cùng mẫu; lặp lại với h2h_matches ≥ 3.",
         "Elo + phong độ → thêm nhóm H2H.", "Lịch sử cũ, mẫu nhỏ và trùng thông tin sức mạnh."),
        ("H5","Lợi thế sân nhà biến đổi theo thời kỳ.",
         "elo_diff và hệ số sân nhà theo thời kỳ","Các mùa đủ; kiểm tra riêng giai đoạn COVID.",
         "Hệ số sân nhà cố định → hệ số học trên quá khứ theo thời kỳ.", "Không cộng lợi thế +65 hai lần khi dùng biến đổi Elo."),
        ("H6","Phong độ đúng sân bổ sung cho phong độ tổng.",
         "home_home_points5, away_away_points5 và bàn ghi/thua theo sân","Cùng mẫu có lịch sử sân; kiểm tra độ dài lịch sử.",
         "Phong độ tổng → thêm nhóm phong độ sân.", "Lịch sử đúng sân thường cũ hơn cửa sổ tổng."),
        ("H7","Độ mới của lịch sử hoặc khoảng nghỉ được định nghĩa đúng có ích.",
         "EPL gap / cờ lịch sử cũ / lịch mọi đấu trường nếu bổ sung","Chỉ sau khi xác minh định nghĩa và xây dựng biến thay thế.",
         "Elo + phong độ → thêm cờ gián đoạn; kiểm tra lịch nghỉ thực riêng.", "Tạm hoãn raw rest_days như thước đo mệt mỏi."),
        ("H8","Chỉ số lối chơi FotMob bổ sung thông tin ngoài nhóm cơ bản.",
         "diff_possession5, diff_big_chances5, diff_box_touches5, diff_xgot5","Cùng mẫu gần đây có phủ nguồn; kiểm tra thiếu và số quan sát hợp lệ.",
         "Elo + phong độ + xG → thêm từng nhóm FotMob.", "Phạm vi ngắn và thiếu có cấu trúc; tránh chọn biến từ test."),
        ("H9","Odds đúng thời điểm cung cấp mốc tham chiếu hoặc thông tin bổ sung.",
         "odds_home_prob, odds_draw_prob, odds_away_prob","Chỉ các trận có odds và xác nhận thời điểm chốt dự đoán.",
         "Tách kịch bản chỉ bóng đá / có odds; so trên cùng mẫu.", "Chưa có timestamp thu thập; không suy hiệu quả tài chính.")
    ]
    return pd.DataFrame([
        dict(id=i,hypothesis_vi=h,features=c,cohort_vi=d,comparison_vi=e,
             success_criterion_vi=criterion,caution_vi=r)
        for i,h,c,d,e,r in rows
    ])


def build_report(raw_m,raw_f,m,f,seasons,core,teams,checks,issues,catalog,overall,
                 common,cond,xg,stable,group,shots,hypotheses,meta):
    report=Report()
    counts=m.result.value_counts().reindex(LABELS)
    full_counts=core.result.value_counts().reindex(LABELS)
    goals=core.home_goals+core.away_goals
    per_season=core.assign(adv=core.result.map({"H":1,"D":0,"A":-1}),
                           gd=core.home_goals-core.away_goals).groupby("season")[["adv","gd"]].mean()
    rng=np.random.default_rng(42)
    boot=rng.integers(0,len(per_season),size=(4000,len(per_season)))
    adv_ci=np.quantile(per_season.adv.to_numpy()[boot].mean(axis=1),[.025,.975])
    gd_ci=np.quantile(per_season.gd.to_numpy()[boot].mean(axis=1),[.025,.975])
    audit=meta["audit_details"]
    report.section("EDA Premier League: matches.csv và features.csv",1)
    report.p("Báo cáo khám phá dữ liệu sạch phục vụ phân tích bóng đá và chuẩn bị dự đoán trước trận. "
             "Mọi con số mô tả snapshot đang có trên máy, không xác nhận kết quả ngoài đời hoặc dữ liệu mới hơn ngày cuối của snapshot.")
    report.p(f"Thời điểm lập: {meta['created_at'][:19]} (UTC+07). Hai bảng có {nfmt(len(m))} trận khớp theo match_id; "
             f"matches có {raw_m.shape[1]} cột, features có {raw_f.shape[1]} cột, trong đó {len(catalog)} đặc trưng ứng viên.")
    report.section("1. Các phát hiện chính")
    report.bullets([
        f"Tập dữ liệu chỉ gồm Premier League, {team_rows(m,f).club_id.nunique()} đội, từ "
        f"{m.date.min():%d/%m/%Y} đến {m.date.max():%d/%m/%Y}. Phân tích so sánh qua mùa dùng "
        f"{int(seasons.complete.sum())} mùa đủ, {nfmt(len(core))} trận.",
        f"Trên toàn snapshot: chủ nhà thắng {pct(counts.H/len(m))}, hòa {pct(counts.D/len(m))}, "
        f"đội khách thắng {pct(counts.A/len(m))}. Lợi thế sân nhà hiện diện trong thống kê quan sát, có biến động qua mùa.",
        f"Ở các mùa đủ, trung bình có {vfmt(core.home_goals.mean())} bàn nhà và {vfmt(core.away_goals.mean())} bàn khách/trận. "
        f"Chênh bàn trung bình nhà−khách là {vfmt(per_season.gd.mean())}; khoảng bootstrap theo mùa 95% "
        f"[{vfmt(gd_ci[0])}; {vfmt(gd_ci[1])}].",
        "Elo, odds, phong độ và đối đầu có liên hệ đơn biến với kết quả. Khi giới hạn các đội gần sức mạnh Elo, "
        "chênh lệch liên quan đến điểm phong độ và H2H thu hẹp; xG gần đây là giả thuyết bổ sung đáng kiểm tra, chưa được chứng minh bằng mô hình.",
        f"Có {audit['shot_components_exceed_total_unique_matches']} trận cần kiểm tra tính nhất quán giữa thống kê sút khác nguồn. "
        "rest_days là khoảng cách tới trận EPL quan sát trước, không phải nghỉ mọi đấu trường.",
        f"{audit['selected_temporal_feature_count']} cột lịch sử được tính lại độc lập trên toàn bộ trận và có "
        f"{audit['selected_temporal_mismatch_count']} sai khác. Điều này không xác nhận timestamp công bố dữ liệu hay mọi tình huống của toàn bộ 87 biến.",
    ])

    report.section("2. Phạm vi, đơn vị quan sát và cách lấy mẫu")
    report.table(pd.DataFrame([
        ["matches.csv",nfmt(len(raw_m)),str(raw_m.shape[1]),"Một trận; tỷ số, thống kê cùng trận, nguồn và cờ chất lượng"],
        ["features.csv",nfmt(len(raw_f)),str(raw_f.shape[1]),"Một trận; đặc trưng trước trận theo thiết kế, định danh và nhãn"],
        ["Mẫu so sánh mùa đủ",nfmt(len(core)),str(int(seasons.complete.sum()))+" mùa","380 trận, 20 đội và 38 trận mỗi đội trong từng mùa"],
    ],columns=["Bảng / mẫu","Số dòng","Số cột / mùa","Mục đích"]))
    incomplete=seasons.loc[~seasons.complete,["matches","teams"]].reset_index()
    incomplete["Ghi chú"]=np.where(incomplete.season.eq(m.season.max()),"Mùa đang diễn ra trong snapshot","Mùa lịch sử thiếu trận")
    report.table(incomplete.rename(columns={"season":"Mùa","matches":"Số trận","teams":"Số đội"}))
    report.p("Không thực hiện so sánh liên giải vì không có giải thứ hai. Tất cả dòng hiện tại đều là played; "
             "không có provisional, postponed hoặc upcoming. Các thống kê toàn snapshot vẫn giữ mùa thiếu; "
             "các biểu đồ xu hướng, nhóm đội và phân phối bàn thắng chính dùng riêng mẫu mùa đủ, được ghi tại từng hình.")
    report.image("01_coverage.png","Hình 01. Mùa thiếu và độ phủ chỉ số theo thời gian; màu cam không đồng nghĩa dữ liệu sai.")

    report.section("3. Kiểm toán dữ liệu và dữ liệu thiếu")
    qc=checks.status.value_counts().reindex(["pass","warn","info","fail"],fill_value=0)
    report.table(pd.DataFrame({"Trạng thái":["Đạt","Cảnh báo cần diễn giải","Thông tin","Không đạt"],"Số kiểm tra":qc.to_numpy()}))
    report.p("Hai bảng không trùng match_id, ghép một–một và khớp các trường ngày, đội, tỷ số, kết quả. "
             "Kiểm toán kiểm tra miền giá trị, quan hệ tỷ số–nhãn, thống kê và lịch sử trước trận. "
             "Các cảnh báo về khác nguồn, khán giả được điền và lịch sử cũ được giữ riêng; không tự xóa hoặc sửa dòng nguồn.")
    report.link("Chi tiết kiểm tra dữ liệu","data_quality_checks.csv")
    report.link("Các dòng được gắn cờ và lý do","data_issues.csv")
    missing_cols=["home_points5","home_ppg","h2h_home_points","diff_xg5","diff_possession5",
                  "diff_big_chances5","diff_box_touches5","diff_xgot5","odds_home_prob","gameweek"]
    miss_table=pd.DataFrame({
        "Cột":missing_cols,
        "Số thiếu":[nfmt(raw_f[c].isna().sum()) for c in missing_cols],
        "Tỷ lệ thiếu":[pct(raw_f[c].isna().mean()) for c in missing_cols],
    })
    report.table(miss_table)
    report.p(f"Xóa mọi dòng có bất kỳ ô trống trên cả 102 cột chỉ còn {nfmt(audit['all_column_complete_case_rows'])} trận. "
             f"Chỉ xét các biến số dự báo, bỏ gameweek và nhãn, cũng chỉ còn {nfmt(audit['numeric_predictor_complete_case_rows'])} trận đủ. "
             "Vì vậy không dùng dropna toàn bảng hoặc điền 0 đồng loạt. xG bắt đầu từ 2014/15; "
             "các biến FotMob có mốc phủ khác nhau. Thiếu theo thời kỳ không tương đương một giá trị 0 thật.")
    report.image("02_missingness.png","Hình 02. Cột đại diện ở phía chủ nhà và các biến chênh lệch; CSV danh mục có mức thiếu của toàn bộ đặc trưng.")
    source=m.source_flags.value_counts()
    report.table(pd.DataFrame({"Tổ hợp nguồn":source.index,"Số trận":[nfmt(v) for v in source],"Tỷ lệ":[pct(v/len(m)) for v in source]}))
    report.p("FD = Football-Data; US = Understat; TM = Transfermarkt; FM = FotMob. Cờ đã ghép nguồn không bảo đảm "
             "mọi chỉ số của nguồn đó đều được ghi nhận. gameweek chỉ có ở mùa hiện tại nên không phù hợp làm điều kiện giữ dòng lịch sử.")

    report.section("4. Bàn thắng, bàn thua, nhãn và tỷ số")
    label_rows=[]
    for scope,ct,total in [("Toàn snapshot",counts,len(m)),("Mùa đủ",full_counts,len(core))]:
        for k in LABELS:
            lo,hi=wilson(ct[k],total)
            label_rows.append([scope,NAMES[k],nfmt(ct[k]),pct(ct[k]/total),f"[{pct(float(lo))}; {pct(float(hi))}]"])
    report.table(pd.DataFrame(label_rows,columns=["Mẫu","Kết quả","Số trận","Tỷ lệ","Wilson 95% xấp xỉ"]))
    report.p(f"Lớp lớn nhất/nhỏ nhất có tỷ số {vfmt(counts.max()/counts.min())}. "
             "Cần theo dõi riêng lớp hòa; mức lệch nhãn quan sát không tự động dẫn đến oversampling. "
             "Các khoảng Wilson chỉ mô tả dưới xấp xỉ quan sát độc lập; đội bóng lặp lại khiến suy luận chính thức cần tính đến phụ thuộc.")
    report.image("03_outcomes_goals.png","Hình 03. Phân phối kết quả trên toàn snapshot; phân phối bàn thắng dùng 24 mùa đủ.")
    moment=[]
    for label,s in [("Bàn nhà",core.home_goals),("Bàn khách",core.away_goals),("Tổng bàn",goals)]:
        moment.append([label,vfmt(s.mean(),3),vfmt(s.var(),3),vfmt(s.var()/s.mean(),3),pct(s.eq(0).mean())])
    report.table(pd.DataFrame(moment,columns=["Đại lượng","Trung bình","Phương sai","Phương sai/TB","Tỷ lệ 0"]))
    report.p(f"Tỷ lệ hai đội cùng ghi bàn là {pct((core.home_goals.gt(0)&core.away_goals.gt(0)).mean())}; "
             f"chủ nhà giữ sạch lưới {pct(core.away_goals.eq(0).mean())}, đội khách giữ sạch lưới {pct(core.home_goals.eq(0).mean())}. "
             f"Tương quan Pearson bàn nhà–khách là {vfmt(core.home_goals.corr(core.away_goals),3)}. "
             "Các kiểm tra này giúp đặt giả thuyết cho mô hình đếm; không đủ kết luận phân phối Poisson hoặc độc lập là đúng.")
    report.p("Khi gộp toàn bộ đội–trận của giải, tổng bàn ghi và tổng bàn thua bằng nhau, kể cả phân phối đối ứng của chúng. "
             "Khác biệt tấn công/phòng ngự cần xét theo đội, mùa và sân, không diễn giải hai tổng toàn giải như hai nguồn thông tin độc lập.")
    report.image("04_score_counts.png","Hình 04. Tỷ số và đối chiếu tổng bàn với Poisson có cùng trung bình; đây là mô tả phân phối, không phải mô hình dự báo.")

    report.section("5. Mùa giải và lợi thế sân nhà")
    report.image("05_season_trends.png","Hình 05. Chỉ dùng mùa đủ; mỗi điểm có 380 trận nên các tỷ lệ và trung bình có cùng mẫu số.")
    season_goals=core.assign(total=core.home_goals+core.away_goals).groupby("season").total.mean()
    report.p(f"Trong các mùa đủ, tổng bàn/trận thấp nhất ở {season_goals.idxmin()} "
             f"({vfmt(season_goals.min())}) và cao nhất ở {season_goals.idxmax()} ({vfmt(season_goals.max())}). "
             f"Chênh tỷ lệ H−A thấp nhất ở {per_season.adv.idxmin()} ({pct(per_season.adv.min())}), "
             f"cao nhất ở {per_season.adv.idxmax()} ({pct(per_season.adv.max())}). "
             "Đây là cực trị trong mẫu, không chứng minh xu hướng tuyến tính hoặc khác biệt có ý nghĩa xác nhận.")
    report.table(pd.DataFrame([
        ["Tỷ lệ H trừ tỷ lệ A",pct(per_season.adv.mean()),f"[{pct(adv_ci[0])}; {pct(adv_ci[1])}]"],
        ["Bàn nhà trừ bàn khách",vfmt(per_season.gd.mean(),3),f"[{vfmt(gd_ci[0],3)}; {vfmt(gd_ci[1],3)}]"],
    ],columns=["Thước đo sân nhà ở mùa đủ","Ước lượng","Bootstrap theo mùa 95%"]))
    report.p("Khoảng trên được tính bằng 4.000 lần lấy mẫu lại 24 mùa (seed 42), giữ các trận trong cùng mùa đi cùng nhau. "
             "Nó mô tả biến động giữa các mùa đã quan sát; 24 cụm là hữu hạn và các mùa vẫn có đội lặp lại. "
             "Không diễn giải đây là hiệu ứng nhân quả của sân nhà.")
    report.image("06_home_advantage.png","Hình 06. Thanh cam đánh dấu 2019/20 và 2020/21; so sánh khán giả chỉ mang tính mô tả trong từng mùa.")
    report.p(f"Có {nfmt(m.attendance_imputed.fillna(False).sum())} giá trị khán giả được điền; "
             "các giá trị 0 giai đoạn COVID có cờ attendance_imputed. Khác biệt giữa nhóm có/không khán giả "
             "còn chịu ảnh hưởng của lịch, sức mạnh đội và thời điểm; không quy toàn bộ khác biệt cho khán giả.")

    report.section("6. Nhóm đội và khả năng ghi bàn/phòng ngự")
    report.image("07_team_groups.png","Hình 07. Nhóm Elo lấy từ giá trị trước trận; ngưỡng phân vị toàn mẫu chỉ phục vụ EDA. "
                 "Không dùng thứ hạng cuối mùa để tạo nhóm dự báo.")
    group_table=group.reset_index().rename(columns={"strength":"Nhóm Elo trước trận","venue":"Sân","n":"Đội–trận","pts":"Điểm/trận","gf":"Bàn ghi/trận","ga":"Bàn thua/trận"})
    for c in ["Điểm/trận","Bàn ghi/trận","Bàn thua/trận"]:
        group_table[c]=group_table[c].map(vfmt)
    group_table["Đội–trận"]=group_table["Đội–trận"].map(nfmt)
    report.table(group_table)
    report.p("Đội không có mặt mùa EPL trước được nhận diện bằng lịch sử tham dự quan sát, gồm đội lần đầu xuất hiện hoặc trở lại. "
             "Đây là nhóm mô tả, chưa xác nhận toàn bộ lịch thi đấu hạng dưới. Nhóm đầu tiên của lịch sử dữ liệu được loại khỏi so sánh này.")
    ts=teams.groupby("team").agg(n=("pts","size"),ppg=("pts","mean"),gf=("gf","mean"),ga=("ga","mean"))
    best=ts[ts.n.ge(114)].sort_values("ppg",ascending=False).head(8).reset_index()
    report.table(pd.DataFrame({
        "Đội (ít nhất 3 mùa đủ tương đương)":best.team,"Đội–trận":best.n.map(nfmt),
        "Điểm/trận":best.ppg.map(vfmt),"Bàn ghi/trận":best.gf.map(vfmt),"Bàn thua/trận":best.ga.map(vfmt),
    }))
    report.p("Bảng trên tổng hợp các lần xuất hiện trong mẫu mùa đủ, không phải bảng xếp hạng của một mùa. "
             "Các đội có số mùa góp mặt khác nhau; không suy rằng khác biệt do một đặc trưng duy nhất.")

    report.section("7. Thống kê cùng trận và đối chiếu nguồn xG")
    report.image("15_match_statistics.png","Hình 15. Ba đồ thị hộp thể hiện trung vị và khoảng tứ phân vị; điểm ngoài râu vẫn được giữ trong dữ liệu. "
                 "Thẻ đỏ dùng tỷ lệ có ít nhất một thẻ vì trung vị và tứ phân vị đều bằng 0.")
    report.p("Số sút, xG, kiểm soát bóng và thẻ của chính trận liên hệ với kết quả theo mô tả. "
             "Chúng không có trước trận nên không được đưa trực tiếp vào mô hình dự đoán trước trận. "
             "Thẻ và kiểm soát bóng cũng có thể là hệ quả của diễn biến/tỷ số, không chỉ là nguyên nhân.")
    xd=xg.fotmob-xg.understat
    report.table(pd.DataFrame([
        ["Đội–trận có cả hai xG",nfmt(len(xg))],
        ["Spearman Understat–FotMob",vfmt(xg.understat.corr(xg.fotmob,method="spearman"),3)],
        ["Chênh trung bình FotMob−Understat",vfmt(xd.mean(),3)],
        ["Dải TB ±1,96 SD của chênh",f"[{vfmt(xd.mean()-1.96*xd.std(),3)}; {vfmt(xd.mean()+1.96*xd.std(),3)}]"],
    ],columns=["Thước đo trên cùng mẫu","Giá trị"]))
    report.image("12_xg_sources.png","Hình 12. Tương quan cao không đồng nghĩa hai mô hình xG có thể thay thế hoàn toàn; dải SD không phải khoảng tin cậy của trung bình.")
    report.p("Khi dựng đặc trưng, cần giữ nhất quán nguồn xG hoặc kiểm tra nhạy cảm khi đổi nguồn. "
             "Báo cáo không tự ghép hai nguồn thành một chỉ số mới.")

    report.section("8. Ý nghĩa và thời điểm có đặc trưng")
    report.table(pd.DataFrame([
        ["points5, goals_for5, goals_against5","Trung bình tối đa 5 trận EPL trước, không phải tổng 5 trận","Cửa sổ có thể ngắn hơn 5 và không reset qua mùa"],
        ["xg5, xga5 và các trung bình FotMob","Trung bình các giá trị không thiếu trong cửa sổ tối đa 5 trận","Có thể chỉ dựa trên 1–4 quan sát; cần thêm số quan sát hợp lệ"],
        ["ppg, played, goals_for_pg, position","Thống kê từ phần mùa đã quan sát trước trận","Mùa lịch sử thiếu không tái dựng đúng toàn bộ bảng xếp hạng"],
        ["h2h_home_points / goal_diff","TB tối đa 5 lần gặp trước, quy về đội đang làm chủ nhà","Có thể rất cũ; kiểm tra n đối đầu"],
        ["elo_home_win_prob","Điểm kết quả kỳ vọng Elo với H=1, D=0,5, A=0","Không phải P(H) ba lớp đã hiệu chuẩn; biến đổi đơn điệu của Elo"],
        ["rest_days","Ngày từ trận EPL quan sát gần nhất","Không phải thời gian nghỉ mọi đấu trường"],
        ["odds_*_prob","Nghịch đảo odds Avg, B365 dự phòng, chuẩn hóa tổng bằng 1","Chưa có timestamp thu thập; đóng khung thời điểm dự đoán trước khi dùng"],
    ],columns=["Nhóm biến","Định nghĩa đúng","Lưu ý sử dụng"]))
    short_history=((f.home_wins5+f.home_draws5+f.home_losses5).lt(5) |
                   (f.away_wins5+f.away_draws5+f.away_losses5).lt(5))
    report.p(f"Có {nfmt(short_history.sum())} trận có ít nhất một đội với cửa sổ kết quả đã biết nhưng chưa đủ 5 trận; "
             f"{nfmt(f.h2h_matches.eq(0).sum())} trận chưa có đối đầu quan sát trước. "
             f"H2H với ít nhất 3 lần gặp có {nfmt(f.h2h_matches.ge(3).sum())} trận. "
             "Cửa sổ phong độ vẫn có thể chứa kết quả nhiều năm trước khi một đội trở lại EPL.")
    report.p(f"Kiểm toán mới tính lại độc lập {audit['selected_temporal_feature_count']} cột trên toàn bộ "
             f"{nfmt(len(f))} trận, gồm Elo, phong độ kết quả/bàn/xG, phong độ sân, thống kê mùa, ngày EPL và H2H; "
             f"có {audit['selected_temporal_mismatch_count']} ô sai khác. Kiểm tra này đọc trạng thái trước khi cập nhật kết quả hiện tại. "
             "Không thay thế kiểm chứng timestamp dữ liệu, thời gian kickoff thiếu, toàn bộ biến FotMob hoặc dữ liệu nguồn thực tế.")

    report.section("9. Phong độ, bàn thắng trung bình, đối đầu và kết quả")
    report.image("08_feature_associations.png","Hình 08. Các phân vị là nhóm mô tả toàn mẫu, không phải ngưỡng quyết định của mô hình. "
                 "Một số giá trị bằng nhau làm kích thước nhóm không đều.")
    show=["odds_home_prob","elo_diff","diff_ppg","diff_xg5","diff_shots_on_target5",
          "diff_points5","diff_goals_for5","diff_goals_against5","h2h_home_points","h2h_home_goal_diff"]
    assoc=overall.loc[show]
    report.table(pd.DataFrame({
        "Biến":assoc.index,"Số trận hợp lệ":assoc.n.map(nfmt),
        "Spearman với H":assoc.rho_H.map(lambda v:vfmt(v,3)),
        "Spearman với hiệu số bàn":assoc.rho_GD.map(lambda v:vfmt(v,3)),
    }))
    report.p("Nhãn H/D/A không được mã hóa thành một thang số tùy ý để tính tương quan. "
             "Báo cáo dùng H=1, còn lại=0 hoặc hiệu số bàn (đại lượng có thứ tự tự nhiên). "
             "Các hệ số trên dùng tập trận hợp lệ riêng, vì vậy không xếp hạng khả năng dự đoán trực tiếp giữa chúng.")
    report.image("10_common_sample.png",f"Hình 10. So sánh bổ sung trên cùng {nfmt(common.n.iloc[0])} trận đủ sáu biến; "
                 "mẫu này thiên về giai đoạn có xG và không đại diện đồng đều toàn lịch sử.")
    report.image("09_strength_conditioned.png","Hình 09. So nhóm thấp/cao theo tercile toàn mẫu trong các trận gần Elo; "
                 "đây chỉ là điều kiện thô, chưa loại hết khác biệt về sức mạnh và thời kỳ.")
    cond_rows=[]
    for col,g in cond.groupby("feature",sort=False):
        low=g[g.group.eq("Thấp")].iloc[0]
        high=g[g.group.eq("Cao")].iloc[0]
        cond_rows.append([FEATURE_NAMES[col],f"≤{vfmt(low.low,3)} / ≥{vfmt(low.high,3)}",
                          f"{pct(low.rate)} (n={nfmt(low.n)})",f"{pct(high.rate)} (n={nfmt(high.n)})",
                          vfmt((high.rate-low.rate)*100)+" điểm %",
                          f"{vfmt(low.mean_elo,1)} / {vfmt(high.mean_elo,1)}"])
    report.table(pd.DataFrame(cond_rows,columns=["Biến","Ngưỡng thấp / cao","Tỷ lệ H thấp","Tỷ lệ H cao","Chênh cao−thấp","Elo TB thấp / cao"]))
    report.p("Trong nhóm gần Elo, khác biệt của phong độ điểm, bàn ghi và đối đầu giảm mạnh so với liên hệ thô. "
             "xG giữ chênh lệch lớn hơn nhưng hai nhóm vẫn khác Elo trung bình và phạm vi thời kỳ. "
             "Đây là lý do đề xuất kiểm định giá trị tăng thêm của xG bằng mô hình theo thời gian; chưa phải bằng chứng nhân quả hoặc cải thiện dự báo.")

    report.section("10. Bất thường và quyết định xử lý")
    issue_summary=issues.groupby("category").agg(flags=("category","size"),matches=("match_id","nunique")).reset_index()
    meanings={
        "cross_source_shots":"Trong + ngoài vòng cấm (FotMob) vượt tổng sút (Football-Data): xác minh định nghĩa/nguồn",
        "cross_source_zero_shots":"Sút trong và ngoài vòng cấm bằng 0 nhưng Football-Data có sút: xác minh giá trị 0 và độ phủ nguồn",
        "observed_epl_gap":"Khoảng cách EPL >30 ngày: gồm nghỉ mùa, vắng giải; không tự coi là lỗi",
        "attendance_imputed":"Khán giả được điền: giữ cờ khi phân tích",
        "suspicious_possession":"Kiểm soát bóng cực đoan: kiểm tra nguồn, không tự xóa",
        "extreme_possession":"Kiểm soát bóng cực đoan: kiểm tra nguồn, không tự xóa",
    }
    issue_summary["Diễn giải"]=issue_summary.category.map(meanings).fillna("Xem reason_vi trong bảng chi tiết")
    report.table(issue_summary.rename(columns={"category":"Nhóm cờ","flags":"Số cờ","matches":"Trận duy nhất trong nhóm"}))
    report.p(f"Trong/ngoài vòng cấm vượt tổng sút ở {audit['shot_components_exceed_total_unique_matches']} trận, "
             "nhưng các chỉ số đến từ hai nguồn nên đây là cảnh báo tính nhất quán, chưa đủ để kết luận tổng nào sai. "
             "Ngoài ra có trận Newcastle–Tottenham ngày 15/05/2016 ghi kiểm soát bóng 3%–97% và tỷ số 5–1; cần xác minh nguồn.")
    report.p(f"Có thêm {audit.get('zero_shot_components_positive_total_unique_matches',0)} trận mà hai thành phần sút FotMob "
             "đều bằng 0 trong khi tổng sút Football-Data dương. Các số 0 này cần được kiểm tra về độ phủ/ghi nhận nguồn, "
             "không tự diễn giải là đội không có cú sút nào.")
    gaps=pd.concat([f.home_rest_days,f.away_rest_days]).dropna()
    report.p(f"Khoảng cách EPL lớn nhất là {nfmt(gaps.max())} ngày; {nfmt(gaps.gt(365).sum())} đội–trận có khoảng cách trên một năm. "
             "Không cắt các giá trị này rồi gọi chúng là nghỉ thực tế. Có thể thiết kế cờ lịch sử cũ, khoảng cách trong mùa, "
             "hoặc bổ sung lịch mọi đấu trường trước khi kiểm định mệt mỏi.")
    report.image("13_anomalies.png","Hình 13. Không có dòng nào bị tự động loại khỏi dữ liệu nguồn. Phân phối log cho thấy vì sao mean/std đơn thuần không diễn giải được rest_days.")

    report.section("11. Đặc trưng tiềm năng, trùng thông tin và độ ổn định")
    report.image("11_redundancy.png","Hình 11. Tương quan từng cặp không cùng mẫu; quan hệ xác định đại số được kiểm tra thêm bằng công thức.")
    report.p("14 cột diff_* có sẵn đều bằng home−away của chỉ số tương ứng. Không đưa đồng thời cả ba biến vào "
             "hồi quy tuyến tính không điều chuẩn. elo_home_win_prob là biến đổi đơn điệu của elo_diff; "
             "position_diff và diff_ppg, cùng hai biến H2H, có mức trùng thông tin lớn. "
             "Không cần PCA bắt buộc cho EDA; ưu tiên tính giải thích và chọn theo nhóm.")
    selected=["elo_diff","diff_ppg","diff_points5","diff_goals_for5","diff_goals_against5","diff_shots_on_target5",
              "diff_xg5","diff_xga5","h2h_home_points","h2h_home_goal_diff","diff_possession5",
              "diff_big_chances5","diff_box_touches5","diff_xgot5","odds_home_prob","home_rest_days"]
    cat=catalog.set_index("feature").loc[selected]
    report.table(pd.DataFrame({
        "Biến":cat.index,"Thiếu":cat.missing_pct.map(lambda x:vfmt(x,1)+"%"),
        "Quyết định đề xuất":cat.decision_vi,
    }))
    report.link("Danh mục đầy đủ 87 đặc trưng: định nghĩa, nguồn, thời điểm có, thiếu, tương quan và quyết định","feature_candidates.csv")
    report.p("Tương quan chỉ hỗ trợ khám phá. Các quyết định là thứ tự kiểm định, không phải danh sách đã được chứng minh có ích. "
             "Giữ hai kịch bản: chỉ dùng dữ liệu bóng đá và có thêm odds đúng thời điểm. "
             "Không dùng thiếu dữ liệu của một thời kỳ như đường tắt nhận diện nhãn.")
    report.image("16_temporal_stability.png","Hình 16. Hướng liên hệ ổn định qua mùa chưa chứng minh dự báo ngoài mẫu. "
                 "Mốc ≥100 quan sát tránh diễn giải hệ số từ rất ít trận.")
    st=stable.groupby("feature").agg(seasons=("season","nunique"),positive=("rho_H",lambda s:int(s.gt(0).sum())),
                                    minimum=("rho_H","min"),maximum=("rho_H","max"))
    report.table(pd.DataFrame({
        "Biến":st.index,"Mùa đủ điều kiện":st.seasons,"Số mùa tương quan dương":st.positive,
        "Khoảng Spearman":[f"[{vfmt(a,3)}; {vfmt(b,3)}]" for a,b in zip(st.minimum,st.maximum)],
    }))
    report.image("14_odds_reliability.png","Hình 14. Kiểm tra mô tả xác suất từ odds trên snapshot, không phải kết quả backtest hoặc hiệu quả tài chính. "
                 "Không vẽ Elo expectation như P(H).")
    report.p("Các odds được dùng để tạo features là Avg với B365 dự phòng, chuẩn hóa nghịch đảo; "
             "không phải tự động là closing odds. Timestamp thu thập chưa được cung cấp, nên độ sẵn có ở mốc dự đoán trước trận một ngày chưa được xác nhận.")

    report.section("12. Giả thuyết cần kiểm tra bằng mô hình")
    report.table(hypotheses[["id","hypothesis_vi","comparison_vi"]].rename(columns={
        "id":"Mã","hypothesis_vi":"Giả thuyết","comparison_vi":"So sánh đề xuất"}))
    report.link("Chi tiết giả thuyết, tập dữ liệu, tiêu chí và lưu ý","hypotheses.csv")
    report.p("Không giả thuyết nào được chấp nhận chỉ từ EDA. Mô hình nền nên gồm tần suất nhãn học từ train, "
             "sau đó một mô hình H/D/A đơn giản với Elo. Khi dự đoán tỷ số, kiểm định riêng Poisson/biến thể đếm "
             "dựa trên quá khứ, độ phân tán và phụ thuộc bàn nhà–khách; hình đối chiếu Poisson chưa phải mô hình dự báo.")

    report.section("13. Kế hoạch đánh giá và giới hạn diễn giải")
    report.bullets([
        "Chốt mục tiêu: dự đoán H/D/A hay tỷ số; chốt mốc trước giờ bóng lăn hoặc trước một ngày. Chỉ dùng các trường đã sẵn có ở mốc đó.",
        "Đánh giá mở rộng theo thời gian: train bằng các mùa trước, validation bằng mùa tiếp theo; các trận cùng ngày không tách hai phía ranh giới.",
        "Để so nhóm biến cơ bản/xG/FotMob/H2H/odds, dùng cùng trận và cùng fold. Báo cáo riêng mẫu dài hạn và mẫu giàu thống kê gần đây.",
        "Điền thiếu, chuẩn hóa, chọn biến, PCA nếu có và hiệu chỉnh xác suất chỉ được học trong train của từng fold. Không dùng kết quả test để chọn biến.",
        "Thước đo chính cho H/D/A là log loss đa lớp; bổ sung Brier, macro-F1, recall lớp hòa, confusion matrix và hiệu chỉnh xác suất. Báo cáo biến động qua mùa.",
        "Giữ một tập kiểm tra khóa sau khi chốt đặc trưng và quy trình. Vì EDA này đã xem toàn snapshot, một mùa đã được xem không còn hoàn toàn chưa đụng tới; ưu tiên kiểm chứng xác nhận bằng snapshot tương lai chưa xem.",
        "Kiểm tra nhạy cảm với hai mùa thiếu, đội trở lại EPL, cửa sổ ít hơn 5, H2H ít trận, định nghĩa nguồn xG và khoảng trống lịch sử.",
        "Mọi liên hệ trong báo cáo là mô tả, chưa suy ra nhân quả. Các đội lặp lại và nhiều phép so sánh làm các khoảng đơn giản không đủ cho suy luận xác nhận.",
    ])
    report.p("Mức 'đã xử lý' và 108 kiểm tra QA của pipeline nguồn không đồng nghĩa dữ liệu không còn vấn đề cho mọi bài toán. "
             "Kiểm toán mới tập trung thêm vào mục tiêu EDA và thời điểm dự đoán. Không huấn luyện mô hình dự đoán trong lần bàn giao này.")
    report.link("Tham khảo phương pháp: tránh rò rỉ dữ liệu (scikit-learn)","https://scikit-learn.org/stable/common_pitfalls.html#data-leakage")
    report.link("Tham khảo phương pháp: đánh giá theo thời gian (scikit-learn)","https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-of-time-series-data")

    report.section("14. Bàn giao và tái lập")
    report.bullets([
        "report.html: báo cáo tự chứa toàn bộ 16 biểu đồ, mở ngoại tuyến bằng trình duyệt.",
        "report.md và figures/: báo cáo Markdown cùng ảnh PNG có thể đưa vào tài liệu hoặc slide.",
        "feature_candidates.csv: danh mục 87 biến và quyết định kiểm định đề xuất.",
        "hypotheses.csv: 9 giả thuyết cùng phép so sánh và tiêu chí.",
        "data_quality_checks.csv và data_issues.csv: kết quả kiểm tra và dòng được gắn cờ; đây là sản phẩm kiểm toán, không phải dữ liệu nguồn đã sửa.",
        "analysis_metadata.json: thời điểm tạo, phiên bản thư viện, SHA256 của hai nguồn và chi tiết kiểm toán.",
    ])
    report.p("Nguồn: clean/matches.csv và clean/features.csv, đã tải từ thư mục clean của kho "
             "Toibinguz/epl-premier-league-dataset trên Hugging Face. Định nghĩa lịch sử được đối chiếu mã "
             "origin/Data_itegration tại commit eaef4cd, thư mục Data integration and preprocessing. "
             "Mã EDA chỉ đọc nguồn và ghi trong reports/eda_matches_features; dấu vân tay được so trước và sau chạy.")
    report.table(pd.DataFrame([
        [name,item["path"],item["sha256"]] for name,item in meta["inputs"].items()
    ],columns=["Nguồn","Đường dẫn","SHA256"]))
    report.p("Lệnh tái tạo từ thư mục dự án (cần pandas, numpy, matplotlib và scipy):")
    report.code("python reports/eda_matches_features/run_eda.py")
    report.write()


if __name__=="__main__":
    main()
