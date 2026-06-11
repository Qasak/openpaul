"""Test the 'Elo overrates CONMEBOL' alternative explanation for the South
American positive value edges (REPORT §8.2).

Method: on inter-confederation matches involving a CONMEBOL side (2018+,
pre-match recomputed Elo mapped to official scale), compare the Elo-model
expected points share of the CONMEBOL team against the actual points share.
If Elo systematically overrates CONMEBOL, actual should fall short of
expected. Caveats (disclosed in REPORT §8.2): the production params were fit
on a window overlapping these matches (in-sample for the global (a,b,ρ) —
they are confederation-blind, so the direction of the test is unaffected);
the SE treats matches as i.i.d. — team-clustered SE would be somewhat wider.

CONMEBOL members (10): Argentina, Bolivia, Brazil, Chile, Colombia, Ecuador,
Paraguay, Peru, Uruguay, Venezuela.

Usage:  python3 -m src.conmebol_check   (writes data/conmebol_check.json)
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from .fit import HOME_ADV, load_fit_matches
from .load_data import DATA
from .model import MatchModel

CONMEBOL = {"Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Ecuador",
            "Paraguay", "Peru", "Uruguay", "Venezuela"}


def main() -> None:
    hist = load_fit_matches()           # 2018+ with mapped pre-match Elo + d
    params = json.load(open(os.path.join(DATA, "params_fit.json")))
    mm = MatchModel(params)

    is_c_home = hist["home_team"].isin(CONMEBOL)
    is_c_away = hist["away_team"].isin(CONMEBOL)
    inter = hist[is_c_home ^ is_c_away].copy()   # exactly one CONMEBOL side

    exp_pts, act_pts, per_match_diff = [], [], []
    for r in inter.itertuples(index=False):
        c_home = r.home_team in CONMEBOL
        d = r.d if c_home else -r.d
        w, dr, l = mm.wdl(float(d))
        e = w + 0.5 * dr                          # expected score of CONMEBOL side
        sc, so = (r.home_score, r.away_score) if c_home else (r.away_score, r.home_score)
        a = 1.0 if sc > so else (0.5 if sc == so else 0.0)
        exp_pts.append(e); act_pts.append(a); per_match_diff.append(a - e)

    diff = np.array(per_match_diff)
    out = {
        "n_matches": int(len(inter)),
        "window": [str(inter["date"].min()), str(inter["date"].max())],
        "expected_score_share": float(np.mean(exp_pts)),
        "actual_score_share": float(np.mean(act_pts)),
        "actual_minus_expected": float(diff.mean()),
        "se": float(diff.std(ddof=1) / np.sqrt(len(diff))),
        "interpretation": "negative actual_minus_expected = CONMEBOL underperforms "
                          "its Elo expectation in inter-confederation play "
                          "(supports the 'Elo overrates CONMEBOL' explanation); "
                          "positive = outperforms (supports genuine market "
                          "underpricing of South American teams)",
    }
    with open(os.path.join(DATA, "conmebol_check.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"inter-confederation matches with one CONMEBOL side (2018+): {out['n_matches']}")
    print(f"CONMEBOL expected score share {out['expected_score_share']:.4f} vs "
          f"actual {out['actual_score_share']:.4f}  ->  "
          f"actual-expected = {out['actual_minus_expected']:+.4f} ± {out['se']:.4f}")


if __name__ == "__main__":
    main()
