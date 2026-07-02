"""Round-4 check: does Elo systematically misprice whole confederations?

Trigger: in the 2026 group stage + R32, CAF sides beat the model's expected
points by +11.4 over 30 cross-confederation matches (largest of any confed)
while AFC fell short by -9.6. Before adding any confederation offset to the
model, test whether the pattern exists HISTORICALLY (2018+, pre-2026 data) —
the same design as src/conmebol_check.py, extended to all confederations.

Confederation membership is derived from the match dataset itself (which
continental championship / qualification a team appears in most), so names
align with the data by construction — no hand-typed member lists. Teams that
never appear in a continental competition are left unassigned and their
matches are skipped (counted in the output).

Usage:  python3 -m src.confed_check   (writes data/confed_check.json)
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from .fit import load_fit_matches
from .load_data import DATA
from .model import MatchModel

CONFED_MARKERS = {
    "CAF": ("African Cup of Nations", "Africa Cup of Nations", "African Nations Championship"),
    "AFC": ("AFC Asian Cup",),
    "UEFA": ("UEFA Euro", "UEFA Nations League"),
    "CONMEBOL": ("Copa América", "Copa America"),
    "CONCACAF": ("Gold Cup", "CONCACAF"),
    "OFC": ("OFC Nations Cup", "Oceania Nations Cup"),
}


def derive_membership() -> dict[str, str]:
    """team -> confederation, by majority of continental-competition entries
    over the FULL history (invitational appearances get out-voted)."""
    df = pd.read_csv(os.path.join(DATA, "raw", "international_results.csv"))
    votes: dict[str, Counter] = defaultdict(Counter)
    for r in df.itertuples(index=False):
        t = str(r.tournament)
        for confed, markers in CONFED_MARKERS.items():
            if any(m in t for m in markers):
                votes[str(r.home_team)][confed] += 1
                votes[str(r.away_team)][confed] += 1
                break
    return {team: c.most_common(1)[0][0] for team, c in votes.items()}


def main() -> None:
    from .load_data import canon
    membership_raw = derive_membership()
    membership = {canon(t): c for t, c in membership_raw.items()}
    hist = load_fit_matches()          # 2018+ with mapped pre-match Elo + d
    params = json.load(open(os.path.join(DATA, "params_fit.json")))
    mm = MatchModel(params)

    hist = hist.copy()
    hist["c_home"] = hist["home_team"].map(membership)
    hist["c_away"] = hist["away_team"].map(membership)
    unassigned = hist["c_home"].isna() | hist["c_away"].isna()
    cross = hist[(~unassigned) & (hist["c_home"] != hist["c_away"])]

    per: dict[str, list] = defaultdict(list)
    for r in cross.itertuples(index=False):
        w, dr, l = mm.wdl(float(r.d))
        e_home = w + 0.5 * dr
        a_home = 1.0 if r.home_score > r.away_score else \
            (0.5 if r.home_score == r.away_score else 0.0)
        per[r.c_home].append(a_home - e_home)
        per[r.c_away].append((1.0 - a_home) - (1.0 - e_home))

    rows = {}
    for confed, diffs in sorted(per.items()):
        arr = np.array(diffs)
        rows[confed] = {
            "n_matches": int(len(arr)),
            "actual_minus_expected_share": float(arr.mean()),
            "se": float(arr.std(ddof=1) / np.sqrt(len(arr))),
            "z": float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))),
        }
    out = {
        "window": [str(cross["date"].min()), str(cross["date"].max())],
        "n_cross_confed_matches": int(len(cross)),
        "n_skipped_unassigned": int(unassigned.sum()),
        "membership_size": dict(Counter(membership.values())),
        "per_confederation": rows,
        "interpretation": "positive share = confederation outperforms its "
                          "Elo expectation in cross-confederation play "
                          "(Elo underrates it); the 2026 in-tournament "
                          "deltas were CAF +11.4 pts / AFC -9.6 pts over "
                          "30/27 matches",
        "note": "same caveats as conmebol_check: global params are "
                "confederation-blind; i.i.d. SE (team clustering would "
                "widen it)",
    }
    with open(os.path.join(DATA, "confed_check.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"cross-confed matches 2018+: {len(cross)} "
          f"(skipped {int(unassigned.sum())} with unassigned side)")
    for confed, r in sorted(rows.items(), key=lambda x: -x[1]["z"]):
        print(f"  {confed:9s} n={r['n_matches']:>4}  act-exp "
              f"{r['actual_minus_expected_share']:+.4f} ± {r['se']:.4f}  "
              f"z={r['z']:+.2f}")


if __name__ == "__main__":
    main()
