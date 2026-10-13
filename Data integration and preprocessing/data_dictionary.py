
import pandas as pd

from integrate import HERE, OUT_DIR

OUTPUT = HERE / "DATA_DICTIONARY.md"

# timing = when the value is known; only "Trước trận" / "Sát giờ" columns may be used as model inputs
ID, PRE, LATE, POST, TARGET, FLAG = "Định danh", "Trước trận", "Sát giờ", "Sau trận", "Mục tiêu", "Kỹ thuật"
SIDES = {"home": "đội nhà", "away": "đội khách"}

# column -> (description, unit / domain, source, timing); "{s}" = đội nhà / đội khách
MATCH_COLUMNS = {
    "match_id": ("Mã trận duy nhất `EPL_<năm bắt đầu>_<năm kết thúc>_<NHÀ>_<KHÁCH>`", "chuỗi", "tạo ra", ID),
    "league": ("Tên giải đấu", "`Premier League`", "tạo ra", ID),
    "league_code": ("Mã giải của football-data", "`E0`", "football-data", ID),
    "season": ("Mùa giải", "`2000/01` … `2026/27`", "tạo ra", ID),
    "season_start_year": ("Năm bắt đầu mùa giải", "2000 … 2026", "tạo ra", ID),
    "date": ("Ngày thi đấu", "`YYYY-MM-DD`", "football-data / FPL", PRE),
    "kickoff_time": ("Giờ bóng lăn, giờ Anh", "`HH:MM`", "football-data / FPL", PRE),
    "gameweek": ("Vòng đấu chính thức (chỉ mùa đang diễn ra)", "1 … 38", "FPL", PRE),
    "{side}_club_id": ("Mã 3 chữ cái của {s}", "`ARS`, `MCI`, …", "club_mapping", ID),
    "{side}_team": ("Tên chuẩn của {s}", "chuỗi", "club_mapping", ID),
    "score": ("Tỉ số cả trận `nhà-khách`", "`2-1`", "football-data", TARGET),
    "{side}_goals": ("Số bàn thắng cả trận của {s}", "bàn", "football-data", TARGET),
    "result": ("Kết quả cả trận: H = đội nhà thắng, D = hòa, A = đội khách thắng", "H / D / A", "football-data", TARGET),
    "ht_score": ("Tỉ số hiệp 1", "`1-0`", "football-data", POST),
    "ht_{side}_goals": ("Số bàn hiệp 1 của {s}", "bàn", "football-data", POST),
    "ht_result": ("Kết quả hiệp 1", "H / D / A", "football-data", POST),
    "{side}_shots": ("Tổng số cú sút của {s}", "cú sút", "football-data", POST),
    "{side}_shots_on_target": ("Số cú sút trúng đích của {s}", "cú sút", "football-data", POST),
    "{side}_corners": ("Số quả phạt góc của {s}", "lần", "football-data", POST),
    "{side}_fouls": ("Số lỗi {s} phạm phải", "lần", "football-data", POST),
    "{side}_yellow_cards": ("Số thẻ vàng của {s}", "thẻ", "football-data", POST),
    "{side}_red_cards": ("Số thẻ đỏ của {s}", "thẻ", "football-data", POST),
    "{side}_xg": ("Bàn thắng kỳ vọng (xG) của {s}", "xG ≥ 0", "Understat", POST),
    "attendance": ("Số khán giả; 0 = đá sân không khán giả (COVID)", "người", "Transfermarkt / football-data", POST),
    "stadium": ("Sân thi đấu", "chuỗi", "Transfermarkt", PRE),
    "referee": ("Trọng tài chính, chuẩn hóa `<chữ cái đầu> <họ>`", "`M Oliver`", "football-data", PRE),
    "{side}_manager": ("Huấn luyện viên của {s}", "chuỗi", "Transfermarkt", PRE),
    "{side}_formation": ("Sơ đồ chiến thuật xuất phát của {s} (nhãn gốc)", "`4-3-3 Attacking`", "Transfermarkt", LATE),
    "{side}_formation_shape": ("Sơ đồ của {s}, chỉ giữ phần số", "`4-3-3`", "tạo ra", LATE),
    "b365_{o}_odds": ("Kèo mở Bet365 cho {O}", "tỉ lệ thập phân > 1", "football-data", PRE),
    "pinnacle_{o}_odds": ("Kèo mở Pinnacle cho {O}", "tỉ lệ thập phân > 1", "football-data", PRE),
    "avg_{o}_odds": ("Kèo mở trung bình thị trường cho {O}", "tỉ lệ thập phân > 1", "football-data", PRE),
    "b365_close_{o}_odds": ("Kèo chốt Bet365 cho {O}", "tỉ lệ thập phân > 1", "football-data", LATE),
    "pinnacle_close_{o}_odds": ("Kèo chốt Pinnacle cho {O}", "tỉ lệ thập phân > 1", "football-data", LATE),
    "avg_close_{o}_odds": ("Kèo chốt trung bình thị trường cho {O}", "tỉ lệ thập phân > 1", "football-data", LATE),
    "{side}_possession": ("Tỉ lệ kiểm soát bóng của {s}", "%", "FotMob", POST),
    "{side}_big_chances": ("Số tình huống ngon ăn (big chance) {s} tạo ra", "lần", "FotMob", POST),
    "{side}_big_chances_missed": ("Số tình huống ngon ăn {s} bỏ lỡ", "lần", "FotMob", POST),
    "{side}_shots_inside_box": ("Số cú sút trong vòng cấm của {s}", "cú sút", "FotMob", POST),
    "{side}_shots_outside_box": ("Số cú sút ngoài vòng cấm của {s}", "cú sút", "FotMob", POST),
    "{side}_passes": ("Tổng số đường chuyền của {s}", "đường chuyền", "FotMob", POST),
    "{side}_accurate_passes": ("Số đường chuyền chính xác của {s}", "đường chuyền", "FotMob", POST),
    "{side}_pass_accuracy": ("`accurate_passes / passes × 100` của {s}", "%", "tạo ra (FotMob)", POST),
    "{side}_touches_opp_box": ("Số lần chạm bóng trong vòng cấm đối phương của {s}", "lần", "FotMob", POST),
    "{side}_tackles": ("Số pha tắc bóng của {s}", "lần", "FotMob", POST),
    "{side}_interceptions": ("Số pha cắt bóng của {s}", "lần", "FotMob", POST),
    "{side}_clearances": ("Số pha phá bóng của {s}", "lần", "FotMob", POST),
    "{side}_duels_won": ("Số pha tranh chấp thắng của {s}", "lần", "FotMob", POST),
    "{side}_saves": ("Số pha cứu thua của thủ môn {s}", "lần", "FotMob", POST),
    "{side}_fm_xg": ("xG của {s} theo FotMob (khác mô hình xG của Understat)", "xG ≥ 0", "FotMob", POST),
    "{side}_fm_npxg": ("xG không tính phạt đền của {s}", "xG ≥ 0", "FotMob", POST),
    "{side}_fm_xg_open_play": ("xG từ bóng sống của {s}", "xG ≥ 0", "FotMob", POST),
    "{side}_fm_xg_set_play": ("xG từ bóng chết của {s}", "xG ≥ 0", "FotMob", POST),
    "{side}_fm_xgot": ("xG của các cú sút trúng đích (xGOT) của {s}: chất lượng dứt điểm", "xG ≥ 0", "FotMob", POST),
    "fm_match_id": ("Mã trận của FotMob (truy ngược file `raw_data/sofascore`)", "số nguyên", "FotMob", ID),
    "source_flags": ("Nguồn đã ghép: FD = football-data, US = Understat, TM = Transfermarkt, FM = FotMob, "
                     "FPL = lịch FPL", "`FD+US+TM+FM`", "tạo ra", FLAG),
    "attendance_imputed": ("True nếu `attendance` = 0 được suy ra do đá sân không khán giả", "bool", "tạo ra", FLAG),
    "season_complete": ("True nếu mùa có đủ 380 trận", "bool", "tạo ra", FLAG),
    "status": ("`played` = đã đá, `upcoming` = sắp đá / bị hoãn", "played / upcoming", "tạo ra", FLAG),
    "provisional": ("True nếu kết quả tạm lấy từ Understat, chờ football-data", "bool", "tạo ra", FLAG),
    "postponed": ("True nếu trận sắp đá đang bị hoãn", "bool", "tạo ra", FLAG),
}

