"""Per-match knockout advancement forecasts (rolling update).

For every fixture in data/schedule_ko.csv with both teams determined:
  p(team1 advances) = P(win 90') + P(draw 90') * (P(win ET) + 0.5 * P(draw ET))
— the analytic counterpart of MatchModel.sample_knockout (extra time as a
plain double Poisson at 1/3 rate, penalty shootout as a coin flip; both
documented simplifications). The headline number averages the primary
simulation's Elo strength-uncertainty noise (sigma=75) by Gauss-Hermite
quadrature over d ~ N(d0, sigma*sqrt(2)); the sigma=0 point estimate is
reported alongside. Ratings are the frozen pre-tournament Elo, so the
numbers for already-played fixtures are identical to what the model said
before kickoff.

Round 4 runs TWO variants in parallel (public dual-track verification):
  r4 (production): ratings from data/elo_current.csv (rolled forward with
     played matches, src/elo_update.py) -> data/ko_forecast.csv +
     append-only ledger predictions/ko_forecasts_r4.csv
  v2 (sealed baseline): frozen 2026-06-10 ratings -> data/ko_forecast_v2.csv
     + the original ledger predictions/ko_forecasts.csv
Both ledgers follow the same sealing rule: a fixture is added exactly once,
and only while the source feed still lists it as SCHEDULED; rows are never
rewritten — the git commit supplies the public timestamp. Feed unreachable
-> no append this run (integrity over coverage). score.py Brier-scores the
two ledgers head-to-head on their common matches.

Usage:  python3 -m src.ko_forecast [--variant r4|v2]     (default r4)
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
from scipy.stats import poisson

from .load_data import DATA, HOSTS, canon, load_all
from .model import ET_FACTOR, MAX_GOALS, MatchModel, wdl_from_grid

ROOT = os.path.dirname(DATA)
SCHED_KO = os.path.join(DATA, "schedule_ko.csv")
VARIANTS = {
    "r4": {"out": os.path.join(DATA, "ko_forecast.csv"),
           "ledger": os.path.join(ROOT, "predictions", "ko_forecasts_r4.csv"),
           "elo": "current"},
    "v2": {"out": os.path.join(DATA, "ko_forecast_v2.csv"),
           "ledger": os.path.join(ROOT, "predictions", "ko_forecasts.csv"),
           "elo": "frozen"},
}
SIGMA = 75.0          # primary simulation's strength-uncertainty (backtest-selected)
GH_NODES = 41

OUT_COLS = ["match", "round", "date", "city", "country", "team1", "team2",
            "elo1", "elo2", "p1_advance", "p1_advance_sigma0",
            "p1_win90", "p_draw90", "p2_win90"]
LEDGER_COLS = ["forecast_at", "sigma"] + OUT_COLS


def p_advance_point(mm: MatchModel, d: float) -> tuple[float, float, float, float]:
    """(p1_advance, w90, d90, l90) for a single Elo difference d."""
    lam1, lam2 = mm.rates(d)
    from .model import score_grid
    w, dr, l = wdl_from_grid(score_grid(lam1, lam2, mm.rho))
    goals = np.arange(MAX_GOALS + 1)
    g = np.outer(poisson.pmf(goals, lam1 * ET_FACTOR),
                 poisson.pmf(goals, lam2 * ET_FACTOR))
    g = g / g.sum()
    w_et = float(np.tril(g, -1).sum())
    d_et = float(np.trace(g))
    return w + dr * (w_et + 0.5 * d_et), w, dr, l


def forecast_fixture(mm: MatchModel, d0: float, sigma: float = SIGMA) -> dict:
    """sigma-averaged advancement/90' probabilities via Gauss-Hermite."""
    nodes, weights = np.polynomial.hermite_e.hermegauss(GH_NODES)
    weights = weights / weights.sum()
    s = sigma * np.sqrt(2.0)          # Var(noise1 - noise2) = 2 sigma^2
    acc = np.zeros(4)
    for x, wt in zip(nodes, weights):
        acc += wt * np.array(p_advance_point(mm, d0 + s * float(x)))
    p0 = p_advance_point(mm, d0)[0]
    return {"p1_advance": float(acc[0]), "p1_advance_sigma0": float(p0),
            "p1_win90": float(acc[1]), "p_draw90": float(acc[2]),
            "p2_win90": float(acc[3])}


