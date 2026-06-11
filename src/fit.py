"""Empirical MLE fit + out-of-sample validation of the match model.

Fits (a, b, rho) of the Elo-driven Dixon-Coles model by maximum likelihood
on real international matches (recomputed pre-match Elo, affine-mapped to the
official eloratings.net scale), and scores the round-1 curve-calibrated
parameters against the MLE refit on a held-out window.

The DC tau adjustment is exactly probability-preserving (sum of adjustments
is zero), so the per-match log-likelihood is
  log Pois(h; lam1) + log Pois(a; lam2) + log tau(h, a)
with no renormalization needed. (Known small mismatch vs deployment: the
likelihood here is untruncated while the simulator samples from a 0..10 grid
renormalized after truncation, and observed scores are capped at 10 for
fitting; P(any score > 10) is ~1e-7 at typical rates, so the effect is
negligible — documented for completeness.)

Usage:  python3 -m src.fit
Writes: data/params_fit.json, data/fit_validation.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from .load_data import DATA
from .model import LAMBDA_CLIP

TRAIN_START = "2018-01-01"
TRAIN_END = "2024-12-31"      # test window: 2025-01-01 .. present
HOME_ADV = 100.0
MAX_GOAL_CAP = 10


def load_fit_matches() -> pd.DataFrame:
    hist = pd.read_csv(os.path.join(DATA, "raw", "matches_with_elo.csv"))
    meta = json.load(open(os.path.join(DATA, "elo_recompute_meta.json")))
    hist = hist[hist["date"] >= TRAIN_START].copy()
    for side in ("home", "away"):
        hist[f"elo_{side}_m"] = meta["slope"] * hist[f"elo_{side}_pre"] + meta["intercept"]
    hist["d"] = (hist["elo_home_m"] + np.where(hist["neutral"], 0.0, HOME_ADV)
                 - hist["elo_away_m"])
    hist["h"] = hist["home_score"].clip(upper=MAX_GOAL_CAP)
    hist["a"] = hist["away_score"].clip(upper=MAX_GOAL_CAP)
    return hist


def nll(params: np.ndarray, d: np.ndarray, h: np.ndarray, a: np.ndarray) -> float:
    aa, bb, rho = params
    lam1 = np.clip(np.exp(aa + bb * d), *LAMBDA_CLIP)
    lam2 = np.clip(np.exp(aa - bb * d), *LAMBDA_CLIP)
    ll = poisson.logpmf(h, lam1) + poisson.logpmf(a, lam2)
    tau = np.ones_like(lam1)
    m00 = (h == 0) & (a == 0)
    m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0)
    m11 = (h == 1) & (a == 1)
    tau[m00] = 1.0 - lam1[m00] * lam2[m00] * rho
    tau[m01] = 1.0 + lam1[m01] * rho
    tau[m10] = 1.0 + lam2[m10] * rho
    tau[m11] = 1.0 - rho
    if (tau <= 1e-9).any():
        return 1e12
    return float(-(ll + np.log(tau)).sum())


def wdl_logloss(params: dict, d: np.ndarray, h: np.ndarray, a: np.ndarray) -> dict:
    """W/D/L log-loss and Brier of the model on given matches (grid method).
    Also returns the per-match logloss vector for paired comparisons."""
    from .model import score_grid, goal_rates, wdl_from_grid
    n = len(d)
    per_match = np.empty(n)
    brier = 0.0
    draws_pred = 0.0
    for i in range(n):
        lam1, lam2 = goal_rates(float(d[i]), params["a"], params["b"])
        w, dr, l = wdl_from_grid(score_grid(lam1, lam2, params["rho"]))
        probs = np.array([w, dr, l])
        obs = 0 if h[i] > a[i] else (1 if h[i] == a[i] else 2)
        onehot = np.zeros(3); onehot[obs] = 1.0
        per_match[i] = -np.log(max(probs[obs], 1e-12))
        brier += ((probs - onehot) ** 2).sum()
        draws_pred += dr
    return {"logloss": float(per_match.mean()), "brier": brier / n, "n": n,
            "draw_rate_pred": draws_pred / n, "_per_match": per_match}


def main() -> None:
    hist = load_fit_matches()
    train = hist[hist["date"] <= TRAIN_END]
    test = hist[hist["date"] > TRAIN_END]
    print(f"train {len(train)} matches ({TRAIN_START}..{TRAIN_END}), "
          f"test {len(test)} (..{hist['date'].max()})")

    d_tr = train["d"].to_numpy(); h_tr = train["h"].to_numpy(); a_tr = train["a"].to_numpy()
    d_te = test["d"].to_numpy(); h_te = test["h"].to_numpy(); a_te = test["a"].to_numpy()

    # round-1 curve-calibrated params for comparison
    p_curve = json.load(open(os.path.join(DATA, "params.json")))

    x0 = np.array([p_curve["a"], p_curve["b"], p_curve["rho"]])
    res = minimize(nll, x0, args=(d_tr, h_tr, a_tr), method="Nelder-Mead",
                   options={"xatol": 1e-6, "fatol": 1e-4, "maxiter": 4000})
    if not res.success:
        raise RuntimeError(f"train-window MLE did not converge: {res.message}")
    a_fit, b_fit, rho_fit = (float(v) for v in res.x)
    print(f"MLE fit (train): a={a_fit:.4f} (base lambda {np.exp(a_fit):.3f}), "
          f"b={b_fit:.5f}, rho={rho_fit:.4f}; converged={res.success}")

    p_mle_train = {"a": a_fit, "b": b_fit, "rho": rho_fit}

    # out-of-sample comparison on the test window
    val = {
        "train_window": [TRAIN_START, TRAIN_END], "n_train": int(len(train)),
        "test_window": [TRAIN_END, str(hist["date"].max())], "n_test": int(len(test)),
        "empirical_test": {
            "draw_rate": float((h_te == a_te).mean()),
            "avg_goals": float((test["home_score"] + test["away_score"]).mean()),
        },
        "curve_params": {k: p_curve[k] for k in ("a", "b", "rho")},
        "mle_params_train": p_mle_train,
    }
    test_curve = wdl_logloss(p_curve, d_te, h_te, a_te)
    test_mle = wdl_logloss(p_mle_train, d_te, h_te, a_te)
    # paired per-match comparison: is the MLE improvement outside MC noise?
    diff = test_curve.pop("_per_match") - test_mle.pop("_per_match")
    val["test_curve"] = test_curve
    val["test_mle"] = test_mle
    val["paired_diff"] = {
        "mean": float(diff.mean()),
        "se": float(diff.std(ddof=1) / np.sqrt(len(diff))),
        "note": "mean per-match logloss(curve) - logloss(MLE) on the test window; "
                "mean/se > 2 indicates a statistically solid improvement",
    }
    val["test_uniform_logloss"] = float(np.log(3))
    print(f"test W/D/L logloss: curve={val['test_curve']['logloss']:.4f}  "
          f"MLE={val['test_mle']['logloss']:.4f}  uniform={np.log(3):.4f}  "
          f"paired diff {val['paired_diff']['mean']:.4f} ± {val['paired_diff']['se']:.4f}")
    print(f"test draw rate: empirical={val['empirical_test']['draw_rate']:.3f}  "
          f"curve-pred={val['test_curve']['draw_rate_pred']:.3f}  "
          f"MLE-pred={val['test_mle']['draw_rate_pred']:.3f}")

    # final params: refit on the FULL window for forward use
    d_all = hist["d"].to_numpy(); h_all = hist["h"].to_numpy(); a_all = hist["a"].to_numpy()
    res2 = minimize(nll, res.x, args=(d_all, h_all, a_all), method="Nelder-Mead",
                    options={"xatol": 1e-6, "fatol": 1e-4, "maxiter": 4000})
    if not res2.success:
        raise RuntimeError(f"full-window MLE did not converge: {res2.message}")
    a2, b2, rho2 = (float(v) for v in res2.x)
    params_fit = {
        "a": a2, "b": b2, "rho": rho2,
        "home_elo": HOME_ADV, "base_lambda": float(np.exp(a2)),
        "max_goals": 10, "lambda_clip": list(LAMBDA_CLIP),
        "fit": "MLE on international matches "
               f"{TRAIN_START}..{hist['date'].max()} (n={len(hist)}), "
               "recomputed Elo affine-mapped to official scale",
    }
    with open(os.path.join(DATA, "params_fit.json"), "w") as f:
        json.dump(params_fit, f, indent=2)
    val["mle_params_full"] = {"a": a2, "b": b2, "rho": rho2}
    with open(os.path.join(DATA, "fit_validation.json"), "w") as f:
        json.dump(val, f, indent=2)
    print(f"full-window MLE: a={a2:.4f} (base lambda {np.exp(a2):.3f}), "
          f"b={b2:.5f}, rho={rho2:.4f} -> data/params_fit.json")


if __name__ == "__main__":
    main()
