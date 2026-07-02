"""Round-4 experiment: per-team attack/defence offsets on top of Elo (the
item descoped in round 2), with ridge shrinkage and leak-free tuning.

Model: on top of the FIXED production (a, b, rho),
  lam1 = exp(a + b*d + att[home] + leak[away])
  lam2 = exp(a - b*d + att[away] + leak[home])
att = scoring style residual, leak = concessions residual (positive = leaky);
both ~ ridge N(0, tau^2), teams below the match floor pinned at 0. Offsets
are fitted on plain double-Poisson NLL (analytic gradient; the DC tau term
is offset-insensitive) and EVALUATED with the full DC grid.

Windows (leak-free): fit 2022-2023, choose tau on 2024, final OOS 2025+.

PRE-REGISTERED ship criteria (all must hold):
  T1. final-OOS paired W/D/L logloss improvement >= 1 SE vs no offsets;
  T2. the tau chosen on 2024 is interior on the grid (a corner at max
      shrinkage means "no effect", a corner at min shrinkage means the
      grid missed the optimum — either way not shippable as-is).

Usage:  python3 -m src.team_offsets_r4
Writes: data/team_offsets_r4.json
"""
from __future__ import annotations

import json
import os

import numpy as np
from scipy.optimize import minimize

from .fit import load_fit_matches
from .load_data import DATA
from .model import MatchModel, score_grid

FIT_END = "2023-12-31"
TUNE_END = "2024-12-31"
MIN_MATCHES = 10
TAU_GRID = [0.05, 0.10, 0.20, 0.40]


def prepare():
    hist = load_fit_matches()
    hist = hist[hist["date"] >= "2022-01-01"].copy()
    fit = hist[hist["date"] <= FIT_END]
    tune = hist[(hist["date"] > FIT_END) & (hist["date"] <= TUNE_END)]
    test = hist[hist["date"] > TUNE_END]
    counts = fit["home_team"].value_counts().add(
        fit["away_team"].value_counts(), fill_value=0)
    teams = sorted(counts[counts >= MIN_MATCHES].index)
    tidx = {t: i for i, t in enumerate(teams)}
    return fit, tune, test, teams, tidx


def arrays(df, tidx):
    ih = df["home_team"].map(lambda t: tidx.get(t, -1)).to_numpy()
    ia = df["away_team"].map(lambda t: tidx.get(t, -1)).to_numpy()
    return (df["d"].to_numpy(), df["h"].to_numpy().astype(float),
            df["a"].to_numpy().astype(float), ih, ia)


def fit_offsets(p, d, h, a, ih, ia, n_teams, tau):
    """Ridge-penalized plain double-Poisson MLE, analytic gradient."""
    A, B = p["a"], p["b"]

    def unpack(x):
        att, leak = x[:n_teams], x[n_teams:]
        oh_att = np.where(ih >= 0, att[np.maximum(ih, 0)], 0.0)
        oa_att = np.where(ia >= 0, att[np.maximum(ia, 0)], 0.0)
        oh_leak = np.where(ih >= 0, leak[np.maximum(ih, 0)], 0.0)
        oa_leak = np.where(ia >= 0, leak[np.maximum(ia, 0)], 0.0)
        lam1 = np.exp(np.clip(A + B * d + oh_att + oa_leak, -2.0, 2.0))
        lam2 = np.exp(np.clip(A - B * d + oa_att + oh_leak, -2.0, 2.0))
        return lam1, lam2

    def obj_grad(y):
        # whitened parameterization x = tau*y keeps the ridge curvature at 1
        # regardless of tau (raw x at small tau breaks the line search)
        x = tau * y
        lam1, lam2 = unpack(x)
        nll = float((lam1 - h * np.log(lam1)).sum() + (lam2 - a * np.log(lam2)).sum())
        nll += float((y ** 2).sum() / 2.0)
        g1, g2 = lam1 - h, lam2 - a
        grad = np.zeros_like(x)
        mh, ma = ih >= 0, ia >= 0
        np.add.at(grad, ih[mh], g1[mh])                       # att[home] in lam1
        np.add.at(grad, ia[ma], g2[ma])                       # att[away] in lam2
        np.add.at(grad, n_teams + ia[ma], g1[ma])             # leak[away] in lam1
        np.add.at(grad, n_teams + ih[mh], g2[mh])             # leak[home] in lam2
        return nll, tau * grad + y

    res = minimize(obj_grad, np.zeros(2 * n_teams), jac=True, method="L-BFGS-B",
                   options={"maxiter": 1000})
    if not res.success and "ABNORMAL" in str(res.message):
        raise RuntimeError(f"offset fit failed: {res.message}")
    x = tau * res.x
    return x[:n_teams], x[n_teams:]