def scheduled_on_feed(fixtures: list[dict], teams: set[str]) -> set[int] | None:
    """Match numbers among `fixtures` the feed still lists as SCHEDULED.
    None = feed unreachable (caller must not append to the ledger)."""
    from .ingest_results import parse_espn, resolve
    status: dict[frozenset, str] = {}
    try:
        for d in sorted({f["date"] for f in fixtures}):
            for ev in parse_espn(d.replace("-", "")):
                th = resolve(ev["home"], teams, set())
                ta = resolve(ev["away"], teams, set())
                if th and ta:
                    status[frozenset((th, ta))] = ev["status"]
    except Exception as e:
        print(f"WARNING: feed unreachable ({e}) — no ledger append this run",
              file=sys.stderr)
        return None
    return {int(f["match"]) for f in fixtures
            if status.get(frozenset((f["team1"], f["team2"]))) == "STATUS_SCHEDULED"}


def main(variant: str = "r4") -> int:
    cfg = VARIANTS[variant]
    OUT, LEDGER = cfg["out"], cfg["ledger"]
    data = load_all()
    if not os.path.exists(SCHED_KO):
        print("no data/schedule_ko.csv yet — nothing to forecast")
        return 0
    fit_path = os.path.join(DATA, "params_fit.json")
    params_path = fit_path if os.path.exists(fit_path) else \
        os.path.join(DATA, "params.json")
    mm = MatchModel(json.load(open(params_path)))
    elo = data["elo"]
    elo_cur = os.path.join(DATA, "elo_current.csv")
    if cfg["elo"] == "current" and os.path.exists(elo_cur):
        import pandas as pd
        cur = pd.read_csv(elo_cur)
        elo = {canon(t): e for t, e in zip(cur["team"], cur["elo"])}

    with open(SCHED_KO, encoding="utf-8") as f:
        fixtures = [r for r in csv.DictReader(f)]

    rows = []
    for r in fixtures:
        t1, t2 = canon(r["team1"]), canon(r["team2"])
        country = r.get("country") or None
        d0 = mm.diff(elo[t1], elo[t2],
                     t1 in HOSTS and t1 == country,
                     t2 in HOSTS and t2 == country)
        fc = forecast_fixture(mm, d0)
        rows.append({
            "match": int(r["match"]), "round": r["group"], "date": r["date"],
            "city": r["city"], "country": r.get("country", ""),
            "team1": t1, "team2": t2,
            "elo1": elo[t1], "elo2": elo[t2],
            **{k: round(v, 4) for k, v in fc.items()},
        })

    with open(OUT + ".tmp", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS)
        w.writeheader()
        for row in sorted(rows, key=lambda x: x["match"]):
            w.writerow(row)
    os.replace(OUT + ".tmp", OUT)
    print(f"{os.path.basename(OUT)} [{variant}]: {len(rows)} fixture(s)")

    # ---- append-only pre-match ledger
    ledgered: set[int] = set()
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            ledgered = {int(r["match"]) for r in csv.DictReader(f)}
    recorded = {int(m) for m in data["results"][data["results"]["match"] >= 73]["match"]}
    candidates = [r for r in rows
                  if r["match"] not in ledgered and r["match"] not in recorded]
    if not candidates:
        return 0
    cand_fx = [{"match": r["match"], "date": r["date"],
                "team1": r["team1"], "team2": r["team2"]} for r in candidates]
    pre_match = scheduled_on_feed(cand_fx, set(data["teams"]["team"]))
    if pre_match is None:
        return 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = [r for r in candidates if r["match"] in pre_match]
    skipped = [r["match"] for r in candidates if r["match"] not in pre_match]
    if skipped:
        print(f"ledger: skipped match(es) {skipped} — no longer SCHEDULED on "
              f"the feed (in play or finished), pre-match window missed")
    if new:
        exists = os.path.exists(LEDGER)
        with open(LEDGER, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
            if not exists:
                w.writeheader()
            for r in sorted(new, key=lambda x: x["match"]):
                w.writerow({"forecast_at": now, "sigma": SIGMA, **r})
        print(f"ledger[{variant}]: {len(new)} pre-match forecast(s) appended "
              f"({[r['match'] for r in new]})")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="r4")
    sys.exit(main(ap.parse_args().variant))
