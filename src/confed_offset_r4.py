"""Round-4 candidate: confederation-level Elo offsets, fit + OOS validation.

confed_check.py shows the mispricing is HISTORICAL, not tournament noise
(2018+ cross-confed: CAF +0.065 share z=+3.2, UEFA +0.061 z=+3.4,
CONCACAF -0.070 z=-3.9, OFC -0.28 z=-6.2). Elo's known weakness: the
inter-confederation match graph is sparse, so cluster-level rating offsets
can persist for years.

Model: effective rating = elo + theta[confed]. Offsets cancel exactly in
intra-confederation matches; only cross-confed d shifts:
  d' = d + theta[c_home] - theta[c_away].
Fit the 6 thetas by the same Dixon-Coles score MLE as the production fit
(global (a,b,rho) FIXED at production values; train 2018-2024 cross-confed
matches), with a match-count-weighted zero-sum constraint (5 free params).

PRE-REGISTERED ship criteria (all must hold):
  C1. OOS (2025+) cross-confed paired W/D/L logloss improves by >= 1 SE
      vs no offsets;
  C2. OOS improvement is not driven by OFC alone (paired diff excluding
      OFC-involved matches still >= 0) — OFC is 1 team in this World Cup
      and a -200-class offset must not carry the decision;
  C3. offsets are stable across a 2018-2021 / 2022-2024 split (sign match
      for every confed whose |theta| > 15 Elo).

Usage:  python3 -m src.confed_offset_r4
Writes: data/confed_offsets_r4.json
"""
from __future__ import annotations

import json
import os

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from .confed_check import derive_membership
from .fit import TRAIN_END, load_fit_matches
from .load_data import DATA, canon
from .model import LAMBDA_CLIP, MatchModel

CONFEDS = ["AFC", "CAF", "CONCACAF", "CONMEBOL", "OFC", "UEFA"]


def prepare():
    membership = {canon(t): c for t, c in derive_membership().items()}
    hist = load_fit_matches().copy()
    hist["c_home"] = hist["home_team"].map(membership)
    hist["c_away"] = hist["away_team"].map(membership)
    cross = hist[hist["c_home"].notna() & hist["c_away"].notna()
                 & (hist["c_home"] != hist["c_away"])].copy()
    ci = {c: i for i, c in enumerate(CONFEDS)}
    cross["ih"] = cross["c_home"].map(ci)
    cross["ia"] = cross["c_away"].map(ci)
    return cross


def nll_theta(theta: np.ndarray, p: dict, d, h, a, ih, ia) -> float:
    dd = d + theta[ih] - theta[ia]
    lam1 = np.clip(np.exp(p["a"] + p["b"] * dd), *LAMBDA_CLIP)
    lam2 = np.clip(np.exp(p["a"] - p["b"] * dd), *LAMBDA_CLIP)
    ll = poisson.logpmf(h, lam1) + poisson.logpmf(a, lam2)
    rho = p["rho"]
    tau = np.ones_like(lam1)
    m00 = (h == 0) & (a == 0); m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0); m11 = (h == 1) & (a == 1)
    tau[m00] = 1.0 - lam1[m00] * lam2[m00] * rho
    tau[m01] = 1.0 + lam1[m01] * rho
    tau[m10] = 1.0 + lam2[m10] * rho
    tau[m11] = 1.0 - rho
    return float(-(ll + np.log(tau)).sum())


def fit_thetas(cross, p, weights) -> np.ndarray:
    """5 free params; the 6th absorbs the weighted zero-sum constraint."""
    d = cross["d"].to_numpy(); h = cross["h"].to_numpy(); a = cross["a"].to_numpy()
    ih = cross["ih"].to_numpy(); ia = cross["ia"].to_numpy()
    w = weights / weights.sum()

    def expand(free: np.ndarray) -> np.ndarray:
        theta = np.append(free, 0.0)
        return theta - (w * theta).sum() / 1.0   # weighted zero-sum
    def obj(free: np.ndarray) -> float:
        return nll_theta(expand(free), p, d, h, a, ih, ia)
    res = minimize(obj, np.zeros(len(CONFEDS) - 1), method="Nelder-Mead",
                   options={"xatol": 1e-3, "fatol": 1e-5, "maxiter": 20000})
    if not res.success:
        raise RuntimeError(f"confed offset MLE did not converge: {res.message}")
    return expand(res.x)


