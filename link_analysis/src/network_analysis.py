"""Bước 2 – Phân tích mạng lưới đối đầu Premier League.

  * Đồ thị "ưu thế" (có hướng): bên thua/hòa "bầu" cho bên thắng theo phần điểm; PageRank = sức mạnh mạng lưới.
  * Đồ thị "số lần gặp" (vô hướng): trọng số = số trận giữa hai CLB; dùng cho cộng đồng (Louvain) và cặp đáng chú ý.

Đầu ra (thư mục --out): node_metrics_by_season.csv, node_metrics_alltime.csv, edges_alltime.csv, notable_pairs.csv,
validation_by_season.csv, network_summary.json; 4 hình PNG (1, 1b, 2, 3) vào thư mục --figures, vẽ bằng figures.py.

Chạy:  python src/network_analysis.py --matches matches.csv --out results --figures figures
"""
import argparse
import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import figures as fg
from build_graph_features import MARGIN_CAP, PR_ALPHA, pagerank

EXAMPLE_SEASON = "2015/16"   # mùa minh họa: đội vô địch không đứng đầu PageRank


def share(hg, ag):
    return 0.5 + 0.5 * np.clip(hg - ag, -MARGIN_CAP, MARGIN_CAP) / MARGIN_CAP


def dominance_pagerank(df, teams):
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    h, a = df.home_team.map(ix).values, df.away_team.map(ix).values
    s = share(df.home_goals.values.astype(float), df.away_goals.values.astype(float))
    V = np.zeros((n, n))
    np.add.at(V, (a, h), s)
    np.add.at(V, (h, a), 1 - s)
    active = np.where(V.sum(1) + V.sum(0) > 0)[0]
    return dict(zip(teams, pagerank(V, active, PR_ALPHA))), V


