"""Round-4 validation of the Elo rolling update (the one shipping change).

Question: with the real group results locked, do GROUP-STAGE-UPDATED ratings
predict the knockout stages better than the frozen start-of-tournament
ratings? Backtested on 2018 and 2022 exactly like production: official
start Elo, leak-free per-tournament (a,b,rho), sigma=75, the real bracket,
group results locked, knockout simulated.

Rolling rule is production's (src/elo_update.py): eloratings.net update,
K=60, margin multiplier G, shootout=draw, home side +100 when not neutral
(the dataset's own neutral flag).

Scored on stage-reach (qf/sf/final/champion, cumulative binary logloss +
Brier) over the 32 teams; the r16 stage is omitted — group results are
locked, so it is deterministic and identical for both rating sets.

Group standings are NOT re-derived from the locked scores: 2018 group H was
decided by fair-play points (Japan over Senegal on identical points/GD/
goals/h2h), which no score-based tiebreaker can reproduce. Instead the real
winner/runner-up of every group is read off the REAL round-of-16 matches in
the dataset — in the 32-team format every R16 tie pairs the winner of one
group with the runner-up of another, so group identity alone assigns both.

Usage:  python3 -m src.backtest_roll_r4   (writes data/backtest_roll_r4.json)
"""
from __future__ import annotations

import json
import os
from collections import Counter

import numpy as np
import pandas as pd

from .backtest import FINAL, N_SIMS, QF, SEED, SF, fit_window
from .elo_history import g_multiplier
from .load_data import DATA, canon
from .model import MatchModel
from .tournament import parse_source

STAGES = ["qf", "sf", "final", "champion"]
STAGE_RANK = {"groups": 0, "r16": 1, "qf": 2, "sf": 3, "final": 4, "champion": 5}
GROUP_WINDOWS = {2018: ("2018-06-14", "2018-06-29"), 2022: ("2022-11-20", "2022-12-02")}
R16_WINDOWS = {2018: ("2018-06-30", "2018-07-03"), 2022: ("2022-12-03", "2022-12-06")}


def real_group_results(year: int, teams: set[str]) -> pd.DataFrame:
    hist = pd.read_csv(os.path.join(DATA, "raw", "matches_with_elo.csv"))
    lo, hi = GROUP_WINDOWS[year]
    wc = hist[(hist["tournament"] == "FIFA World Cup")
              & (hist["date"] >= lo) & (hist["date"] <= hi)].copy()
    for c in ("home_team", "away_team"):
        wc[c] = wc[c].map(canon)
    wc = wc[wc["home_team"].isin(teams) & wc["away_team"].isin(teams)]
    if len(wc) != 48:
        raise ValueError(f"{year}: expected 48 group matches, got {len(wc)}")
    return wc


def roll_elo(elo: dict[str, float], matches: pd.DataFrame) -> dict[str, float]:
    r = dict(elo)
    for m in matches.sort_values("date").itertuples(index=False):
        h, a = m.home_team, m.away_team
        d = r[h] + (0.0 if m.neutral else 100.0) - r[a]
        we = 1.0 / (1.0 + 10.0 ** (-d / 400.0))
        w = 1.0 if m.home_score > m.away_score else \
            (0.0 if m.home_score < m.away_score else 0.5)
        delta = 60.0 * g_multiplier(abs(int(m.home_score - m.away_score))) * (w - we)
        r[h] += delta
        r[a] -= delta
    return r


def real_standings(year: int, groups: dict, teams: set[str],
                   host: str) -> tuple[dict, dict, set]:
    """Real group winners/runners-up, read off the real R16 matches: each
    R16 tie is Winner(gX) vs Runner-up(gY), so group identity assigns both."""
    hist = pd.read_csv(os.path.join(DATA, "raw", "matches_with_elo.csv"))
    lo, hi = R16_WINDOWS[year]
    r16 = hist[(hist["tournament"] == "FIFA World Cup")
               & (hist["date"] >= lo) & (hist["date"] <= hi)].copy()
    for c in ("home_team", "away_team"):
        r16[c] = r16[c].map(canon)
    r16 = r16[r16["home_team"].isin(teams) & r16["away_team"].isin(teams)]
    if len(r16) != 8:
        raise ValueError(f"{year}: expected 8 R16 matches, got {len(r16)}")
    team_group = {t: g for g, ts in groups.items() for t in ts}
    winners, runners = {}, {}
    # pass 1: non-host ties — the dataset's home side carries FIFA's
    # designation = the group winner. Host ties are listed with the host at
    # home regardless of designation (2018 Russia-Spain), so they are
    # resolved in pass 2 from the one-winner-one-runner-per-group constraint.
    deferred = []
    for m in r16.itertuples(index=False):
        if host in (m.home_team, m.away_team):
            deferred.append(m)
            continue
        winners[team_group[m.home_team]] = m.home_team
        runners[team_group[m.away_team]] = m.away_team
    for m in deferred:
        options = []
        for w, r in ((m.home_team, m.away_team), (m.away_team, m.home_team)):
            gw, gr = team_group[w], team_group[r]
            if winners.get(gw, w) == w and runners.get(gr, r) == r:
                options.append((gw, w, gr, r))
        if len(options) != 1:
            raise ValueError(f"{year}: host tie {m.home_team} vs {m.away_team} "
                             f"has {len(options)} consistent role assignments")
        gw, w, gr, r = options[0]
        winners[gw], runners[gr] = w, r
    if set(winners) != set(groups) or set(runners) != set(groups):
        raise ValueError(f"{year}: R16 matches do not cover all groups "
                         f"(winners {sorted(winners)}, runners {sorted(runners)})")
    real_pairs = {frozenset((m.home_team, m.away_team))
                  for m in r16.itertuples(index=False)}
    return winners, runners, real_pairs


