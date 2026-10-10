---
license: mit
task_categories:
- tabular-classification
- tabular-regression
tags:
- football
- soccer
- premier-league
- sports-analytics
- betting-odds
- expected-goals
- machine-learning
pretty_name: English Premier League Multi-Season Analytics Dataset (2021-2027)
size_categories:
- 10K<n<100K
---

# ⚽ English Premier League Multi-Season Analytics Dataset (2021-2027)

Comprehensive, multi-source normalized dataset for the **English Premier League** covering 6 seasons (**2021/2022** through **2026/2027**), automatically crawled, normalized, and updated daily via GitHub Actions.

## 📊 Dataset Overview

- **Time Range**: August 2021 – May 2027 (Ongoing daily updates)
- **Total Matches**: 1,950+ fixtures (1,900 completed across 5 historical seasons + 50 current season matches)
- **Data Completeness**: 100% referential integrity across 29 clubs and 1,760+ master players
- **Quality Assurance**: 278 automated validation tests passing (100%)

---

## 📁 Directory Structure & 9 Standardized Tables

Each season directory (`2021_2022/` ... `2026_2027/`) follows an identical schema:

```
├── 00_master_entities/
│   ├── club_mapping.csv            # Cross-mapping dictionary for 29 Premier League clubs
│   ├── clubs.csv                   # Immutable club profiles (stadiums, coordinates, capacity)
│   ├── players.csv                 # Demographic & career profiles of 1,761 players
│   └── player_mapping.csv          # Cross-entity player ID mapping (Transfermarkt, FotMob, Understat, Opta, FPL)
│
├── <season>/                       # 2021_2022 to 2026_2027
│   ├── clubs/
│   │   ├── club_season_profile.csv      # FPL club overall, attack & defense ratings (home/away)
│   │   └── club_match_performance.csv   # 54 granular team match stats (xG, passes, tackles, duels, box touches)
│   ├── matches/
│   │   ├── match_context.csv            # 44 match context features (scores, formations, referee, weather, xG, FDR)
│   │   ├── match_betting_odds.csv       # 119+ bookmaker odds (Bet365, Pinnacle, overrounds, implied prob, drift)
│   │   └── match_lineups.csv            # Starters, bench, jersey numbers, positions, captain status
│   ├── players/
│   │   ├── player_valuations.csv        # Market value timeline (Transfermarkt)
│   │   ├── player_match_stats.csv       # 32 individual player match stats (minutes, xG, xA, ICT Index, FPL points)
│   │   └── player_season_understat.csv  # 19 Understat season stats (xG, xA, xGChain, xGBuildup, key passes)
│   └── events/
│       └── match_shots.csv              # 29 spatial shot features (pitch x/y, goalmouth x/y, xG, xGOT, situations)
```

---

## 🚀 Quick Start in Python

### Using Pandas:
```python
import pandas as pd

# Load 2026/27 match context
matches = pd.read_csv("https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset/raw/main/2026_2027/matches/match_context.csv")

# Load betting odds
odds = pd.read_csv("https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset/raw/main/2026_2027/matches/match_betting_odds.csv")

# Load spatial shot events
shots = pd.read_csv("https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset/raw/main/2026_2027/events/match_shots.csv")
```

---

## 🔄 Daily Automated Updates

This dataset is updated automatically every morning at **03:00 UTC** via GitHub Actions workflow in repository [toibinguz/EPL_Data_Crawler](https://github.com/toibinguz/EPL_Data_Crawler).