def season_table(d):
    rows = []
    for _, r in d.iterrows():
        rows.append((r.home_team, 3 if r.home_goals > r.away_goals else int(r.home_goals == r.away_goals), r.home_goals - r.away_goals))
        rows.append((r.away_team, 3 if r.away_goals > r.home_goals else int(r.home_goals == r.away_goals), r.away_goals - r.home_goals))
    t = pd.DataFrame(rows, columns=["team", "pts", "gd"]).groupby("team").agg(pts=("pts", "sum"), gd=("gd", "sum"), played=("pts", "size"))
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", required=True)
    ap.add_argument("--out", default="results")
    ap.add_argument("--figures", default="figures")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    fig_dir = Path(a.figures)
    fig_dir.mkdir(exist_ok=True)
    m = pd.read_csv(a.matches)
    m = m[m.status == "played"].copy()
    teams = sorted(set(m.home_team) | set(m.away_team))
    seasons = sorted(m.season.unique())
    # ma CLB chuan cua khau Tich hop du lieu (home_club_id/away_club_id) de cac khau khac ghep truc tiep
    club_id = {}
    if {"home_club_id", "away_club_id"} <= set(m.columns):
        club_id = dict(zip(m.home_team, m.home_club_id)) | dict(zip(m.away_team, m.away_club_id))

    def with_id(df, team_col, id_col):
        df.insert(df.columns.get_loc(team_col) + 1, id_col, df[team_col].map(club_id))
        return df

    # ---------------- 1. PageRank theo tung mua + doi chieu bang xep hang
    rows, val = [], []
    for s in seasons:
        d = m[m.season == s]
        pr, V = dominance_pagerank(d, teams)
        tab = season_table(d)
        tab["pagerank"] = pd.Series(pr)
        tab = tab.dropna(subset=["pagerank"])
        tab["rank_points"] = tab[["pts", "gd"]].apply(tuple, axis=1).rank(ascending=False, method="min")
        tab["rank_pagerank"] = tab.pagerank.rank(ascending=False, method="min")
        tab["season"] = s
        rows.append(tab.reset_index())
        # chi so hop le chi voi mua du tran (bo mua thieu tran)
        val.append({"season": s, "n_matches": len(d), "spearman_pagerank_vs_points":
                    spearmanr(tab.pagerank, tab.pts + tab.gd / 1000).statistic})
    by_season = pd.concat(rows, ignore_index=True)
    by_season["rank_gap"] = by_season.rank_points - by_season.rank_pagerank  # >0: PageRank xep cao hon bang diem
    with_id(by_season, "team", "club_id").round(4).to_csv(out / "node_metrics_by_season.csv", index=False)
    val = pd.DataFrame(val)
    val.round(4).to_csv(out / "validation_by_season.csv", index=False)

    # ---------------- 2. Mang luoi toan thoi gian
    pr_all, V_all = dominance_pagerank(m, teams)
    G = nx.Graph()
    G.add_nodes_from(teams)
    cnt = m.assign(a=np.where(m.home_team < m.away_team, m.home_team, m.away_team),
                   b=np.where(m.home_team < m.away_team, m.away_team, m.home_team)).groupby(["a", "b"]).size()
    for (x, y), w in cnt.items():
        G.add_edge(x, y, weight=int(w))
    comms = nx.community.louvain_communities(G, weight="weight", resolution=1.0, seed=7)
    comms = sorted(comms, key=lambda c: -len(c))
    cid = {t: i for i, c in enumerate(comms) for t in c}

    full_seasons = set(val[val.n_matches >= 335].season)
    n_seasons = by_season.groupby("team").size()
    first = by_season.groupby("team").season.min()
    last = by_season.groupby("team").season.max()
    nodes = pd.DataFrame({
        "team": teams,
        "community": [cid[t] for t in teams],
        "pagerank_alltime": [pr_all[t] for t in teams],
        "degree_opponents": [G.degree(t) for t in teams],
        "strength_matches": [G.degree(t, weight="weight") for t in teams],
        "seasons_in_league": [int(n_seasons.get(t, 0)) for t in teams],
        "first_season": [first.get(t) for t in teams], "last_season": [last.get(t) for t in teams],
        # TB PageRank cac mua du tran (bo mua dang dien ra): phan anh suc manh, khong bi thoi phong boi so mua choi
        "mean_season_pagerank": [by_season[(by_season.team == t) & by_season.season.isin(full_seasons)].pagerank.mean() for t in teams],
    }).sort_values("pagerank_alltime", ascending=False)
    with_id(nodes, "team", "club_id").round(4).to_csv(out / "node_metrics_alltime.csv", index=False)
    edges = pd.DataFrame([{"team_a": x, "team_b": y, "meetings": d["weight"]} for x, y, d in G.edges(data=True)])
    edges = with_id(with_id(edges, "team_b", "club_id_b"), "team_a", "club_id_a")
    edges.sort_values("meetings", ascending=False).to_csv(out / "edges_alltime.csv", index=False)

    # ---------------- 3. Cap doi dau dang chu y
    recs = []
    for (x, y), w in cnt.items():
        d = m[((m.home_team == x) & (m.away_team == y)) | ((m.home_team == y) & (m.away_team == x))]
        px = sum(3 if (r.home_team == x and r.home_goals > r.away_goals) or (r.away_team == x and r.away_goals > r.home_goals)
                 else int(r.home_goals == r.away_goals) for r in d.itertuples())
        py = sum(3 if (r.home_team == y and r.home_goals > r.away_goals) or (r.away_team == y and r.away_goals > r.home_goals)
                 else int(r.home_goals == r.away_goals) for r in d.itertuples())
        wins_x = sum((r.home_team == x and r.home_goals > r.away_goals) or (r.away_team == x and r.away_goals > r.home_goals) for r in d.itertuples())
        wins_y = sum((r.home_team == y and r.home_goals > r.away_goals) or (r.away_team == y and r.away_goals > r.home_goals) for r in d.itertuples())
        recs.append({"team_a": x, "team_b": y, "meetings": int(w), "wins_a": wins_x, "wins_b": wins_y,
                     "draws": int(w) - wins_x - wins_y, "points_share_a": px / max(px + py, 1)})
    pairs = pd.DataFrame(recs)
    pairs["lopsidedness"] = (pairs.points_share_a - 0.5).abs()
    pairs = with_id(with_id(pairs, "team_b", "club_id_b"), "team_a", "club_id_a")
    pairs.round(4).to_csv(out / "notable_pairs.csv", index=False)

    summary = {
        "n_teams": len(teams), "n_matches": len(m), "n_edges_undirected": G.number_of_edges(),
        "density": round(nx.density(G), 4),
        "communities": [{"id": i, "size": len(c), "mean_seasons": round(float(nodes[nodes.team.isin(c)].seasons_in_league.mean()), 1),
                         "members": sorted(c, key=lambda t: -pr_all[t])} for i, c in enumerate(comms)],
        "modularity": round(nx.community.modularity(G, comms, weight="weight"), 4),
        "spearman_mean": round(val[val.n_matches == 380].spearman_pagerank_vs_points.mean(), 4),
        "spearman_min": round(val[val.n_matches == 380].spearman_pagerank_vs_points.min(), 4),
    }
    json.dump(summary, open(out / "network_summary.json", "w"), ensure_ascii=False, indent=1)

    make_figures(G, comms, nodes, pairs, by_season, val, fig_dir, m)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ hình (vẽ bằng src/figures.py)
