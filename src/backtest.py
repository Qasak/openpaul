"""Backtest the tournament model on the 2018 and 2022 World Cups to choose
the strength-uncertainty parameter sigma empirically.

For each tournament we use:
  - official eloratings.net START-of-tournament ratings (2018_World_Cup_start.tsv /
    2022_World_Cup_start.tsv via data/raw/research_round2.json),
  - model parameters (a, b, rho) MLE-fitted ONLY on matches played BEFORE the
    tournament (no leakage), on recomputed Elo affine-mapped to official scale,
  - the real group draw and knockout bracket (verified against FIFA match
    schedule PDFs), 2018/2022 tiebreaker order (overall GD before head-to-head).

For each sigma on a grid we simulate the tournament N times and score the
predicted stage-reach probabilities (r16/qf/sf/final/champion, cumulative)
for all 32 teams against what actually happened, with binary log-loss and
Brier. sigma* minimizes total log-loss across both tournaments.

Usage:  python3 -m src.backtest        (writes data/backtest_sigma.json)
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .fit import HOME_ADV, nll
from .load_data import DATA
from .model import MatchModel
from .tournament import group_table, parse_source

SIGMA_GRID = [0, 25, 50, 75, 100, 125, 150]
N_SIMS = 20_000
SEED = 7

# QF/SF/final shape shared by 2018 and 2022 (verified vs FIFA schedule PDFs)
QF = [(57, 49, 50), (58, 53, 54), (59, 51, 52), (60, 55, 56)]
SF = [(61, 57, 58), (62, 59, 60)]
FINAL = (64, 61, 62)

STAGE_ORDER = ["r16", "qf", "sf", "final", "champion"]
STAGE_RANK = {"groups": 0, "r16": 1, "qf": 2, "sf": 3, "final": 4, "champion": 5}


def fit_window(start: str, end: str) -> dict:
    hist = pd.read_csv(os.path.join(DATA, "raw", "matches_with_elo.csv"))
    meta = json.load(open(os.path.join(DATA, "elo_recompute_meta.json")))
    hist = hist[(hist["date"] >= start) & (hist["date"] < end)].copy()
    for side in ("home", "away"):
        hist[f"elo_{side}_m"] = meta["slope"] * hist[f"elo_{side}_pre"] + meta["intercept"]
    d = (hist["elo_home_m"] + np.where(hist["neutral"], 0.0, HOME_ADV)
         - hist["elo_away_m"]).to_numpy()
    h = hist["home_score"].clip(upper=10).to_numpy()
    a = hist["away_score"].clip(upper=10).to_numpy()
    res = minimize(nll, np.array([0.26, 0.002, -0.05]), args=(d, h, a),
                   method="Nelder-Mead",
                   options={"xatol": 1e-6, "fatol": 1e-4, "maxiter": 4000})
    if not res.success:
        raise RuntimeError(f"backtest window {start}..{end} MLE did not converge: "
                           f"{res.message}")
    aa, bb, rho = (float(v) for v in res.x)
    return {"a": aa, "b": bb, "rho": rho, "home_elo": HOME_ADV,
            "n_fit": int(len(hist)), "window": [start, end]}


def simulate_tournament(elo: dict, groups: dict, r16: list, host: str,
                        params: dict, sigma: float, n_sims: int,
                        rng: np.random.Generator) -> dict:
    """Returns per-team stage-reach probabilities {team: {stage: p}}."""
    mm = MatchModel(params)
    teams = sorted(elo)
    tidx = {t: i for i, t in enumerate(teams)}
    elo_rank = {t: i + 1 for i, t in enumerate(sorted(teams, key=elo.get, reverse=True))}
    counts = {s: Counter() for s in STAGE_ORDER}
    pairs = {g: [(ts[i], ts[j]) for i in range(4) for j in range(i + 1, 4)]
             for g, ts in groups.items()}

    noise = (rng.normal(0.0, sigma, (len(teams), n_sims)).astype(np.float32)
             if sigma > 0 else None)

    def e(t: str, s: int) -> float:
        base = elo[t]
        return base + float(noise[tidx[t], s]) if noise is not None else base

    for s in range(n_sims):
        winners, runners = {}, {}
        for g, ts in groups.items():
            results = []
            for t1, t2 in pairs[g]:
                d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
                g1, g2 = mm.sample_scores(d, 1, rng)
                results.append((t1, t2, int(g1[0]), int(g2[0])))
            ranked, *_ = group_table(ts, results, elo_rank, order_rule="overall_first")
            winners[g], runners[g] = ranked[0], ranked[1]
            counts["r16"][ranked[0]] += 1
            counts["r16"][ranked[1]] += 1

        mwin = {}
        for mno, hs, as_ in r16:
            t1 = winners[hs[1]] if hs[0] == "W" else runners[hs[1]]
            t2 = winners[as_[1]] if as_[0] == "W" else runners[as_[1]]
            d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
            w = t1 if mm.sample_knockout(d, rng) else t2
            mwin[mno] = w
            counts["qf"][w] += 1
        for stage, matches in (("sf", QF), ("final", SF)):
            for mno, m1, m2 in matches:
                t1, t2 = mwin[m1], mwin[m2]
                d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
                w = t1 if mm.sample_knockout(d, rng) else t2
                mwin[mno] = w
                counts[stage][w] += 1
        t1, t2 = mwin[FINAL[1]], mwin[FINAL[2]]
        d = mm.diff(e(t1, s), e(t2, s), t1 == host, t2 == host)
        w = t1 if mm.sample_knockout(d, rng) else t2
        counts["champion"][w] += 1

    return {t: {st: counts[st][t] / n_sims for st in STAGE_ORDER} for t in teams}


def score(probs: dict, stage_reached: dict) -> dict:
    ll, brier, n = 0.0, 0.0, 0
    for t, ps in probs.items():
        actual = STAGE_RANK[stage_reached[t]]
        for i, st in enumerate(STAGE_ORDER, start=1):
            p = min(max(ps[st], 1e-9), 1 - 1e-9)
            y = 1.0 if actual >= i else 0.0
            ll += -(y * np.log(p) + (1 - y) * np.log(1 - p))
            brier += (p - y) ** 2
            n += 1
    return {"logloss": ll / n, "brier": brier / n, "n": n}


def main() -> None:
    t0 = time.time()
    r2 = json.load(open(os.path.join(DATA, "raw", "research_round2.json")))
    snaps = {s["date"]: {x["team"]: x["elo"] for x in s["top_teams"]}
             for s in r2["elo-methodology"]["archived_snapshots"]}
    tournaments = {t["year"]: t for t in r2["backtest-structures"]["tournaments"]}
    cfg = {
        2018: {"elo": snaps["2018-06-13"], "host": "Russia",
               "fit": ("2010-01-01", "2018-06-13")},
        2022: {"elo": snaps["2022-11-19"], "host": "Qatar",
               "fit": ("2014-01-01", "2022-11-19")},
    }

    out = {"sigma_grid": SIGMA_GRID, "n_sims": N_SIMS, "per_tournament": {}, "totals": {}}
    fitted = {}
    for year, c in cfg.items():
        t = tournaments[year]
        groups = {g["group"]: g["teams"] for g in t["groups"]}
        flat = [x for ts in groups.values() for x in ts]
        missing = [x for x in flat if x not in c["elo"]]
        if missing:
            raise ValueError(f"{year}: missing start Elo for {missing}")
        r16 = [(p["match"], parse_source(p["home_source"]), parse_source(p["away_source"]))
               for p in t["r16_pairings"]]
        fitted[year] = fit_window(*c["fit"])
        print(f"{year}: params a={fitted[year]['a']:.4f} b={fitted[year]['b']:.5f} "
              f"rho={fitted[year]['rho']:.4f} (n={fitted[year]['n_fit']})")

    for sigma in SIGMA_GRID:
        tot_ll, tot_br, tot_n = 0.0, 0.0, 0
        for year, c in cfg.items():
            t = tournaments[year]
            groups = {g["group"]: g["teams"] for g in t["groups"]}
            r16 = [(p["match"], parse_source(p["home_source"]), parse_source(p["away_source"]))
                   for p in t["r16_pairings"]]
            stage = {x["team"]: x["stage"] for x in t["stage_reached"]}
            rng = np.random.default_rng(SEED + year + sigma)
            probs = simulate_tournament(c["elo"], groups, r16, c["host"],
                                        fitted[year], float(sigma), N_SIMS, rng)
            sc = score(probs, stage)
            out["per_tournament"].setdefault(str(year), {})[str(sigma)] = sc
            tot_ll += sc["logloss"] * sc["n"]; tot_br += sc["brier"] * sc["n"]; tot_n += sc["n"]
        out["totals"][str(sigma)] = {"logloss": tot_ll / tot_n, "brier": tot_br / tot_n}
        print(f"sigma={sigma:>3}: logloss={tot_ll/tot_n:.4f} brier={tot_br/tot_n:.4f}")

    best = min(out["totals"], key=lambda k: out["totals"][k]["logloss"])
    out["sigma_star"] = int(best)
    out["params_per_tournament"] = fitted
    out["runtime_s"] = round(time.time() - t0, 1)
    with open(os.path.join(DATA, "backtest_sigma.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nsigma* = {best} (runtime {out['runtime_s']}s)")


if __name__ == "__main__":
    main()
