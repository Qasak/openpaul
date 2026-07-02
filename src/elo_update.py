"""Roll the official pre-tournament Elo forward through played 2026 matches.

Round-4 fix for the frozen-ratings amplifier: the production ratings stay at
their 2026-06-10 eloratings.net values all tournament, so in-tournament
evidence (e.g. Cape Verde drawing Spain and Uruguay) never reaches later
forecasts. This module applies the eloratings.net update rule — the same
rule validated in src/elo_history.py against the official table (corr 0.986)
— to data/results.csv, starting FROM the official values, so there is no
accumulation error, only the rule itself:

  R' = R + K * G * (W - W_e),  K = 60 (World Cup finals),
  G = 1 / 1.5 / (11+N)/8 for margin 0-1 / 2 / >=3,
  W_e = 1/(1+10^(-d/400)), d includes +100 for a TRUE home side (host
  playing in its own country; venue country from the schedule files),
  penalty shootouts count as draws at the post-ET score.

No fitted parameters, no look-ahead: uses only finished matches.

Usage:  python3 -m src.elo_update
Writes: data/elo_current.csv (team, elo, elo_pre, delta, n_played)
        data/elo_current_meta.json
"""
from __future__ import annotations

import csv
import json
import os

from .elo_history import g_multiplier
from .load_data import DATA, canon, load_all

K_WORLD_CUP = 60.0
HOME_ADV = 100.0
OUT = os.path.join(DATA, "elo_current.csv")
META = os.path.join(DATA, "elo_current_meta.json")


def venue_country_by_match() -> dict[int, str]:
    out: dict[int, str] = {}
    for fname in ("schedule_group.csv", "schedule_ko.csv"):
        path = os.path.join(DATA, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                c = canon(str(r.get("country") or ""))
                if c:
                    out[int(r["match"])] = c
    return out


def rolled_ratings(data: dict) -> tuple[dict[str, float], dict[str, int], int]:
    """(current ratings for all 48 teams, matches counted per team, n applied)."""
    ratings = dict(data["elo"])
    venue = venue_country_by_match()
    played = {t: 0 for t in ratings}
    res = data["results"].sort_values(["date", "match"])
    n = 0
    for _, r in res.iterrows():
        t1, t2 = r["team1"], r["team2"]
        s1, s2 = int(r["score1"]), int(r["score2"])
        country = venue.get(int(r["match"]))
        d = (ratings[t1] + HOME_ADV * (t1 == country)
             - ratings[t2] - HOME_ADV * (t2 == country))
        we = 1.0 / (1.0 + 10.0 ** (-d / 400.0))
        w = 1.0 if s1 > s2 else (0.0 if s1 < s2 else 0.5)  # shootout = draw
        delta = K_WORLD_CUP * g_multiplier(abs(s1 - s2)) * (w - we)
        ratings[t1] += delta
        ratings[t2] -= delta
        played[t1] += 1
        played[t2] += 1
        n += 1
    return ratings, played, n


def main() -> None:
    data = load_all()
    current, played, n = rolled_ratings(data)
    pre = data["elo"]
    rows = sorted(((t, current[t]) for t in pre), key=lambda x: -x[1])
    with open(OUT + ".tmp", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["team", "elo", "elo_pre", "delta", "n_played"])
        for t, e in rows:
            w.writerow([t, round(e, 1), pre[t], round(e - pre[t], 1), played[t]])
    os.replace(OUT + ".tmp", OUT)
    movers = sorted(pre, key=lambda t: abs(current[t] - pre[t]), reverse=True)[:8]
    meta = {
        "n_matches_applied": n,
        "rule": "eloratings.net, K=60, G margin multiplier, shootout=draw, "
                "host home adv +100 (validated in elo_history: corr 0.986)",
        "base": "official eloratings.net 2026-06-10 (data/elo.csv)",
        "top_movers": {t: round(current[t] - pre[t], 1) for t in movers},
    }
    with open(META, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"elo_current.csv: {n} matches applied to 48 teams")
    print("top movers:", ", ".join(f"{t} {current[t]-pre[t]:+.1f}" for t in movers))


if __name__ == "__main__":
    main()
