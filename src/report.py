"""Build all derived report artifacts from the simulation/market outputs.

Usage:  python3 -m src.report [round_tag]

Produces:
  data/report_summary.csv                     merged per-team table (all sigma tiers + edges)
  predictions/<date>_<tag>_pretournament.csv  tournament-level snapshot (copy of summary)
  predictions/<date>_<tag>_matches.csv        per-match W/D/L + expected-goals forecasts for
                                              all not-yet-played group fixtures (public
                                              verification record; Brier-scoreable)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pandas as pd

from .load_data import CITY_COUNTRY, DATA, HOSTS, canon, load_all
from .model import MatchModel
from .simulate import venue_bonus

ROOT = os.path.dirname(DATA)
PRED = os.path.join(ROOT, "predictions")


def build_summary(sigma_tiers: tuple[int, ...] = (0, 150)) -> pd.DataFrame:
    base = pd.read_csv(os.path.join(DATA, "value_analysis.csv"))
    out = base
    market_col = "p_market_sharp" if "p_market_sharp" in out.columns else "p_market_power"
    for sig in sigma_tiers:
        p = os.path.join(DATA, f"sim_probs_sigma{sig}.csv")
        if os.path.exists(p):
            s = pd.read_csv(p)[["team", "p_champion"]].rename(
                columns={"p_champion": f"p_s{sig}"})
            out = out.merge(s, on="team", how="left")
            out[f"edge_s{sig}_pp"] = (out[f"p_s{sig}"] - out[market_col]) * 100
    cols = ["team", "elo", "p_champion"] + \
           [c for c in out.columns if c.startswith("p_s") and c != "p_sf"] + \
           ["decimal_odds", "decimal_odds_sharp", "p_market_prop",
            "p_market_power", "p_market_sharp", "edge_prop_pp",
            "edge_power_pp", "edge_sharp_pp", "ev_sharp"] + \
           [c for c in out.columns if c.startswith("edge_s") and
            c not in ("edge_sharp_pp",)] + \
           ["p_final", "p_sf", "p_qf", "p_r16", "p_r32", "p_group_winner"]
    seen = set()
    ordered = [c for c in cols if c in out.columns and not (c in seen or seen.add(c))]
    out = out[ordered].sort_values("p_champion", ascending=False)
    out.to_csv(os.path.join(DATA, "report_summary.csv"), index=False)
    return out


def build_match_forecasts(today: str) -> pd.DataFrame:
    data = load_all()
    fit_path = os.path.join(DATA, "params_fit.json")
    params_path = fit_path if os.path.exists(fit_path) else \
        os.path.join(DATA, "params.json")
    params = json.load(open(params_path))
    mm = MatchModel(params)
    elo = data["elo"]
    played = set()
    for _, r in data["results"].iterrows():
        played.add(frozenset((r["team1"], r["team2"])))

    rows = []
    for _, r in data["sched"].sort_values("match").iterrows():
        t1, t2 = r["team1"], r["team2"]
        raw_country = canon(str(r.get("country") or ""))
        country = raw_country if raw_country in HOSTS else \
            CITY_COUNTRY.get(str(r.get("city") or "").split(" (")[0].strip())
        d = mm.diff(elo[t1], elo[t2], venue_bonus(t1, country), venue_bonus(t2, country))
        w, dr, l = mm.wdl(d)
        lam1, lam2 = mm.rates(d)
        rows.append({
            "match": int(r["match"]), "date": r["date"], "group": r["group"],
            "team1": t1, "team2": t2,
            "p_team1_win": round(w, 4), "p_draw": round(dr, 4),
            "p_team2_win": round(l, 4),
            "xg_team1": round(lam1, 3), "xg_team2": round(lam2, 3),
            "already_played_when_forecast": frozenset((t1, t2)) in played,
            "forecast_made": today,
        })
    return pd.DataFrame(rows)


def main(tag: str = "round1", date: str | None = None) -> None:
    """date pins the output filenames (and forecast_made stamp) so the
    documented reproduction chain is not wall-clock fragile."""
    today = date or dt.date.today().isoformat()
    os.makedirs(PRED, exist_ok=True)
    summary = build_summary()
    summary.to_csv(os.path.join(PRED, f"{today}_{tag}_pretournament.csv"), index=False)
    matches = build_match_forecasts(today)
    matches.to_csv(os.path.join(PRED, f"{today}_{tag}_matches.csv"), index=False)
    print(f"report_summary.csv: {len(summary)} teams; "
          f"match forecasts: {len(matches)} fixtures "
          f"({int(matches['already_played_when_forecast'].sum())} already played)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "round1",
         sys.argv[2] if len(sys.argv) > 2 else None)
