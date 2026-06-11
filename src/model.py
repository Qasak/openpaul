"""Match-level scoring model.

Core idea: team strength comes from World Football Elo ratings; the score
distribution comes from a Dixon-Coles-adjusted double Poisson whose goal
rates are an exponential function of the Elo difference. The mapping
Elo-diff -> goal rates is calibrated so that the goal model's implied match
expectation (win + 0.5*draw) reproduces the canonical Elo expectation curve
E = 1 / (1 + 10^(-d/400)) — i.e. the goal model is anchored to Elo, and adds
a realistic score structure on top (needed for goal-difference tiebreakers
and draw/extra-time handling).

Calibrated parameters live in data/params.json (produced by calibrate()).
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import poisson

MAX_GOALS = 10            # score grid covers 0..MAX_GOALS goals per team
HOME_ELO = 100.0          # Elo bonus for a true home team (eloratings.net convention)
BASE_LAMBDA = 1.30        # expected goals per team between equal sides (intl. average ~2.6 total)
RHO = -0.10               # Dixon-Coles low-score correction (literature value; see REPORT limitations)
LAMBDA_CLIP = (0.15, 6.0) # guard against absurd rates for extreme Elo gaps
ET_FACTOR = 1.0 / 3.0     # extra time = 30 min of a 90-min match

_GOALS = np.arange(MAX_GOALS + 1)


def elo_expectation(d: np.ndarray | float) -> np.ndarray | float:
    """Canonical Elo expected score for rating difference d (incl. home bonus)."""
    return 1.0 / (1.0 + 10.0 ** (-np.asarray(d, dtype=float) / 400.0))


def goal_rates(d: float, a: float, b: float) -> tuple[float, float]:
    """Goal rates (lam1, lam2) for a match with Elo difference d = elo1 - elo2."""
    lam1 = float(np.clip(math.exp(a + b * d), *LAMBDA_CLIP))
    lam2 = float(np.clip(math.exp(a - b * d), *LAMBDA_CLIP))
    return lam1, lam2


def score_grid(lam1: float, lam2: float, rho: float = RHO) -> np.ndarray:
    """(MAX_GOALS+1)^2 matrix of P(score1=i, score2=j), Dixon-Coles adjusted."""
    p1 = poisson.pmf(_GOALS, lam1)
    p2 = poisson.pmf(_GOALS, lam2)
    grid = np.outer(p1, p2)
    # Dixon-Coles tau adjustment on the four low-score cells
    grid[0, 0] *= max(1.0 - lam1 * lam2 * rho, 1e-12)
    grid[0, 1] *= max(1.0 + lam1 * rho, 1e-12)
    grid[1, 0] *= max(1.0 + lam2 * rho, 1e-12)
    grid[1, 1] *= max(1.0 - rho, 1e-12)
    return grid / grid.sum()


def wdl_from_grid(grid: np.ndarray) -> tuple[float, float, float]:
    w = float(np.tril(grid, -1).sum())   # score1 > score2
    dr = float(np.trace(grid))
    l = float(np.triu(grid, 1).sum())
    return w, dr, l


def _model_expectation(d: float, a: float, b: float, rho: float) -> float:
    lam1, lam2 = goal_rates(d, a, b)
    w, dr, _ = wdl_from_grid(score_grid(lam1, lam2, rho))
    return w + 0.5 * dr


def calibrate(out_path: str | None = None) -> dict:
    """Fit slope b so the goal model reproduces the Elo expectation curve.

    a is anchored at log(BASE_LAMBDA). Returns the params dict and optionally
    writes it to JSON.
    """
    a = math.log(BASE_LAMBDA)
    ds = np.arange(-800, 801, 25)
    target = elo_expectation(ds)

    def loss(b: float) -> float:
        pred = np.array([_model_expectation(float(d), a, b, RHO) for d in ds])
        return float(np.mean((pred - target) ** 2))

    res = minimize_scalar(loss, bounds=(5e-4, 5e-3), method="bounded")
    b = float(res.x)
    pred = np.array([_model_expectation(float(d), a, b, RHO) for d in ds])
    params = {
        "a": a,
        "b": b,
        "rho": RHO,
        "home_elo": HOME_ELO,
        "base_lambda": BASE_LAMBDA,
        "max_goals": MAX_GOALS,
        "lambda_clip": list(LAMBDA_CLIP),
        "fit_rmse": float(np.sqrt(np.mean((pred - target) ** 2))),
        "fit_max_abs_err": float(np.max(np.abs(pred - target))),
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(params, f, indent=2)
    return params


class MatchModel:
    """Samples match scores; caches score grids keyed by rounded Elo diff."""

    def __init__(self, params: dict):
        self.a = params["a"]
        self.b = params["b"]
        self.rho = params["rho"]
        self.home_elo = params["home_elo"]
        self._cache: dict[int, np.ndarray] = {}   # d_rounded -> flat cumsum
        self._et_cache: dict[int, np.ndarray] = {}

    def _key(self, d: float) -> int:
        return int(round(d / 5.0) * 5)

    def _cumsum(self, d: float) -> np.ndarray:
        k = self._key(d)
        if k not in self._cache:
            lam1, lam2 = goal_rates(k, self.a, self.b)
            self._cache[k] = np.cumsum(score_grid(lam1, lam2, self.rho).ravel())
        return self._cache[k]

    def _et_cumsum(self, d: float) -> np.ndarray:
        k = self._key(d)
        if k not in self._et_cache:
            lam1, lam2 = goal_rates(k, self.a, self.b)
            g = np.outer(poisson.pmf(_GOALS, lam1 * ET_FACTOR),
                         poisson.pmf(_GOALS, lam2 * ET_FACTOR))
            self._et_cache[k] = np.cumsum((g / g.sum()).ravel())
        return self._et_cache[k]

    def diff(self, elo1: float, elo2: float, home1: bool = False, home2: bool = False) -> float:
        return (elo1 + self.home_elo * home1) - (elo2 + self.home_elo * home2)

    def sample_scores(self, d: float, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
        """Sample n 90-minute scores for Elo diff d. Returns (goals1, goals2)."""
        idx = np.searchsorted(self._cumsum(d), rng.random(n))
        return np.divmod(idx, MAX_GOALS + 1)

    def sample_scores_vec(self, d_vec: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
        """Sample one score per element of d_vec (one Elo diff per simulation)."""
        out = np.empty(d_vec.shape[0], dtype=np.int64)
        keys = (np.round(d_vec / 5.0) * 5).astype(int)
        for k in np.unique(keys):
            mask = keys == k
            out[mask] = np.searchsorted(self._cumsum(float(k)), rng.random(int(mask.sum())))
        return np.divmod(out, MAX_GOALS + 1)

    def sample_knockout(self, d: float, rng: np.random.Generator) -> bool:
        """Simulate one knockout tie. Returns True iff team1 advances."""
        g1, g2 = self.sample_scores(d, 1, rng)
        if g1[0] != g2[0]:
            return bool(g1[0] > g2[0])
        # extra time: plain double Poisson at 1/3 rate
        idx = int(np.searchsorted(self._et_cumsum(d), rng.random()))
        e1, e2 = divmod(idx, MAX_GOALS + 1)
        if e1 != e2:
            return e1 > e2
        # penalty shootout: coin flip (documented simplification)
        return bool(rng.random() < 0.5)

    def wdl(self, d: float) -> tuple[float, float, float]:
        lam1, lam2 = goal_rates(d, self.a, self.b)
        return wdl_from_grid(score_grid(lam1, lam2, self.rho))
