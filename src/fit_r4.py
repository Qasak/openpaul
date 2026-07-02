"""Round-4 refit: two-slope goal-rate mapping, historically validated.

Trigger (2026 group stage + R32, n=20 big-gap matches): observed draws at
|d|>=300 were 35% vs 15.3% predicted, underdog wins 0% vs 7.1% predicted —
the single-slope map assumes open play in mismatches, while real underdogs
sit deep (total goals shrink, 0-0 fattens, upset WINS get rarer).

Discipline: the fix must be justified on HISTORICAL data, not on the
triggering tournament. Same protocol as the round-2 fit: train 2018-2024,
out-of-sample 2025+ (all matches strictly before the 2026 World Cup), then
full-window refit for production.

PRE-REGISTERED ship criteria (fixed before looking at the results; all three
must hold or the two-slope model does NOT go to production):
  S1. OOS paired W/D/L logloss not worse than single-slope (mean diff >= 0);
  S2. OOS big-gap (|d|>=300) draw-rate calibration error strictly smaller
      than single-slope's;
  S3. sigma regrid on the 2018/2022 backtests (src/backtest_r4.py) within
      0.002 logloss of the single-slope backtest optimum (i.e. no
      tournament-level degradation).
S3 is checked by backtest_r4; this module records S1/S2 and its own verdict.

Usage:  python3 -m src.fit_r4
Writes: data/params_fit_r4.json, data/fit_validation_r4.json
"""
from __future__ import annotations

import json
import os

import numpy as np
from scipy.optimize import minimize

from .fit import TRAIN_END, load_fit_matches, nll
from .load_data import DATA
from .model import LAMBDA_CLIP, MatchModel

GAP_BUCKETS = [(0, 150), (150, 300), (300, 10_000)]


def nll2(params: np.ndarray, d: np.ndarray, h: np.ndarray, a: np.ndarray) -> float:
    """Two-slope Dixon-Coles NLL; nests the single-slope model at bf == bu."""
    aa, bf, bu, rho = params
    s1 = np.where(d >= 0, bf, bu)
    s2 = np.where(d >= 0, bu, bf)
    lam1 = np.clip(np.exp(aa + s1 * d), *LAMBDA_CLIP)
    lam2 = np.clip(np.exp(aa - s2 * d), *LAMBDA_CLIP)
    from scipy.stats import poisson
    ll = poisson.logpmf(h, lam1) + poisson.logpmf(a, lam2)
    tau = np.ones_like(lam1)
    m00 = (h == 0) & (a == 0); m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0); m11 = (h == 1) & (a == 1)
    tau[m00] = 1.0 - lam1[m00] * lam2[m00] * rho
    tau[m01] = 1.0 + lam1[m01] * rho
    tau[m10] = 1.0 + lam2[m10] * rho
    tau[m11] = 1.0 - rho
    if (tau <= 1e-9).any():
        return 1e12
    return float(-(ll + np.log(tau)).sum())


def eval_params(params: dict, d, h, a) -> dict:
    """W/D/L logloss + per-|d|-bucket draw/underdog-win calibration."""
    mm = MatchModel(params)
    n = len(d)
    per_match = np.empty(n)
    pred_draw = np.empty(n)
    pred_dogwin = np.empty(n)
    obs_draw = (h == a).astype(float)
    obs_dogwin = np.where(d >= 0, (a > h), (h > a)).astype(float)
    for i in range(n):
        w, dr, l = mm.wdl(float(d[i]))
        probs = np.array([w, dr, l])
        obs = 0 if h[i] > a[i] else (1 if h[i] == a[i] else 2)
        per_match[i] = -np.log(max(probs[obs], 1e-12))
        pred_draw[i] = dr
        pred_dogwin[i] = l if d[i] >= 0 else w
    out = {"logloss": float(per_match.mean()), "n": n, "buckets": {}}
    gaps = np.abs(d)
    for lo, hi in GAP_BUCKETS:
        m = (gaps >= lo) & (gaps < hi)
        if not m.any():
            continue
        out["buckets"][f"{lo}-{hi}"] = {
            "n": int(m.sum()),
            "pred_draw": float(pred_draw[m].mean()),
            "obs_draw": float(obs_draw[m].mean()),
            "pred_dogwin": float(pred_dogwin[m].mean()),
            "obs_dogwin": float(obs_dogwin[m].mean()),
        }
    out["_per_match"] = per_match
    return out


