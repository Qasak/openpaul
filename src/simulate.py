"""Monte Carlo simulation of the full 2026 World Cup.

Usage:  python3 -m src.simulate [--n 100000] [--seed 42] [--sigma 75]
                                [--params data/params_fit.json] [--suffix ""]

Already-completed real matches (data/results.csv) are locked to their actual
results in every simulation run (conditional simulation) — group fixtures by
score, knockout matches by the 'winner' column (covers ET/penalty outcomes).
Outputs:
  data/sim_probs<suffix>.csv   per-team probabilities of reaching each stage
  data/sim_meta<suffix>.json   run metadata (n_sims, seed, sigma, params, locks)
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

from .load_data import DATA, CITY_COUNTRY, HOSTS, canon, load_all
from .model import MatchModel, calibrate
from .tournament import Bracket, allocate_thirds, group_table, rank_thirds

STAGES = ["champion", "final", "sf", "qf", "r16", "r32", "group_winner"]


def venue_bonus(team: str, country: str | None) -> bool:
    return team in HOSTS and country is not None and team == country


def run(n_sims: int = 100_000, seed: int = 42, sigma: float = 0.0,
        out_suffix: str = "", params_path: str | None = None) -> None:
    """sigma > 0 adds per-simulation, per-team Gaussian noise to Elo ratings
    (strength-uncertainty / overdispersion control, FiveThirtyEight-style).
    The noise is drawn once per (team, simulation) and persists through that
    simulated tournament. params_path selects the match-model parameters
    (default: MLE-fitted data/params_fit.json, falling back to the
    curve-calibrated data/params.json)."""
    t0 = time.time()
    data = load_all()
    if params_path is None:
        fit_path = os.path.join(DATA, "params_fit.json")
        params_path = fit_path if os.path.exists(fit_path) else \
            os.path.join(DATA, "params.json")
    if os.path.exists(params_path):
        params = json.load(open(params_path))
    else:
        params = calibrate(params_path)
    mm = MatchModel(params)
    rng = np.random.default_rng(seed)

    elo = data["elo"]
    fifa_rank = dict(zip(data["fifa"]["team"], data["fifa"]["rank"]))
    sched = data["sched"].sort_values("match").reset_index(drop=True)

    teams_all_sorted = sorted(data["teams"]["team"])
    tidx = {t: i for i, t in enumerate(teams_all_sorted)}
    rng_noise = np.random.default_rng(seed + 1)
    noise = (rng_noise.normal(0.0, sigma, (len(tidx), n_sims)).astype(np.float32)
             if sigma > 0 else None)

    def elo_of(team: str, s: int | None = None) -> float:
        base = elo[team]
        if noise is None or s is None:
            return base
        return base + float(noise[tidx[team], s])
    bracket = Bracket.from_data(data["bracket"]["rounds"], CITY_COUNTRY)
    locked = {}
    locked_ko = {}   # knockout: match number -> winner name
    for _, r in data["results"].iterrows():
        if int(r["match"]) >= 73 and isinstance(r.get("winner"), str) and r["winner"]:
            locked_ko[int(r["match"])] = canon(r["winner"])
        else:
            locked[(r["team1"], r["team2"])] = (int(r["score1"]), int(r["score2"]))

    # ---- pre-sample all 72 group fixtures across all sims (fixtures are fixed)
    fixtures = []
    g1 = np.empty((len(sched), n_sims), dtype=np.int16)
    g2 = np.empty((len(sched), n_sims), dtype=np.int16)
    n_locked = 0
    for i, r in sched.iterrows():
        t1, t2, grp = r["team1"], r["team2"], r["group"]
        raw_country = canon(str(r.get("country") or ""))
        country = raw_country if raw_country in HOSTS else \
            CITY_COUNTRY.get(str(r.get("city") or "").split(" (")[0].strip())
        fixtures.append((grp, t1, t2))
        if (t1, t2) in locked:
            s1, s2 = locked[(t1, t2)]
            g1[i, :] = s1; g2[i, :] = s2
            n_locked += 1
        elif (t2, t1) in locked:
            s2, s1 = locked[(t2, t1)]
            g1[i, :] = s1; g2[i, :] = s2
            n_locked += 1
        else:
            d = mm.diff(elo[t1], elo[t2],
                        venue_bonus(t1, country), venue_bonus(t2, country))
            if noise is None:
                a, b = mm.sample_scores(d, n_sims, rng)
            else:
                d_vec = d + noise[tidx[t1], :] - noise[tidx[t2], :]
                a, b = mm.sample_scores_vec(d_vec, rng)
            g1[i, :] = a; g2[i, :] = b

    group_teams = data["teams"].groupby("group")["team"].apply(list).to_dict()
    group_fixture_idx = defaultdict(list)
    for i, (grp, _, _) in enumerate(fixtures):
        group_fixture_idx[grp].append(i)

    counts = {s: Counter() for s in STAGES}
    third_alloc_fallbacks = 0
    ko_lock_mismatches = 0

    for s in range(n_sims):
        # ---- group stage
        winners, runners, thirds_stats, third_team = {}, {}, [], {}
        for grp, tlist in group_teams.items():
            results = [(fixtures[i][1], fixtures[i][2], int(g1[i, s]), int(g2[i, s]))
                       for i in group_fixture_idx[grp]]
            ranked, pts, gd, gf = group_table(tlist, results, fifa_rank)
            winners[grp], runners[grp] = ranked[0], ranked[1]
            third = ranked[2]
            third_team[grp] = third
            thirds_stats.append((grp, third, pts[third], gd[third], gf[third]))
            counts["group_winner"][ranked[0]] += 1

        qualified_third_groups = rank_thirds(thirds_stats, fifa_rank)
        alloc, used_fallback = allocate_thirds(qualified_third_groups,
                                               bracket.third_slots())
        if used_fallback:
            third_alloc_fallbacks += 1

        # ---- knockout
        match_winner, match_loser = {}, {}

        def resolve(src) -> str:
            kind, val = src
            if kind == "W":
                return winners[val]
            if kind == "R":
                return runners[val]
            if kind == "M":
                return match_winner[val]
            if kind == "L":
                return match_loser[val]
            raise ValueError(f"unexpected source kind {kind}")

        for slot in bracket.slots:
            sides = []
            for side in ("home", "away"):
                kind, val = slot[side]
                if kind == "T":
                    sides.append(third_team[alloc[slot["match"]]])
                else:
                    sides.append(resolve(slot[side]))
            t1, t2 = sides
            country = slot["country"]
            if slot["match"] in locked_ko:
                w = locked_ko[slot["match"]]
                if w not in (t1, t2):
                    # the sim routed different teams here than reality did —
                    # possible only if upstream real results are not locked;
                    # counted and surfaced in meta as a data-consistency alarm
                    ko_lock_mismatches += 1
                l = t2 if w == t1 else t1
            else:
                d = mm.diff(elo_of(t1, s), elo_of(t2, s),
                            venue_bonus(t1, country), venue_bonus(t2, country))
                t1_wins = mm.sample_knockout(d, rng)
                w, l = (t1, t2) if t1_wins else (t2, t1)
            match_winner[slot["match"]] = w
            match_loser[slot["match"]] = l

            rnd = slot["round"].upper()
            if rnd == "R32":
                counts["r32"][t1] += 1; counts["r32"][t2] += 1
                counts["r16"][w] += 1
            elif rnd == "R16":
                counts["qf"][w] += 1
            elif rnd == "QF":
                counts["sf"][w] += 1
            elif rnd == "SF":
                counts["final"][w] += 1
            elif rnd == "FINAL":
                counts["champion"][w] += 1

    # ---- output
    rows = []
    for t in teams_all_sorted:
        rows.append({
            "team": t,
            "p_champion": counts["champion"][t] / n_sims,
            "p_final": counts["final"][t] / n_sims,
            "p_sf": counts["sf"][t] / n_sims,
            "p_qf": counts["qf"][t] / n_sims,
            "p_r16": counts["r16"][t] / n_sims,
            "p_r32": counts["r32"][t] / n_sims,
            "p_group_winner": counts["group_winner"][t] / n_sims,
            "elo": elo[t],
        })
    import pandas as pd
    df = pd.DataFrame(rows).sort_values("p_champion", ascending=False)
    df.to_csv(os.path.join(DATA, f"sim_probs{out_suffix}.csv"), index=False)
    meta = {
        "n_sims": n_sims, "seed": seed, "sigma": sigma, "params": params,
        "params_path": params_path,
        "locked_matches": n_locked, "locked_knockout": len(locked_ko),
        "ko_lock_mismatches": ko_lock_mismatches,
        "runtime_s": round(time.time() - t0, 1),
        "third_alloc_fallbacks": third_alloc_fallbacks,
    }
    with open(os.path.join(DATA, f"sim_meta{out_suffix}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(df.head(15).to_string(index=False))
    print(f"\n{n_sims} sims in {meta['runtime_s']}s, locked real matches: {n_locked}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sigma", type=float, default=75.0,
                    help="Elo strength-uncertainty noise (backtest-calibrated default)")
    ap.add_argument("--params", type=str, default=None,
                    help="params JSON (default data/params_fit.json if present)")
    ap.add_argument("--suffix", type=str, default="",
                    help='output suffix, e.g. "_sigma0" (default: primary outputs)')
    args = ap.parse_args()
    run(args.n, args.seed, args.sigma, args.suffix, args.params)
