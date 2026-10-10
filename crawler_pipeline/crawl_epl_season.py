
import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
UA = {"User-Agent": "Mozilla/5.0"}
TM_DUMP = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/{}.csv.gz"
TM_POSITION_ORDER = [
    "Goalkeeper", "Centre-Back", "Left-Back", "Right-Back", "Defensive Midfield", "Central Midfield",
    "Right Midfield", "Left Midfield", "Attacking Midfield", "Left Winger", "Right Winger",
    "Second Striker", "Centre-Forward",
]


def season_label(year):
    return f"{year}_{str(year + 1)[-2:]}"


# ---------------------------------------------------------------- Understat
def crawl_understat(year, season, out):
    r = requests.get(
        f"https://understat.com/getLeagueData/EPL/{year}",
        headers={**UA, "X-Requested-With": "XMLHttpRequest", "Referer": f"https://understat.com/league/EPL/{year}"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    # players
    players = pd.DataFrame(data["players"])
    int_cols = ["id", "games", "time", "goals", "assists", "shots", "key_passes", "yellow_cards", "red_cards", "npg"]
    float_cols = ["xG", "xA", "npxG", "xGChain", "xGBuildup"]
    players[int_cols] = players[int_cols].astype(int)
    players[float_cols] = players[float_cols].astype(float)
    players.insert(0, "season", season)
    players = players[["season", "id", "player_name", "games", "time", "goals", "xG", "assists", "xA", "shots",
                       "key_passes", "yellow_cards", "red_cards", "position", "team_title", "npg", "npxG",
                       "xGChain", "xGBuildup"]]
    players.to_csv(out / "understat.csv", index=False)

    # league table (aggregated from per-match team history)
    rows = []
    for team in data["teams"].values():
        h = pd.DataFrame(team["history"])
        rows.append({
            "team": team["title"], "M": len(h), "W": int(h.wins.sum()), "D": int(h.draws.sum()),
            "L": int(h.loses.sum()), "G": int(h.scored.sum()), "GA": int(h.missed.sum()), "PTS": int(h.pts.sum()),
            "xG": h.xG.sum(), "xGA": h.xGA.sum(), "xPTS": h.xpts.sum(),
        })
    t = pd.DataFrame(rows)
    t["GD"] = t.G - t.GA
    t["xG_diff"] = t.xG - t.G
    t["xGA_diff"] = t.xGA - t.GA
    t["xPTS_diff"] = t.xPTS - t.PTS
    t = t.sort_values(["PTS", "GD", "G"], ascending=False).reset_index(drop=True)
    t.insert(0, "rank", t.index + 1)
    t.insert(0, "season", season)
    for c in ["xG", "xGA", "xPTS", "xG_diff", "xGA_diff", "xPTS_diff"]:
        t[c] = t[c].round(2)
    t.to_csv(out / "understat_table.csv", index=False)

    # club_players: one row per (club, player); players who moved mid-season appear under each club
    cp = players.assign(club=players.team_title.str.split(",")).explode("club")
    cp = cp.rename(columns={"player_name": "player", "time": "minutes"})
    cp = cp.sort_values(["club", "minutes"], ascending=[True, False])
    cp = cp[["season", "club", "player", "position", "games", "minutes", "goals", "assists", "xG", "xA"]]
    cp.to_csv(out / "club_players.csv", index=False)

    sq = cp.groupby("club", sort=True).player.agg(lambda s: ", ".join(s)).reset_index(name="players")
    sq["num_players"] = sq.players.str.count(", ") + 1
    sq.insert(0, "season", season)
    sq[["season", "club", "num_players", "players"]].to_csv(out / "club_squads.csv", index=False)

    played = sum(1 for m in data["dates"] if m["isResult"])
    print(f"  understat: {len(players)} players, {len(t)} teams, {played}/{len(data['dates'])} matches played")


# ---------------------------------------------------------------- ESPN
def _espn_date(s):
    # ESPN now prints "Sat, Aug 23"; 2024_25 files use "Sat, 23 Aug"
    try:
        dow, rest = s.split(", ")
        mon, day = rest.split()
        return f"{dow}, {int(day)} {mon}"
    except ValueError:
        return s


def crawl_espn(year, season, out):
    base = f"https://www.espn.com/soccer/stats/_/league/ENG.1/season/{year}/view/"
    for view, prefix in [("scoring", "espn_scoring"), ("performance", "espn_performance")]:
        r = requests.get(base + view, headers=UA, timeout=30)
        r.raise_for_status()
        for i, tbl in enumerate(pd.read_html(io.StringIO(r.text))):
            if "RK" in tbl.columns:
                tbl["RK"] = tbl["RK"].ffill()  # ESPN leaves tied ranks blank
            if "DATE" in tbl.columns:
                tbl["DATE"] = tbl["DATE"].map(_espn_date)
            tbl.insert(0, "season", season)
            tbl.to_csv(out / f"{prefix}_{i}.csv", index=False)
            print(f"  espn: {prefix}_{i}.csv ({len(tbl)} rows)")


# ---------------------------------------------------------------- Transfermarkt (open dump)
_tm_cache = {}


def _tm(name):
    if name not in _tm_cache:
        r = requests.get(TM_DUMP.format(name), headers=UA, timeout=120)
        r.raise_for_status()
        _tm_cache[name] = pd.read_csv(io.BytesIO(r.content), compression="gzip")
    return _tm_cache[name]


def _fee_text(fee):
    if pd.isna(fee):
        return "?"
    if fee == 0:
        return "free transfer"
    return f"€{fee / 1e6:.2f}m" if fee >= 1e6 else f"€{fee / 1e3:.0f}k"


def _value_text(v):
    if pd.isna(v):
        return None
    return f"€{v / 1e6:.2f}m" if v >= 1e6 else f"€{v / 1e3:.0f}k"


def crawl_transfermarkt(year, season, out):
    clubs, players, vals, transfers = _tm("clubs"), _tm("players"), _tm("player_valuations"), _tm("transfers")
    start, end = pd.Timestamp(f"{year}-07-01"), pd.Timestamp(f"{year + 1}-06-30")
    vals["date"] = pd.to_datetime(vals.date)
    if vals.date.max() < start + pd.Timedelta(days=60) or players.last_season.max() < year:
        print(f"  transfermarkt: open dump ends {vals.date.max().date()} - does not cover {season}, skipped")
        return

    epl = clubs[(clubs.domestic_competition_id == "GB1") & (clubs.last_season >= year)]
    v = vals[(vals.date >= start) & (vals.date <= end) & vals.current_club_id.isin(epl.club_id)]
    v = v.sort_values("date").groupby(["player_id", "current_club_id"]).tail(1)

    sq = v.merge(players, on="player_id", suffixes=("", "_p"))
    sq = sq.merge(epl[["club_id", "name"]].rename(columns={"club_id": "current_club_id", "name": "club"}),
                  on="current_club_id")

    tr = transfers.copy()
    tr["transfer_date"] = pd.to_datetime(tr.transfer_date)
    tr = tr[tr.transfer_date <= end].sort_values("transfer_date").groupby(["player_id", "to_club_id"]).tail(1)
    sq = sq.merge(tr[["player_id", "to_club_id", "transfer_date", "from_club_name", "transfer_fee"]],
                  left_on=["player_id", "current_club_id"], right_on=["player_id", "to_club_id"], how="left")

    dob = pd.to_datetime(sq.date_of_birth)
    sq["age"] = ((start + pd.Timedelta(days=31) - dob).dt.days // 365.25).astype("Int64")
    sq["date_of_birth"] = dob.dt.strftime("%d/%m/%Y")
    sq["joined"] = sq.transfer_date.dt.strftime("%d/%m/%Y")
    sq["contract_until"] = pd.to_datetime(sq.contract_expiration_date).dt.strftime("%d/%m/%Y")
    sq["signed_from"] = [None if pd.isna(c) else f"{c}: Ablöse {_fee_text(f)}"
                         for c, f in zip(sq.from_club_name, sq.transfer_fee)]
    sq["position"] = sq.sub_position.fillna(sq.position)
    sq["market_value_eur"] = sq.market_value_in_eur.astype(float)
    sq["market_value_text"] = sq.market_value_eur.map(_value_text)
    sq["nationality"] = sq.country_of_citizenship
    sq["player"] = sq["name"]
    sq["number"] = None  # shirt numbers are not in the open dump
    sq["raw"] = None     # raw HTML row only exists when scraping the site directly
    sq.insert(0, "season", season)

    # clubs summary
    cl = sq.groupby("club").agg(players=("player", "size"), avg_age=("age", "mean"),
                                total_value_eur=("market_value_eur", "sum"),
                                avg_value_eur=("market_value_eur", "mean")).reset_index()
    cl["avg_age"] = cl.avg_age.astype(float).round(1)
    cl["avg_value_eur"] = cl.avg_value_eur.round(1)
    cl = cl.sort_values("total_value_eur", ascending=False)
    cl.insert(0, "season", season)
    cl.to_csv(out / "transfermarkt_clubs.csv", index=False)

    pos_rank = {p: i for i, p in enumerate(TM_POSITION_ORDER)}
    sq["_club_rank"] = sq.club.map({c: i for i, c in enumerate(cl.club)})
    sq["_pos_rank"] = sq.position.map(pos_rank).fillna(len(pos_rank))
    sq = sq.sort_values(["_club_rank", "_pos_rank", "market_value_eur"], ascending=[True, True, False])
    cols = ["season", "club", "number", "player", "position", "date_of_birth", "age", "nationality", "joined",
            "signed_from", "contract_until", "market_value_eur", "market_value_text", "raw"]
    sq[cols].to_csv(out / "transfermarkt_squads.csv", index=False)
    print(f"  transfermarkt: {len(cl)} clubs, {len(sq)} players (open dump up to {vals.date.max().date()})")


def main(years):
    for year in years:
        season = season_label(year)
        out = DATA_DIR / season
        out.mkdir(parents=True, exist_ok=True)
        print(f"== {season}  ({datetime.now():%Y-%m-%d %H:%M})")
        crawl_understat(year, season, out)
        crawl_espn(year, season, out)
        crawl_transfermarkt(year, season, out)


if __name__ == "__main__":
    main([int(y) for y in sys.argv[1:]] or [2025, 2026])