FEATURE_COLUMNS = {
    "home_elo": ("Elo của đội nhà trước trận", "điểm", PRE),
    "away_elo": ("Elo của đội khách trước trận", "điểm", PRE),
    "elo_diff": ("`home_elo − away_elo`", "điểm", PRE),
    "elo_home_win_prob": ("Điểm kỳ vọng của đội nhà theo Elo (đã cộng 65 điểm sân nhà): thắng + ½ hòa",
                          "0 … 1", PRE),
    "{side}_position": ("Vị trí của {s} trên bảng xếp hạng trước trận (điểm → hiệu số → bàn thắng); "
                        "trống trước trận đầu tiên của mùa", "1 … 20", PRE),
    "position_diff": ("`home_position − away_position` (âm = đội nhà xếp trên)", "−19 … 19", PRE),
    "{side}_form": ("Kết quả 5 trận gần nhất của {s}, cũ → mới", "`WWDLW`", PRE),
    "{side}_wins5": ("Số trận thắng trong 5 trận gần nhất của {s}", "0 … 5", PRE),
    "{side}_draws5": ("Số trận hòa trong 5 trận gần nhất của {s}", "0 … 5", PRE),
    "{side}_losses5": ("Số trận thua trong 5 trận gần nhất của {s}", "0 … 5", PRE),
    "{side}_points5": ("Điểm trung bình/trận, 5 trận gần nhất của {s}", "0 … 3", PRE),
    "{side}_goals_for5": ("Bàn thắng trung bình/trận, 5 trận gần nhất của {s}", "bàn", PRE),
    "{side}_goals_against5": ("Bàn thua trung bình/trận, 5 trận gần nhất của {s}", "bàn", PRE),
    "{side}_shots_on_target5": ("Sút trúng đích trung bình/trận, 5 trận gần nhất của {s}", "cú sút", PRE),
    "{side}_xg5": ("xG trung bình/trận, 5 trận gần nhất của {s}", "xG", PRE),
    "{side}_xga5": ("xG để thủng (xGA) trung bình/trận, 5 trận gần nhất của {s}", "xG", PRE),
    "{side}_opp_elo5": ("Elo trung bình (trước trận) của 5 đối thủ gần nhất của {s}: lịch đấu khó hay dễ",
                        "điểm", PRE),
    "{side}_perf_vs_elo5": ("Phong độ đã tính sức mạnh đối thủ: trung bình (kết quả − điểm kỳ vọng Elo) "
                            "5 trận gần nhất của {s}; > 0 = đá tốt hơn Elo dự đoán", "−1 … 1", PRE),
    "{side}_possession5": ("Kiểm soát bóng trung bình, 5 trận gần nhất của {s}", "%", PRE),
    "{side}_pass_accuracy5": ("Tỉ lệ chuyền chính xác trung bình, 5 trận gần nhất của {s}", "%", PRE),
    "{side}_big_chances5": ("Tình huống ngon ăn tạo ra trung bình/trận, 5 trận gần nhất của {s}", "lần", PRE),
    "{side}_big_chances_against5": ("Tình huống ngon ăn để đối thủ tạo ra trung bình/trận, 5 trận gần nhất của {s}",
                                    "lần", PRE),
    "{side}_box_shots5": ("Sút trong vòng cấm trung bình/trận, 5 trận gần nhất của {s}", "cú sút", PRE),
    "{side}_box_shots_against5": ("Cú sút trong vòng cấm phải nhận trung bình/trận, 5 trận gần nhất của {s}",
                                  "cú sút", PRE),
    "{side}_box_touches5": ("Chạm bóng trong vòng cấm đối phương trung bình/trận, 5 trận gần nhất của {s}",
                            "lần", PRE),
    "{side}_box_touches_against5": ("Số lần đối thủ chạm bóng trong vòng cấm của {s}, trung bình 5 trận gần nhất",
                                    "lần", PRE),
    "{side}_xgot5": ("xGOT (FotMob) trung bình/trận, 5 trận gần nhất của {s}", "xG", PRE),
    "{side}_xgot_against5": ("xGOT phải nhận trung bình/trận, 5 trận gần nhất của {s}", "xG", PRE),
    "home_home_points5": ("Điểm trung bình 5 trận **sân nhà** gần nhất của đội nhà", "0 … 3", PRE),
    "home_home_goals_for5": ("Bàn thắng trung bình 5 trận sân nhà gần nhất của đội nhà", "bàn", PRE),
    "home_home_goals_against5": ("Bàn thua trung bình 5 trận sân nhà gần nhất của đội nhà", "bàn", PRE),
    "away_away_points5": ("Điểm trung bình 5 trận **sân khách** gần nhất của đội khách", "0 … 3", PRE),
    "away_away_goals_for5": ("Bàn thắng trung bình 5 trận sân khách gần nhất của đội khách", "bàn", PRE),
    "away_away_goals_against5": ("Bàn thua trung bình 5 trận sân khách gần nhất của đội khách", "bàn", PRE),
    "{side}_played": ("Số trận {s} đã đá trong mùa trước trận này", "0 … 37", PRE),
    "{side}_ppg": ("Điểm/trận của {s} từ đầu mùa", "0 … 3", PRE),
    "{side}_goals_for_pg": ("Bàn thắng/trận của {s} từ đầu mùa", "bàn", PRE),
    "{side}_goals_against_pg": ("Bàn thua/trận của {s} từ đầu mùa", "bàn", PRE),
    "{side}_rest_days": ("Số ngày nghỉ của {s} kể từ trận Premier League trước", "ngày", PRE),
    "h2h_matches": ("Số lần hai đội gặp nhau được tính (tối đa 5 lần gần nhất)", "0 … 5", PRE),
    "h2h_home_points": ("Điểm trung bình của đội nhà trong các lần gặp đó", "0 … 3", PRE),
    "h2h_home_goal_diff": ("Hiệu số trung bình của đội nhà trong các lần gặp đó", "bàn", PRE),
    "odds_{o}_prob": ("Xác suất {O} ngụ ý từ kèo mở (trung bình thị trường, thiếu thì Bet365), đã bỏ biên nhà cái",
                      "0 … 1", PRE),
    "diff_points5": ("`home_points5 − away_points5`", "", PRE),
    "diff_goals_for5": ("`home_goals_for5 − away_goals_for5`", "", PRE),
    "diff_goals_against5": ("`home_goals_against5 − away_goals_against5`", "", PRE),
    "diff_shots_on_target5": ("`home_shots_on_target5 − away_shots_on_target5`", "", PRE),
    "diff_xg5": ("`home_xg5 − away_xg5`", "", PRE),
    "diff_xga5": ("`home_xga5 − away_xga5`", "", PRE),
    "diff_ppg": ("`home_ppg − away_ppg`", "", PRE),
    **{f"diff_{k}": (f"`home_{k} − away_{k}`", "", PRE)
       for k in ["perf_vs_elo5", "possession5", "big_chances5", "big_chances_against5", "box_touches5",
                 "xgot5", "xgot_against5"]},
}
OUTCOMES = {"home": "đội nhà thắng", "draw": "hòa", "away": "đội khách thắng"}