def main() -> None:
    hist = load_fit_matches()
    train = hist[hist["date"] <= TRAIN_END]
    test = hist[hist["date"] > TRAIN_END]
    d_tr, h_tr, a_tr = (train[c].to_numpy() for c in ("d", "h", "a"))
    d_te, h_te, a_te = (test[c].to_numpy() for c in ("d", "h", "a"))
    print(f"train {len(train)}, test {len(test)}")

    p2 = json.load(open(os.path.join(DATA, "params_fit.json")))   # v2 single-slope

    x0 = np.array([p2["a"], p2["b"], p2["b"], p2["rho"]])
    res = minimize(nll2, x0, args=(d_tr, h_tr, a_tr), method="Nelder-Mead",
                   options={"xatol": 1e-7, "fatol": 1e-5, "maxiter": 8000})
    if not res.success:
        raise RuntimeError(f"two-slope train MLE did not converge: {res.message}")
    a4, bf4, bu4, rho4 = (float(v) for v in res.x)
    print(f"two-slope (train): a={a4:.4f} b_fav={bf4:.5f} b_dog={bu4:.5f} "
          f"rho={rho4:.4f}  (v2 b={p2['b']:.5f})")
    p4_train = {"a": a4, "b_fav": bf4, "b_dog": bu4, "rho": rho4,
                "home_elo": p2["home_elo"]}

    # likelihood-ratio vs nested single-slope on the train window
    nll_1 = nll(np.array([p2["a"], p2["b"], p2["rho"]]), d_tr, h_tr, a_tr)
    # (v2 params were full-window; refit single-slope on train for a fair LR)
    res1 = minimize(nll, np.array([p2["a"], p2["b"], p2["rho"]]),
                    args=(d_tr, h_tr, a_tr), method="Nelder-Mead",
                    options={"xatol": 1e-6, "fatol": 1e-4, "maxiter": 4000})
    lr = 2 * (res1.fun - res.fun)
    print(f"train LR stat (1 df) vs refit single-slope: {lr:.1f}")

    ev2 = eval_params(p2, d_te, h_te, a_te)
    ev4 = eval_params(p4_train, d_te, h_te, a_te)
    diff = ev2.pop("_per_match") - ev4.pop("_per_match")
    paired = {"mean": float(diff.mean()),
              "se": float(diff.std(ddof=1) / np.sqrt(len(diff)))}

    big = f"{GAP_BUCKETS[-1][0]}-{GAP_BUCKETS[-1][1]}"
    cal2 = abs(ev2["buckets"][big]["pred_draw"] - ev2["buckets"][big]["obs_draw"])
    cal4 = abs(ev4["buckets"][big]["pred_draw"] - ev4["buckets"][big]["obs_draw"])
    s1_pass = paired["mean"] >= 0.0
    s2_pass = cal4 < cal2
    print(f"OOS logloss: v2={ev2['logloss']:.4f}  r4={ev4['logloss']:.4f}  "
          f"paired diff {paired['mean']:+.4f} ± {paired['se']:.4f}  -> S1 {'PASS' if s1_pass else 'FAIL'}")
    print(f"OOS big-gap draw calib error: v2={cal2:.4f}  r4={cal4:.4f}  "
          f"-> S2 {'PASS' if s2_pass else 'FAIL'}")
    for k in ev4["buckets"]:
        b2, b4 = ev2["buckets"][k], ev4["buckets"][k]
        print(f"  |d| {k:>10}: n={b4['n']:>4}  draw obs {b4['obs_draw']:.3f} "
              f"pred v2 {b2['pred_draw']:.3f} / r4 {b4['pred_draw']:.3f}   "
              f"dogwin obs {b4['obs_dogwin']:.3f} pred v2 {b2['pred_dogwin']:.3f} "
              f"/ r4 {b4['pred_dogwin']:.3f}")

    # full-window refit for production candidate
    res_full = minimize(nll2, res.x, args=(hist["d"].to_numpy(),
                                           hist["h"].to_numpy(),
                                           hist["a"].to_numpy()),
                        method="Nelder-Mead",
                        options={"xatol": 1e-7, "fatol": 1e-5, "maxiter": 8000})
    if not res_full.success:
        raise RuntimeError(f"two-slope full-window MLE did not converge: {res_full.message}")
    af, bff, buf, rhof = (float(v) for v in res_full.x)
    params4 = {
        "a": af, "b_fav": bff, "b_dog": buf, "rho": rhof,
        "home_elo": p2["home_elo"], "base_lambda": float(np.exp(af)),
        "max_goals": 10, "lambda_clip": list(LAMBDA_CLIP),
        "fit": "Round-4 two-slope MLE on international matches "
               f"2018-01-01..{hist['date'].max()} (n={len(hist)}); "
               "ship criteria S1/S2 recorded in fit_validation_r4.json",
    }
    with open(os.path.join(DATA, "params_fit_r4.json"), "w") as f:
        json.dump(params4, f, indent=2)

    val = {
        "n_train": int(len(train)), "n_test": int(len(test)),
        "params_v2": {k: p2[k] for k in ("a", "b", "rho")},
        "params_r4_train": p4_train,
        "params_r4_full": {k: params4[k] for k in ("a", "b_fav", "b_dog", "rho")},
        "train_lr_stat_1df": float(lr),
        "oos": {"v2": {k: v for k, v in ev2.items()},
                "r4": {k: v for k, v in ev4.items()},
                "paired_diff_v2_minus_r4": paired},
        "ship_criteria": {
            "S1_oos_not_worse": bool(s1_pass),
            "S2_biggap_draw_calibration_better": bool(s2_pass),
            "S3_sigma_backtest": "see data/backtest_sigma_r4.json",
        },
    }
    with open(os.path.join(DATA, "fit_validation_r4.json"), "w") as f:
        json.dump(val, f, indent=2)
    print(f"full-window: a={af:.4f} b_fav={bff:.5f} b_dog={buf:.5f} rho={rhof:.4f} "
          f"-> data/params_fit_r4.json")


if __name__ == "__main__":
    main()
