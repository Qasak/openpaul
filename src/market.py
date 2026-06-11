"""Market odds -> implied probabilities -> value (edge) analysis.

Usage:  python3 -m src.market

De-vig methods:
  proportional  p_i = q_i / sum(q)            (q_i = 1/decimal_odds)
  power         p_i = q_i^k with k solved so sum(p) = 1
                (k > 1 shrinks longshots harder -> corrects favourite-longshot bias)

Edge = model champion probability (from simulation) - market implied probability.
This is the headline 'value' quantity reported by market-deviation pickers.

Outputs data/value_analysis.csv.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .load_data import DATA, load_all


def devig_proportional(decimal_odds: np.ndarray) -> np.ndarray:
    q = 1.0 / decimal_odds
    return q / q.sum()


def devig_power(decimal_odds: np.ndarray) -> np.ndarray:
    q = 1.0 / decimal_odds

    def f(k: float) -> float:
        return (q ** k).sum() - 1.0

    k = brentq(f, 0.5, 5.0)
    return q ** k


def run() -> pd.DataFrame:
    data = load_all()
    odds = data["odds"].dropna(subset=["decimal_odds"]).copy()
    sim = pd.read_csv(os.path.join(DATA, "sim_probs.csv"))

    o = odds["decimal_odds"].to_numpy(dtype=float)
    overround = (1.0 / o).sum() - 1.0
    odds["p_market_prop"] = devig_proportional(o)
    odds["p_market_power"] = devig_power(o)

    keep = ["team", "decimal_odds", "p_market_prop", "p_market_power"]
    has_sharp = "decimal_odds_sharp" in odds.columns and \
        odds["decimal_odds_sharp"].notna().all()
    if not has_sharp:
        import sys
        print("\n" + "!" * 72 +
              "\nWARNING: odds.csv has no complete decimal_odds_sharp column —"
              "\nthe PRIMARY sharp-book baseline (edge_sharp_pp / ev_sharp) will be"
              "\nMISSING from value_analysis.csv. Run `python3 -m src.ingest_odds`"
              "\nto rebuild the round-2 market snapshot before src.market.\n" +
              "!" * 72 + "\n", file=sys.stderr)
    if has_sharp:
        osh = odds["decimal_odds_sharp"].to_numpy(dtype=float)
        overround_sharp = (1.0 / osh).sum() - 1.0
        odds["p_market_sharp"] = devig_power(osh)
        keep += ["decimal_odds_sharp", "p_market_sharp"]

    df = sim.merge(odds[keep], on="team", how="left")
    df["edge_prop_pp"] = (df["p_champion"] - df["p_market_prop"]) * 100
    df["edge_power_pp"] = (df["p_champion"] - df["p_market_power"]) * 100
    if has_sharp:
        # primary edge: vs sharp/low-margin consensus (Pinnacle/Betfair/
        # Polymarket/Kalshi); EV = expected profit per unit staked at that price
        df["edge_sharp_pp"] = (df["p_champion"] - df["p_market_sharp"]) * 100
        df["ev_sharp"] = df["p_champion"] * df["decimal_odds_sharp"] - 1.0
    df = df.sort_values("p_champion", ascending=False)
    df.to_csv(os.path.join(DATA, "value_analysis.csv"), index=False)

    print(f"overround all-books: {overround:.3f}" +
          (f"  sharp: {overround_sharp:.3f}" if has_sharp else ""))
    edge_col = "edge_sharp_pp" if has_sharp else "edge_power_pp"
    cols = ["team", "p_champion", "p_market_power"] + \
           (["p_market_sharp", "edge_sharp_pp", "ev_sharp"] if has_sharp
            else ["edge_power_pp"])
    print("\n=== top 10 by model champion probability ===")
    print(df[cols].head(10).to_string(index=False))
    print(f"\n=== top 10 by edge ({edge_col}) — 'undervalued by the market' ===")
    print(df.dropna(subset=[edge_col])
            .sort_values(edge_col, ascending=False)[cols]
            .head(10).to_string(index=False))
    return df


if __name__ == "__main__":
    run()