def expand(spec):
    """Expand {side} / {o} templates into one entry per real column."""
    out = {}
    for key, val in spec.items():
        if "{side}" in key:
            for side, word in SIDES.items():
                out[key.format(side=side)] = tuple(v.format(s=word) if isinstance(v, str) else v for v in val)
        elif "{o}" in key:
            for o, word in OUTCOMES.items():
                out[key.format(o=o)] = tuple(v.replace("{O}", word) if isinstance(v, str) else v for v in val)
        else:
            out[key] = val
    return out


def type_name(s):
    if pd.api.types.is_bool_dtype(s) or set(s.dropna().unique()) <= {True, False, "True", "False"}:
        return "bool"
    if pd.api.types.is_integer_dtype(s) or (pd.api.types.is_float_dtype(s) and s.dropna().mod(1).eq(0).all()
                                            and s.notna().any()):
        return "int"
    return "float" if pd.api.types.is_float_dtype(s) else "text"


def measure(df, col):
    played = df[df.status == "played"]
    s = df[col]
    filled = df.loc[s.notna(), "season"]
    example = s.dropna().iloc[len(s.dropna()) // 2] if s.notna().any() else ""
    typ = type_name(s)
    if typ == "int" and example != "":
        example = int(float(example))
    elif isinstance(example, float):
        example = round(example, 3)
    return (typ, f"{played[col].isna().mean():.1%}", filled.min() if len(filled) else "–", f"`{example}`")


def table(df, spec, with_source):
    head = "| Cột | Kiểu | Mô tả | Đơn vị / miền | " + ("Nguồn | " if with_source else "") + \
           "Thời điểm | Có từ mùa | % thiếu* | Ví dụ |"
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    for col in df.columns:
        if col not in spec:
            raise ValueError(f"no description for column {col}")
        d = spec[col]
        typ, miss, first, ex = measure(df, col)
        src = f"{d[2]} | " if with_source else ""
        timing = d[3] if with_source else d[2]
        lines.append(f"| `{col}` | {typ} | {d[0]} | {d[1]} | {src}{timing} | {first} | {miss} | {ex} |")
    return "\n".join(lines)


def main():
    m = pd.read_csv(OUT_DIR / "matches.csv", low_memory=False)
    f = pd.read_csv(OUT_DIR / "features.csv", low_memory=False)
    match_spec = expand(MATCH_COLUMNS)
    feat_spec = {**{c: (v[0], v[1], v[3]) for c, v in match_spec.items()}, **expand(FEATURE_COLUMNS)}
    played = m.status.eq("played")

    doc = f"""# Data Dictionary – Premier League 2000/01 → 2026/27

Bộ dữ liệu sạch và bảng đặc trưng cho bài toán dự đoán tỉ số và xác suất Thắng/Hòa/Thua.
Tạo bởi `Data integration and preprocessing/` chỉ từ `data/raw_data/`; file này được sinh tự động
bằng `data_dictionary.py` – các cột *Kiểu*, *Có từ mùa*, *% thiếu*, *Ví dụ* được đo trên dữ liệu hiện tại.

## Tổng quan

| File (`data/clean/`) | Một dòng là | Số dòng | Số cột | Dùng cho |
|---|---|---|---|---|
| `matches.csv` | một trận | {len(m):,} ({played.sum():,} đã đá, {(~played).sum()} sắp đá) | {m.shape[1]} | phân tích, bộ dữ liệu sạch |
| `features.csv` | một trận | {len(f):,} | {f.shape[1]} | huấn luyện / dự đoán |

- Khóa chung: `match_id` (một trận chỉ có một dòng trong mỗi file).
- Mùa: {m.season.min()} → {m.season.max()}; 2003/04 và 2004/05 chỉ có 335/380 trận (thiếu trong nguồn gốc);
  mùa {m.season.max()} đang diễn ra.
- Cột **Thời điểm** cho biết khi nào giá trị được biết:

| Thời điểm | Ý nghĩa | Dùng làm đầu vào mô hình? |
|---|---|---|
| {ID} | khóa, tên | không (chỉ để ghép / tra cứu) |
| {PRE} | biết trước ngày thi đấu | **có** |
| {LATE} | chỉ biết ngay trước giờ bóng lăn (kèo chốt, đội hình) | có, nếu dự đoán sát giờ |
| {POST} | thống kê của chính trận đấu | **không** – chỉ dùng qua trung bình các trận trước (`features.csv`) |
| {TARGET} | điều cần dự đoán | là nhãn |
| {FLAG} | cờ chất lượng / trạng thái | lọc dữ liệu |

\\* % thiếu tính trên các trận **đã đá**. Trận sắp đá không có kết quả, thống kê và kèo.

## 1. `matches.csv` – bộ dữ liệu sạch

{table(m, match_spec, with_source=True)}

## 2. `features.csv` – bảng đặc trưng

Mọi đặc trưng chỉ dùng các trận đá **trước** trận đang xét (đã tự kiểm tra, xem `features_report.json`).
Trận sắp đá dùng mọi kết quả đã biết tới lúc chạy. Tiền tố `home_` / `away_` = đội nhà / đội khách.

{table(f, feat_spec, with_source=False)}

### Elo
Bắt đầu 1500 (mùa 2000/01), đội mới lên hạng 1450; sau mỗi trận đội nhà nhận
Δ = 20 × hệ số cách biệt × (kết quả − E), đội khách nhận −Δ, với kết quả = 1 / 0,5 / 0 và
E = 1 / (1 + 10^((Elo_khách − Elo_nhà − 65) / 400)); hệ số cách biệt = 1 (≤1 bàn), 1,5 (2 bàn),
(11 + cách biệt) / 8 (≥3 bàn). Đầu mỗi mùa Elo được kéo 20 % về 1500.

## 3. Lưu ý khi sử dụng
- Chia train/test **theo thời gian**, không chia ngẫu nhiên.
- Nên bỏ mùa 2000/01 khi huấn luyện (Elo, phong độ chưa có lịch sử).
- xG chỉ có từ 2014/15; kèo chốt và giờ đá từ 2019/20; sân, HLV, sơ đồ từ 2012/13 đến 2025/26.
- FotMob (thư mục `raw_data/sofascore`) có từ 2010/11 (mùa này thiếu 111 trận): đến 2015/16 chỉ có kiểm soát bóng;
  tình huống ngon ăn, sút trong vòng cấm, chuyền, tắc bóng… từ 2016/17; xG, xGOT FotMob từ 2020/21;
  chạm bóng trong vòng cấm đủ từ 2024/25 (khoảng 70–90 % các mùa 2020/21–2023/24). Các đặc trưng `*5` từ FotMob
  trống khi 5 trận trước không có số liệu – nên huấn luyện mô hình dùng chúng trên dữ liệu từ 2016/17.
- `provisional = True`: kết quả tạm (chỉ có tỉ số + xG), sẽ được thay khi football-data cập nhật.
- Quy tắc làm sạch và mọi ô đã sửa: `data/clean/cleaning_log.csv`, `clean_report.json`.
"""
    OUTPUT.write_text(doc, encoding="utf-8")
    print(f"{OUTPUT.name}: {len(match_spec)} + {len(expand(FEATURE_COLUMNS))} column descriptions")


if __name__ == "__main__":
    main()