def make_figures(G, comms, nodes, pairs, by_season, val, out, m):
    info = nodes.set_index("team")
    strength = info.mean_season_pagerank.fillna(info.mean_season_pagerank.min())
    fg.network(G, comms, strength, info.seasons_in_league, pairs, highlight=False, path=out / "1_mang_luoi.png")
    fg.network(G, comms, strength, info.seasons_in_league, pairs, highlight=True, path=out / "1b_mang_luoi_noi_bat.png")

    full = val[val.n_matches == 380].rename(columns={"spearman_pagerank_vs_points": "spearman"})
    bs = by_season[by_season.season.isin(full.season)]
    champ_rank = bs[bs.rank_points == 1].set_index("season").rank_pagerank
    low = full.loc[full.spearman.idxmin()]
    d = bs[bs.season == low.season]
    gaps = d[d.rank_gap.abs() == d.rank_gap.abs().max()].sort_values("rank_points")
    note = f"{low.season}: {fg.vn(low.spearman, 3)}, thấp nhất\n" + "\n".join(
        f"{g.team}: hạng {int(g.rank_points)} theo điểm, hạng {int(g.rank_pagerank)} theo PageRank" for g in gaps.itertuples())
    fg.spearman_by_season(full[["season", "spearman"]], champ_rank, note_text=note, path=out / "2_pagerank_vs_bxh.png")

    ex = bs[bs.season == EXAMPLE_SEASON].set_index("team")
    champ = ex.rank_points.idxmin()
    top_pr = ex.rank_pagerank.idxmin()
    d = m[m.season == EXAMPLE_SEASON]

    def big_wins(t):  # số trận thắng cách biệt từ MARGIN_CAP bàn (phần điểm tối đa)
        return int((((d.home_team == t) & (d.home_goals - d.away_goals >= MARGIN_CAP))
                    | ((d.away_team == t) & (d.away_goals - d.home_goals >= MARGIN_CAP))).sum())

    focus = {champ: f"vô địch, {int(ex.pts[champ])} điểm, hạng {int(ex.rank_pagerank[champ])} PageRank,\n"
                    f"thắng cách biệt từ {MARGIN_CAP} bàn {big_wins(champ)} trận"}
    if top_pr != champ:
        focus[top_pr] = (f"hạng {int(ex.rank_points[top_pr])} theo điểm ({int(ex.pts[top_pr])}) nhưng đứng đầu PageRank,\n"
                         f"thắng cách biệt từ {MARGIN_CAP} bàn {big_wins(top_pr)} trận")
    fg.season_example(ex[["pts", "pagerank"]], EXAMPLE_SEASON, focus, path=out / "3_vi_du_mua_2015_16.png")


if __name__ == "__main__":
    main()
