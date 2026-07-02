"""Score archived per-match forecasts against real results (public verification).

For every completed match in data/results.csv, looks up the archived pre-match
forecast in predictions/<file> and computes per-match Brier score and log-loss.
If a market 1X2 baseline file exists (predictions/market_1x2*.csv), scores the
de-vigged market probabilities on the same matches for comparison.

Usage:  python3 -m src.score [forecast_csv]
        (default: predictions/2026-06-11_round2_matches.csv — the canonical
         scoring record declared in REPORT §9)
Writes: data/score_log.csv and prints a summary.
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

from .load_data import DATA, canon

ROOT = os.path.dirname(DATA)
DEFAULT_FORECAST = os.path.join(ROOT, "predictions", "2026-06-11_round2_matches.csv")


def outcome_index(s1: int, s2: int) -> int:
    return 0 if s1 > s2 else (1 if s1 == s2 else 2)


def brier_logloss(p: np.ndarray, obs: int) -> tuple[float, float]:
    onehot = np.zeros(3)
    onehot[obs] = 1.0
    return float(((p - onehot) ** 2).sum()), float(-np.log(max(p[obs], 1e-12)))


def devig_1x2(o1: float, od: float, o2: float) -> np.ndarray:
    q = np.array([1 / o1, 1 / od, 1 / o2])
    return q / q.sum()


def score_knockout() -> None:
    """Binary advancement Brier for knockout matches, scored ONLY against the
    pre-match ledgers (rows appended before kickoff, never rewritten):
    v2 = predictions/ko_forecasts.csv (frozen ratings, sealed baseline),
    r4 = predictions/ko_forecasts_r4.csv (rolled ratings, production).
    Two-class sum convention: 2*(p-o)^2 — not directly comparable to the
    3-outcome group-stage Brier. Prints a head-to-head on common matches."""
    logs = {}
    for tag, fname, out_name in (
            ("v2", "ko_forecasts.csv", "score_log_ko.csv"),
            ("r4", "ko_forecasts_r4.csv", "score_log_ko_r4.csv")):
        ledger = os.path.join(ROOT, "predictions", fname)
        if os.path.exists(ledger):
            logs[tag] = _score_ko_ledger(pd.read_csv(ledger), tag, out_name)
    if all(t in logs and logs[t] is not None for t in ("v2", "r4")):
        common = logs["v2"].merge(logs["r4"], on="match", suffixes=("_v2", "_r4"))
        if len(common):
            print(f"head-to-head (n={len(common)} common): "
                  f"v2 Brier {common['brier_v2'].mean():.4f} vs "
                  f"r4 Brier {common['brier_r4'].mean():.4f}")


def _score_ko_ledger(fc: pd.DataFrame, tag: str, out_name: str):
    res = pd.read_csv(os.path.join(DATA, "results.csv"))
    res = res[res["match"] >= 73].dropna(subset=["winner"])
    rows = []
    for _, r in res.iterrows():
        m = fc[fc["match"] == int(r["match"])]
        if not len(m):
            continue                    # played before the ledger existed
        m = m.iloc[0]
        if {canon(m["team1"]), canon(m["team2"])} != \
                {canon(r["team1"]), canon(r["team2"])}:
            print(f"WARNING: ko match {int(r['match'])} teams disagree between "
                  f"{tag} ledger and result — skipping")
            continue
        p1 = float(m["p1_advance"])
        o1 = 1.0 if canon(r["winner"]) == canon(m["team1"]) else 0.0
        p_win = p1 if o1 else 1.0 - p1
        rows.append({"match": int(r["match"]), "round": r.get("group", ""),
                     "team1": canon(m["team1"]), "team2": canon(m["team2"]),
                     "winner": canon(r["winner"]),
                     "p_advancer": round(p_win, 4),
                     "brier": round(2 * (p1 - o1) ** 2, 4),
                     "logloss": round(-float(np.log(max(p_win, 1e-12))), 4),
                     "forecast_at": m["forecast_at"]})
    if not rows:
        return None
    out = pd.DataFrame(rows).sort_values("match")
    out.to_csv(os.path.join(DATA, out_name), index=False)
    print(f"\nknockout [{tag}] (pre-match ledger only):")
    print(out.to_string(index=False))
    print(f"ko[{tag}]: mean Brier {out['brier'].mean():.4f}  "
          f"mean logloss {out['logloss'].mean():.4f}  (n={len(out)}, "
          f"binary two-class convention)")
    return out


def main(forecast_path: str = DEFAULT_FORECAST) -> None:
    score_group(forecast_path)
    score_knockout()


def score_group(forecast_path: str = DEFAULT_FORECAST) -> None:
    fc = pd.read_csv(forecast_path)
    fc["team1"] = fc["team1"].map(canon)
    fc["team2"] = fc["team2"].map(canon)
    res = pd.read_csv(os.path.join(DATA, "results.csv"))
    res = res.dropna(subset=["score1", "score2"])
    res = res[res["match"] <= 72]        # knockout scoring: score_knockout()
    if not len(res):
        print("no completed matches in data/results.csv yet — nothing to score")
        return
    res["team1"] = res["team1"].map(canon)
    res["team2"] = res["team2"].map(canon)

    market = []
    for p in sorted(glob.glob(os.path.join(ROOT, "predictions", "market_1x2*.csv"))):
        market.append(pd.read_csv(p))
    mk = pd.concat(market, ignore_index=True) if market else None
    if mk is not None:
        mk["team1"] = mk["team1"].map(canon)
        mk["team2"] = mk["team2"].map(canon)

    rows = []
    for _, r in res.iterrows():
        # join on official match number first (robust to knockout rematches of
        # group-stage pairings); fall back to the team pair
        m = pd.DataFrame()
        flip = False
        if "match" in fc.columns and not pd.isna(r.get("match")):
            m = fc[fc["match"] == int(r["match"])]
            if len(m) and {m.iloc[0]["team1"], m.iloc[0]["team2"]} != \
                    {r["team1"], r["team2"]}:
                print(f"WARNING: match {int(r['match'])} teams disagree between "
                      f"forecast and result — skipping")
                continue
            if len(m):
                flip = m.iloc[0]["team1"] != r["team1"]
        if not len(m):
            m = fc[(fc["team1"] == r["team1"]) & (fc["team2"] == r["team2"])]
            if not len(m):
                m = fc[(fc["team1"] == r["team2"]) & (fc["team2"] == r["team1"])]
                flip = True
        if not len(m):
            print(f"WARNING: no forecast found for {r['team1']} vs {r['team2']}")
            continue
        m = m.iloc[0]
        p_model = np.array([m["p_team1_win"], m["p_draw"], m["p_team2_win"]])
        if flip:
            p_model = p_model[::-1]
        obs = outcome_index(int(r["score1"]), int(r["score2"]))
        br, ll = brier_logloss(p_model, obs)
        row = {"match": r.get("match"), "team1": r["team1"], "team2": r["team2"],
               "score": f"{int(r['score1'])}-{int(r['score2'])}",
               "p_model": round(float(p_model[obs]), 4),
               "brier_model": round(br, 4), "logloss_model": round(ll, 4)}
        if mk is not None:
            q = mk[(mk["team1"] == r["team1"]) & (mk["team2"] == r["team2"])]
            qflip = False
            if not len(q):
                q = mk[(mk["team1"] == r["team2"]) & (mk["team2"] == r["team1"])]
                qflip = True
            if len(q):
                q = q.iloc[0]
                p_mkt = devig_1x2(q["odds_team1"], q["odds_draw"], q["odds_team2"])
                if qflip:
                    p_mkt = p_mkt[::-1]
                br_m, ll_m = brier_logloss(p_mkt, obs)
                row.update({"brier_market": round(br_m, 4),
                            "logloss_market": round(ll_m, 4),
                            "market_book": q.get("book", "")})
        rows.append(row)

    if not rows:
        print("no scoreable matches (results exist but none match the forecast "
              "archive — knockout forecasts are archived separately)")
        return
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(DATA, "score_log.csv"), index=False)
    print(out.to_string(index=False))
    print(f"\nmodel:  mean Brier {out['brier_model'].mean():.4f}  "
          f"mean logloss {out['logloss_model'].mean():.4f}  (n={len(out)})")
    if "brier_market" in out.columns and out["brier_market"].notna().any():
        ok = out.dropna(subset=["brier_market"])
        print(f"market: mean Brier {ok['brier_market'].mean():.4f}  "
              f"mean logloss {ok['logloss_market'].mean():.4f}  (n={len(ok)})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FORECAST)
