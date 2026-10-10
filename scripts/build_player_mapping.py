"""
scripts/build_player_mapping.py
Build Phase 1/2 Cross-Entity Master Player Mapping:
Generates data/00_master_entities/player_mapping.csv

Cross-maps identities across:
1. Transfermarkt: player_id, player_name (full_name)
2. FotMob: fotmob_id, fotmob_name, opta_id
3. Understat: understat_id, understat_name
4. FPL: fpl_name
"""

import os
import glob
import json
import html
import unicodedata
import re
import pandas as pd
import numpy as np
from collections import defaultdict
from difflib import SequenceMatcher

ROOT_DIR = r"z:\Desktop\Bet"
RAW_DIR = os.path.join(ROOT_DIR, "raw")
DATA_DIR = os.path.join(ROOT_DIR, "data")
MASTER_DIR = os.path.join(DATA_DIR, "00_master_entities")
os.makedirs(MASTER_DIR, exist_ok=True)

# Standard club mapping dictionary to normalized code
CLUB_MAP = {
    "Arsenal": "ARS", "Arsenal FC": "ARS",
    "Aston Villa": "AVL",
    "Bournemouth": "BOU", "AFC Bournemouth": "BOU",
    "Brentford": "BRE", "Brentford FC": "BRE",
    "Brighton": "BHA", "Brighton & Hove Albion": "BHA",
    "Burnley": "BUR", "Burnley FC": "BUR",
    "Chelsea": "CHE", "Chelsea FC": "CHE",
    "Coventry": "COV", "Coventry City": "COV",
    "Crystal Palace": "CRY",
    "Everton": "EVE", "Everton FC": "EVE",
    "Fulham": "FUL", "Fulham FC": "FUL",
    "Hull": "HUL", "Hull City": "HUL",
    "Ipswich": "IPS", "Ipswich Town": "IPS",
    "Leeds": "LEE", "Leeds United": "LEE",

    "Leicester": "LEI", "Leicester City": "LEI",
    "Liverpool": "LIV", "Liverpool FC": "LIV",
    "Luton": "LUT", "Luton Town": "LUT",
    "Manchester City": "MCI", "Man City": "MCI",
    "Manchester United": "MUN", "Man Utd": "MUN",
    "Newcastle": "NEW", "Newcastle United": "NEW",
    "Norwich": "NOR", "Norwich City": "NOR",
    "Nottingham Forest": "NFO", "Nott'm Forest": "NFO",
    "Sheffield United": "SHU", "Sheffield Utd": "SHU",
    "Southampton": "SOU", "Southampton FC": "SOU",
    "Tottenham": "TOT", "Tottenham Hotspur": "TOT", "Spurs": "TOT",
    "Watford": "WAT", "Watford FC": "WAT",
    "West Ham": "WHU", "West Ham United": "WHU",
    "Wolves": "WOL", "Wolverhampton Wanderers": "WOL",
}

