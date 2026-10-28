"""Bước 1 – Dựng đồ thị đội bóng và tính đặc trưng đồ thị cho từng trận (khâu Link Analysis).

Đồ thị: nút = CLB; cạnh = trận đấu. Trận h (nhà) gặp a (khách) có "phần điểm" của đội nhà
    s = 0,5 + 0,5 × clip(hiệu số bàn thắng, −3, 3) / 3      (thắng 1-0 → 0,67; thắng từ 3 bàn → 1; hòa → 0,5)
và trọng số theo độ gần đây  w = 0,5 ^ (số ngày trước / HALF_LIFE_DAYS).

Không rò rỉ dữ liệu: đặc trưng của trận ngày D chỉ dùng các trận có ngày < D
(kiểm bằng --selftest và test_graph_features.py).

Đặc trưng (tiền tố gr_):
  gr_pagerank_home/away, gr_pagerank_log_ratio   PageRank trên đồ thị "thua → thắng" (cửa sổ WINDOW_DAYS)
  gr_common_opp_diff, gr_common_opp_n            kết quả gián tiếp qua các đối thủ chung
  gr_trans2_diff                                 ưu thế bắc cầu 2 bước (A thắng C, C thắng B) trừ chiều ngược lại
  gr_h2h_share, gr_h2h_gd, gr_h2h_weight         đối đầu có trọng số thời gian, co về trung tính khi ít trận

Chạy:
  python src/build_graph_features.py --dataset matches.csv --out results/graph_features_matches.csv --selftest
"""
import argparse

import numpy as np
import pandas as pd

HALF_LIFE_DAYS = 365      # PageRank / doi thu chung: tran cu 1 nam co trong so 0.5
WINDOW_DAYS = 1095        # cua so 3 nam cho do thi mang luoi
H2H_HALF_LIFE_DAYS = 1095
H2H_WINDOW_DAYS = 3650
H2H_SHRINK = 1.0          # tuong duong 1 tran "trung tinh" de tranh nhieu khi it lan gap
MARGIN_CAP = 3
PR_ALPHA = 0.85
KEY = ["season", "date", "home_team", "away_team"]
FEATURES = ["gr_pagerank_home", "gr_pagerank_away", "gr_pagerank_log_ratio", "gr_common_opp_diff",
            "gr_common_opp_n", "gr_trans2_diff", "gr_h2h_share", "gr_h2h_gd", "gr_h2h_weight"]


def share_of_home(hg, ag):
    return 0.5 + 0.5 * np.clip(hg - ag, -MARGIN_CAP, MARGIN_CAP) / MARGIN_CAP


def pagerank(V, active, alpha=PR_ALPHA, iters=200, tol=1e-10):
    """PageRank tren ma tran V (V[i, j] = muc do i 'bau' cho j). Tra ve vector chuan hoa co trung binh = 1."""
    k = len(active)
    sub = V[np.ix_(active, active)]
    out = sub.sum(1, keepdims=True)
    P = np.where(out > 0, sub / np.where(out > 0, out, 1), 1.0 / k)  # nut khong co canh ra -> nhay deu
    r = np.full(k, 1.0 / k)
    for _ in range(iters):
        new = alpha * (r @ P) + (1 - alpha) / k
        if np.abs(new - r).sum() < tol:
            r = new
            break
        r = new
    res = np.full(V.shape[0], np.nan)
    res[active] = r * k
    return res


