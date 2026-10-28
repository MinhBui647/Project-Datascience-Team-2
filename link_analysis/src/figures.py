"""Các hình của Link Analysis, dùng chung cho script (network_analysis.py, evaluate.py) và notebook.

Quy ước màu, giống nhau ở mọi hình:
  xanh dương  = dữ liệu chính         đỏ   = điểm cần chú ý
  xám nhạt    = bối cảnh, làm nền      cam / xanh lá = nhóm B / nhóm C của Louvain (chỉ ở hình mạng lưới)
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MPath

BLUE, ORANGE, GREEN, RED = "#2a78d6", "#e8833a", "#2ca58d", "#d1453b"
GROUP_COLORS = [BLUE, ORANGE, GREEN]
INK, INK2, MUTED, EDGE, BG = "#1d1d1b", "#6b6a66", "#cfcdc6", "#9e9c95", "#fcfcfb"
FAINT = "#c9c6bd"
BOX = dict(boxstyle="round,pad=0.5", fc="white", ec="#dedcd5", lw=0.8)
ARROW = dict(arrowstyle="-", color="#8a8882", lw=0.8, shrinkA=0, shrinkB=4)


def vn(x, nd=2):
    """Số kiểu Việt Nam: dấu phẩy thập phân."""
    return f"{x:.{nd}f}".replace(".", ",")


def _clean(ax, grid="y"):
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}".replace(".", ",")))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}".replace(".", ",")))
    ax.set_facecolor(BG)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=9)
    if grid:
        ax.grid(axis=grid, color="#ebe9e4", lw=0.8)
    ax.set_axisbelow(True)


def _title(fig, ax, title, sub=None):
    lines = sub.count("\n") + 1 if sub else 0
    ax.set_title(title, loc="left", fontsize=13.5, fontweight="bold", color=INK, pad=10 + 14 * lines)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=9.5, color=INK2, va="bottom")


def _save(fig, path):
    if path:
        fig.savefig(path, dpi=170, bbox_inches="tight", pad_inches=0.3, facecolor=BG)
        plt.close(fig)
    return fig


# --------------------------------------------------------------------------- 1. mạng lưới
def network(G, comms, strength, n_seasons, pairs, highlight=False, top_pct=8, period="2000/01 – 12/10/2026", path=None):
    """Mạng lưới toàn thời gian trên bố cục vòng tròn.

    G: nx.Graph (trọng số = số lần gặp); comms: cộng đồng Louvain (list các set, lớn trước);
    strength: Series PageRank TB theo mùa theo CLB; n_seasons: Series số mùa đá Premier League;
    pairs: bảng notable_pairs.csv. highlight=False: bản thường; True: bản làm nổi bật điểm chính.
    """
    import networkx as nx

    cid = {t: i for i, c in enumerate(comms) for t in c}
    names = []
    for i, c in enumerate(comms):
        m = n_seasons[list(c)].mean()
        kind = "trụ hạng lâu" if m >= 12 else ("lên xuống hạng" if m >= 5 else "chỉ đá vài mùa")
        names.append(f"Nhóm {'ABC'[i]} (Louvain): {kind} – {len(c)} CLB, trung bình {m:.0f} mùa ở Premier League")

    # vị trí: theo nhóm rồi theo sức mạnh, chừa khoảng trống giữa các nhóm
    order = sorted(G.nodes, key=lambda t: (cid[t], -strength[t]))
    slots, k = {}, 0.0
    for i, t in enumerate(order):
        if i and cid[t] != cid[order[i - 1]]:
            k += 1.5
        slots[t], k = k, k + 1
    total = k + 1.5
    ang = {t: np.pi / 2 - 2 * np.pi * slots[t] / total for t in order}
    pos = {t: np.array([np.cos(ang[t]), np.sin(ang[t])]) for t in order}
    lo, hi = strength.min(), strength.max()

    def size(v):
        return 30 + 650 * (max(v - lo, 0) / (hi - lo)) ** 1.3

    def curve(u, v, r=0.9):
        p0, p2 = pos[u] * r, pos[v] * r
        return MPath([p0, (p0 + p2) / 2 * 0.25, p2], [MPath.MOVETO, MPath.CURVE3, MPath.CURVE3])

    ws = np.array([d["weight"] for _, _, d in G.edges(data=True)])
    thr = np.percentile(ws, 100 - top_pct)
    strong = sorted([(u, v, d["weight"]) for u, v, d in G.edges(data=True) if d["weight"] >= thr], key=lambda e: e[2])
    min_w = int(min(e[2] for e in strong))
    big6 = list(strength.sort_values(ascending=False).index[:6])
    lop = pairs[pairs.meetings >= 20].sort_values("lopsidedness", ascending=False).iloc[0]
    weak = lop.team_a if lop.points_share_a < 0.5 else lop.team_b
    other = lop.team_b if weak == lop.team_a else lop.team_a

    def color(t):
        if not highlight:
            return GROUP_COLORS[cid[t] % 3]
        if t in big6:
            return BLUE
        if cid[t] == 2:
            return GREEN
        return RED if t == weak else MUTED

    top, bot = (2.0, -2.6) if highlight else (1.8, -2.25)
    leg_y = -1.68 if highlight else -1.62
    fig, ax = plt.subplots(figsize=(11, 11 * (top - bot) / 3.3), facecolor=BG)
    ax.set_facecolor(BG)

    # mọi cặp đã gặp đều có một đường mảnh: không có đường = chưa gặp lần nào
    for u, v, d in G.edges(data=True):
        if d["weight"] < thr:
            ax.add_patch(PathPatch(curve(u, v), fc="none", ec=FAINT, lw=0.3, alpha=0.3 if not highlight else 0.22, zorder=0))
    for u, v, w in strong:
        t = (w - thr) / (ws.max() - thr + 1e-9)
        key = highlight and u in big6 and v in big6
        col, alpha = ("#7d7b75", 0.45 + 0.4 * t) if not highlight else ((BLUE, 0.7) if key else (MUTED, 0.55))
        ax.add_patch(PathPatch(curve(u, v), fc="none", ec=col, lw=0.8 + 2.2 * t, alpha=alpha, zorder=2 if key else 1))
    if highlight:
        ax.add_patch(PathPatch(curve(lop.team_a, lop.team_b), fc="none", ec=RED, lw=2.4, ls=(0, (4, 2)), zorder=3))

    for t in order:
        col = color(t)
        on = highlight and col != MUTED
        ax.scatter(*pos[t], s=size(strength[t]), color=col, edgecolor=BG, linewidth=1.5, zorder=4)
        deg = np.degrees(ang[t])
        left = pos[t][0] < -1e-9
        ax.text(*(pos[t] * 1.09), t, fontsize=9 if on else 8.5, fontweight="bold" if on else "normal",
                color=INK if (on or not highlight) else "#a9a7a0", va="center", ha="right" if left else "left",
                rotation=deg + 180 if left else deg, rotation_mode="anchor")

    n = G.number_of_nodes()
    n_pairs = n * (n - 1) // 2
    never = n_pairs - G.number_of_edges()
    sub = (f"{n} CLB · {G.number_of_edges()} trên {n_pairs:,} cặp CLB đã gặp nhau ít nhất 1 lần (mật độ {nx.density(G):.2f}) "
           f"· modularity {nx.community.modularity(G, comms, weight='weight'):.2f}")
    sub = sub.replace(",", "#").replace(".", ",").replace("#", ".")
    ax.text(-1.62, top - 0.08, "Những điểm đáng chú ý trong mạng lưới đối đầu" if highlight
            else f"Mạng lưới đối đầu Premier League, {period}", fontsize=16, fontweight="bold", color=INK)
    ax.text(-1.62, top - 0.17, sub, fontsize=10, color=INK2)

    if highlight:
        ax.text(-1.62, -1.45, "Mạng gần như đầy đủ, modularity rất thấp\n(thường từ 0,3 mới coi là có cụm): vị trí trong mạng\n"
                "ít phân biệt các đội; thông tin chính nằm ở\nsức mạnh (PageRank), vốn trùng với Elo.",
                fontsize=9.5, color=INK, va="center", bbox=BOX)
        c6 = np.mean([pos[t] for t in big6], axis=0)
        m6 = int(round(np.mean([G[u][v]["weight"] for i, u in enumerate(big6) for v in big6[i + 1:]])))
        ax.annotate(f"6 CLB có PageRank trung bình cao nhất,\nđá Premier League gần như mọi mùa:\nmỗi cặp gặp nhau khoảng {m6} trận\nnên tạo thành các đường dày nhất",
                    xy=c6 / np.linalg.norm(c6) * 1.47, xytext=(0.45, 1.62), fontsize=9.5, color=INK, bbox=BOX,
                    arrowprops=ARROW, va="center")
        if len(comms) > 2:
            c3 = np.mean([pos[t] for t in comms[2]], axis=0)
            yrs = n_seasons[list(comms[2])]
            ax.annotate(f"Nhóm C không phải \"cụm đối thủ\": {len(comms[2])} CLB chỉ có\n{yrs.min()}–{yrs.max()} mùa ở Premier League, cùng đá mùa 2000/01",
                        xy=c3 / np.linalg.norm(c3) * 1.42, xytext=(-1.62, 1.62), fontsize=9.5, color=INK, bbox=BOX,
                        arrowprops=ARROW, va="center")
        v = curve(lop.team_a, lop.team_b).vertices
        mid = 0.25 * v[0] + 0.5 * v[1] + 0.25 * v[2]
        share = min(lop.points_share_a, 1 - lop.points_share_a)
        w_weak, w_other = (lop.wins_a, lop.wins_b) if weak == lop.team_a else (lop.wins_b, lop.wins_a)
        ax.annotate(f"Cặp chênh lệch nhất trong các cặp gặp ≥ 20 trận:\n{weak} – {other}, {int(lop.meetings)} trận: {weak} thắng {int(w_weak)},\n"
                    f"hòa {int(lop.draws)}, thua {int(w_other)} (giành {share * 100:.0f}% tổng điểm của cặp)",
                    xy=mid, xytext=(0.62, -1.45), fontsize=9.5, color="#a8352c", bbox=BOX, arrowprops=ARROW, va="center")
        h1 = [plt.Line2D([], [], marker="o", ls="", ms=9, color=BLUE, label="6 CLB có PageRank trung bình cao nhất"),
              plt.Line2D([], [], color=BLUE, lw=2, label="Đường nối giữa 6 CLB này"),
              plt.Line2D([], [], marker="o", ls="", ms=9, color=GREEN, label="Nhóm C của Louvain"),
              plt.Line2D([], [], color=RED, lw=2.4, ls=(0, (4, 2)), label="Cặp chênh lệch nhất (chấm đỏ: đội lép vế)"),
              plt.Line2D([], [], marker="o", ls="", ms=9, color=MUTED, label="Các CLB còn lại")]
    else:
        h1 = [plt.Line2D([], [], marker="o", ls="", ms=9, color=GROUP_COLORS[i % 3], label=names[i]) for i in range(len(comms))]
    heavy_lab = (f"Đường dày: {len(strong)} cặp gặp nhau từ {min_w} trận trở lên (càng dày càng gặp nhiều)" if not highlight
                 else f"Đường xám dày: các cặp khác gặp nhau từ {min_w} trận trở lên")
    h1 += [plt.Line2D([], [], color="#7d7b75" if not highlight else MUTED, lw=2.2, label=heavy_lab),
           plt.Line2D([], [], color=FAINT, lw=0.8, label=f"Đường mảnh: {G.number_of_edges() - len(strong)} cặp gặp nhau từ 1 đến {min_w - 1} trận"),
           plt.Line2D([], [], color="none", label=f"Không có đường: {never} cặp chưa gặp lần nào (phần lớn chưa từng cùng đá một mùa)")]
    anchor_y = (leg_y - bot) / (top - bot)
    leg1 = ax.legend(handles=h1, loc="upper left", bbox_to_anchor=(0.0, anchor_y), frameon=False, fontsize=9,
                     labelcolor=INK2, labelspacing=0.9, title="Màu và đường nối", title_fontsize=9.5, alignment="left")
    ax.add_artist(leg1)
    h2 = [plt.Line2D([], [], marker="o", ls="", color="#b9b7b0", ms=np.sqrt(size(v_)), label=vn(v_, 1)) for v_ in (0.8, 1.0, 1.2)]
    ax.legend(handles=h2, loc="upper left", bbox_to_anchor=(0.70, anchor_y), frameon=False,
              fontsize=9, labelcolor=INK2, labelspacing=1.6, borderpad=0.8, handletextpad=1.2,
              title="Kích thước chấm: PageRank trung bình theo mùa\n(1 = mức trung bình của giải)", title_fontsize=9.5, alignment="left")

    ax.set_xlim(-1.65, 1.65)
    ax.set_ylim(bot, top)
    ax.set_aspect("equal")
    ax.set_axis_off()
    return _save(fig, path)


# --------------------------------------------------------------------------- 2. PageRank so với bảng xếp hạng theo mùa
def spearman_by_season(val, champ_rank, note_season=None, note_text=None, path=None):
    """val: cột season, spearman (chỉ các mùa đủ 380 trận); champ_rank: Series hạng PageRank của đội vô địch theo mùa."""
    v = val.reset_index(drop=True)
    x = np.array([int(s[:4]) for s in v.season])          # trục hoành theo năm thật: mùa bị bỏ hiện thành khoảng trống
    mean = v.spearman.mean()
    low = int(v.spearman.idxmin())
    fig, ax = plt.subplots(figsize=(10.5, 4.4), facecolor=BG)
    _clean(ax)
    ax.axhline(mean, color=EDGE, lw=1, ls=(0, (3, 3)), zorder=1)
    ax.text(x.max() + 0.4, mean + 0.004, f"trung bình {vn(mean, 3)}", ha="right", va="bottom", fontsize=9, color=INK2)
    cols = [RED if i == low else BLUE for i in range(len(v))]
    ax.scatter(x, v.spearman, s=46, color=cols, edgecolor=BG, linewidth=1.2, zorder=3)
    ax.annotate(note_text or f"{v.season[low]}: {vn(v.spearman[low], 3)}, thấp nhất", xy=(x[low], v.spearman[low]),
                xytext=(x[low] + 1.6, v.spearman[low] + 0.02), fontsize=9, color="#a8352c", bbox=BOX, arrowprops=ARROW, va="center")
    n1, n3 = int((champ_rank == 1).sum()), int((champ_rank <= 3).sum())
    ax.text(0.99, 0.04, (f"Đội vô địch xếp hạng 1 theo PageRank ở {n1}/{len(champ_rank)} mùa"
             + (",\nvà luôn nằm trong top 3" if n3 == len(champ_rank) else f",\ntrong top 3 ở {n3}/{len(champ_rank)} mùa")),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=9, color=INK, bbox=BOX)
    ticks = list(range(x.min(), x.max() + 1, 2))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t}/{(t + 1) % 100:02d}" for t in ticks], fontsize=8.5, rotation=45, ha="right")
    gap = sorted(set(range(x.min(), x.max() + 1)) - set(x))
    if gap:
        ax.axvspan(gap[0] - 0.5, gap[-1] + 0.5, color="#efede8", zorder=0)
        ax.text((gap[0] + gap[-1]) / 2, 0.985, "bỏ: mùa\nthiếu trận", ha="center", va="top", fontsize=8, color=INK2)
    ax.set_ylim(0.76, 1.0)
    ax.set_ylabel("Tương quan hạng Spearman", fontsize=9, color=INK2)
    _title(fig, ax, "PageRank mùa giải khớp bảng xếp hạng cuối mùa",
           "Mỗi chấm là một mùa đủ 380 trận (bỏ 2003/04, 2004/05 vì dữ liệu chỉ có 335 trận, và mùa 2026/27 đang đá). 1 = cùng thứ tự hoàn toàn.")
    return _save(fig, path)


# --------------------------------------------------------------------------- 3. ví dụ một mùa
def season_example(tab, season, focus, path=None):
    """tab: index = CLB, cột pts, pagerank. focus: dict {CLB: chú thích} để làm nổi bật."""
    fig, ax = plt.subplots(figsize=(8.5, 5.6), facecolor=BG)
    _clean(ax, grid="both")
    others = tab.drop(index=list(focus))
    ax.scatter(others.pts, others.pagerank, s=36, color=MUTED, zorder=2)
    offs = [(0, -230), (-60, 4), (-110, -45)]          # vị trí hộp chú thích (lệch so với chấm, pixel)
    for (t, text), off in zip(focus.items(), offs):
        r = tab.loc[t]
        ax.scatter(r.pts, r.pagerank, s=75, color=RED if "vô địch" in text else BLUE, edgecolor=BG, linewidth=1.5, zorder=4)
        ax.annotate(f"{t}: {text}", (r.pts, r.pagerank), xytext=off, textcoords="offset points", fontsize=9, color=INK,
                    bbox=BOX, arrowprops=ARROW, ha="right", va="center")
    ax.set_xlabel("Điểm cuối mùa", fontsize=9.5, color=INK2)
    ax.set_ylabel("PageRank mùa giải (trung bình các đội = 1)", fontsize=9.5, color=INK2)
    _title(fig, ax, f"Mùa {season}: PageRank khác bảng điểm ở nhóm đầu bảng",
           "Mỗi chấm là một CLB. Bảng điểm chỉ tính thắng/hòa/thua; PageRank tính thêm cách biệt tỷ số (tối đa 3 bàn)\n"
           "và sức mạnh đối thủ. PageRank ở đây chỉ dùng 380 trận của mùa này.")
    return _save(fig, path)


# --------------------------------------------------------------------------- 4. kết quả đánh giá
def evaluation(rows, ref_label, path=None):
    """rows: DataFrame cột label, delta, lo, hi, kind ('graph' = cấu hình thêm đồ thị, 'ref' = mốc so sánh khác)."""
    r = rows.iloc[::-1].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(10, 0.55 * len(r) + 1.9), facecolor=BG)
    _clean(ax, grid="x")
    ax.axvline(0, color=INK2, lw=1, zorder=1)
    for i, row in r.iterrows():
        col = BLUE if row.kind == "graph" else EDGE
        ax.plot([row.lo, row.hi], [i, i], color=col, lw=2.2, solid_capstyle="round", zorder=2)
        ax.scatter(row.delta, i, s=55, color=col, edgecolor=BG, linewidth=1.2, zorder=3)
        ax.text(row.delta, i + 0.2, f"{row.delta:+.4f}".replace(".", ","), va="bottom", ha="center", fontsize=8.5,
                color=INK if row.kind == "graph" else INK2, bbox=dict(fc=BG, ec="none", pad=1))
    n_ref = int((r.kind != "graph").sum())
    if 0 < n_ref < len(r):
        ax.axhline(n_ref - 0.5, color="#e2e0da", lw=1, zorder=0)
    ax.set_yticks(range(len(r)))
    ax.set_yticklabels(r.label, fontsize=9.5)
    for lab, row in zip(ax.get_yticklabels(), r.itertuples()):
        lab.set_color(INK if row.kind == "graph" else INK2)
        lab.set_fontweight("bold" if row.kind == "graph" else "normal")
    xmax = max(abs(r.lo).max(), abs(r.hi).max()) * 1.25
    ax.set_xlim(-xmax, xmax)
    ax.text(-xmax * 0.97, len(r) - 0.35, "← tốt hơn", fontsize=9, color=INK2, va="bottom")
    ax.text(xmax * 0.97, len(r) - 0.35, "kém hơn →", fontsize=9, color=INK2, va="bottom", ha="right")
    ax.set_ylim(-0.6, len(r) - 0.1)
    ax.set_xlabel(f"Chênh lệch log loss so với mô hình dùng \"{ref_label}\" của nhóm, cùng loại mô hình "
                  "(chấm: trung bình; vạch: khoảng tin cậy 95%, bootstrap theo cặp)", fontsize=9, color=INK2)
    _title(fig, ax, "Thêm đặc trưng đồ thị không cải thiện dự đoán",
           "Kiểm thử từng mùa 2010/11 – 2026/27 (6.140 trận), mỗi mùa chỉ học từ các mùa trước. "
           "Vạch cắt qua 0 hoặc nằm bên phải: không tốt hơn.")
    return _save(fig, path)