def wdl_logloss(p, d, h, a, ih, ia, att=None, leak=None):
    """Per-match W/D/L logloss with the full DC grid (rho from production)."""
    out = np.empty(len(d))
    for i in range(len(d)):
        o_h_att = att[ih[i]] if (att is not None and ih[i] >= 0) else 0.0
        o_a_att = att[ia[i]] if (att is not None and ia[i] >= 0) else 0.0
        o_h_lk = leak[ih[i]] if (leak is not None and ih[i] >= 0) else 0.0
        o_a_lk = leak[ia[i]] if (leak is not None and ia[i] >= 0) else 0.0
        lam1 = float(np.exp(np.clip(p["a"] + p["b"] * d[i] + o_h_att + o_a_lk, -2, 2)))
        lam2 = float(np.exp(np.clip(p["a"] - p["b"] * d[i] + o_a_att + o_h_lk, -2, 2)))
        grid = score_grid(lam1, lam2, p["rho"])
        w = float(np.tril(grid, -1).sum()); dr = float(np.trace(grid))
        probs = np.array([w, dr, 1.0 - w - dr])
        obs = 0 if h[i] > a[i] else (1 if h[i] == a[i] else 2)
        out[i] = -np.log(max(probs[obs], 1e-12))
    return out


def main() -> None:
    p = json.load(open(os.path.join(DATA, "params_fit.json")))
    fit, tune, test, teams, tidx = prepare()
    print(f"fit {len(fit)} (2022-2023), tune {len(tune)} (2024), "
          f"test {len(test)} (2025+); {len(teams)} teams with >= {MIN_MATCHES} matches")
    d_f, h_f, a_f, ih_f, ia_f = arrays(fit, tidx)
    d_v, h_v, a_v, ih_v, ia_v = arrays(tune, tidx)
    d_t, h_t, a_t, ih_t, ia_t = arrays(test, tidx)

    base_tune = wdl_logloss(p, d_v, h_v, a_v, ih_v, ia_v)
    results = {}
    best_tau, best_ll, best_off = None, np.inf, None
    for tau in TAU_GRID:
        att, leak = fit_offsets(p, d_f, h_f, a_f, ih_f, ia_f, len(teams), tau)
        ll = wdl_logloss(p, d_v, h_v, a_v, ih_v, ia_v, att, leak)
        results[str(tau)] = {"tune_logloss": float(ll.mean()),
                             "off_norm": float(np.sqrt((att**2).mean() + (leak**2).mean()))}
        print(f"tau={tau}: tune logloss {ll.mean():.4f} "
              f"(base {base_tune.mean():.4f}), rms offset {results[str(tau)]['off_norm']:.4f}")
        if ll.mean() < best_ll:
            best_tau, best_ll, best_off = tau, float(ll.mean()), (att, leak)

    t2_pass = best_tau not in (TAU_GRID[0], TAU_GRID[-1]) and best_ll < float(base_tune.mean())
    att, leak = best_off
    base_test = wdl_logloss(p, d_t, h_t, a_t, ih_t, ia_t)
    off_test = wdl_logloss(p, d_t, h_t, a_t, ih_t, ia_t, att, leak)
    diff = base_test - off_test
    mean, se = float(diff.mean()), float(diff.std(ddof=1) / np.sqrt(len(diff)))
    t1_pass = mean >= se
    print(f"final OOS paired diff (base − offsets): {mean:+.4f} ± {se:.4f} "
          f"-> T1 {'PASS' if t1_pass else 'FAIL'}")
    print(f"tau*={best_tau} interior+improving -> T2 {'PASS' if t2_pass else 'FAIL'}")

    wc = [t for t in ("Cape Verde", "Morocco", "Spain", "Argentina", "Germany")
          if t in tidx]
    sample = {t: {"att": round(float(att[tidx[t]]), 4),
                  "leak": round(float(leak[tidx[t]]), 4)} for t in wc}
    print("sample offsets:", sample)

    out = {
        "windows": {"fit": ["2022-01-01", FIT_END], "tune": ["2024", "2024"],
                    "test": ["2025-01-01", str(test['date'].max())]},
        "n": {"fit": int(len(fit)), "tune": int(len(tune)), "test": int(len(test)),
              "teams": len(teams)},
        "tau_grid": {k: v for k, v in results.items()},
        "tau_star": best_tau,
        "final_oos": {"paired_diff_mean": mean, "se": se},
        "ship_criteria": {"T1_oos_ge_1se": bool(t1_pass),
                          "T2_tau_interior": bool(t2_pass)},
        "ship": bool(t1_pass and t2_pass),
        "sample_offsets": sample,
    }
    with open(os.path.join(DATA, "team_offsets_r4.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSHIP={out['ship']} -> data/team_offsets_r4.json")


if __name__ == "__main__":
    main()
