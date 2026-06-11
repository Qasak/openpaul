"""Convert the phase-1 research workflow output (data/raw/research.json)
into the normalized files load_data.py expects.

NOTE: odds are deliberately NOT written here. The market snapshot is owned by
src/ingest_odds.py (round-2 same-day multi-book board with the sharp-book
consensus); writing the round-1 board from research.json would clobber it.
The round-1 board is archived at data/raw/odds_round1_archive.csv.

Usage:  python3 -m src.ingest
"""
from __future__ import annotations

import json
import os

import pandas as pd

from .load_data import DATA, canon

RAW = os.path.join(DATA, "raw", "research.json")


def main() -> None:
    with open(RAW) as f:
        r = json.load(f)

    # teams.csv
    rows = []
    for g in r["groups"]["groups"]:
        for t in g["teams"]:
            rows.append({
                "team": canon(t["name"]),
                "group": g["group"].strip().upper(),
                "fifa_code": t.get("fifa_code", ""),
                "confederation": t.get("confederation", ""),
                "is_host": bool(t.get("is_host", False)),
            })
    pd.DataFrame(rows).sort_values(["group", "team"]).to_csv(
        os.path.join(DATA, "teams.csv"), index=False)

    # schedule_group.csv
    sched = pd.DataFrame(r["group-schedule"]["matches"])
    sched["team1"] = sched["team1"].map(canon)
    sched["team2"] = sched["team2"].map(canon)
    sched["group"] = sched["group"].str.strip().str.upper()
    sched.sort_values("match").to_csv(os.path.join(DATA, "schedule_group.csv"), index=False)

    # bracket.json (kept as structured JSON)
    with open(os.path.join(DATA, "bracket.json"), "w") as f:
        json.dump(r["bracket"], f, indent=2, ensure_ascii=False)

    # elo.csv
    elo = pd.DataFrame(r["elo"]["ratings"])
    elo["team"] = elo["team"].map(canon)
    elo[["team", "elo"]].to_csv(os.path.join(DATA, "elo.csv"), index=False)

    # fifa_rankings.csv
    fifa = pd.DataFrame(r["fifa-rankings"]["rankings"])
    fifa["team"] = fifa["team"].map(canon)
    fifa.to_csv(os.path.join(DATA, "fifa_rankings.csv"), index=False)

    # results.csv (completed real matches)
    # schema: knockout rows (match >= 73) additionally need 'winner' (the team
    # advancing, covering ET/penalty outcomes) — see load_data.validate
    # GUARD: research.json predates the tournament, so once real results have
    # been appended to results.csv, re-running ingest must NOT clobber them
    # (same self-destruct failure class as an earlier odds.csv ingest bug).
    res_path = os.path.join(DATA, "results.csv")
    existing_rows = 0
    if os.path.exists(res_path):
        existing_rows = len(pd.read_csv(res_path))
    comp = r["results"]["completed"]
    if existing_rows > len(comp):
        print(f"results.csv: kept existing file ({existing_rows} rows > "
              f"{len(comp)} in research.json) — live results are never clobbered")
    else:
        res = pd.DataFrame(comp, columns=["match", "date", "group", "team1",
                                          "team2", "score1", "score2", "winner"])
        if len(res):
            res["team1"] = res["team1"].map(canon)
            res["team2"] = res["team2"].map(canon)
        res.to_csv(res_path, index=False)

    # context for the report (news, benchmarks, sources) — keep raw
    for key in ("news", "benchmarks"):
        with open(os.path.join(DATA, f"{key}.json"), "w") as f:
            json.dump(r.get(key, {}), f, indent=2, ensure_ascii=False)

    print("ingested:",
          f"{len(rows)} teams, {len(sched)} group matches,",
          f"{len(elo)} elo, {len(res)} completed results",
          "(odds: see src.ingest_odds)")


if __name__ == "__main__":
    main()