def ko_probs(elo: dict, winners: dict, runners: dict, r16: list,
             host: str, params: dict, sigma: float, seed: int) -> dict:
    """Real standings pinned; knockout simulated."""
    mm = MatchModel(params)
    teams = sorted(elo)
    tidx = {t: i for i, t in enumerate(teams)}
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma, (len(teams), N_SIMS)).astype(np.float32) \
        if sigma > 0 else None

    def e(t, s):
        return elo[t] + (float(noise[tidx[t], s]) if noise is not None else 0.0)

    counts = {st: Counter() for st in STAGES}
    for s in range(N_SIMS):
        mwin = {}
        for mno, hs, as_ in r16:
            t1 = winners[hs[1]] if hs[0] == "W" else runners[hs[1]]
            t2 = winners[as_[1]] if as_[0] == "W" else runners[as_[1]]
            d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
            mwin[mno] = t1 if mm.sample_knockout(d, rng) else t2
            counts["qf"][mwin[mno]] += 1
        for stage, matches in (("sf", QF), ("final", SF)):
            for mno, m1, m2 in matches:
                t1, t2 = mwin[m1], mwin[m2]
                d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
                mwin[mno] = t1 if mm.sample_knockout(d, rng) else t2
                counts[stage][mwin[mno]] += 1
        t1, t2 = mwin[FINAL[1]], mwin[FINAL[2]]
        d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
        counts["champion"][t1 if mm.sample_knockout(d, rng) else t2] += 1
    return {t: {st: counts[st][t] / N_SIMS for st in STAGES} for t in teams}


def score(probs: dict, stage_reached: dict) -> dict:
    ll, brier, n = 0.0, 0.0, 0
    for t, ps in probs.items():
        actual = STAGE_RANK[stage_reached[t]]
        for st in STAGES:
            p = min(max(ps[st], 1e-9), 1 - 1e-9)
            y = 1.0 if actual >= STAGE_RANK[st] else 0.0
            ll += -(y * np.log(p) + (1 - y) * np.log(1 - p))
            brier += (p - y) ** 2
            n += 1
    return {"logloss": ll / n, "brier": brier / n, "n": n}


def main() -> None:
    r2 = json.load(open(os.path.join(DATA, "raw", "research_round2.json")))
    snaps = {s["date"]: {x["team"]: x["elo"] for x in s["top_teams"]}
             for s in r2["elo-methodology"]["archived_snapshots"]}
    tournaments = {t["year"]: t for t in r2["backtest-structures"]["tournaments"]}
    cfg = {2018: {"elo": snaps["2018-06-13"], "host": "Russia",
                  "fit": ("2010-01-01", "2018-06-13")},
           2022: {"elo": snaps["2022-11-19"], "host": "Qatar",
                  "fit": ("2014-01-01", "2022-11-19")}}
    out = {"n_sims": N_SIMS, "sigma": 75, "per_tournament": {}}
    tot = {"frozen": [0.0, 0], "rolled": [0.0, 0]}
    for year, c in cfg.items():
        t = tournaments[year]
        groups = {g["group"]: g["teams"] for g in t["groups"]}
        teams = {x for ts in groups.values() for x in ts}
        r16 = [(p["match"], parse_source(p["home_source"]), parse_source(p["away_source"]))
               for p in t["r16_pairings"]]
        stage = {x["team"]: x["stage"] for x in t["stage_reached"]}
        params = fit_window(*c["fit"])
        results = real_group_results(year, teams)
        winners, runners, real_pairs = real_standings(year, groups, teams,
                                                      c["host"])
        derived_pairs = {frozenset((winners[hs[1]], runners[as_[1]]))
                         for _, hs, as_ in r16}
        if derived_pairs != real_pairs:
            raise ValueError(f"{year}: derived standings do not reproduce the "
                             f"real R16 pairings — home/away designation in "
                             f"the dataset is not the group winner")
        rolled = roll_elo(c["elo"], results)
        movers = sorted(teams, key=lambda x: abs(rolled[x] - c["elo"][x]), reverse=True)[:3]
        print(f"{year}: top movers " + ", ".join(
            f"{m} {rolled[m]-c['elo'][m]:+.0f}" for m in movers))
        row = {}
        for name, elo in (("frozen", c["elo"]), ("rolled", rolled)):
            probs = ko_probs(elo, winners, runners, r16, c["host"], params,
                             75.0, SEED + year)
            sc = score(probs, stage)
            row[name] = sc
            tot[name][0] += sc["logloss"] * sc["n"]
            tot[name][1] += sc["n"]
            print(f"  {name:>6}: KO stage-reach logloss {sc['logloss']:.4f} "
                  f"brier {sc['brier']:.4f} (n={sc['n']})")
        out["per_tournament"][str(year)] = row
    out["totals"] = {k: v[0] / v[1] for k, v in tot.items()}
    out["verdict"] = "rolled better" if out["totals"]["rolled"] < \
        out["totals"]["frozen"] else "frozen better"
    print(f"\ntotals: frozen {out['totals']['frozen']:.4f}  "
          f"rolled {out['totals']['rolled']:.4f}  -> {out['verdict']}")
    with open(os.path.join(DATA, "backtest_roll_r4.json"), "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