def clean_str(val):
    """Normalize string: unescape HTML, replace special chars/diacritics, strip punctuation."""
    if not isinstance(val, str):
        return ""
    val = html.unescape(val)
    val = val.replace("-", " ").replace("_", " ")
    replacements = {
        'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'ß': 'ss',
        'đ': 'd', 'Đ': 'd', 'ð': 'd', 'Ð': 'd', 'ł': 'l', 'Ł': 'l',
        'þ': 'th', 'Þ': 'th', 'í': 'i', 'á': 'a', 'é': 'e', 'ó': 'o',
        'ú': 'u', 'ã': 'a', 'õ': 'o', 'ç': 'c', 'ñ': 'n', 'ü': 'u',
        'ö': 'o', 'ä': 'a', 'ë': 'e', 'ï': 'i', 'ÿ': 'y', 'ý': 'y',
        'č': 'c', 'ć': 'c', 'š': 's', 'ž': 'z', 'ř': 'r'
    }
    for k, v in replacements.items():
        val = val.replace(k, v)
    val = "".join(c for c in unicodedata.normalize("NFD", val) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", val.lower().strip())

# Common English and international nicknames / abbreviations
NICKNAMES = {
    "joe": "joseph", "joseph": "joe",
    "dan": "daniel", "daniel": "dan", "danny": "daniel",
    "ben": "benjamin", "benjamin": "ben",
    "tom": "thomas", "thomas": "tom", "tommy": "thomas",
    "matt": "matthew", "matthew": "matt", "matty": "matthew",
    "alex": "alexander", "alexander": "alex",
    "oli": "oliver", "oliver": "oli", "ollie": "oliver",
    "chris": "christopher", "christopher": "chris",
    "will": "william", "william": "will", "bill": "william", "billy": "william",
    "sam": "samuel", "samuel": "sam", "sammy": "samuel",
    "rob": "robert", "robert": "rob", "bobby": "robert", "bob": "robert",
    "ed": "edward", "edward": "ed", "eddie": "edward",
    "dave": "david", "david": "dave",
    "josh": "joshua", "joshua": "josh",
    "andy": "andrew", "andrew": "andy",
    "tim": "timothy", "timothy": "tim",
    "mike": "michael", "michael": "mike", "mikey": "michael",
    "tony": "anthony", "anthony": "tony",
    "max": "maximilian", "maximilian": "max",
    "tino": "valentino", "valentino": "tino",
    "willy": "wilfredo", "wilfredo": "willy",
    "aji": "ajibola", "ajibola": "aji",
    "dom": "dominic", "dominic": "dom",
    "esapa": "detlef", "detlef": "esapa",
    "val": "valintino", "valintino": "val",
}

# Curated alias dictionary matching variations across sources to verified TM player IDs
KNOWN_TM_ALIASES = {
    "joseph gomez": 256178,
    "oliver mcburnie": 298477,
    "jonny": 175446,
    "kepa": 192279,
    "estupinan": 349599,
    "fernando marcal": 137745,
    "fode toure": 296422,
    "fode ballo toure": 296422,
    "fode ballo": 296422,
    "tanguy ndombele alvaro": 450936,
    "tanguy ndombele": 450936,
    "oghenekaro etebo": 409106,
    "louis beyer": 403898,
    "bobby reid": 186186,
    "bobby de cordova reid": 186186,
    "naif aguerd": 361914,
    "nayef aguerd": 361914,
    "juan camilo hernandez": 459463,
    "juan hernandez": 459463,
    "cucho hernandez": 459463,
    "hamed junior traore": 391065,
    "hamed traore": 391065,
    "emile smith rowe": 392765,
    "benoit badiashile mukinayi": 463603,
    "ryan john giles": 504581,
    "joshua sargent": 393325,
    "josh sargent": 393325,
    "max kilman": 525247,
    "bryan gil salvatierra": 537382,
    "reinildo": 240692,
    "reinildo mandava": 240692,
    "ezri konsa ngoyo": 413403,
    "anssumane fati": 466810,
    "ansu fati": 466810,
    "arnaud kalimuendo muinga": 585959,
    "arnaut danjuma groeneveld": 355861,
    "mathis cherki": 607223,
    "rayan cherki": 607223,
    "amad diallo traore": 536835,
    "amad diallo": 536835,
    "amad": 536835,
    "william smallbone": 444211,
    "cheick oumar doucoure": 543387,
    "cheick doucoure": 543387,
    "stefan ortega moreno": 85941,
    "stefan ortega": 85941,
    "iyenoma destiny udogie": 556385,
    "destiny udogie": 556385,
    "iyenoma udogie": 556385,
    "matthew cash": 425334,
    "trincao": 412669,
    "francisco trincao": 412669,
    "florentino luis": 357162,
    "florentino": 357162,
    "pape sarr": 568693,
    "pape matar sarr": 568693,
    "yeremi pino": 568157,
    "jaden philogene bidace": 665390,
    "jaden philogene": 665390,
    "chimuanya ugochukwu": 711999,
    "lesley ugochukwu": 711999,
    "valentino livramento": 503981,
    "tino livramento": 503981,
    "hannibal mejbri": 607224,
    "dimitris giannoulis": 295630,
    "jacob sorensen": 390664,
    "jacob lungi sorensen": 390664,
    "vitalii mykolenko": 404842,
    "jamie bynoe gittens": 670882,
    "jamie gittens": 670882,
    "rasmus kristensen": 369684,
    "joseph hodge": 503975,
    "joe ayodele aribo": 468198,
    "joe aribo": 468198,
    "vinicius souza": 663581,
    "vinicius de souza costa": 663581,
    "valentin castellanos": 522784,
    "taty castellanos": 522784,
    "ben doak": 719673,
    "mykhailo mudryk": 537860,
    "illia zabarnyi": 659089,
    "eli junior kroupi": 955357,
    "junior kroupi": 955357,
    "savio": 743591,
    "savinho": 743591,
    "abduqodir khusanov": 763079,
    "yehor yarmolyuk": 717411,
    "alejandro jimenez": 741257,
    "alex jimenez": 741257,
    "treymaurice nyoni": 1045123,
    "andy irving": 430310,
    "thiago": 739443,
    "chido obi martin": 1042886,
    "chido obi": 1042886,
    "jair": 984096,
    "samir": 255744,
    "alisson becker": 105470,
    "ederson moraes": 238223,
    "dan burn": 134270,
    "daniel burn": 134270,
    "joe willock": 340329,
    "joseph willock": 340329,
    "tommy doyle": 433183,
    "thomas doyle": 433183,
    "rob elliot": 39037,
    "robert elliot": 39037,
    "zanka": 52059,
    "mathias jorgensen": 52059,
    "salomon rondon": 80197,
    "jose salomon rondon": 80197,
    "kiko femenia": 76467,
    "kiko": 76467,
    "wesley moraes": 381362,
    "wesley": 381362,
    "adrian": 71271,
    "anwar el ghazi": 183720,
    "anwar elghazi": 183720,
    "ben woodburn": 344015,
    "benjamin woodburn": 344015,
    "victor lindelof": 184573,
    "victor nilsson lindelof": 184573,
    "mads bech sorensen": 372240,
    "mads bech": 372240,
    "patrik sigurdur gunnarsson": 429563,
    "patrik gunnarsson": 429563,
    "luke mbete": 609883,
    "luke mbete tabu": 609883,
    "kaine hayden": 626888,
    "kaine kesler hayden": 626888,
    "tariqe fosu henry": 249873,
    "dominic ballard": 864496,
    "dom ballard": 864496,
    "alysson edward": 1005583,
    "djiamgone jocelin ta bi": 1322090,
    "jocelin ta bi": 1322090,
    "edward nketiah": 340324,
    "eddie nketiah": 340324,
    "wilfredo caballero": 19948,
    "willy caballero": 19948,
    "ajibola alese": 502065,
    "aji alese": 502065,
    "toti gomes": 606718,
    "toti": 606718,
    "benjamin chrisene": 670846,
    "ben chrisene": 670846,
    "arthur": 362842,
    "arthur melo": 362842,
    "vinicius": 525287,
    "carlos vinicius": 525287,
    "michael dacosta": 854722,
    "michael dacosta gonzalez": 854722,
    "wanya marcal madivadua": 670988,
    "wanya marcal": 670988,
    "maxwell kinsey wellings": 939615,
    "max kinsey wellings": 939615,
    "max kinsey": 939615,
    "igor": 380350,
    "igor julio": 380350,
    "ismaila cheick coulibaly": 490224,
    "ismaila coulibaly": 490224,
    "deivid de souza": 1082850,
    "deivid washington": 1082850,
    "valintino adedokun": 747996,
    "val adedokun": 747996,
    "detlef osong": 921532,
    "esapa osong": 921532,
    "franco umeh chibueze": 943560,
    "franco umeh": 943560,
    "dominic martins": 1203143,
    "dom martins": 1203143,
    "william crellin": 536794,
    "billy crellin": 536794,
    "gaga slonina": 656316,
    "gabriel slonina": 656316,
    "malcom dacosta": 1323654,
    "malcom dacosta gonzalez": 1323654,
    "daniel imray": 862109,
    "dan imray": 862109,
    "danny imray": 862109,
    "edmond paris maghoma": 470596,
    "paris maghoma": 470596,
    "josh king": 91059,
    "joshua king": 91059,
    "emerson royal": 476344,

    "jamaldeen jimoh": 954105,
    "jamaldeen jimoh aloba": 954105,
    "fernando lopez": 1064471,
    "fer lopez": 1064471,
    # FPL Latin & Portuguese full name registrations
    "carlos henrique casimiro": 16306,
    "ederson santana de moraes": 238223,
    "rodrigo rodri hernandez": 357565,
    "gabriel dos santos magalhaes": 435338,
    "gabriel fernando de jesus": 363205,
    "gabriel teodoro martinelli silva": 655488,
    "lucas tolentino coelho de lima": 444523,
    "richarlison de andrade": 378710,
    "joelinton cassio apolinario de lira": 333241,
    "fabio henrique tavares": 225693,
    "jorge luiz frello filho": 102017,
    "fernando luiz rosa": 26267,
    "frederico rodrigues de paula santos": 191614,
    "willian borges da silva": 52769,
    "murillo santiago costa dos santos": 1005649,
    "antony matheus dos santos": 602105,
    "emerson aparecido leite de souza junior": 476344,
    "pedro lomba neto": 111819,
    "danilo dos santos de oliveira": 808509,
    "evanilson aparecido ferreira": 711337,
    "savio moreira de oliveira": 743591,
    "andre trindade da costa neto": 800176,
    "norberto bercique gomes betuncal": 595809,
    "felipe augusto de almeida monteiro": 156501,
    "allan marques loureiro": 126422,
    "lucas rodrigues moura da silva": 77100,
    "raphael dias belloli": 411295,
    "vitor hugo naum dos santos": 468249,
    "matheus santos carneiro da cunha": 517894,
    "rodrigo moreno machado": 131505,
    "lyanco evangelista silveira neves vojnovic": 379807,
    "tiago da silva chiquinho": 695454,
    "marcus vinicius oliveira alencar": 668268,
    "joao pedro ferreira silva": 663244,
    "pedro henrique cardoso de lima": 1193867,
    "rayan vitor": 1012564,
}



def load_transfermarkt_master():
    """Load Master TM players and enrich with club histories and cleaned aliases."""
    tm_df = pd.read_csv(os.path.join(MASTER_DIR, "players.csv"))
    raw_tm_players = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_players.csv"))
    tm_df = tm_df.merge(
        raw_tm_players[["player_id", "player_code", "current_club_id", "current_club_name"]],
        on="player_id",
        how="left"
    )

    # TM appearances to get all clubs each player played for
    tm_app = pd.read_csv(os.path.join(RAW_DIR, "transfermarkt", "epl_appearances.csv"), usecols=["player_id", "player_club_id"])
    club_map_df = pd.read_csv(os.path.join(MASTER_DIR, "club_mapping.csv"))
    tm_cid_to_code = dict(zip(club_map_df["tm_club_id"], club_map_df["club_id"]))

    tm_player_clubs = defaultdict(set)
    for pid, tmcid in zip(tm_app["player_id"], tm_app["player_club_id"]):
        code = tm_cid_to_code.get(tmcid)
        if code:
            tm_player_clubs[pid].add(code)
    for pid, tmcid in zip(tm_df["player_id"], tm_df["current_club_id"]):
        code = tm_cid_to_code.get(tmcid)
        if code:
            tm_player_clubs[pid].add(code)

    tm_by_id = {}
    tm_clean_lookup = defaultdict(set)
    for _, r in tm_df.iterrows():
        pid = int(r["player_id"])
        full_name = r["full_name"]
        first_name = str(r["first_name"]) if pd.notna(r["first_name"]) else ""
        last_name = str(r["last_name"]) if pd.notna(r["last_name"]) else ""
        player_code = str(r["player_code"]) if pd.notna(r["player_code"]) else ""
        
        c_full = clean_str(full_name)
        c_code = clean_str(player_code.replace("-", " "))
        c_fn_ln = clean_str(f"{first_name} {last_name}")
        c_ln_fn = clean_str(f"{last_name} {first_name}")

        tm_by_id[pid] = {
            "player_id": pid,
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "player_code": player_code,
            "clubs": tm_player_clubs[pid],
            "c_full": c_full,
            "c_code": c_code,
            "c_fn_ln": c_fn_ln,
            "c_ln_fn": c_ln_fn,
            "c_ln": clean_str(last_name),
            "c_fn": clean_str(first_name),
        }

        for cand in [c_full, c_code, c_fn_ln, c_ln_fn]:
            if cand:
                tm_clean_lookup[cand].add(pid)

    return tm_df, tm_by_id, tm_clean_lookup


def match_player_to_tm(name, clubs, first_name="", last_name="", tm_by_id=None, tm_clean_lookup=None):
    """Match a player from FotMob or Understat to Transfermarkt master."""
    c_name = clean_str(name)
    c_fn = clean_str(first_name)
    c_ln = clean_str(last_name)
    fn_ln = clean_str(f"{first_name} {last_name}")
    ln_fn = clean_str(f"{last_name} {first_name}")

    # Special club-based disambiguation
    if c_name == "emerson" or fn_ln == "emerson":
        if "TOT" in clubs:
            return 476344, "club_disambig_tot"  # Emerson Royal
        if "CHE" in clubs or "WHU" in clubs:
            return 181778, "club_disambig_whu"  # Emerson Palmieri
    if c_name in ["josh king", "joshua king"] or fn_ln in ["josh king", "joshua king"]:
        if "WAT" in clubs or "EVE" in clubs or "BOU" in clubs:
            return 91059, "club_disambig_wat_king"  # Joshua King (Watford)
        if "FUL" in clubs:
            return 1011131, "club_disambig_ful_king"  # Josh King (Fulham)

    # 1. Alias lookup
    for key in [c_name, fn_ln, ln_fn]:
        if key in KNOWN_TM_ALIASES:
            return KNOWN_TM_ALIASES[key], "alias"



    # 2. Exact match in clean lookup
    for cand in [c_name, fn_ln, ln_fn]:
        if cand in tm_clean_lookup:
            pids = tm_clean_lookup[cand]
            if len(pids) == 1:
                return list(pids)[0], "exact"
            # Disambiguate by club
            for pid in pids:
                if tm_by_id[pid]["clubs"].intersection(clubs):
                    return pid, "exact_club"
            return list(pids)[0], "exact_first"

    # 3. Nickname resolution
    parts = c_name.split()
    if len(parts) >= 2:
        fn_part, ln_part = parts[0], " ".join(parts[1:])
        if fn_part in NICKNAMES:
            alt_name = f"{NICKNAMES[fn_part]} {ln_part}"
            if alt_name in tm_clean_lookup:
                pids = tm_clean_lookup[alt_name]
                for pid in pids:
                    if not clubs or tm_by_id[pid]["clubs"].intersection(clubs):
                        return pid, "nickname"

    # 4. Club-validated token matching
    if clubs:
        club_pids = set()
        for c in clubs:
            for pid, p in tm_by_id.items():
                if c in p["clubs"]:
                    club_pids.add(pid)
        
        name_tokens = set(c_name.split())
        for pid in club_pids:
            p = tm_by_id[pid]
            tm_tokens = set(p["c_full"].split()).union(p["c_code"].split())
            if p["c_ln"] and p["c_ln"] in c_name:
                if len(name_tokens.intersection(tm_tokens)) >= 2:
                    return pid, "club_tokens"
            sm = SequenceMatcher(None, c_name, p["c_full"]).ratio()
            if sm >= 0.85:
                return pid, f"club_similarity_{sm:.2f}"

    return None, "unmatched"


def extract_understat_players(tm_by_id, tm_clean_lookup):
    """Load and match all Understat players across 5 seasons."""
    u_path = os.path.join(RAW_DIR, "understat", "understat_players_5seasons.csv")
    u_df = pd.read_csv(u_path)
    u_players = u_df.groupby("id").agg({
        "player_name": "first",
        "team_title": lambda s: set(s.dropna()),
        "games": "sum"
    }).reset_index()

    u_matched_dict = {}
    for _, r in u_players.iterrows():
        uid = int(r["id"])
        uname = r["player_name"]
        codes = set()
        for t_str in r["team_title"]:
            for t in t_str.split(","):
                c = CLUB_MAP.get(t.strip())
                if c:
                    codes.add(c)
        tm_id, method = match_player_to_tm(
            uname, codes, tm_by_id=tm_by_id, tm_clean_lookup=tm_clean_lookup
        )
        u_matched_dict[uid] = {
            "understat_id": uid,
            "understat_name": uname,
            "tm_id": tm_id,
            "method": method,
            "games": r["games"],
            "clubs": codes
        }
    return u_matched_dict


def extract_fotmob_players(tm_by_id, tm_clean_lookup):
    """Load and match all FotMob players from 1,901 match lineups and shots."""
    fotmob_players = {}
    
    # 1. From match lineup cache
    for p in glob.glob(os.path.join(RAW_DIR, "sofascore", "cache_matches", "*.json")):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        general = data.get("general", {})
        home_name = general.get("homeTeam", {}).get("name")
        away_name = general.get("awayTeam", {}).get("name")
        
        lineup = data.get("content", {}).get("lineup", {})
        for t_key, t_name in [("homeTeam", home_name), ("awayTeam", away_name)]:
            t = lineup.get(t_key, {})
            for pl in t.get("starters", []) + t.get("subs", []):
                pid = pl.get("id")
                if not pid:
                    continue
                pid = int(pid)
                if pid not in fotmob_players:
                    fotmob_players[pid] = {
                        "fotmob_id": pid,
                        "opta_id": pl.get("optaId"),
                        "fotmob_name": pl.get("name", ""),
                        "first_name": pl.get("firstName", ""),
                        "last_name": pl.get("lastName", ""),
                        "teams": set()
                    }
                if t_name:
                    c_code = CLUB_MAP.get(t_name)
                    if c_code:
                        fotmob_players[pid]["teams"].add(c_code)

    # 2. From detailed shots
    shots_df = pd.read_csv(os.path.join(RAW_DIR, "sofascore", "fotmob_epl_47_details.csv"), low_memory=False)
    for _, row in shots_df.drop_duplicates(subset=["player_id"]).iterrows():
        pid = row["player_id"]
        if pd.isna(pid):
            continue
        pid = int(pid)
        if pid not in fotmob_players:
            fotmob_players[pid] = {
                "fotmob_id": pid,
                "opta_id": None,
                "fotmob_name": row["player_name"] if pd.notna(row["player_name"]) else "",
                "first_name": row["first_name"] if pd.notna(row["first_name"]) else "",
                "last_name": row["last_name"] if pd.notna(row["last_name"]) else "",
                "teams": set()
            }

    fm_matched_dict = {}
    for pid, p in fotmob_players.items():
        tm_id, method = match_player_to_tm(
            p["fotmob_name"], p["teams"], p["first_name"], p["last_name"],
            tm_by_id=tm_by_id, tm_clean_lookup=tm_clean_lookup
        )
        fm_matched_dict[pid] = {
            "fotmob_id": pid,
            "fotmob_name": p["fotmob_name"],
            "opta_id": p["opta_id"],
            "tm_id": tm_id,
            "method": method,
            "clubs": p["teams"]
        }
    return fm_matched_dict


def extract_fpl_players(tm_by_id, tm_clean_lookup):
    """Load and match FPL players across 5 seasons."""
    fpl_files = glob.glob(os.path.join(RAW_DIR, "fpl_api", "fpl_merged_gw_*.csv"))
    fpl_players = {}
    for f in fpl_files:
        df = pd.read_csv(f, usecols=["name", "element", "team", "minutes"], low_memory=False)
        for _, r in df.iterrows():
            pname = r["name"]
            if not isinstance(pname, str):
                continue
            if pname not in fpl_players:
                fpl_players[pname] = {"fpl_name": pname, "minutes": 0, "teams": set()}
            if pd.notna(r["minutes"]):
                fpl_players[pname]["minutes"] += int(r["minutes"])
            if pd.notna(r["team"]):
                c_code = CLUB_MAP.get(str(r["team"]).strip())
                if c_code:
                    fpl_players[pname]["teams"].add(c_code)

    fpl_matched_dict = {}
    for pname, info in fpl_players.items():
        tm_id, method = match_player_to_tm(
            pname, info["teams"], tm_by_id=tm_by_id, tm_clean_lookup=tm_clean_lookup
        )
        if not tm_id:
            parts = pname.split()
            if len(parts) >= 2:
                rev_name = " ".join(reversed(parts))
                tm_id, method = match_player_to_tm(
                    rev_name, info["teams"], tm_by_id=tm_by_id, tm_clean_lookup=tm_clean_lookup
                )
        if tm_id:
            fpl_matched_dict[pname] = {
                "fpl_name": pname,
                "tm_id": tm_id,
                "minutes": info["minutes"]
            }
    return fpl_matched_dict


def main():
    print("=" * 80)
    print("BUILDING MASTER ENTITY: PLAYER_MAPPING.CSV")
    print("=" * 80)

    # 1. Load Transfermarkt Master
    print("\n[1/4] Loading Transfermarkt master player demographics...")
    tm_df, tm_by_id, tm_clean_lookup = load_transfermarkt_master()
    print(f"  Loaded {len(tm_df)} Transfermarkt master players.")

    # 2. Extract and match Understat
    print("\n[2/4] Cross-mapping Understat players...")
    u_matched = extract_understat_players(tm_by_id, tm_clean_lookup)
    u_success = sum(1 for v in u_matched.values() if v["tm_id"] is not None)
    print(f"  Understat matching result: {u_success} / {len(u_matched)} ({u_success/len(u_matched):.2%})")

    # 3. Extract and match FotMob
    print("\n[3/4] Cross-mapping FotMob players (lineups & shots)...")
    fm_matched = extract_fotmob_players(tm_by_id, tm_clean_lookup)
    fm_success = sum(1 for v in fm_matched.values() if v["tm_id"] is not None)
    print(f"  FotMob matching result: {fm_success} / {len(fm_matched)} ({fm_success/len(fm_matched):.2%})")

    # 4. Extract and match FPL
    print("\n[4/4] Cross-mapping FPL players...")
    fpl_matched = extract_fpl_players(tm_by_id, tm_clean_lookup)
    print(f"  FPL matched players count: {len(fpl_matched)}")

    # Invert lookups by TM player_id
    u_by_tmid = {}
    for uid, uinfo in u_matched.items():
        tmid = uinfo["tm_id"]
        if tmid:
            u_by_tmid[tmid] = uinfo

    fm_by_tmid = {}
    for fmid, fminfo in fm_matched.items():
        tmid = fminfo["tm_id"]
        if tmid:
            fm_by_tmid[tmid] = fminfo

    fpl_by_tmid = {}
    for pname, fpminfo in fpl_matched.items():
        tmid = fpminfo["tm_id"]
        if tmid not in fpl_by_tmid or fpminfo["minutes"] > fpl_by_tmid[tmid]["minutes"]:
            fpl_by_tmid[tmid] = fpminfo

    # Build master mapping dataframe
    records = []
    for _, tm_row in tm_df.iterrows():
        tmid = int(tm_row["player_id"])
        pname = tm_row["full_name"]
        
        u_info = u_by_tmid.get(tmid)
        fm_info = fm_by_tmid.get(tmid)
        fpl_info = fpl_by_tmid.get(tmid)

        records.append({
            "player_id": tmid,
            "player_name": pname,
            "fotmob_id": int(fm_info["fotmob_id"]) if fm_info else np.nan,
            "fotmob_name": fm_info["fotmob_name"] if fm_info else None,
            "understat_id": int(u_info["understat_id"]) if u_info else np.nan,
            "understat_name": u_info["understat_name"] if u_info else None,
            "opta_id": fm_info["opta_id"] if (fm_info and fm_info.get("opta_id")) else None,
            "fpl_name": fpl_info["fpl_name"] if fpl_info else None,
        })

    mapping_df = pd.DataFrame(records)

    # Convert nullable integer IDs for clean CSV output
    mapping_df["fotmob_id"] = mapping_df["fotmob_id"].astype("Int64")
    mapping_df["understat_id"] = mapping_df["understat_id"].astype("Int64")

    out_path = os.path.join(MASTER_DIR, "player_mapping.csv")
    mapping_df.to_csv(out_path, index=False)

    print("\n" + "=" * 80)
    print(f"SAVED: {out_path}")
    print(f"Total Master Players: {len(mapping_df)}")
    print(f"Players with FotMob ID: {mapping_df['fotmob_id'].notna().sum()} ({mapping_df['fotmob_id'].notna().mean():.1%})")
    print(f"Players with Understat ID: {mapping_df['understat_id'].notna().sum()} ({mapping_df['understat_id'].notna().mean():.1%})")
    print(f"Players with Opta ID: {mapping_df['opta_id'].notna().sum()} ({mapping_df['opta_id'].notna().mean():.1%})")
    print(f"Players with FPL Name: {mapping_df['fpl_name'].notna().sum()} ({mapping_df['fpl_name'].notna().mean():.1%})")
    print("=" * 80)


if __name__ == "__main__":
    main()