def build(played, targets):
    """played: cac tran da da (co home_goals/away_goals); targets: cac tran can tinh dac trung.
    Moi target chi nhin thay cac tran trong `played` co ngay < ngay cua target."""
    teams = sorted(set(played.home_team) | set(played.away_team) | set(targets.home_team) | set(targets.away_team))
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    p = played.sort_values("date", kind="stable").reset_index(drop=True)
    pd_days = pd.to_datetime(p.date).values.astype("datetime64[D]").astype(np.int64)
    ph = p.home_team.map(ix).values
    pa = p.away_team.map(ix).values
    psh = share_of_home(p.home_goals.values.astype(float), p.away_goals.values.astype(float))
    pgd = (p.home_goals - p.away_goals).values.astype(float)

    pair_idx = {}
    for m, (h, a) in enumerate(zip(ph, pa)):
        pair_idx.setdefault((min(h, a), max(h, a)), []).append(m)
    pair_idx = {k: np.array(v) for k, v in pair_idx.items()}

    t = targets.copy()
    t["_day"] = pd.to_datetime(t.date).values.astype("datetime64[D]").astype(np.int64)
    rows = {}
    for day, grp in t.groupby("_day", sort=True):
        end = np.searchsorted(pd_days, day, side="left")          # chi cac tran co ngay < day
        start = np.searchsorted(pd_days, day - WINDOW_DAYS, side="left")
        sl = slice(start, end)
        w = 0.5 ** ((day - pd_days[sl]) / HALF_LIFE_DAYS)
        h, a, s = ph[sl], pa[sl], psh[sl]

        V = np.zeros((n, n))                     # V[i, j]: i "bau" cho j (i thua/hoa j)
        np.add.at(V, (a, h), w * s)
        np.add.at(V, (h, a), w * (1 - s))
        W = np.zeros((n, n))                     # tong trong so cac tran giua i va j
        np.add.at(W, (h, a), w)
        np.add.at(W, (a, h), w)
        S = np.zeros((n, n))                     # tong trong so * diem cua i truoc j
        np.add.at(S, (h, a), w * s)
        np.add.at(S, (a, h), w * (1 - s))
        A = np.where(W > 0, S / np.where(W > 0, W, 1) - 0.5, 0.0)
        T = np.clip(A, 0, None)
        T2 = T @ T
        active = np.where(W.sum(1) > 0)[0]
        pr = pagerank(V, active) if len(active) else np.full(n, np.nan)

        for idx, r in grp.iterrows():
            hi, ai = ix[r.home_team], ix[r.away_team]
            mask = (W[hi] > 0) & (W[ai] > 0)
            mask[[hi, ai]] = False
            if mask.any():
                wt = np.minimum(W[hi][mask], W[ai][mask])
                co = float(np.average(A[hi][mask] - A[ai][mask], weights=wt))
            else:
                co = np.nan
            # doi dau co trong so
            ids = pair_idx.get((min(hi, ai), max(hi, ai)), np.array([], dtype=int))
            ids = ids[(ids < end) & (pd_days[ids] >= day - H2H_WINDOW_DAYS)]
            if len(ids):
                hw = 0.5 ** ((day - pd_days[ids]) / H2H_HALF_LIFE_DAYS)
                sign_home = ph[ids] == hi
                sh = np.where(sign_home, psh[ids], 1 - psh[ids])
                gd = np.where(sign_home, pgd[ids], -pgd[ids])
                wsum = hw.sum()
                h2h_share = ((hw * sh).sum() + 0.5 * H2H_SHRINK) / (wsum + H2H_SHRINK) - 0.5
                h2h_gd = (hw * gd).sum() / (wsum + H2H_SHRINK)
            else:
                wsum, h2h_share, h2h_gd = 0.0, 0.0, 0.0
            rows[idx] = {
                "gr_pagerank_home": pr[hi], "gr_pagerank_away": pr[ai],
                "gr_pagerank_log_ratio": np.log(pr[hi] / pr[ai]) if pr[hi] > 0 and pr[ai] > 0 else np.nan,
                "gr_common_opp_diff": co, "gr_common_opp_n": int(mask.sum()),
                "gr_trans2_diff": T2[hi, ai] - T2[ai, hi],
                "gr_h2h_share": h2h_share, "gr_h2h_gd": h2h_gd, "gr_h2h_weight": wsum,
            }
    out = pd.DataFrame.from_dict(rows, orient="index").reindex(t.index)
    key = [c for c in ["match_id"] + KEY if c in t.columns]   # match_id (neu co) la khoa ghep chinh
    return pd.concat([t[key], out[FEATURES]], axis=1)


def selftest(played, targets, full, n_dates=6, seed=0):
    """Cat bo MOI tran co ngay >= D roi tinh lai: ket qua phai trung khop -> khong dung thong tin tuong lai."""
    rng = np.random.default_rng(seed)
    dates = np.sort(targets.date.unique())
    dates = dates[len(dates) // 10:]
    bad = 0
    for D in rng.choice(dates, n_dates, replace=False):
        part = build(played[played.date < D], targets[targets.date == D])
        ref = full.loc[part.index, FEATURES]
        diff = np.nanmax(np.abs(part[FEATURES].values.astype(float) - ref.values.astype(float)))
        same_nan = (part[FEATURES].isna().values == ref.isna().values).all()
        ok = diff < 1e-9 and same_nan
        bad += not ok
        print(f"  selftest {D}: {len(part)} tran, sai lech toi da {diff:.2e}, NaN khop={same_nan} -> {'OK' if ok else 'LOI'}")
    if bad:
        raise SystemExit("Selftest THAT BAI: co ro ri du lieu")
    print("Selftest dat: dac trung chi phu thuoc vao cac tran truoc ngay du doan.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="matches.csv của khâu Tích hợp dữ liệu (cần cột match_id, season, date, home_team, away_team, status, home_goals, away_goals)")
    ap.add_argument("--out", default="graph_features.csv")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.dataset)
    played = df[df.status == "played"].copy()
    graph = build(played, df)
    graph.to_csv(args.out, index=False)
    print(f"{args.out}: {len(graph)} dong x {len(FEATURES)} dac trung")
    print(graph[FEATURES].describe().T[["count", "mean", "std", "min", "max"]].round(3).to_string())
    if args.selftest:
        selftest(played, df, graph)


if __name__ == "__main__":
    main()
