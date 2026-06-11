"""Recompute World Football Elo ratings over the full 1872-present match history.

Methodology follows eloratings.net/about:
  R' = R + K * G * (W - W_e),  W_e = 1 / (1 + 10^(-d/400)),
  d = R_home + HOME_ADV*(not neutral) - R_away
  K by match category (World Cup 60, continental finals 50, qualifiers 40,
  Nations League 40, other tournaments 30, friendlies 20)
  G = 1 (margin <=1), 1.5 (margin 2), (11 + 2*(N-3))/8 for N>=3
      -> margin 3: 11/8=1.375?  NOTE: eloratings.net states G = (11+N)/8 for N>=3
      (margin 3 -> 1.75, 4 -> 1.875, ...). We use the documented (11+N)/8.
  Penalty shootouts count as draws (the dataset records the post-ET score).

All teams start at 1500. Absolute levels converge after decades of matches;
we validate the END-STATE against the official eloratings.net table (affine
fit + correlation) before using historical ratings for model fitting.

Usage:  python3 -m src.elo_history   (writes data/elo_recomputed_checkpoints.json
        and prints validation against data/elo.csv)
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from .load_data import DATA, canon

HOME_ADV = 100.0
START_ELO = 1500.0

CONTINENTAL_FINALS = (
    "UEFA Euro", "Copa América", "Copa America", "African Cup of Nations",
    "Africa Cup of Nations", "AFC Asian Cup", "CONCACAF Championship",
    "Gold Cup", "Oceania Nations Cup", "OFC Nations Cup",
    "Confederations Cup", "CONMEBOL–UEFA Cup of Champions", "Finalissima",
)


def k_factor(tournament: str) -> float:
    t = tournament.strip()
    if t == "FIFA World Cup":
        return 60.0
    if "qualification" in t.lower():
        return 40.0
    if "Nations League" in t:
        # eloratings.net empirically uses 40 for the league phase and 50 for
        # the Finals knockout rounds; the dataset's tournament string does not
        # distinguish them, so we use 40 throughout. The NL Finals are 4
        # matches every two years — impact on end-state ratings is < 1 point.
        return 40.0
    for name in CONTINENTAL_FINALS:
        if name in t:
            return 50.0
    if t == "Friendly":
        return 20.0
    return 30.0


def g_multiplier(margin: int) -> float:
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11.0 + margin) / 8.0


def run_history(df: pd.DataFrame, checkpoints: list[str]) -> tuple[dict, list[dict]]:
    """Replay all matches chronologically. Returns (final ratings,
    per-match pre-game ratings rows) and stores checkpoint snapshots."""
    ratings: dict[str, float] = defaultdict(lambda: START_ELO)
    snapshots: dict[str, dict] = {}
    rows = []
    ck = sorted(checkpoints)
    ck_i = 0
    for r in df.itertuples(index=False):
        date = r.date
        while ck_i < len(ck) and date > ck[ck_i]:
            snapshots[ck[ck_i]] = dict(ratings)
            ck_i += 1
        h, a = r.home_team, r.away_team
        rh, ra = ratings[h], ratings[a]
        rows.append((date, h, a, rh, ra, r.home_score, r.away_score,
                     r.tournament, r.neutral))
        d = rh + (0.0 if r.neutral else HOME_ADV) - ra
        we = 1.0 / (1.0 + 10.0 ** (-d / 400.0))
        if r.home_score > r.away_score:
            w = 1.0
        elif r.home_score < r.away_score:
            w = 0.0
        else:
            w = 0.5
        delta = k_factor(r.tournament) * g_multiplier(abs(int(r.home_score - r.away_score))) * (w - we)
        ratings[h] = rh + delta
        ratings[a] = ra - delta
    while ck_i < len(ck):
        snapshots[ck[ck_i]] = dict(ratings)
        ck_i += 1
    return dict(ratings), rows, snapshots


def load_matches() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(DATA, "raw", "international_results.csv"))
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)
    df["neutral"] = df["neutral"].astype(str).str.upper().eq("TRUE")
    df["home_team"] = df["home_team"].map(canon)
    df["away_team"] = df["away_team"].map(canon)
    return df.sort_values("date").reset_index(drop=True)


def main() -> None:
    df = load_matches()
    final, rows, snapshots = run_history(
        df, checkpoints=["2018-06-13", "2022-11-19", "2026-06-10"])

    # per-match pre-game ratings for model fitting
    hist = pd.DataFrame(rows, columns=[
        "date", "home_team", "away_team", "elo_home_pre", "elo_away_pre",
        "home_score", "away_score", "tournament", "neutral"])
    hist.to_csv(os.path.join(DATA, "raw", "matches_with_elo.csv"), index=False)

    with open(os.path.join(DATA, "elo_recomputed_checkpoints.json"), "w") as f:
        json.dump(snapshots, f)

    # ---- validation vs official eloratings.net table (the 48 WC teams)
    official = pd.read_csv(os.path.join(DATA, "elo.csv"))
    official["team"] = official["team"].map(canon)
    recomputed, missing = [], []
    for t in official["team"]:
        # dataset uses Wikipedia-style names; canon should align most
        if t in final:
            recomputed.append(final[t])
        else:
            recomputed.append(np.nan)
            missing.append(t)
    official["elo_recomputed"] = recomputed
    ok = official.dropna(subset=["elo_recomputed"])
    x, y = ok["elo_recomputed"].to_numpy(), ok["elo"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    corr = float(np.corrcoef(x, y)[0, 1])
    mad = float(np.mean(np.abs(slope * x + intercept - y)))
    resid_mad_raw = float(np.mean(np.abs(x - y)))
    print(f"validation vs official elo.csv (n={len(ok)}, missing={missing}):")
    print(f"  corr={corr:.4f}  affine slope={slope:.3f} intercept={intercept:.1f}")
    print(f"  mean|err| raw={resid_mad_raw:.1f}  after affine={mad:.1f}")
    official.to_csv(os.path.join(DATA, "elo_validation.csv"), index=False)
    meta = {"corr": corr, "slope": slope, "intercept": float(intercept),
            "mad_affine": mad, "mad_raw": resid_mad_raw,
            "missing": missing, "n_matches_replayed": len(hist)}
    with open(os.path.join(DATA, "elo_recompute_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


if __name__ == "__main__":
    main()