def paired_logloss(cross, p, theta: np.ndarray | None):
    mm = MatchModel(p)
    out = np.empty(len(cross))
    for i, r in enumerate(cross.itertuples(index=False)):
        dd = float(r.d) + ((theta[r.ih] - theta[r.ia]) if theta is not None else 0.0)
        w, dr, l = mm.wdl(dd)
        probs = np.array([w, dr, l])
        obs = 0 if r.h > r.a else (1 if r.h == r.a else 2)
        out[i] = -np.log(max(probs[obs], 1e-12))
    return out


def main() -> None:
    p = json.load(open(os.path.join(DATA, "params_fit.json")))
    cross = prepare()
    train = cross[cross["date"] <= TRAIN_END]
    test = cross[cross["date"] > TRAIN_END]
    counts = np.array([((train["ih"] == i).sum() + (train["ia"] == i).sum())
                       for i in range(len(CONFEDS))], dtype=float)
    print(f"cross-confed: train {len(train)}, test {len(test)}")

    theta = fit_thetas(train, p, counts)
    print("offsets (train):", {c: round(float(t), 1) for c, t in zip(CONFEDS, theta)})

    # C3: split-half stability
    mid = "2022-01-01"
    th_a = fit_thetas(train[train["date"] < mid], p, counts)
    th_b = fit_thetas(train[train["date"] >= mid], p, counts)
    c3_pass = all(
        (abs(t) <= 15.0) or (np.sign(ta) == np.sign(tb))
        for t, ta, tb in zip(theta, th_a, th_b))
    print("split 2018-2021:", {c: round(float(t), 1) for c, t in zip(CONFEDS, th_a)})
    print("split 2022-2024:", {c: round(float(t), 1) for c, t in zip(CONFEDS, th_b)})

    # C1/C2: OOS paired comparison
    ll0 = paired_logloss(test, p, None)
    ll1 = paired_logloss(test, p, theta)
    diff = ll0 - ll1
    mean, se = float(diff.mean()), float(diff.std(ddof=1) / np.sqrt(len(diff)))
    c1_pass = mean >= se
    no_ofc = np.array([(r.ih != CONFEDS.index("OFC")) and (r.ia != CONFEDS.index("OFC"))
                       for r in test.itertuples(index=False)])
    mean_no_ofc = float(diff[no_ofc].mean())
    c2_pass = mean_no_ofc >= 0.0
    print(f"OOS paired diff (no-offset − offset): {mean:+.4f} ± {se:.4f} "
          f"-> C1 {'PASS' if c1_pass else 'FAIL'}")
    print(f"OOS paired diff excl. OFC matches:    {mean_no_ofc:+.4f} (n={int(no_ofc.sum())}) "
          f"-> C2 {'PASS' if c2_pass else 'FAIL'}")
    print(f"split-half sign stability             -> C3 {'PASS' if c3_pass else 'FAIL'}")

    ship = bool(c1_pass and c2_pass and c3_pass)
    # production thetas: refit on the FULL window only if shipping
    theta_full = fit_thetas(cross, p, counts) if ship else theta
    out = {
        "confeds": CONFEDS,
        "theta_train": {c: float(t) for c, t in zip(CONFEDS, theta)},
        "theta_full": {c: float(t) for c, t in zip(CONFEDS, theta_full)},
        "split_2018_2021": {c: float(t) for c, t in zip(CONFEDS, th_a)},
        "split_2022_2024": {c: float(t) for c, t in zip(CONFEDS, th_b)},
        "oos": {"n": int(len(test)), "paired_diff_mean": mean, "se": se,
                "paired_diff_excl_ofc": mean_no_ofc,
                "n_excl_ofc": int(no_ofc.sum())},
        "ship_criteria": {"C1_oos_ge_1se": bool(c1_pass),
                          "C2_not_ofc_driven": bool(c2_pass),
                          "C3_split_sign_stable": bool(c3_pass)},
        "ship": ship,
        "application": "effective_elo = elo + theta[confed]; cancels within "
                       "a confederation, shifts only cross-confed matches",
    }
    with open(os.path.join(DATA, "confed_offsets_r4.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSHIP={ship} -> data/confed_offsets_r4.json")


if __name__ == "__main__":
    main()
