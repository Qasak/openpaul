"""Load and validate the normalized data files in data/.

Expected files (produced from the phase-1 research workflow output):
  teams.csv            team,group,fifa_code,confederation,is_host
  schedule_group.csv   match,date,group,team1,team2,city,country
  bracket.json         {"rounds":[{"round":"R32","slots":[{match,date,home_source,away_source,city}]}],
                        "third_place_rule": str, "tiebreaker_rules": str}
  elo.csv              team,elo
  fifa_rankings.csv    team,rank,points
  odds.csv             team,decimal_odds,decimal_odds_sharp,n_books,n_sharp,as_of
                       (written by src/ingest_odds.py, NOT src/ingest.py)
  results.csv          match,date,group,team1,team2,score1,score2,winner
                       (completed matches only; 'winner' required for knockout
                        rows match>=73 — the advancing team, covers ET/pens)

All team names are canonicalized through ALIASES at load time.
"""
from __future__ import annotations

import json
import os

import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# canonical name <- variants seen across sources
ALIASES = {
    "USA": "United States", "United States of America": "United States",
    "Korea Republic": "South Korea", "Republic of Korea": "South Korea",
    "IR Iran": "Iran", "Iran IR": "Iran",
    "Côte d'Ivoire": "Ivory Coast", "Cote d'Ivoire": "Ivory Coast",
    "Cabo Verde": "Cape Verde",
    "Curacao": "Curaçao",
    "Czech Republic": "Czechia",
    "Congo DR": "DR Congo", "Democratic Republic of the Congo": "DR Congo",
    "UAE": "United Arab Emirates",
    "Ireland": "Republic of Ireland",
    "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Netherlands ": "Netherlands", "Holland": "Netherlands",
}

# host-city -> country, for venue-based home advantage in knockouts
CITY_COUNTRY = {
    "Atlanta": "United States", "Boston": "United States", "Foxborough": "United States",
    "Dallas": "United States", "Arlington": "United States", "Houston": "United States",
    "Kansas City": "United States", "Los Angeles": "United States", "Inglewood": "United States",
    "Miami": "United States", "Miami Gardens": "United States",
    "New York": "United States", "New York/New Jersey": "United States",
    "East Rutherford": "United States", "New Jersey": "United States",
    "Philadelphia": "United States", "San Francisco": "United States",
    "San Francisco Bay Area": "United States", "Santa Clara": "United States",
    "Seattle": "United States",
    "Toronto": "Canada", "Vancouver": "Canada",
    "Mexico City": "Mexico", "Guadalajara": "Mexico", "Zapopan": "Mexico",
    "Monterrey": "Mexico", "Guadalupe": "Mexico",
}

HOSTS = {"United States", "Canada", "Mexico"}


def canon(name: str) -> str:
    return ALIASES.get(str(name).strip(), str(name).strip())


def load_all() -> dict:
    teams = pd.read_csv(os.path.join(DATA, "teams.csv"))
    teams["team"] = teams["team"].map(canon)

    sched = pd.read_csv(os.path.join(DATA, "schedule_group.csv"))
    for c in ("team1", "team2"):
        sched[c] = sched[c].map(canon)

    with open(os.path.join(DATA, "bracket.json")) as f:
        bracket = json.load(f)

    elo = pd.read_csv(os.path.join(DATA, "elo.csv"))
    elo["team"] = elo["team"].map(canon)

    fifa = pd.read_csv(os.path.join(DATA, "fifa_rankings.csv"))
    fifa["team"] = fifa["team"].map(canon)

    odds = pd.read_csv(os.path.join(DATA, "odds.csv"))
    odds["team"] = odds["team"].map(canon)

    res_path = os.path.join(DATA, "results.csv")
    results = pd.read_csv(res_path) if os.path.exists(res_path) else pd.DataFrame(
        columns=["match", "date", "group", "team1", "team2", "score1", "score2",
                 "winner"])
    if len(results):
        for c in ("team1", "team2"):
            results[c] = results[c].map(canon)
        ko = results[results["match"] >= 73]
        if "winner" not in results.columns:
            bad = list(ko["match"]) if len(ko) else []
        else:
            bad = list(ko[ko["winner"].isna() |
                          (ko["winner"].astype(str).str.strip() == "")]["match"])
        if bad:
            raise ValueError(
                f"knockout results missing 'winner' (matches {bad}); without it"
                " simulate.py cannot lock the tie and would silently re-simulate it")

    validate(teams, sched, elo, fifa, odds)
    return {"teams": teams, "sched": sched, "bracket": bracket,
            "elo": dict(zip(elo["team"], elo["elo"])),
            "fifa": fifa, "odds": odds, "results": results}


def validate(teams: pd.DataFrame, sched: pd.DataFrame, elo: pd.DataFrame,
             fifa: pd.DataFrame | None = None,
             odds: pd.DataFrame | None = None) -> None:
    errs = []
    if len(teams) != 48:
        errs.append(f"expected 48 teams, got {len(teams)}")
    groups = teams.groupby("group")["team"].count()
    if not (groups == 4).all():
        errs.append(f"groups without exactly 4 teams: {groups[groups != 4].to_dict()}")
    if len(sched) != 72:
        errs.append(f"expected 72 group matches, got {len(sched)}")
    per_group = sched.groupby("group")["match"].count()
    if not (per_group == 6).all():
        errs.append(f"groups without exactly 6 matches: {per_group[per_group != 6].to_dict()}")
    team_set = set(teams["team"])
    sched_teams = set(sched["team1"]) | set(sched["team2"])
    if sched_teams - team_set:
        errs.append(f"schedule teams not in teams.csv: {sorted(sched_teams - team_set)}")
    missing_elo = team_set - set(elo["team"])
    if missing_elo:
        errs.append(f"teams missing Elo: {sorted(missing_elo)}")
    if fifa is not None:
        missing_fifa = team_set - set(fifa["team"])
        if missing_fifa:
            errs.append(f"teams missing FIFA ranking: {sorted(missing_fifa)}")
    if odds is not None:
        missing_odds = team_set - set(odds["team"])
        if missing_odds:
            errs.append(f"teams missing odds: {sorted(missing_odds)}")
    if errs:
        raise ValueError("data validation failed:\n  - " + "\n  - ".join(errs))
