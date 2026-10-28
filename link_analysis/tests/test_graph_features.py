"""Kiểm thử tự động cho phần Link Analysis.   Chạy từ thư mục link_analysis/:  python tests/test_graph_features.py   (hoặc pytest -q tests)
Cần matches.csv đặt trong link_analysis/.

Ba nhóm kiểm thử:
  1. Công thức: phần điểm của trận, PageRank trên đồ thị nhỏ có đáp án biết trước, đối chiếu với networkx.pagerank.
  2. Không rò rỉ dữ liệu: đảo ngẫu nhiên kết quả của MỌI trận từ ngày D trở đi, đặc trưng của ngày D phải không đổi.
  3. Toàn vẹn đầu ra: khóa match_id, số dòng, miền giá trị.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_graph_features import FEATURES, build, pagerank, share_of_home

MATCHES = ROOT / "matches.csv"


def test_share_of_home():
    assert share_of_home(0, 0) == 0.5
    assert abs(share_of_home(1, 0) - (0.5 + 0.5 / 3)) < 1e-12      # thắng 1-0 -> 0,667
    assert share_of_home(5, 0) == 1.0                                # chặn ở 3 bàn
    assert share_of_home(0, 4) == 0.0
    assert share_of_home(2, 0) + share_of_home(0, 2) == 1.0          # đối xứng


def test_pagerank_chain_and_mean():
    # A thắng B, B thắng C: bên thua "bầu" cho bên thắng -> V[B, A] = 1, V[C, B] = 1
    V = np.zeros((3, 3))
    V[1, 0] = 1
    V[2, 1] = 1
    pr = pagerank(V, np.array([0, 1, 2]))
    assert pr[0] > pr[1] > pr[2]
    assert abs(pr.mean() - 1.0) < 1e-9


def test_pagerank_symmetric_cycle():
    V = np.zeros((3, 3))
    V[0, 1] = V[1, 2] = V[2, 0] = 1
    pr = pagerank(V, np.array([0, 1, 2]))
    assert np.allclose(pr, 1.0)


def test_pagerank_matches_networkx():
    """Đối chiếu với networkx.pagerank trên đồ thị có trọng số ngẫu nhiên (có cả nút không có cạnh ra)."""
    import networkx as nx
    rng = np.random.default_rng(1)
    V = rng.random((12, 12)) * (rng.random((12, 12)) < 0.4)
    np.fill_diagonal(V, 0)
    V[3] = 0                                                    # nút kết (dangling)
    ours = pagerank(V, np.arange(12))
    G = nx.DiGraph()
    G.add_nodes_from(range(12))
    G.add_weighted_edges_from((i, j, V[i, j]) for i in range(12) for j in range(12) if V[i, j] > 0)
    ref = nx.pagerank(G, alpha=0.85, weight="weight", tol=1e-12, max_iter=1000)
    assert np.allclose(ours, np.array([ref[i] for i in range(12)]) * 12, atol=1e-8)


def _load():
    df = pd.read_csv(MATCHES)
    return df[df.status == "played"].reset_index(drop=True)


def test_future_results_do_not_change_features():
    """Đảo ngẫu nhiên kết quả của mọi trận có ngày >= D: đặc trưng của các trận ngày D phải y hệt."""
    played = _load()
    rng = np.random.default_rng(7)
    dates = np.sort(played.date.unique())
    for D in rng.choice(dates[len(dates) // 10:], 8, replace=False):
        targets = played[played.date == D]
        ref = build(played, targets)[FEATURES]
        shaken = played.copy()
        fut = shaken.date >= D
        shaken.loc[fut, "home_goals"] = rng.integers(0, 6, fut.sum())
        shaken.loc[fut, "away_goals"] = rng.integers(0, 6, fut.sum())
        got = build(shaken, targets)[FEATURES]
        assert np.allclose(ref.values.astype(float), got.values.astype(float), equal_nan=True), f"rò rỉ ở ngày {D}"


def test_output_file_integrity():
    out = pd.read_csv(ROOT / "results" / "graph_features_matches.csv")
    m = pd.read_csv(MATCHES)
    assert len(out) == len(m) and out.match_id.is_unique and set(out.match_id) == set(m.match_id)
    assert list(out[FEATURES].columns) == FEATURES
    assert out.gr_pagerank_home.dropna().between(0, 10).all()
    assert out.gr_h2h_share.between(-0.5, 0.5).all()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)
