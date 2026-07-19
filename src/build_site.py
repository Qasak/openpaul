"""Build the public, read-only showcase site (docs/index.html).

"决赛之夜" edition — cinematic dark-stadium data showcase.
  - hero carousel over the live contenders (auto-cycle + click-to-switch)
  - OpenPaul mascot above the hero: a short-legged octopus that hops over to
    the active contender's flag on every switch (click or auto-cycle), picks
    it up overhead (❗), and occasionally scratches its head (❓)
  - 3D probability terrain (equalizer-wave intro that settles to real data;
    manual drag only, no auto-rotate)
  - 3D night-earth globe with glowing championship pillars (auto-cruise,
    paused when offscreen / tab hidden)
  - scroll-triggered digit-flicker and bar-growth effects in sections 04-07
  - ALL assets self-hosted under docs/ (echarts, echarts-gl, fonts, flags,
    earth texture) — no runtime third-party dependency, mainland-China safe;
    downloaded once at build time and cached
  - graceful degradation: no WebGL -> 2D fallback; JS dead -> content still
    visible (reveal-hiding is gated behind an html.js class)
  - results-only

Regenerate after every matchday recalc:  python3 -m src.build_site
Set the repo link once it exists:  WC26_REPO_URL=https://github.com/you/repo
"""
from __future__ import annotations

import base64
import csv
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

from .altdata import build as build_altdata
from .load_data import DATA
from .webapp import api_all

ROOT = os.path.dirname(DATA)
OUT = os.path.join(ROOT, "docs")
REPO_URL = os.environ.get("WC26_REPO_URL", "")

ISO = {
    "Mexico": "mx", "South Africa": "za", "South Korea": "kr", "Czechia": "cz",
    "Canada": "ca", "Bosnia and Herzegovina": "ba", "Qatar": "qa",
    "Switzerland": "ch", "Brazil": "br", "Morocco": "ma", "Haiti": "ht",
    "Scotland": "gb-sct", "United States": "us", "Paraguay": "py",
    "Australia": "au", "Turkey": "tr", "Germany": "de", "Ecuador": "ec",
    "Ivory Coast": "ci", "Curaçao": "cw", "Netherlands": "nl", "Japan": "jp",
    "Tunisia": "tn", "Sweden": "se", "Belgium": "be", "Egypt": "eg",
    "Iran": "ir", "New Zealand": "nz", "Spain": "es", "Uruguay": "uy",
    "Saudi Arabia": "sa", "Cape Verde": "cv", "France": "fr", "Senegal": "sn",
    "Norway": "no", "Iraq": "iq", "Argentina": "ar", "Algeria": "dz",
    "Austria": "at", "Jordan": "jo", "Portugal": "pt", "Colombia": "co",
    "Uzbekistan": "uz", "DR Congo": "cd", "England": "gb-eng",
    "Croatia": "hr", "Ghana": "gh", "Panama": "pa",
}

VENDOR = {
    "vendor/echarts.min.js":
        "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js",
    "vendor/echarts-gl.min.js":
        "https://cdn.jsdelivr.net/npm/echarts-gl@2.0.9/dist/echarts-gl.min.js",
    "assets/earth-night.jpg":
        "https://cdn.jsdelivr.net/npm/three-globe@2.31.0/example/img/earth-night.jpg",
}
FONT_CSS_URL = ("https://fonts.googleapis.com/css2?"
                "family=Space+Grotesk:wght@400;600;700&display=swap")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def ensure_assets() -> str:
    """Download every runtime asset into docs/ once (cached). Returns the
    @font-face CSS block (self-hosted woff2), or '' on font failure."""
    for sub in ("vendor", "assets", "flags", "fonts"):
        os.makedirs(os.path.join(OUT, sub), exist_ok=True)
    for rel, url in VENDOR.items():
        path = os.path.join(OUT, rel)
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            print(f"  fetching {rel} ...")
            with open(path, "wb") as f:
                f.write(_get(url))
    for iso in sorted(set(ISO.values())):
        for w in (80, 320):
            path = os.path.join(OUT, "flags", f"{iso}-w{w}.png")
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(_get(f"https://flagcdn.com/w{w}/{iso}.png"))
    # fonts: latin subset woff2 for weights 400/600/700
    try:
        css = _get(FONT_CSS_URL).decode()
        blocks = re.findall(
            r"/\* latin \*/\s*@font-face\s*\{[^}]*?font-weight:\s*(\d+);[^}]*?"
            r"src: url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", css)
        faces = []
        for weight, url in blocks:
            fname = f"fonts/sg-{weight}.woff2"
            path = os.path.join(OUT, fname)
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(_get(url))
            faces.append(
                f"@font-face{{font-family:'Space Grotesk';font-style:normal;"
                f"font-weight:{weight};font-display:swap;"
                f"src:url({fname}) format('woff2');"
                f"unicode-range:U+0000-00FF,U+2013-2014,U+2212;}}")
        return "\n".join(faces)
    except Exception as e:
        print(f"  font self-host failed ({e}); falling back to system fonts")
        return ""


PRE_SNAPSHOT = os.path.join(ROOT, "predictions",
                            "2026-06-11_round2_pretournament.csv")
PENALTY_SHOOTOUTS = os.path.join(DATA, "penalty_shootouts.json")
TOURNAMENT_COMPLETION = os.path.join(DATA, "tournament_completion.json")


BLEND_MARKET_W = 0.5   # knockout-stage market weight (see market_champion_meta.json)


def apply_market_blend(d: dict) -> None:
    """Market-anchored headline champion probability (in-place).

    The saved outright board can predate the current knockout round. We first
    condition its de-vigged probabilities on teams that are still alive in the
    simulation, then blend (1-w)*model + w*market. This prevents eliminated
    teams from retaining title probability and makes the old market snapshot's
    role explicit. The pure-model and conditioned-market numbers remain on
    every row. No file -> untouched (pure model)."""
    mc_path = os.path.join(DATA, "market_champion.csv")
    if not os.path.exists(mc_path):
        return
    import pandas as pd
    from .market import devig_power
    mc = pd.read_csv(mc_path)
    meta_path = os.path.join(DATA, "market_champion_meta.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    w = float(meta.get("blend_weight_market", BLEND_MARKET_W))
    p_mkt = devig_power(mc["decimal_odds"].to_numpy(dtype=float))
    mkt_raw = {t: float(p) for t, p in zip(mc["team"], p_mkt)}
    live = [row for row in d.get("summary", [])
            if float(row.get("p_champion") or 0) > 0]
    live_market_total = sum(mkt_raw.get(row["team"], 0.0) for row in live)
    if not live or live_market_total <= 0:
        return
    mkt = {row["team"]: mkt_raw.get(row["team"], 0.0) / live_market_total
           for row in live}
    for row in d.get("summary", []):
        pm = float(row["p_champion"])
        row["p_champion_model"] = pm
        pk = mkt.get(row["team"], 0.0)
        row["p_market_champ"] = pk
        row["p_champion_blended"] = (1.0 - w) * pm + w * pk
    tot = sum(r["p_champion_blended"] for r in d["summary"]) or 1.0
    for r in d["summary"]:
        r["p_champion_blended"] /= tot
        r["p_champion"] = r["p_champion_blended"]   # headline = blend everywhere
    d["summary"].sort(key=lambda r: -r["p_champion"])
    d["blend"] = {"weight_market": w, "as_of": meta.get("as_of"),
                  "source": meta.get("source"), "snapshot_stage": meta.get("stage"),
                  "conditioned_to": [r["team"] for r in live],
                  "devig": meta.get("devig", "power")}


def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _mean(rows: list[dict], key: str) -> float | None:
    vals = [float(r[key]) for r in rows if r.get(key) not in (None, "")]
    return sum(vals) / len(vals) if vals else None


def build_tournament_review() -> dict | None:
    """Build the completed-tournament recap from sealed and realised data.

    The function deliberately keeps three scoring universes separate:
    group-stage 1X2 Brier, knockout binary advancement Brier, and the
    pre-tournament outright snapshot.  Mixing those scales would make the
    final report look simpler while making it statistically misleading.
    """
    results = _read_csv(os.path.join(DATA, "results.csv"))
    by_match = {int(r["match"]): r for r in results}
    final = by_match.get(104)
    third = by_match.get(103)
    if len(results) != 104 or not final or not third:
        return None
    if not final.get("winner") or not third.get("winner"):
        raise ValueError("completed tournament is missing a final/third-place winner")

    def loser(r: dict) -> str:
        if r["winner"] == r["team1"]:
            return r["team2"]
        if r["winner"] == r["team2"]:
            return r["team1"]
        raise ValueError(f"winner not in match {r['match']}")

    completion = {}
    if os.path.exists(TOURNAMENT_COMPLETION):
        with open(TOURNAMENT_COMPLETION, encoding="utf-8") as f:
            completion = json.load(f)
    statuses = completion.get("matches", {})
    final_status = statuses.get("104", {}).get("status")
    third_status = statuses.get("103", {}).get("status")

    pre = _read_csv(PRE_SNAPSHOT)
    pre_by_team = {r["team"]: r for r in pre}
    pre_abs = sorted(pre, key=lambda r: -float(r["p_champion"]))
    pre_value = sorted(
        (r for r in pre if r.get("edge_sharp_pp") not in (None, "")),
        key=lambda r: -float(r["edge_sharp_pp"]),
    )

    podium = [
        {"place": 1, "team": final["winner"], "code": "champion"},
        {"place": 2, "team": loser(final), "code": "runner_up"},
        {"place": 3, "team": third["winner"], "code": "third"},
        {"place": 4, "team": loser(third), "code": "fourth"},
    ]
    for row in podium:
        p = pre_by_team.get(row["team"], {})
        row["pre_champion"] = (float(p["p_champion"])
                               if p.get("p_champion") else None)
        row["pre_rank"] = (next((i + 1 for i, x in enumerate(pre_abs)
                                  if x["team"] == row["team"]), None))

    scores = [(int(r["score1"]), int(r["score2"])) for r in results]
    total_goals = sum(a + b for a, b in scores)
    draws = sum(a == b for a, b in scores)
    shootouts = sum(int(r["match"]) >= 73
                    and int(r["score1"]) == int(r["score2"])
                    and bool(r.get("winner")) for r in results)

    group = _read_csv(os.path.join(DATA, "score_log.csv"))
    group_market = [r for r in group if r.get("brier_market") not in (None, "")]
    v2 = _read_csv(os.path.join(DATA, "score_log_ko.csv"))
    r4 = _read_csv(os.path.join(DATA, "score_log_ko_r4.csv"))
    v2_by_match = {int(r["match"]): r for r in v2}
    r4_by_match = {int(r["match"]): r for r in r4}
    common_matches = sorted(set(v2_by_match) & set(r4_by_match))
    v2_common = [v2_by_match[m] for m in common_matches]
    r4_common = [r4_by_match[m] for m in common_matches]

    def accuracy(rows: list[dict]) -> float | None:
        if not rows:
            return None
        return sum(float(r["p_advancer"]) >= 0.5 for r in rows) / len(rows)

    def sealed_match(path: str, match_no: int, winner: str) -> dict | None:
        rows = [r for r in _read_csv(path) if int(r["match"]) == match_no]
        if not rows:
            return None
        r = rows[-1]
        p1 = float(r["p1_advance"])
        winner_p = p1 if winner == r["team1"] else 1.0 - p1
        return {"team1": r["team1"], "team2": r["team2"],
                "p1_advance": p1, "winner_probability": winner_p,
                "forecast_at": r["forecast_at"]}

    # Actual finish for the retrospective 48-team table.  A later knockout
    # appearance overwrites an earlier one; podium places are then made exact.
    finish = {r["team"]: "group" for r in pre}
    round_code = {"R32": "r32", "R16": "r16", "QF": "qf", "SF": "sf"}
    for r in sorted(results, key=lambda x: int(x["match"])):
        if int(r["match"]) < 73 or r.get("group") not in round_code:
            continue
        for team in (r["team1"], r["team2"]):
            finish[team] = round_code[r["group"]]
    finish.update({row["team"]: row["code"] for row in podium})

    return {
        "podium": podium,
        "finish": finish,
        "final": {"match": 104, "team1": final["team1"],
                  "team2": final["team2"], "score1": int(final["score1"]),
                  "score2": int(final["score2"]), "winner": final["winner"],
                  "status": final_status},
        "third_place": {"match": 103, "team1": third["team1"],
                        "team2": third["team2"], "score1": int(third["score1"]),
                        "score2": int(third["score2"]), "winner": third["winner"],
                        "status": third_status},
        "stats": {"matches": len(results), "goals": total_goals,
                  "goals_per_match": total_goals / len(results),
                  "draws": draws, "shootouts": shootouts},
        "pre": {
            "absolute_leader": pre_abs[0] if pre_abs else None,
            "value_leader": pre_value[0] if pre_value else None,
            "top_four": pre_abs[:4],
            "champion": pre_by_team.get(final["winner"]),
            "runner_up": pre_by_team.get(loser(final)),
            "germany": pre_by_team.get("Germany"),
        },
        "final_forecasts": {
            "r4": sealed_match(os.path.join(ROOT, "predictions", "ko_forecasts_r4.csv"), 104, final["winner"]),
            "v2": sealed_match(os.path.join(ROOT, "predictions", "ko_forecasts.csv"), 104, final["winner"]),
        },
        "third_forecasts": {
            "r4": sealed_match(os.path.join(ROOT, "predictions", "ko_forecasts_r4.csv"), 103, third["winner"]),
            "v2": sealed_match(os.path.join(ROOT, "predictions", "ko_forecasts.csv"), 103, third["winner"]),
        },
        "scoring": {
            "group": {"n": len(group), "model_brier": _mean(group, "brier_model"),
                      "model_logloss": _mean(group, "logloss_model"),
                      "market_common_n": len(group_market),
                      "model_common_brier": _mean(group_market, "brier_model"),
                      "market_common_brier": _mean(group_market, "brier_market")},
            "knockout": {"v2_n": len(v2), "v2_brier": _mean(v2, "brier"),
                         "v2_logloss": _mean(v2, "logloss"),
                         "v2_accuracy": accuracy(v2), "r4_n": len(r4),
                         "r4_brier": _mean(r4, "brier"),
                         "r4_logloss": _mean(r4, "logloss"),
                         "r4_accuracy": accuracy(r4), "common_n": len(common_matches),
                         "v2_common_brier": _mean(v2_common, "brier"),
                         "r4_common_brier": _mean(r4_common, "brier")},
        },
        "source": completion.get("source", "ESPN scoreboard"),
    }


def build_final_analysis(d: dict) -> dict | None:
    """Assemble the Spain–Argentina final dashboard from reproducible data.

    Match probabilities come from the production rolling-Elo knockout forecast;
    tournament records come from results.csv; scoreline probabilities reuse the
    same Dixon-Coles model and sigma=75 quadrature as the forecast. Historical
    head-to-head is background only and never enters the model.
    """
    # Use the append-only pre-kickoff ledger. data/ko_forecast.csv is a rolling
    # production view and is recalculated after the result, so it is not valid
    # evidence for what the model said before the final.
    forecast_path = os.path.join(ROOT, "predictions", "ko_forecasts_r4.csv")
    results_path = os.path.join(DATA, "results.csv")
    if not os.path.exists(forecast_path) or not os.path.exists(results_path):
        return None
    with open(forecast_path, encoding="utf-8") as f:
        forecasts = list(csv.DictReader(f))
    finals = [r for r in forecasts if r.get("round") == "FINAL"]
    if not finals:
        return None
    fc = finals[-1]
    team1, team2 = fc["team1"], fc["team2"]
    teams = {
        t: {"played": 0, "wins": 0, "draws": 0, "losses": 0,
            "gf": 0, "ga": 0, "clean_sheets": 0, "route": []}
        for t in (team1, team2)
    }
    round_order = {"R32": 1, "R16": 2, "QF": 3, "SF": 4, "FINAL": 5}
    with open(results_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("score1") in (None, "") or r.get("score2") in (None, ""):
                continue
            s1, s2 = int(r["score1"]), int(r["score2"])
            for t in (team1, team2):
                if t not in (r["team1"], r["team2"]):
                    continue
                own, opp = ((s1, s2) if r["team1"] == t else (s2, s1))
                other = r["team2"] if r["team1"] == t else r["team1"]
                st = teams[t]
                st["played"] += 1
                st["gf"] += own
                st["ga"] += opp
                st["clean_sheets"] += int(opp == 0)
                if own > opp:
                    st["wins"] += 1
                elif own == opp:
                    st["draws"] += 1
                else:
                    st["losses"] += 1
                if int(r["match"]) >= 73:
                    st["route"].append({"round": r.get("group", ""),
                                        "match": int(r["match"]),
                                        "opponent": other,
                                        "gf": own, "ga": opp})
    for st in teams.values():
        st["gd"] = st["gf"] - st["ga"]
        st["route"].sort(key=lambda r: round_order.get(r["round"], 99))

    p1_advance = float(fc["p1_advance"])
    p1_win90 = float(fc["p1_win90"])
    p_draw90 = float(fc["p_draw90"])
    p2_win90 = float(fc["p2_win90"])
    analysis = {
        "match": int(fc["match"]), "date": fc["date"], "city": fc["city"],
        "team1": team1, "team2": team2,
        "elo1": float(fc["elo1"]), "elo2": float(fc["elo2"]),
        "p1_advance": p1_advance, "p2_advance": 1.0 - p1_advance,
        "p1_win90": p1_win90, "p_draw90": p_draw90,
        "p2_win90": p2_win90,
        "p1_after_draw": ((p1_advance - p1_win90) / p_draw90
                          if p_draw90 > 0 else None),
        "teams": teams,
    }

    # Sigma-averaged 90-minute score matrix and expected goals.
    try:
        import numpy as np
        from .ko_forecast import GH_NODES, SIGMA
        from .model import MAX_GOALS, MatchModel, score_grid
        params_path = os.path.join(DATA, "params_fit.json")
        if not os.path.exists(params_path):
            params_path = os.path.join(DATA, "params.json")
        with open(params_path, encoding="utf-8") as f:
            mm = MatchModel(json.load(f))
        d0 = mm.diff(float(fc["elo1"]), float(fc["elo2"]))
        nodes, weights = np.polynomial.hermite_e.hermegauss(GH_NODES)
        weights = weights / weights.sum()
        grid = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
        for x, wt in zip(nodes, weights):
            lam1, lam2 = mm.rates(d0 + SIGMA * np.sqrt(2.0) * float(x))
            grid += wt * score_grid(lam1, lam2, mm.rho)
        analysis["xg1"] = float(sum(i * grid[i, :].sum()
                                    for i in range(MAX_GOALS + 1)))
        analysis["xg2"] = float(sum(j * grid[:, j].sum()
                                    for j in range(MAX_GOALS + 1)))
        scorelines = sorted(
            ({"score1": i, "score2": j, "p": float(grid[i, j])}
             for i in range(MAX_GOALS + 1)
             for j in range(MAX_GOALS + 1)),
            key=lambda r: -r["p"])
        analysis["scorelines"] = scorelines[:6]
    except Exception as e:
        print(f"NOTE: final scoreline grid skipped ({e})")

    # Historical meetings: context only, deliberately excluded from forecast.
    history_path = os.path.join(DATA, "raw", "international_results.csv")
    if os.path.exists(history_path):
        meetings = []
        with open(history_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if {r.get("home_team"), r.get("away_team")} == {team1, team2}:
                    meetings.append(r)
        if meetings:
            w1 = draws = w2 = gf1 = gf2 = 0
            for r in meetings:
                hs, aps = int(float(r["home_score"])), int(float(r["away_score"]))
                a, b = ((hs, aps) if r["home_team"] == team1 else (aps, hs))
                gf1 += a
                gf2 += b
                if a > b:
                    w1 += 1
                elif a == b:
                    draws += 1
                else:
                    w2 += 1
            analysis["h2h"] = {"matches": len(meetings), "team1_wins": w1,
                               "draws": draws, "team2_wins": w2,
                               "gf1": gf1, "gf2": gf2,
                               "last_date": meetings[-1]["date"]}
    try:
        analysis["penalties"] = build_penalty_analysis(
            team1, team2, analysis.get("p1_after_draw"))
    except Exception as e:
        print(f"NOTE: penalty analysis skipped ({e})")
    return analysis


def build_penalty_analysis(team1: str, team2: str,
                           p1_after_draw: float | None) -> dict:
    """Build a transparent, non-model penalty-shootout comparison.

    The source file stores match-level counts. Rates and splits are derived here
    so the public page never relies on hand-written percentages. Keeper saves
    are kept separate from off-target kicks; the displayed save rate uses every
    kick faced as its conservative denominator.
    """
    with open(PENALTY_SHOOTOUTS, encoding="utf-8") as f:
        raw = json.load(f)
    cutoff = raw["recent_since"]

    current = {team1: 0, team2: 0}
    results_path = os.path.join(DATA, "results.csv")
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("winner") not in (team1, team2):
                    continue
                if r.get("score1") in (None, "") or r.get("score2") in (None, ""):
                    continue
                if int(r["score1"]) == int(r["score2"]):
                    for team in current:
                        if team in (r["team1"], r["team2"]):
                            current[team] += 1

    profiles = {}
    for team in (team1, team2):
        src = raw["teams"][team]
        rows = sorted(src["shootouts"], key=lambda r: r["date"])
        if not rows:
            raise ValueError(f"no penalty history for {team}")
        for r in rows:
            for key in ("pens_for", "attempts_for", "pens_against",
                        "attempts_against"):
                if key not in r or int(r[key]) < 0:
                    raise ValueError(
                        f"bad {key} for {team} on {r.get('date', '?')}")
            if (int(r["pens_for"]) > int(r["attempts_for"])
                    or int(r["pens_against"]) > int(r["attempts_against"])):
                raise ValueError(f"more goals than kicks for {team} on {r['date']}")
            expected = "W" if int(r["pens_for"]) > int(r["pens_against"]) else "L"
            if r["result"] != expected:
                raise ValueError(f"bad shootout result for {team} on {r['date']}")

        def record(items):
            wins = sum(r["result"] == "W" for r in items)
            return {"played": len(items), "wins": wins,
                    "losses": len(items) - wins,
                    "win_pct": wins / len(items) if items else None}

        def kicks(items):
            scored = sum(int(r["pens_for"]) for r in items)
            attempts = sum(int(r["attempts_for"]) for r in items)
            opponent_scored = sum(int(r["pens_against"]) for r in items)
            opponent_attempts = sum(int(r["attempts_against"]) for r in items)
            if not items:
                return {
                    "scored": 0, "attempts": 0, "conversion_pct": None,
                    "opponent_scored": 0, "opponent_attempts": 0,
                    "opponent_conversion_pct": None,
                }
            if not attempts or not opponent_attempts:
                raise ValueError(f"empty kick totals for {team}")
            return {
                "scored": scored, "attempts": attempts,
                "conversion_pct": scored / attempts,
                "opponent_scored": opponent_scored,
                "opponent_attempts": opponent_attempts,
                "opponent_conversion_pct": opponent_scored / opponent_attempts,
            }

        world_cup = [r for r in rows if r["competition"] == "FIFA World Cup"]
        recent = [r for r in rows if r["date"] >= cutoff]
        kr = sorted(src["keeper"]["shootouts"], key=lambda r: r["date"])
        faced = sum(int(r["faced"]) for r in kr)
        saves = sum(int(r["saves"]) for r in kr)
        off_target = sum(int(r.get("off_target", 0)) for r in kr)
        if not faced or saves + off_target > faced:
            raise ValueError(f"bad keeper totals for {team}")
        keeper_wins = sum(r["result"] == "W" for r in kr)
        keeper = {
            "name": src["keeper"]["name"], "shootouts": len(kr),
            "wins": keeper_wins, "losses": len(kr) - keeper_wins,
            "faced": faced, "saves": saves, "off_target": off_target,
            "goals": faced - saves - off_target,
            "save_pct": saves / faced,
            "on_target_save_pct": (saves / (faced - off_target)
                                    if faced > off_target else None),
            "history": list(reversed(kr)),
        }
        profiles[team] = {
            "overall": record(rows), "world_cup": record(world_cup),
            "recent": record(recent), "recent_history": list(reversed(recent)),
            "kicks": kicks(rows), "recent_kicks": kicks(recent),
            "current_tournament_shootouts": current[team], "keeper": keeper,
            "taker_pool": src["taker_pool"],
            "taker_note_zh": src["taker_note_zh"],
            "taker_note_en": src["taker_note_en"],
        }

    model_after_draw = None
    if p1_after_draw is not None:
        model_after_draw = {team1: p1_after_draw, team2: 1.0 - p1_after_draw}
    return {
        "as_of": raw["as_of"], "recent_since": cutoff[:4],
        "definition_zh": raw["definition_zh"],
        "definition_en": raw["definition_en"],
        "model_included": False, "model_after_draw": model_after_draw,
        "teams": profiles,
        "sources": raw["sources"],
    }


def build_payload() -> dict:
    d = api_all()
    d.pop("status", None)
    d["benchmarks"] = d.get("benchmarks", [])
    if d.get("meta", {}).get("sim"):
        d["meta"]["sim"].pop("params_path", None)   # no local paths in public blob

    # The value story (edge/EV vs the sharp market) is a PRE-KICKOFF sealed
    # comparison: both sides must come from the same 2026-06-11 window. The
    # rolling pipeline refreshes report_summary.csv with CURRENT conditional
    # probabilities, so here the market/edge columns are pinned back to the
    # sealed snapshot; probability columns stay current.
    if os.path.exists(PRE_SNAPSHOT):
        import pandas as pd
        pre = pd.read_csv(PRE_SNAPSHOT)
        frozen_cols = [c for c in pre.columns if c.startswith(
            ("decimal_odds", "p_market", "edge_", "ev_"))]
        pm = pre.set_index("team")
        for row in d.get("summary", []):
            if row["team"] in pm.index:
                for c in frozen_cols:
                    v = pm.at[row["team"], c]
                    row[c] = None if pd.isna(v) else float(v)
        d["pre"] = []
        for _, r in pre.iterrows():
            row = {}
            for key, value in r.items():
                if pd.isna(value):
                    row[key] = None
                else:
                    row[key] = value.item() if hasattr(value, "item") else value
            d["pre"].append(row)
        d["value_snapshot_date"] = "2026-06-11"
    # Once the group stage is fully played, the completed group-stage panels
    # (standings §05 + the 72-match forecast list §07) fold shut by default so
    # the live knockout bracket is what greets the reader.
    grp = [m for m in d.get("matches", []) if int(m.get("match", 0)) <= 72]
    d["group_stage_done"] = bool(grp) and all(
        m.get("score1") is not None for m in grp)
    d["review"] = build_tournament_review()
    d["tournament_done"] = d["review"] is not None
    if d["review"] and d.get("pre"):
        for row in d["pre"]:
            row["finish"] = d["review"]["finish"].get(row["team"], "group")
    if not d["tournament_done"]:
        apply_market_blend(d)
    d["final"] = build_final_analysis(d)
    if not d["tournament_done"]:
        try:
            d["alt"] = build_altdata()   # fun alt-data sidebar (never in the model)
        except Exception as e:
            print(f"NOTE: alt-data skipped ({e})")
    d["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return d


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2026 世界杯总结：西班牙加时击败阿根廷夺冠｜OpenPaul</title>
<meta name="description" content="西班牙加时 1–0 击败阿根廷，夺得 2026 世界杯；英格兰 6–4 战胜法国获得季军。回看开赛前冠军榜、决赛存证概率，以及 104 场赛果的公开计分。">
<meta name="theme-color" content="#05070f">
<meta name="twitter:card" content="summary_large_image">
<meta property="og:title" content="2026 世界杯总结 · 西班牙夺冠｜OpenPaul 🐙">
<meta property="og:description" content="西班牙加时 1–0 阿根廷夺冠 · 英格兰 6–4 法国获季军 · 赛前预测、决赛账本与完整 Brier 计分复盘">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%99%3C/text%3E%3C/svg%3E">
<style>
__FONTCSS__
:root{
  --bg:#05070f; --bg2:#0a1020; --card:#0d1424; --card2:#0a101e; --line:#1b2740;
  --txt:#e8eef9; --dim:#76879f; --gold:#f0c75e; --gold2:#ffe9a8; --golddeep:#b8862e;
  --pos:#3ad6a8; --neg:#ff5d7a; --blue:#4f8dff; --num:'Space Grotesk',ui-monospace,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
::selection{background:rgba(240,199,94,.35);color:#fff}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--txt);
  font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;
  font-size:14px;line-height:1.6;overflow-x:hidden}
::-webkit-scrollbar{width:10px;background:#070b15}
::-webkit-scrollbar-thumb{background:#1d2a45;border-radius:6px}

#grain{position:fixed;inset:0;z-index:99;pointer-events:none;opacity:.05;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='0.7'/%3E%3C/svg%3E")}
.aurora{position:fixed;border-radius:50%;filter:blur(110px);z-index:-2;pointer-events:none}
.aurora.a1{width:640px;height:640px;left:-200px;top:-160px;background:radial-gradient(circle,rgba(34,72,158,.34),transparent 65%);animation:drift1 28s ease-in-out infinite}
.aurora.a2{width:520px;height:520px;right:-160px;top:45%;background:radial-gradient(circle,rgba(240,199,94,.13),transparent 65%);animation:drift2 36s ease-in-out infinite}
.aurora.a3{width:700px;height:500px;left:30%;bottom:-280px;background:radial-gradient(ellipse,rgba(16,84,56,.30),transparent 65%);animation:drift1 44s ease-in-out infinite reverse}
@keyframes drift1{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(70px,46px) scale(1.1)}}
@keyframes drift2{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(-60px,-50px) scale(.94)}}

nav{position:fixed;top:0;left:0;right:0;z-index:60;display:flex;align-items:center;gap:14px;
  padding:14px 30px;background:rgba(5,8,16,.55);backdrop-filter:blur(14px);
  border-bottom:1px solid rgba(27,39,64,.6)}
.wordmark{font-family:var(--num);font-weight:700;font-size:15px;letter-spacing:.14em;color:var(--gold)}
.wordmark span{color:var(--dim);font-weight:400}
nav .links{margin-left:auto;display:flex;gap:18px;align-items:center;font-size:12.5px}
nav .links a{color:var(--dim);text-decoration:none;transition:color .2s}
nav .links a:hover{color:var(--gold2)}
.badge{font-size:11.5px;padding:3px 11px;border-radius:99px;border:1px solid var(--line);color:var(--dim)}
.badge.live{color:var(--pos);border-color:rgba(58,214,168,.35)}
@media(max-width:760px){nav .links a:not(#ghLink):not(#langBtn){display:none}.badge{display:none}}

/* hero */
#hero{min-height:100vh;position:relative;display:flex;flex-direction:column;justify-content:center;
  padding:64px 6vw 70px;overflow:hidden;
  background:
    radial-gradient(1100px 460px at 50% 118%, rgba(14,68,44,.45), transparent 70%),
    radial-gradient(900px 500px at 82% -10%, rgba(28,56,120,.5), transparent 65%),
    linear-gradient(180deg,#070b16 0%,#05070f 100%)}
#stars{position:absolute;inset:0;pointer-events:none}
#hero::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(120% 90% at 50% 40%,transparent 55%,rgba(2,3,8,.75) 100%)}
.hero-grid{position:relative;z-index:2;display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,.75fr);gap:5vw;align-items:center;margin:auto 0}
@media(max-width:1020px){.hero-grid{grid-template-columns:1fr}}
.overline{font-family:var(--num);font-size:12px;letter-spacing:.34em;color:var(--gold);
  text-transform:uppercase;margin-bottom:22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.overline::before{content:"";width:42px;height:1px;background:linear-gradient(90deg,var(--gold),transparent)}
@media(max-width:560px){.overline{font-size:10px;letter-spacing:.18em}.ol2{display:none}}
.hero-q{font-size:clamp(34px,4.6vw,62px);font-weight:800;line-height:1.14;letter-spacing:.01em;
  margin-bottom:34px;text-wrap:balance}
.hero-q em{font-style:normal;white-space:nowrap;
  background:linear-gradient(100deg,#f7d77c,#fdf0c2 55%,#e3b04b);
  -webkit-background-clip:text;background-clip:text;color:transparent}
.champ-line{display:flex;align-items:center;gap:26px;flex-wrap:wrap}
.champ-line.swap>*{animation:heroswap .55s cubic-bezier(.2,.8,.2,1)}
@keyframes heroswap{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.champ-flag{width:118px;height:88px;border-radius:14px;object-fit:cover;
  box-shadow:0 0 0 1px rgba(240,199,94,.55),0 14px 44px rgba(0,0,0,.6),0 0 60px rgba(240,199,94,.18)}
.champ-name{font-size:clamp(26px,2.6vw,40px);font-weight:800}
.champ-name .en{display:block;font-family:var(--num);font-size:12px;letter-spacing:.3em;color:var(--dim);text-transform:uppercase;margin-top:2px}
.champ-pct{font-family:var(--num);font-weight:700;font-size:clamp(70px,9vw,128px);line-height:.95;
  background:linear-gradient(180deg,#fff3cf 8%,var(--gold) 55%,var(--golddeep));
  -webkit-background-clip:text;background-clip:text;color:transparent;
  filter:drop-shadow(0 6px 30px rgba(240,199,94,.25))}
.champ-sub{color:var(--dim);font-size:13.5px;margin-top:14px;min-height:22px}
.champ-sub b{color:var(--txt)}
.hero-side{display:flex;flex-direction:column;gap:10px}
.hero-side h3{font-family:var(--num);font-size:11px;letter-spacing:.3em;color:var(--dim);text-transform:uppercase;margin-bottom:6px;font-weight:600}
.rk{display:grid;grid-template-columns:30px 44px 1fr auto;gap:12px;align-items:center;
  padding:10px 14px;border:1px solid var(--line);border-radius:12px;background:rgba(13,20,36,.6);
  backdrop-filter:blur(6px);transition:transform .25s,border-color .25s,background .25s;cursor:pointer}
.rk:hover{transform:translateX(6px);border-color:rgba(240,199,94,.4)}
.rk.active{border-color:rgba(240,199,94,.75);background:rgba(36,30,12,.45)}
.rk .no{font-family:var(--num);font-weight:700;color:var(--dim)}
.rk.active .no{color:var(--gold)}
.rk img{width:42px;height:30px;border-radius:5px;object-fit:cover;box-shadow:0 2px 10px rgba(0,0,0,.5)}
.rk .nm{font-weight:600;font-size:14.5px}
.rk .p{font-family:var(--num);font-weight:700;font-size:17px;color:var(--gold2)}
.rk .track{grid-column:1/-1;height:4px;border-radius:2px;background:#131d33;overflow:hidden}
.rk .track i{display:block;height:100%;border-radius:2px;width:0;
  background:linear-gradient(90deg,#3e68b8,#79aaff);transition:width 1.4s cubic-bezier(.2,.8,.2,1)}
.rk.active .track i{background:linear-gradient(90deg,#b8862e,#ffe9a8)}
.hero-stats{position:relative;z-index:2;display:flex;gap:34px;flex-wrap:wrap;margin-top:60px}
.hstat .hv{font-family:var(--num);font-weight:700;font-size:24px;color:var(--txt)}
.hstat .hk{font-size:11.5px;color:var(--dim);letter-spacing:.12em;margin-top:2px}
.scroll-cue{position:absolute;left:50%;bottom:26px;transform:translateX(-50%);z-index:2;color:var(--dim);
  font-size:11px;letter-spacing:.3em;text-align:center;animation:bob 2.2s ease-in-out infinite}
.scroll-cue::after{content:"";display:block;width:1px;height:34px;margin:8px auto 0;
  background:linear-gradient(180deg,var(--gold),transparent)}
@keyframes bob{0%,100%{transform:translate(-50%,0)}50%{transform:translate(-50%,8px)}}

/* OpenPaul mascot — a short-legged octopus that hops over to the active
   contender's flag, picks it up and flashes ❗ */
#paul{position:relative;z-index:2;width:min(680px,94vw);margin:0 auto 14px;pointer-events:none}
#paul svg{display:block;width:100%;height:auto;overflow:visible}
#paulBodyG{transform-box:fill-box;transform-origin:50% 62%;animation:paulBob 4.6s ease-in-out infinite}
@keyframes paulBob{0%,100%{transform:rotate(-2deg)}30%{transform:rotate(2deg) scale(.985,1.015)}60%{transform:rotate(-1deg) scale(1.02,.98)}}
#paulBody{font-size:64px}
#paulArm{stroke:#b23527;stroke-width:8;fill:none;stroke-linecap:round;transform-box:fill-box;
  transform-origin:90% 100%;transform:scale(0);transition:transform .25s cubic-bezier(.34,1.56,.64,1)}
#paul.scratching #paulArm{transform:scale(1);animation:paulScr .42s ease-in-out .25s 3}
#paulHold{stroke:#b23527;stroke-width:7;fill:none;stroke-linecap:round;transform-box:fill-box;
  transform-origin:0% 100%;transform:scale(0);transition:transform .25s cubic-bezier(.34,1.56,.64,1)}
#paul.held #paulHold{transform:scale(1)}
@keyframes paulScr{0%,100%{transform:scale(1) rotate(0)}50%{transform:scale(1) rotate(-14deg)}}
#paulMark{font-size:30px;opacity:0;transform-box:fill-box;transform-origin:50% 100%}
#paul.mark #paulMark{animation:paulPop 1.8s cubic-bezier(.25,1.5,.4,1)}
@keyframes paulPop{0%{opacity:0;transform:scale(0) translateY(10px)}12%{opacity:1;transform:scale(1.3)}22%{transform:scale(1)}75%{opacity:1}100%{opacity:0;transform:scale(.7) translateY(-12px)}}
#paulShadow{fill:#000;opacity:.3}
.pmound{fill:#1b2740}
.pfg{cursor:pointer;pointer-events:auto;outline:none}
.pfg .hit{fill:transparent;stroke:none}
.pfg .pole{stroke:#9fb2cd;stroke-width:2.5;stroke-linecap:round}
.pfg .fin{fill:var(--gold)}
.pfg image{filter:drop-shadow(0 3px 7px rgba(0,0,0,.45));transition:filter .3s}
.pfg rect{fill:none;stroke:rgba(255,255,255,.25)}
.pfg:hover image,.pfg:focus-visible image{filter:drop-shadow(0 0 10px rgba(240,199,94,.55))}
.pfg.lift image{filter:drop-shadow(0 5px 14px rgba(240,199,94,.5))}
@media (prefers-reduced-motion:reduce){#paul.mark #paulMark{opacity:1}}

/* sections */
.sec{position:relative;max-width:1320px;margin:0 auto;padding:90px 6vw 10px}
.sec-head{display:flex;align-items:baseline;gap:18px;margin-bottom:8px}
.sec-no{font-family:var(--num);font-weight:700;font-size:13px;color:var(--gold);letter-spacing:.2em}
h2.sec-h{font-size:clamp(22px,2.6vw,34px);font-weight:800;letter-spacing:.01em}
.sec-d{color:var(--dim);font-size:12.5px;margin:6px 0 26px;max-width:760px}
.ghost{position:absolute;right:2vw;top:34px;font-family:var(--num);font-weight:700;
  font-size:clamp(80px,11vw,150px);line-height:1;color:rgba(118,135,159,.05);pointer-events:none;user-select:none}
.panel{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);
  border-radius:18px;padding:16px;position:relative;overflow:hidden}
.hint{position:absolute;right:18px;top:14px;z-index:5;font-size:11px;color:var(--dim);
  letter-spacing:.1em;background:rgba(5,8,16,.55);padding:4px 10px;border-radius:99px;border:1px solid var(--line)}
.hint .fine{display:inline}.hint .coarse{display:none}
@media(pointer:coarse){.hint .fine{display:none}.hint .coarse{display:inline}}

#terrain{width:100%;height:64vh;min-height:480px}
#globe{width:100%;height:78vh;min-height:540px}
.gl-fall{display:none;width:100%;height:520px}

/* finalists */
.podium{display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:18px;align-items:stretch;max-width:920px;margin:0 auto}
@media(max-width:620px){.podium{grid-template-columns:1fr}}
.pcard{position:relative;border-radius:20px;padding:30px 26px 24px;text-align:center;overflow:hidden;
  background:linear-gradient(180deg,#101a30,#0a111f);border:1px solid var(--line);
  transition:transform .35s cubic-bezier(.2,.8,.2,1),box-shadow .35s}
.pcard:hover{transform:translateY(-10px) scale(1.015);box-shadow:0 26px 60px rgba(0,0,0,.55)}
.pcard.p1{border:1px solid rgba(240,199,94,.55);padding-top:44px;
  background:linear-gradient(180deg,rgba(56,42,12,.5),#0c1322 60%);
  box-shadow:0 0 70px rgba(240,199,94,.12) inset,0 18px 50px rgba(0,0,0,.5)}
.pcard.p2{border-color:rgba(190,205,225,.4)}
.pcard.p3{border-color:rgba(205,137,80,.4)}
.pcard.p4{border-color:rgba(118,135,159,.34)}
.medal{position:absolute;top:14px;left:18px;font-family:var(--num);font-weight:700;font-size:15px;letter-spacing:.18em}
.p1 .medal{color:var(--gold)}.p2 .medal{color:#c9d6e8}.p3 .medal{color:#d99a62}.p4 .medal{color:var(--dim)}
.pflag{width:108px;height:78px;border-radius:12px;object-fit:cover;margin-bottom:16px;
  box-shadow:0 10px 30px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.12)}
.p1 .pflag{width:132px;height:96px;box-shadow:0 14px 40px rgba(0,0,0,.6),0 0 0 1px rgba(240,199,94,.5),0 0 50px rgba(240,199,94,.2)}
.pname{font-size:20px;font-weight:800}
.pper{font-family:var(--num);font-weight:700;font-size:46px;margin:6px 0 2px;color:var(--gold2)}
.p2 .pper{color:#dde7f5}.p3 .pper{color:#eab68b}.p4 .pper{color:#9fb2cd}
.pmeta{font-size:11.5px;color:var(--dim);display:flex;justify-content:center;gap:14px;margin-top:10px}
.pmeta b{color:var(--txt);font-family:var(--num)}

/* final analysis */
.final-board{padding:24px}
.final-matchline{display:grid;grid-template-columns:minmax(150px,1fr) minmax(240px,1.25fr) minmax(150px,1fr);gap:24px;align-items:center}
.final-team{text-align:center;min-width:0}
.final-team img{width:92px;height:62px;object-fit:cover;border-radius:10px;box-shadow:0 12px 34px rgba(0,0,0,.35)}
.final-team .fn{font-size:clamp(21px,2.5vw,31px);font-weight:800;margin-top:11px}
.final-team .fr{font-family:var(--num);font-size:12px;color:var(--dim);margin-top:5px}
.final-mid{text-align:center}
.final-venue{font-family:var(--num);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin-bottom:12px}
.final-probs{display:flex;align-items:baseline;justify-content:center;gap:13px;font-family:var(--num);font-weight:700}
.final-probs b{font-size:clamp(30px,4vw,50px);color:var(--gold2)}
.final-probs span{font-size:12px;color:var(--dim)}
.final-bar{height:10px;border-radius:99px;background:var(--line);overflow:hidden;display:flex;margin:12px 0 8px}
.final-bar i{display:block;height:100%}
.final-bar .sp{background:linear-gradient(90deg,#e8b94f,var(--gold2))}
.final-bar .ar{background:linear-gradient(90deg,#6b9fe9,#9dc7ff)}
.final-sub{font-size:11.5px;color:var(--dim)}
.final-grid{display:grid;grid-template-columns:1.12fr .88fr;gap:18px;margin-top:18px}
.final-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin-bottom:20px}
.final-kpi{padding:4px 14px 13px;text-align:center;border-right:1px solid var(--line)}
.final-kpi:last-child{border-right:0}
.final-kpi .v{font-family:var(--num);font-size:22px;font-weight:700;color:var(--txt);margin-top:5px}
.final-kpi .l{font-size:10.5px;color:var(--dim);letter-spacing:.08em;text-transform:uppercase}
.wdl-final{height:28px;border-radius:9px;display:flex;overflow:hidden;background:var(--line);font-family:var(--num);font-size:10.5px;font-weight:700}
.wdl-final span{display:flex;align-items:center;justify-content:center;white-space:nowrap;overflow:hidden;transition:width .7s cubic-bezier(.2,.8,.2,1)}
.wdl-final .w1{background:rgba(240,199,94,.78);color:#141008}.wdl-final .dr{background:#52627b;color:#f4f7fb}.wdl-final .w2{background:rgba(109,161,232,.82);color:#08111f}
.wdl-labels{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;color:var(--dim);font-size:11px;margin-top:9px;text-align:center}
.scorelist{display:grid;gap:10px;margin-top:12px}
.scoreline{display:grid;grid-template-columns:52px 1fr 48px;gap:10px;align-items:center;font-family:var(--num);font-size:12px}
.scoreline .sc{font-weight:700;color:var(--txt)}
.scoreline .sb{height:7px;border-radius:99px;background:var(--line);overflow:hidden}.scoreline .sb i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--gold),#ffeaac)}
.scoreline .sv{text-align:right;color:var(--dim)}
.final-teams{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
.team-detail{padding:20px}
.team-detail .tdh{display:flex;align-items:center;gap:10px;font-size:19px;font-weight:800}
.team-detail .tdh img{width:34px;height:23px;border-radius:4px;object-fit:cover}
.team-record{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:17px 0}
.team-record div{text-align:center}.team-record b{display:block;font-family:var(--num);font-size:20px}.team-record span{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em}
.route{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}
.route span{font-family:var(--num);font-size:10.5px;border:1px solid var(--line);border-radius:7px;padding:5px 8px;color:var(--dim)}
.route b{color:var(--txt)}
.final-tactics{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:18px}
.tactic{padding:20px}.tactic h3{font-size:16px;margin:0 0 9px}.tactic p{font-size:12.5px;color:var(--dim);margin:0;line-height:1.75}
.final-lineups{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
.lineup{padding:20px}.lineup h3{font-size:16px;margin:0 0 11px}.lineup .xi{font-size:12.5px;line-height:1.8;color:var(--txt);margin:0}.lineup .stars{font-size:11.5px;line-height:1.7;color:var(--dim);margin:12px 0 0;padding-top:11px;border-top:1px solid var(--line)}
.scenario-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}
.scenario{padding:15px 16px;border-top:1px solid var(--line);background:linear-gradient(180deg,rgba(20,31,53,.45),transparent)}
.scenario b{display:block;font-size:13px;color:var(--gold2);margin-bottom:6px}.scenario span{font-size:11.5px;color:var(--dim);line-height:1.65}
.final-verdict{margin-top:18px;padding:22px 24px;border-left:3px solid var(--gold)}
.final-verdict h3{font-size:17px;margin:0 0 7px}.final-verdict p{margin:0;color:var(--dim);font-size:13px;line-height:1.75}
.final-sources{font-size:10.5px;color:var(--dim);line-height:1.65;margin-top:12px}.final-sources a{color:var(--dim);text-decoration-color:var(--line)}.final-sources a:hover{color:var(--gold2)}
@media(max-width:900px){.final-matchline{grid-template-columns:1fr 1fr;gap:20px}.final-mid{grid-column:1/-1;grid-row:1}.final-team{grid-row:2}.final-grid,.final-tactics{grid-template-columns:1fr}.final-teams,.final-lineups{grid-template-columns:1fr}.scenario-grid{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.final-board{padding:18px 14px}.final-matchline{grid-template-columns:1fr 1fr;gap:10px}.final-team img{width:70px;height:47px}.final-team .fn{font-size:19px}.final-probs b{font-size:31px}.final-kpis{grid-template-columns:1fr}.final-kpi{border-right:0;border-bottom:1px solid var(--line);padding:9px}.final-kpi:last-child{border-bottom:0}.team-record{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.scenario-grid{grid-template-columns:1fr}}

/* penalty shootout comparison — historical evidence, not a model input */
.pen-verdict{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(280px,.95fr);gap:26px;align-items:center;padding:24px}
.pen-call .pen-tag{display:inline-block;font-family:var(--num);font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:var(--gold);border:1px solid rgba(240,199,94,.35);border-radius:99px;padding:4px 9px;margin-bottom:10px}
.pen-call h3{font-size:clamp(20px,2.4vw,29px);margin:0 0 7px}.pen-call p{font-size:12.5px;color:var(--dim);line-height:1.75;margin:0}
.pen-model{min-width:0}.pen-model-title{font-size:11px;color:var(--dim);margin-bottom:7px}.pen-model-values{display:flex;justify-content:space-between;gap:12px;font-family:var(--num);font-size:12px}.pen-model-values b{color:var(--txt);font-size:16px}
.pen-modelbar{height:10px;border-radius:99px;overflow:hidden;display:flex;background:var(--line);margin:8px 0}.pen-modelbar i{display:block;height:100%}.pen-modelbar .sp{background:linear-gradient(90deg,#e8b94f,var(--gold2))}.pen-modelbar .ar{background:linear-gradient(90deg,#6b9fe9,#9dc7ff)}
.pen-model-note{font-size:10.5px;color:var(--dim);line-height:1.55}
.pen-metrics{padding:22px 24px;margin-top:18px}.pen-teamheads{display:grid;grid-template-columns:1fr minmax(130px,.7fr) 1fr;gap:14px;align-items:center;margin-bottom:8px}.pen-teamhead{display:flex;align-items:center;gap:9px;font-weight:800}.pen-teamhead.right{justify-content:flex-end;text-align:right}.pen-teamhead img{width:38px;height:26px;border-radius:4px;object-fit:cover}.pen-teamheads .mid{text-align:center;font-family:var(--num);font-size:10px;color:var(--dim);letter-spacing:.12em;text-transform:uppercase}
.pen-mrow{display:grid;grid-template-columns:64px minmax(90px,1fr) minmax(150px,.72fr) minmax(90px,1fr) 64px;gap:10px;align-items:center;padding:13px 0;border-top:1px solid var(--line)}
.pen-mval{font-family:var(--num);font-weight:700;font-size:14px}.pen-mval.right{text-align:right}.pen-track{height:8px;border-radius:99px;background:#131d33;overflow:hidden}.pen-track i{display:block;height:100%;width:0;border-radius:inherit;transition:width .8s cubic-bezier(.2,.8,.2,1)}.pen-track.left i{margin-left:auto;background:linear-gradient(90deg,var(--golddeep),var(--gold2))}.pen-track.right i{background:linear-gradient(90deg,#4f78b6,#9dc7ff)}
.pen-mlabel{text-align:center;min-width:0}.pen-mlabel b{display:block;font-size:11.5px}.pen-mlabel span{display:block;font-family:var(--num);font-size:9.5px;color:var(--dim);margin-top:2px}
.pen-dual{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.pen-card{padding:20px;min-width:0}.pen-card h3{display:flex;align-items:center;gap:9px;font-size:16px;margin:0 0 4px}.pen-card h3 img{width:32px;height:22px;border-radius:4px;object-fit:cover}.pen-card .pen-sub{font-size:10.5px;color:var(--dim);margin-bottom:14px}.pen-big{display:flex;align-items:baseline;gap:9px;margin-bottom:8px}.pen-big b{font-family:var(--num);font-size:29px}.pen-big span{font-size:11px;color:var(--dim)}
.pen-kickbar{height:12px;border-radius:99px;overflow:hidden;display:flex;background:var(--line)}.pen-kickbar i{height:100%;display:block}.pen-kickbar .save{background:var(--pos)}.pen-kickbar .off{background:var(--gold)}.pen-kickbar .goal{background:#52627b}.pen-legend{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 14px;font-size:10px;color:var(--dim)}.pen-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px}.pen-dot.save{background:var(--pos)}.pen-dot.off{background:var(--gold)}.pen-dot.goal{background:#52627b}
.pen-history{display:grid;gap:6px}.pen-hrow{display:grid;grid-template-columns:54px 1fr auto auto;gap:8px;align-items:center;font-size:10.5px;color:var(--dim);padding-top:6px;border-top:1px solid rgba(27,39,64,.55)}.pen-hrow:first-child{border-top:0}.pen-hrow .opp{color:var(--txt);min-width:0}.pen-hrow .opp small{display:block;color:var(--dim);font-size:9px;line-height:1.25}.pen-hrow .res{font-family:var(--num);font-weight:700}.pen-hrow .res.win{color:var(--pos)}.pen-hrow .res.loss{color:var(--neg)}
.pen-takers{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}.pen-takers span{font-size:10.5px;border:1px solid var(--line);border-radius:99px;padding:5px 9px;color:var(--txt)}.pen-note{font-size:11.5px;line-height:1.7;color:var(--dim);margin:0}
.pen-taker-rate{display:flex;align-items:baseline;gap:9px;margin:3px 0 13px}.pen-taker-rate b{font-family:var(--num);font-size:20px}.pen-taker-rate span{font-size:10.5px;color:var(--dim)}
.pen-caveat{margin-top:18px;padding:20px 22px;border-left:3px solid #6b9fe9}.pen-caveat h3{font-size:15px;margin:0 0 6px}.pen-caveat p{font-size:11.5px;color:var(--dim);line-height:1.7;margin:0}.pen-sources{font-size:10.5px;color:var(--dim);line-height:1.65;margin-top:11px}.pen-sources a{color:var(--dim);text-decoration-color:var(--line)}.pen-sources a:hover{color:var(--gold2)}
@media(max-width:900px){.pen-verdict{grid-template-columns:1fr}.pen-dual{grid-template-columns:1fr}}
@media(max-width:560px){.pen-verdict,.pen-metrics,.pen-card{padding:18px 14px}.pen-teamheads{grid-template-columns:1fr 1fr}.pen-teamheads .mid{display:none}.pen-mrow{grid-template-columns:50px 1fr 1fr 50px;gap:8px}.pen-mlabel{grid-column:1/-1;grid-row:1}.pen-mval.left{grid-column:1;grid-row:2}.pen-track.left{grid-column:2;grid-row:2}.pen-track.right{grid-column:3;grid-row:2}.pen-mval.right{grid-column:4;grid-row:2}.pen-hrow{grid-template-columns:45px minmax(0,1fr) auto auto;gap:6px}}

.grid2{display:grid;grid-template-columns:1.08fr .92fr;gap:18px}
@media(max-width:1020px){.grid2{grid-template-columns:1fr}}
.chart{width:100%;height:460px}

/* bench comparison rows */
.brow{display:grid;grid-template-columns:150px 1fr;gap:12px;align-items:center;
  padding:11px 12px;border-bottom:1px solid rgba(27,39,64,.5)}
.brow.mine{border-left:3px solid var(--gold);background:rgba(56,42,12,.14);border-radius:8px}
.brow .bsrc{font-size:12px;color:var(--dim);line-height:1.4}
.brow.mine .bsrc{color:var(--gold2)}
.bb{display:grid;grid-template-columns:44px 1fr 52px;gap:8px;align-items:center;margin:3px 0}
.bb .bl{font-size:11px;color:var(--dim);text-align:right}
.bb .tr{height:9px;border-radius:5px;background:#131d33;overflow:hidden}
.bb .tr i{display:block;height:100%;border-radius:5px;width:0;transition:width .9s cubic-bezier(.2,.8,.2,1)}
.bb.es .tr i{background:linear-gradient(90deg,#b8862e,#ffe9a8)}
.bb.de .tr i{background:linear-gradient(90deg,#33415e,#7a93bd)}
.bb.de.hot .tr i{background:linear-gradient(90deg,#8a3548,#ff8c9e)}
.bb b{font-family:var(--num);font-size:12.5px;font-weight:600;color:var(--txt)}

/* groups */
.groups{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}
.gcard{position:relative;border:1px solid var(--line);border-radius:16px;padding:16px;overflow:hidden;
  background:linear-gradient(180deg,var(--card),var(--card2));transition:transform .3s,border-color .3s}
.gcard:hover{transform:translateY(-5px);border-color:rgba(240,199,94,.35)}
.gcard .glabel{position:absolute;right:8px;top:-14px;font-family:var(--num);font-weight:700;
  font-size:74px;color:rgba(118,135,159,.07);pointer-events:none}
.gcard h4{font-size:12px;letter-spacing:.24em;color:var(--gold);margin-bottom:13px;font-family:var(--num)}
.gteam{display:grid;grid-template-columns:34px 1fr 56px;gap:10px;align-items:center;margin:9px 0;font-size:13px}
.gteam img{width:32px;height:23px;border-radius:4px;object-fit:cover;box-shadow:0 2px 8px rgba(0,0,0,.45)}
.gteam .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.gteam .host{color:var(--gold);font-size:10px;margin-left:4px}
.gteam .pq{text-align:right;font-family:var(--num);font-weight:600;font-size:12.5px;color:var(--dim)}
.gteam .bar{grid-column:1/-1;height:3px;border-radius:2px;background:#131d33;overflow:hidden;margin-top:-3px}
.gteam .bar i{display:block;height:100%;background:linear-gradient(90deg,#2c4f96,#6fa3ff);
  width:0;transition:width 1s cubic-bezier(.2,.8,.2,1)}

/* matches */
.mtools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
select{background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:7px 12px;font-size:13px}
#matchList{max-width:1080px;margin:0 auto}
.match{display:grid;grid-template-columns:62px minmax(150px,1fr) 200px minmax(150px,1fr) auto;
  gap:12px;align-items:center;padding:11px 14px;border-bottom:1px solid rgba(27,39,64,.55);transition:background .2s}
.match:hover{background:rgba(79,141,255,.05)}
.mno{color:var(--dim);font-size:11px;font-family:var(--num)}
.mt{display:flex;align-items:center;gap:10px;font-weight:650;min-width:0}
.mt span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.mt img{width:34px;height:24px;border-radius:4px;object-fit:cover;box-shadow:0 2px 8px rgba(0,0,0,.5);flex:none}
.mt .xg{display:block;color:var(--dim);font-size:10.5px;font-weight:400;font-family:var(--num)}
.mt.r{justify-content:flex-end;text-align:right}
.wdl{height:24px;border-radius:7px;overflow:hidden;display:flex;font-size:10.5px;color:#071019;
  font-weight:700;font-family:var(--num);transform:scaleX(0);transform-origin:left center;
  transition:transform .7s cubic-bezier(.2,.8,.2,1)}
body.m-seen .wdl{transform:none}
.wdl i{display:flex;align-items:center;justify-content:center;overflow:hidden;min-width:0}
.wdl .w{background:linear-gradient(180deg,#7fb6ff,#4f8dff)}
.wdl .d{background:#54657f;color:#dfe8f5}
.wdl .l{background:linear-gradient(180deg,#ffd897,#dfa54e)}
.mact{text-align:right}
.chip{display:inline-block;padding:2px 10px;border-radius:8px;font-size:12.5px;border:1px solid var(--line);font-family:var(--num)}
.chip.res{background:rgba(58,214,168,.12);border-color:rgba(58,214,168,.45);color:var(--pos);font-weight:700}
.chip.todo{color:var(--dim);font-size:11px}
.chip.brier{color:var(--dim);font-size:10.5px;margin-left:5px}
@media(max-width:560px){
  .match{grid-template-columns:1fr auto 1fr;grid-template-areas:"t1 act t2" "bar bar bar";row-gap:7px}
  .match .mno,.mt .xg{display:none}
  .mt.r{grid-area:t1}.mt:not(.r){grid-area:t2}
  .mact{grid-area:act;text-align:center}
  .wdl{grid-area:bar}
  .mt img{width:26px;height:19px}
}

/* table */
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid rgba(27,39,64,.5);white-space:nowrap;font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left}
th{color:var(--dim);font-weight:500;cursor:pointer;user-select:none;position:sticky;top:0;background:#0d1424;z-index:2;font-size:12px}
th:hover{color:var(--gold)}
tr:hover td{background:rgba(79,141,255,.05)}
td .tf{display:inline-flex;align-items:center;gap:9px;font-weight:600}
td .tf img{width:28px;height:20px;border-radius:3px;object-fit:cover}
.num{font-family:var(--num)}
.goldc{color:var(--gold2)} .pos{color:var(--pos)} .neg{color:var(--neg)}
td.dim{color:var(--dim)}
.tablebox{max-height:600px;overflow:auto;border-radius:12px}

/* collapsible completed-stage panels */
details.fold{margin-top:6px;border:1px solid var(--line);border-radius:14px;
  background:linear-gradient(180deg,rgba(13,20,36,.5),rgba(10,17,31,.5));overflow:hidden}
details.fold>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:12px;
  padding:15px 18px;color:var(--dim);font-size:13.5px;transition:background .2s,color .2s}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary:hover{background:rgba(79,141,255,.06);color:var(--txt)}
details.fold .foldtag{font-family:var(--num);font-size:10px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--gold);border:1px solid rgba(240,199,94,.4);border-radius:99px;padding:3px 10px}
details.fold .foldchev{margin-left:auto;color:var(--gold);font-size:11px;transition:transform .25s}
details.fold[open]>summary{border-bottom:1px solid var(--line);color:var(--txt)}
details.fold[open] .foldchev{transform:rotate(180deg)}
details.fold[open] .foldtag{display:none}   /* "complete" tag only while folded shut */
details.fold .f-open{display:none} details.fold[open] .f-closed{display:none}
details.fold[open] .f-open{display:inline}
.foldbody{padding:16px 16px 4px}
.foldbody .sec-d{margin-top:0}
/* knockout-focus: hide pre-tournament / group-stage sections once the KO stage is on */
body.ko .koHidden{display:none !important}
.koOnly{display:none} body.ko .koOnly{display:block}   /* knockout-only sections (e.g. QF alt-data) */
.doneOnly{display:none} body.done .doneOnly{display:block}
body.done .pregameOnly{display:none !important}

/* completed-tournament review */
.outcome-board{padding:28px;display:grid;grid-template-columns:1.25fr .75fr;gap:18px;align-items:stretch}
.outcome-main,.outcome-third{border:1px solid var(--line);border-radius:14px;padding:22px;
  background:linear-gradient(145deg,rgba(240,199,94,.09),rgba(79,141,255,.035))}
.outcome-third{background:rgba(10,16,30,.72)}
.outcome-tag{font-family:var(--num);font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:var(--gold)}
.outcome-row{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;margin-top:15px}
.outcome-team{display:flex;align-items:center;gap:10px;font-weight:800;font-size:clamp(16px,2vw,24px)}
.outcome-team.r{justify-content:flex-end;text-align:right}.outcome-team.r img{order:2}
.outcome-team img{width:44px;height:30px;object-fit:cover;border-radius:5px}
.outcome-score{font-family:var(--num);font-size:clamp(25px,3vw,42px);font-weight:800;color:var(--gold2);white-space:nowrap;text-align:center}
.outcome-note{font-size:11px;color:var(--dim);text-align:center;margin-top:9px}
.review-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:18px}
.review-kpi{padding:18px 12px;text-align:center}.review-kpi .v{font-family:var(--num);font-size:25px;font-weight:800;color:var(--gold2)}
.review-kpi .k{font-size:10.5px;color:var(--dim);letter-spacing:.08em;text-transform:uppercase;margin-top:5px}
.review-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:18px}
.review-card{padding:20px 21px}.review-card .eyebrow{font-family:var(--num);font-size:9.5px;color:var(--gold);letter-spacing:.15em;text-transform:uppercase}
.review-card h3{font-size:16px;margin:8px 0 7px}.review-card p{font-size:12.5px;line-height:1.7;color:var(--dim)}
.review-card b{color:var(--txt)}
.score-review{margin-top:18px;padding:22px}.score-review h3{font-size:16px;margin-bottom:5px}
.score-review>p{font-size:12px;color:var(--dim);line-height:1.65}
.score-pair{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}
.score-box{border:1px solid var(--line);border-radius:12px;padding:16px;background:rgba(5,8,16,.34)}
.score-box .sh{font-family:var(--num);font-size:10px;color:var(--gold);letter-spacing:.12em;text-transform:uppercase}
.score-box .sv{font-family:var(--num);font-size:24px;font-weight:800;margin:8px 0 4px}.score-box .sn{font-size:11.5px;color:var(--dim);line-height:1.55}
.review-limit{margin-top:14px;padding:16px 18px;border-left:3px solid #52627b;font-size:12px;line-height:1.7;color:var(--dim)}
@media(max-width:900px){.outcome-board,.review-grid,.score-pair{grid-template-columns:1fr}.review-kpis{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.outcome-board{padding:15px}.outcome-main,.outcome-third{padding:15px 11px}.outcome-row{gap:7px}.outcome-team{font-size:13px;gap:5px}.outcome-team img{width:30px;height:20px}.review-kpis{grid-template-columns:1fr 1fr}.review-grid{grid-template-columns:1fr}}

/* alt-data arena (fun sidebar) + match-city map */
.altbadge{align-self:center;font-family:var(--num);font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:#8fa6c4;border:1px solid var(--line);border-radius:99px;padding:4px 11px;background:rgba(13,20,36,.6)}
#altMap{width:100%;height:clamp(300px,44vw,500px)}
.altmap-cap{color:var(--dim);font-size:12px;text-align:center;margin-top:8px}
.altcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px;margin-top:22px}
.altcard{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);border-radius:16px;padding:15px 18px}
.altcard .ah{display:flex;align-items:center;justify-content:space-between;gap:8px;font-weight:700;font-size:14.5px;margin-bottom:6px}
.altcard .ah .side{display:flex;align-items:center;gap:8px}
.altcard .ah .side.r{flex-direction:row-reverse}
.altcard .ah img{width:26px;height:18px;border-radius:3px;object-fit:cover}
.altcard .avenue{text-align:center;font-size:11px;color:var(--dim);margin-bottom:4px}
.altcard .altgh{font-family:var(--num);font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);opacity:.82;margin:13px 0 1px}
.altcard .altgrp:first-of-type .altgh{margin-top:6px}
.altfx{margin-top:15px;padding-top:13px;border-top:1px solid var(--line)}
.altfxh{font-family:var(--num);font-size:11px;letter-spacing:.1em;color:var(--gold)}
.altfxh .fxsub{font-size:9px;color:var(--dim);letter-spacing:.1em;margin-left:6px;text-transform:uppercase}
.fxblock{margin-top:9px}
.fxlab{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);margin-bottom:3px}
.fxline{display:flex;align-items:center;gap:8px;font-size:12.5px;color:#cdd8ea;margin:3px 0;line-height:1.45}
.fxline img{width:20px;height:14px;border-radius:2px;object-fit:cover;flex:none}
.fxline b{color:#eef3fb;font-weight:700}
.fxkey{margin-top:11px;font-size:12.5px;line-height:1.65;color:var(--txt)}
.fxkey .fxkk{display:inline-block;font-family:var(--num);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--gold);border:1px solid rgba(240,199,94,.35);border-radius:5px;padding:1px 6px;margin-right:7px;vertical-align:middle}
.altrow{display:grid;grid-template-columns:1fr 1.15fr 1fr;align-items:center;gap:6px;padding:8px 0;border-top:1px solid rgba(27,39,64,.55)}
.altrow .l{text-align:right}.altrow .r{text-align:left}
.altrow .val{font-family:var(--num);font-size:14px;color:#cdd8ea}
.altrow .m{text-align:center;font-size:11px;color:var(--dim);letter-spacing:.03em}
.altrow .win{color:var(--gold2);font-weight:800}
.altcard .tally{margin-top:11px;padding-top:10px;border-top:1px solid var(--line);font-size:12.5px;text-align:center;color:var(--txt)}
.altcard .tally b{color:var(--gold2)}

/* current knockout stage marker */
.stagechip{align-self:center;display:inline-flex;align-items:center;gap:7px;
  font-family:var(--num);font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--gold);
  border:1px solid rgba(240,199,94,.42);border-radius:99px;padding:4px 12px;background:rgba(56,42,12,.3)}
.stagechip b{color:#ffe9a8;font-weight:700}
.bk-col.cur>h5{color:var(--gold);text-shadow:0 0 12px rgba(240,199,94,.4)}
.bk-col.cur .bk-match:not(.done){border-color:rgba(240,199,94,.55);
  box-shadow:0 0 0 1px rgba(240,199,94,.14),0 4px 18px rgba(240,199,94,.08)}

/* knockout bracket */
#bracket{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));gap:8px;padding:6px 2px}
#bracket.stack{display:flex;flex-direction:column;gap:18px}
.bk-col{display:flex;flex-direction:column;justify-content:space-around;gap:8px;min-width:0}
.bk-col h5{font-family:var(--num);font-size:10px;letter-spacing:.18em;color:var(--dim);
  text-transform:uppercase;text-align:center;font-weight:600;margin-bottom:2px}
#bracket.stack .bk-col{gap:8px}
#bracket.stack .bk-col h5{text-align:left;font-size:11px;margin:4px 0 6px;letter-spacing:.24em}
.bk-match{position:relative;border:1px solid var(--line);border-radius:10px;background:rgba(13,20,36,.72);
  padding:7px 8px 6px;min-width:0;transition:border-color .25s}
.bk-match:hover{border-color:rgba(240,199,94,.45)}
.bk-match.done{background:rgba(10,16,30,.85)}
.bk-team{display:flex;align-items:center;gap:6px;min-width:0;padding:2px 0;font-size:11.5px;font-weight:600}
.bk-team img{width:20px;height:14px;border-radius:2px;object-fit:cover;flex:none;box-shadow:0 1px 4px rgba(0,0,0,.5)}
.bk-nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1}
.bk-tbd{color:var(--dim);font-family:var(--num);font-weight:600;font-size:10.5px;letter-spacing:.08em}
.bk-val{font-family:var(--num);font-weight:700;font-size:11.5px;color:var(--dim);flex:none;display:flex;align-items:center;gap:4px}
.bk-team.win{color:var(--gold2)} .bk-team.win .bk-val{color:var(--gold)}
.bk-team.out{opacity:.48}
.bk-p{font-style:normal;font-size:8.5px;padding:1px 4px;border-radius:6px;background:rgba(240,199,94,.16);
  color:var(--gold);letter-spacing:.06em}
.bk-bar{height:3px;border-radius:2px;background:#33415e;overflow:hidden;margin:4px 1px 1px}
.bk-bar i{display:block;height:100%;width:0;border-radius:2px;transition:width 1s cubic-bezier(.16,1,.3,1);
  background:linear-gradient(90deg,var(--golddeep),var(--gold2))}
.bk-date{position:absolute;top:-7px;right:8px;font-family:var(--num);font-size:9px;color:var(--dim);
  background:var(--bg2);padding:0 5px;border-radius:6px;letter-spacing:.08em}
.bk-final .bk-match{border-color:rgba(240,199,94,.5);box-shadow:0 0 28px rgba(240,199,94,.07)}
.bk-third{margin-top:10px;opacity:.85}
.bk-third .bk-match{border-style:dashed}
.bk-cup{text-align:center;font-size:20px;margin-bottom:4px;filter:drop-shadow(0 2px 10px rgba(240,199,94,.35))}
@media(max-width:1500px){#bracket:not(.stack) .bk-team .bk-nm{font-size:10.5px}}

footer{position:relative;margin-top:90px;padding:60px 6vw 80px;border-top:1px solid var(--line);
  color:var(--dim);font-size:12.5px;overflow:hidden}
footer b{color:var(--txt)}
footer a{color:var(--gold);text-decoration:none}
.f2026{position:absolute;right:-10px;bottom:-58px;font-family:var(--num);font-weight:700;
  font-size:230px;line-height:1;color:rgba(118,135,159,.05);pointer-events:none}

/* reveal — gated behind html.js so content is never hidden if JS dies */
html.js .reveal{opacity:0;transform:translateY(16px);
  transition:opacity .45s cubic-bezier(.16,1,.3,1),transform .45s cubic-bezier(.16,1,.3,1)}
html.js .reveal.in{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}
  html.js .reveal{opacity:1;transform:none}.wdl{transform:none!important}}
</style>
</head>
<body class="__BODYCLS__">
<script>document.documentElement.classList.add('js')</script>
<div id="grain" aria-hidden="true"></div>
<div class="aurora a1" aria-hidden="true"></div><div class="aurora a2" aria-hidden="true"></div><div class="aurora a3" aria-hidden="true"></div>

<nav>
  <span class="wordmark">🐙 OPENPAUL<span> · 世界杯预测引擎</span></span>
  <span class="badge" id="bSnap"></span>
  <span class="badge live" id="bRes"></span>
  <span class="links">
    <a href="#terrain-sec" class="koHidden" data-i18n="navTerrain">3D 地形</a><a href="#globe-sec" id="navGlobe" class="koHidden" data-i18n="navGlobe">地球</a><a href="#edge-sec" class="koHidden" data-i18n="navEdge">价值</a>
    <a href="#podium-sec" data-i18n="navPodium">最终四强</a><a href="#review-sec" data-i18n="navFinal">预测复盘</a><a href="#bracket-sec" data-i18n="navBracket">对阵</a><a href="#alt-sec" class="pregameOnly" data-i18n="navAlt">另类</a><a href="#table-sec" data-i18n="navTable">数据</a><a href="#matches-sec" class="koHidden" data-i18n="navMatches">赛程</a>
    <a href="#" id="langBtn" style="font-family:var(--num);font-weight:700">EN</a>
    <a id="ghLink" href="__REPO__" target="_blank" rel="noopener noreferrer" style="display:none">GitHub →</a>
  </span>
</nav>

<section id="hero">
  <canvas id="stars" aria-hidden="true"></canvas>
  <div id="paul" role="group" aria-label="OpenPaul">
    <svg id="paulSvg" viewBox="0 80 760 138" role="presentation">
      <defs>
        <clipPath id="pfClip" clipPathUnits="userSpaceOnUse"><rect x="2" y="-44" width="40" height="28" rx="4"/></clipPath>
      </defs>
      <g id="paulGround"></g>
      <ellipse id="paulShadow" cx="380" cy="208" rx="24" ry="4" aria-hidden="true"/>
      <g id="paulOcto" transform="translate(380,206)" aria-hidden="true">
        <g id="paulBodyG">
          <text id="paulBody" x="0" y="-6" text-anchor="middle">🐙</text>
          <path id="paulArm" d="M-22,-26 C-38,-32 -36,-54 -10,-63"/>
          <path id="paulHold" d="M28,-16 C40,-20 38,-34 25,-42"/>
        </g>
        <text id="paulMark" x="6" y="-72" text-anchor="middle">❓</text>
      </g>
      <g id="paulFlags"></g>
    </svg>
  </div>
  <div class="hero-grid">
    <div>
      <div class="overline" data-i18n="overline">终场 · 104 / 104 场完赛 <span class="ol2">· FINAL REVIEW</span></div>
      <h1 class="hero-q" data-i18n="heroQ">西班牙，2026 <em>世界冠军</em></h1>
      <div class="champ-line" id="champLine">
        <img class="champ-flag" id="champFlag" alt="">
        <div class="champ-name" id="champName"></div>
        <div class="champ-pct"><span id="champPct">1–0</span><span id="champUnit" style="font-size:.32em">AET</span></div>
      </div>
      <div class="champ-sub" id="champSub"></div>
      <div class="hero-stats" id="heroStats"></div>
    </div>
    <div class="hero-side" id="heroSide"><h3 data-i18n="contenders">FINAL FOUR · 最终四强</h3></div>
  </div>
  <div class="scroll-cue" aria-hidden="true">SCROLL</div>
</section>

<section class="sec reveal koHidden" id="terrain-sec">
  <div class="ghost" aria-hidden="true">01</div>
  <div class="sec-head"><span class="sec-no">01</span><h2 class="sec-h" data-i18n="s1t">晋级概率地形</h2></div>
  <div class="sec-d" data-i18n="s1d">24 支最强球队 × 6 个晋级阶段的三维概率山脉——最前排的金色山脊就是夺冠之路，身后的蓝色高墙是 32 强的入场概率。</div>
  <div class="panel"><span class="hint" id="terrainHint"><span class="fine" data-i18n="hintDrag">拖拽旋转 · 滚轮缩放</span><span class="coarse" data-i18n="hintTouch">触摸拖拽旋转</span></span><div id="terrain"></div><div id="terrainFall" class="gl-fall"></div></div>
</section>

<section class="sec reveal" id="podium-sec">
  <div class="ghost" aria-hidden="true">02</div>
  <div class="sec-head"><span class="sec-no">02</span><h2 class="sec-h" data-i18n="s2t">最终四强 · 从赛前到终场</h2></div>
  <div class="sec-d" data-i18n="s2d">真实名次与 6 月 11 日开赛前夺冠概率并列展示。结果不会回填或改写任何赛前数字。</div>
  <div class="podium" id="podium"></div>
</section>

<section class="sec reveal doneOnly" id="review-sec">
  <div class="ghost" aria-hidden="true">03</div>
  <div class="sec-head"><span class="sec-no">03</span><h2 class="sec-h" data-i18n="reviewT">104 场之后 · 预测复盘</h2></div>
  <div class="sec-d" data-i18n="reviewD">冠军命中了，但完整计分比一个结果更重要。以下只比较同口径、同样本的赛前概率。</div>
  <div class="panel outcome-board" id="outcomeBoard"></div>
  <div class="review-kpis" id="reviewKpis"></div>
  <div class="review-grid" id="reviewCards"></div>
  <div class="panel score-review" id="reviewScores"></div>
  <div class="panel review-limit" data-i18n="reviewLimit"></div>
</section>

<section class="sec reveal pregameOnly" id="final-analysis-sec">
  <div class="ghost" aria-hidden="true">03</div>
  <div class="sec-head"><span class="sec-no">03</span><h2 class="sec-h" data-i18n="sFt">冠军战 · 西班牙 vs 阿根廷</h2></div>
  <div class="sec-d" data-i18n="sFd">把同一场决赛拆成四层：单场夺冠概率、90 分钟结果、比分分布与比赛计划。数值来自滚动 Elo + Dixon-Coles；战术段落为编辑判断，不进入模型。</div>
  <div class="panel final-board" id="finalBoard"></div>
  <div class="final-grid">
    <div class="panel" style="padding:22px">
      <div class="sec-no" data-i18n="fa90T">90 分钟结果 · 纯模型</div>
      <div class="final-kpis" id="finalKpis"></div>
      <div class="wdl-final" aria-label="90 minute result probabilities"><span class="w1" id="faW1"></span><span class="dr" id="faDr"></span><span class="w2" id="faW2"></span></div>
      <div class="wdl-labels" id="faWdlLabels"></div>
    </div>
    <div class="panel" style="padding:22px">
      <div class="sec-no" data-i18n="faScoreT">最可能比分 · 90 分钟</div>
      <div class="scorelist" id="scoreList"></div>
    </div>
  </div>
  <div class="final-teams" id="finalTeams"></div>
  <div class="final-tactics">
    <article class="panel tactic"><h3 data-i18n="faSpainT">西班牙的比赛计划</h3><p data-i18n="faSpainD"></p></article>
    <article class="panel tactic"><h3 data-i18n="faArgentinaT">阿根廷的比赛计划</h3><p data-i18n="faArgentinaD"></p></article>
    <article class="panel tactic"><h3 data-i18n="faSwingT">三条胜负分界线</h3><p data-i18n="faSwingD"></p></article>
  </div>
  <div class="final-lineups">
    <article class="panel lineup"><h3 data-i18n="faSpainXiT">西班牙预计首发 · 4-2-3-1</h3><p class="xi" data-i18n="faSpainXi"></p><p class="stars" data-i18n="faSpainStars"></p></article>
    <article class="panel lineup"><h3 data-i18n="faArgentinaXiT">阿根廷预计首发 · 4-4-2</h3><p class="xi" data-i18n="faArgentinaXi"></p><p class="stars" data-i18n="faArgentinaStars"></p></article>
  </div>
  <div class="scenario-grid">
    <article class="scenario"><b data-i18n="faSc1T">西班牙先得分</b><span data-i18n="faSc1D"></span></article>
    <article class="scenario"><b data-i18n="faSc2T">阿根廷先得分</b><span data-i18n="faSc2D"></span></article>
    <article class="scenario"><b data-i18n="faSc3T">60′ 仍是 0–0</b><span data-i18n="faSc3D"></span></article>
    <article class="scenario"><b data-i18n="faSc4T">90′ 打平</b><span data-i18n="faSc4D"></span></article>
  </div>
  <div class="panel final-verdict"><h3 data-i18n="faVerdictT">结论</h3><p data-i18n="faVerdictD"></p></div>
  <div class="final-sources" data-i18n="faSources"></div>
</section>

<section class="sec reveal pregameOnly" id="penalty-sec">
  <div class="ghost" aria-hidden="true">04</div>
  <div class="sec-head"><span class="sec-no">04</span><h2 class="sec-h" data-i18n="sPt">点球大战 · 谁更值得信任</h2></div>
  <div class="sec-d" data-i18n="sPd">历史战绩、当前门将与潜在主罚手分层比较。所有点球数据均为背景证据，不改写主模型概率。</div>
  <div class="panel pen-verdict" id="penVerdict"></div>
  <div class="panel pen-metrics" id="penMetrics"></div>
  <div class="pen-dual" id="penKeepers"></div>
  <div class="pen-dual" id="penRecent"></div>
  <div class="pen-dual" id="penTakers"></div>
  <div class="panel pen-caveat"><h3 data-i18n="pkCaveatT">读法与限制</h3><p data-i18n="pkCaveatD"></p></div>
  <div class="pen-sources" id="penSources"></div>
</section>

<section class="sec reveal koHidden" id="globe-sec">
  <div class="ghost" aria-hidden="true">03</div>
  <div class="sec-head"><span class="sec-no">03</span><h2 class="sec-h" data-i18n="s3t">夺冠概率 · 全球版图</h2></div>
  <div class="sec-d" data-i18n="s3d">48 支参赛队的夺冠概率立柱，矗立在各自国土之上（柱高 ∝ √概率，颜色 ∝ 概率）。伊比利亚半岛上那道金光，就是本届最高的山。</div>
  <div class="panel" id="globePanel"><span class="hint"><span class="fine" data-i18n="hintGlobe">拖拽旋转 · 自动巡航</span><span class="coarse" data-i18n="hintTouch2">触摸拖拽旋转</span></span><div id="globe"></div></div>
</section>

<section class="sec reveal koHidden" id="edge-sec">
  <div class="ghost" aria-hidden="true">04</div>
  <div class="sec-head"><span class="sec-no">04</span><h2 class="sec-h" data-i18n="s4t">市场低估了谁</h2></div>
  <div class="sec-d" data-i18n="s4d">模型概率 − 锐利盘隐含概率（Pinnacle/Betfair/Polymarket/Kalshi 共识，overround 6.1%，幂法去水）。金色 = 被低估，灰蓝 = 被高估；图示正/负边际前 7/6 名，全量 48 队见 §07 表格。</div>
  <div class="grid2">
    <div class="panel"><div id="chEdge" class="chart"></div></div>
    <div class="panel" style="padding:20px 18px">
      <div class="sec-no" style="margin-bottom:14px" data-i18n="benchTitle">模型阵营对照 · 西班牙 vs 德国夺冠概率</div>
      <div id="bench"></div>
      <div style="color:var(--dim);font-size:11.5px;margin-top:14px" id="benchNote"></div>
    </div>
  </div>
</section>

<section class="sec reveal koHidden" id="groups-sec">
  <div class="ghost" aria-hidden="true">05</div>
  <div class="sec-head"><span class="sec-no">05</span><h2 class="sec-h" data-i18n="s5t">十二宫格 · 小组形势</h2></div>
  <details class="fold" id="groupsFold" __GROUPS_OPEN__>
    <summary><span class="foldtag" data-i18n="foldDone">已收官</span><span class="f-closed" data-i18n="foldGroupsC">展开 12 组最终形势</span><span class="f-open" data-i18n="foldGroupsO">收起小组形势</span><span class="foldchev" aria-hidden="true">▾</span></summary>
    <div class="foldbody">
      <div class="sec-d" data-i18n="s5d">底条 = 小组头名概率 · 右侧百分比 = 晋级 32 强概率 · ★ = 东道主。</div>
      <div class="groups" id="groups"></div>
    </div>
  </details>
</section>

<section class="sec reveal" id="bracket-sec">
  <div class="ghost" aria-hidden="true">06</div>
  <div class="sec-head"><span class="sec-no">06</span><h2 class="sec-h" data-i18n="sKOt">淘汰赛对阵 · 通往决赛之路</h2><span class="stagechip" id="koStage" hidden></span></div>
  <div class="sec-d" data-i18n="sKOd"></div>
  <div class="panel" style="overflow-x:auto"><div id="bracket"></div></div>
</section>

<section class="sec reveal koOnly pregameOnly" id="alt-sec">
  <div class="ghost" aria-hidden="true">07</div>
  <div class="sec-head"><span class="sec-no">07</span><h2 class="sec-h" data-i18n="altT">另类擂台 · 章鱼保罗的野路子</h2><span class="altbadge" data-i18n="altBadge">娱乐向 · 不进模型</span></div>
  <div class="sec-d" data-i18n="altD"></div>
  <div class="panel"><div id="altMap"></div><div class="altmap-cap" data-i18n="altMapCap"></div></div>
  <div id="altCards" class="altcards"></div>
</section>

<section class="sec reveal koHidden" id="matches-sec">
  <div class="ghost" aria-hidden="true">07</div>
  <div class="sec-head"><span class="sec-no">07</span><h2 class="sec-h" data-i18n="s6t">逐场预测与结果 · 小组赛 72 场</h2></div>
  <details class="fold" id="matchesFold" __MATCHES_OPEN__>
    <summary><span class="foldtag" data-i18n="foldDone">已收官</span><span class="f-closed" data-i18n="foldMatchesC">展开 72 场逐场预测与结果</span><span class="f-open" data-i18n="foldMatchesO">收起逐场预测</span><span class="foldchev" aria-hidden="true">▾</span></summary>
    <div class="foldbody">
      <div class="sec-d" data-i18n="s6d">概率条为开球前存证预测（git + RFC3161 锚定，赛后不改）：蓝 = 左队胜，灰 = 平，金 = 右队胜。绿色为真实比分，B 为该场模型 Brier 分数（越低越好）。淘汰赛对阵确定后另行存证。</div>
      <div class="panel">
        <div class="mtools">
          <select id="fDate" aria-label="filter by date"><option value="" data-i18n="optAllDates">全部日期</option></select>
          <select id="fGroup" aria-label="filter by group"><option value="" data-i18n="optAllGroups">全部小组</option></select>
          <select id="fState" aria-label="filter by state"><option value="" data-i18n="optAllStates">全部状态</option>
            <option value="done" data-i18n="optDone">已完赛</option><option value="todo" data-i18n="optTodo">未开赛</option></select>
          <span class="badge" id="scoreSummary" style="margin-left:auto"></span>
        </div>
        <div id="matchList"></div>
      </div>
    </div>
  </details>
</section>

<section class="sec reveal" id="table-sec">
  <div class="ghost" aria-hidden="true">08</div>
  <div class="sec-head"><span class="sec-no">08</span><h2 class="sec-h" data-i18n="s7t">48 队全量数据</h2></div>
  <div class="sec-d" data-i18n="s7d">点击表头排序。边际与 EV 以锐利盘共识为基准。</div>
  <div class="panel tablebox"><table id="fullTable"></table></div>
</section>

<footer>
  <div class="f2026" aria-hidden="true">2026</div>
  <span data-i18n="foot1"><b>方法链</b> · eloratings.net 评级 → 49,400 场历史重放重算逐场 Elo（vs 官方 corr 0.986）→ Dixon-Coles 在 8,103 场上 MLE 拟合（其中 1,309 场样本外验证，logloss 0.8325）→ σ=75 实力扰动（2018/2022 两届回测选定）→ 100,000 次全赛事蒙特卡洛（2026 新版规则完整实现）→ 锐利盘市场对照</span><br><br>
  <span data-i18n="foot2"><b>公开核验</b> · 全部 72 场小组赛逐场预测于揭幕战开球前 git 提交，提交哈希经 RFC3161 可信时间戳锚定（freetsa.org）；已完赛场次锁定真实结果条件重模拟，逐场 Brier 公开计分</span> · <span id="fSnap"></span><span id="fRepo"></span><br><br>
  <span data-i18n="foot3">本页面为静态数据快照，方法论演示，<b>非投注建议</b>。</span><br><br>
  <span data-i18n="brand"></span>
</footer>

<script src="vendor/echarts.min.js"></script>
<script>
const D = __DATA__;
const FLAG = __FLAGS__;
const REPO = "__REPO__";
const EARTH='data:image/jpeg;base64,__EARTH__';
const USGEO=__USGEO__;
const GEO={Mexico:[-99.1,19.4],'South Africa':[28.2,-25.7],'South Korea':[127.0,37.6],Czechia:[14.4,50.1],
Canada:[-75.7,45.4],'Bosnia and Herzegovina':[18.4,43.9],Qatar:[51.5,25.3],Switzerland:[7.4,46.9],
Brazil:[-47.9,-15.8],Morocco:[-6.8,34.0],Haiti:[-72.3,18.5],Scotland:[-3.2,55.9],
'United States':[-77.0,38.9],Paraguay:[-57.6,-25.3],Australia:[149.1,-35.3],Turkey:[32.9,39.9],
Germany:[13.4,52.5],Ecuador:[-78.5,-0.2],'Ivory Coast':[-4.0,5.3],'Curaçao':[-68.9,12.1],
Netherlands:[4.9,52.4],Japan:[139.7,35.7],Tunisia:[10.2,36.8],Sweden:[18.1,59.3],Belgium:[4.4,50.8],
Egypt:[31.2,30.0],Iran:[51.4,35.7],'New Zealand':[174.8,-41.3],Spain:[-3.7,40.4],Uruguay:[-56.2,-34.9],
'Saudi Arabia':[46.7,24.7],'Cape Verde':[-23.5,14.9],France:[2.4,48.9],Senegal:[-17.4,14.7],
Norway:[10.8,59.9],Iraq:[44.4,33.3],Argentina:[-58.4,-34.6],Algeria:[3.1,36.8],Austria:[16.4,48.2],
Jordan:[35.9,31.9],Portugal:[-9.1,38.7],Colombia:[-74.1,4.7],Uzbekistan:[69.2,41.3],
'DR Congo':[15.3,-4.3],England:[-0.1,51.5],Croatia:[16.0,45.8],Ghana:[-0.2,5.6],Panama:[-79.5,9.0]};
const ZH={Spain:'西班牙',Argentina:'阿根廷',France:'法国',England:'英格兰',Brazil:'巴西',Portugal:'葡萄牙',
Colombia:'哥伦比亚',Netherlands:'荷兰',Ecuador:'厄瓜多尔',Germany:'德国',Turkey:'土耳其',Norway:'挪威',
Japan:'日本',Switzerland:'瑞士',Belgium:'比利时',Croatia:'克罗地亚',Mexico:'墨西哥',Uruguay:'乌拉圭',
Morocco:'摩洛哥','United States':'美国',Senegal:'塞内加尔',Canada:'加拿大','South Korea':'韩国',
Czechia:'捷克',Austria:'奥地利',Denmark:'丹麦',Sweden:'瑞典',Paraguay:'巴拉圭',Australia:'澳大利亚',
'Ivory Coast':'科特迪瓦',Egypt:'埃及',Iran:'伊朗','New Zealand':'新西兰','Saudi Arabia':'沙特',
'Cape Verde':'佛得角',Algeria:'阿尔及利亚',Jordan:'约旦',Uzbekistan:'乌兹别克斯坦','DR Congo':'刚果(金)',
Ghana:'加纳',Panama:'巴拿马',Tunisia:'突尼斯',Scotland:'苏格兰',Haiti:'海地',Qatar:'卡塔尔',
'South Africa':'南非',Iraq:'伊拉克','Bosnia and Herzegovina':'波黑','Curaçao':'库拉索',
Italy:'意大利',Russia:'俄罗斯',Chile:'智利',Yugoslavia:'南斯拉夫','Republic of Ireland':'爱尔兰'};
const I18N={zh:{
  navTerrain:'3D 地形',navGlobe:'地球',navEdge:'价值',navBracket:'完整对阵',navMatches:'赛程',navPodium:'最终四强',navFinal:'预测复盘',navTable:'赛前档案',
  overline:'终场 · 104 / 104 场完赛 <span class="ol2">· FINAL REVIEW</span>',
  heroQ:'西班牙，2026 <em>世界冠军</em>',
  contenders:'FINAL FOUR · 最终四强',
 snapPrefix:'终场快照',playedPrefix:'已完赛',genAt:'快照生成',repoLink:'代码与数据仓库 →',
 subProb:'夺冠概率（σ=75 主模型）· 敏感区间',subFinal:'进决赛',subSF:'进四强',subDirect:'单场纯模型',
  subBlend:'夺冠概率 · 模型×市场融合',subModel:'纯模型',subMarket:'八强盘·决赛条件化',
  thModel:'纯模型',thMktNow:'八强盘·决赛条件化',
 statSimsV:'10万',statSims:'蒙特卡洛模拟',statLL:'样本外 LOGLOSS',statBrier:'逐场 BRIER',statMatches:'比赛',statGoals:'进球',statGroupScore:'小组赛 BRIER',statKoScore:'KO BRIER · 冻结/滚动',
 statWait:'待开赛',statSigma:'回测选定扰动',
 s1t:'晋级概率地形',
 s1d:'24 支最强球队 × 6 个晋级阶段的三维概率山脉——最前排的金色山脊就是夺冠之路，身后的蓝色高墙是 32 强的入场概率。',
  s2t:'最终四强 · 从赛前到终场',s2d:'真实名次与 6 月 11 日开赛前夺冠概率并列展示。结果不会回填或改写任何赛前数字。',
  reviewT:'104 场之后 · 预测复盘',reviewD:'冠军命中了，但完整计分比一个结果更重要。以下只比较同口径、同样本的赛前概率。',
  outcomeFinal:'决赛 · 世界冠军',outcomeThird:'季军赛',afterExtra:'加时',placeChampion:'冠军',placeRunner:'亚军',placeThird:'季军',placeFourth:'第四',preTitleProb:'赛前夺冠概率',preRankLabel:'赛前排名',
  reviewScoreTitle:'公开计分 · 同口径比较',reviewScoreSub:'小组赛是 90 分钟胜/平/负三分类；淘汰赛是晋级方二分类。两种 Brier 都是越低越好，但不能跨口径直接比较。',groupScore:'小组赛 · 三分类',koScore:'淘汰赛 · 二分类',
  reviewLimit:'<b>限制：</b>市场共同样本只有 16 场，淘汰赛双轨共同样本只有 20 场。一届世界杯不足以证明某个更新策略长期更优；临场首发、伤停、红牌与具体战术事件不在模型中，点球大战仍按 50/50 处理。所有赛前概率均原样保留，终场结果不用于回填。',
  sFt:'冠军战 · 西班牙 vs 阿根廷',sFd:'把同一场决赛拆成四层：单场夺冠概率、90 分钟结果、比分分布与比赛计划。数值来自滚动 Elo + Dixon-Coles；战术与预计首发为编辑判断，不进入模型。',
  sPt:'点球大战 · 谁更值得信任',sPd:'历史战绩、当前门将与潜在主罚手分层比较。所有点球数据均为背景证据，不改写主模型概率。',
  pkEvidenceTag:'历史证据 · 非模型',pkVerdictT:'点球证据明显偏向阿根廷',pkVerdictD:'阿根廷在主要正式赛事 13 胜 6 负、世界杯 6 胜 1 负、2021 年以来 4 胜 0 负；西班牙分别为 7 胜 7 负、1 胜 4 负和 2 胜 3 负。马丁内斯点球大战扑出 8/18，西蒙为 6/24；主罚端阿根廷 71/91，也领先西班牙 48/67。西班牙只在“90 分钟打平后”的整体模型路径略占优势。',
  pkModelT:'90 分钟打平后 · 最终夺冠模型',pkModelNote:'这条路径包含加时赛与默认 50/50 的点球分支，不是点球大战专属预测。',pkCompare:'同口径对比',
  pkOverall:'国家队正式赛事',pkWorldCup:'世界杯',pkRecent:'2021 年以来',pkKeeperRate:'预计首发门将扑救率',pkConversion:'主罚转化率',pkRecentConversion:'球队 2021 年以来点球大战主罚命中率',pkRecord:'胜–负',pkCurrent:'本届点球大战',pkNone:'两队本届均为 0 次',
  pkKeeperT:'门将层',pkKeeperSub:'国家队点球大战',pkFaced:'面对',pkSaved:'扑出',pkOff:'射偏 / 中框',pkGoals:'命中',pkSave:'扑救',pkGoal:'进球',
  pkRecentT:'近期点球大战',pkTakersT:'潜在主罚手池',pkTakersSub:'预计可用人选 · 非官方顺序',pkW:'胜',pkL:'负',pkPens:'点球',
  pkCaveatT:'读法与限制',pkCaveatD:'点球大战是极小样本，高度受主罚顺序、换人、伤停与临场心理影响。门将扑救率以“面对的全部罚球”为分母；对手射偏或击中门框单列，不算门将扑救。历史证据偏阿根廷，不等于一个可精确外推的新概率。',pkAsOf:'数据截至',pkScope:'统计口径',pkSources:'来源',
  faTime:'7月20日 03:00（北京时间）',faPure:'单场纯模型',faBlend:'融合头条',faElo:'滚动 Elo',fa90T:'90 分钟结果 · 纯模型',faScoreT:'最可能比分 · 90 分钟',
  faXg:'模型进球均值',faTopScore:'最高单一比分',faH2H:'历史交锋',faH2HNote:'仅作背景 · 不进模型',faRecord:'赛会战绩',faRoute:'淘汰赛路径',faClean:'零封',faGoals:'进球 / 失球',
  faW1:'西班牙胜',faDraw:'平局',faW2:'阿根廷胜',faWin:'胜',faDrawShort:'平',faLoss:'负',faPlayed:'场',
  faSpainT:'西班牙的比赛计划',faSpainD:'有球时站成 2-3-5：罗德里守住中轴，法比安与奥尔莫占肋部，亚马尔固定右侧制造一对一，波罗在外线提供宽度。真正的保险是丢球后立刻反抢，不能让阿根廷第一脚就穿过中场。',
  faArgentinaT:'阿根廷的比赛计划',faArgentinaD:'无球时保持 4-4-2 中位阵型，塔利亚菲科与左中场共同夹击亚马尔；夺回球后优先找右肋的梅西，再由阿尔瓦雷斯冲击西班牙高位防线身后。若慢下来与西班牙对控，比赛会进入对手最舒服的节奏。',
  faSwingT:'三条胜负分界线',faSwingD:'① 西班牙丢球后的前 6 秒；② 梅西能否在右肋面向球门接球；③ 禁区二点与定位球。模型给 90 分钟平局接近三成，因此体能、换人与点球门将都可能成为最后一层。',
  faSpainXiT:'西班牙预计首发 · 4-2-3-1',faSpainXi:'乌奈·西蒙；佩德罗·波罗、库巴西、拉波尔特、库库雷利亚；罗德里、法比安；亚马尔、奥尔莫、巴埃纳；奥亚萨瓦尔。',faSpainStars:'基于半决赛阵容，并非官方名单。奥亚萨瓦尔本届 5 球；亚马尔无严重伤，波罗仍有轻微肌肉疑问，需赛前确认；佩德里、尼科·威廉斯与费兰·托雷斯是主要变招。',
  faArgentinaXiT:'阿根廷预计首发 · 4-4-2',faArgentinaXi:'埃米利亚诺·马丁内斯；蒙铁尔/莫利纳、克里斯蒂安·罗梅罗、利桑德罗·马丁内斯、塔利亚菲科；西蒙尼/德保罗、帕雷德斯、麦卡利斯特、恩佐；梅西、阿尔瓦雷斯。',faArgentinaStars:'基于半决赛阵容，并非官方名单。梅西截至决赛前 8 球 4 助攻；劳塔罗是最具威胁的首发替代或后手。罗梅罗、帕雷德斯目前均无确认缺阵或停赛。',
  faSc1T:'西班牙先得分',faSc1D:'阿根廷必须提前投入德保罗或劳塔罗，身后空间增大；比赛更接近 2–0 / 2–1。',
  faSc2T:'阿根廷先得分',faSc2D:'西班牙会抬高两名边后卫并增加佩德里或尼科的控球/纵深；梅西反击空间随之变大。',
  faSc3T:'60′ 仍是 0–0',faSc3D:'西班牙多约一天恢复且本届未踢加时；阿根廷则有更强的淘汰赛韧性与门将心理优势。',
  faSc4T:'90′ 打平',faSc4D:'整体模型路径里西班牙最终夺冠约 51%，几乎五五开；它包含加时与默认 50/50 的点球分支。下一节的历史与人员证据则偏向阿根廷。',
  faVerdictT:'结论',faVerdictD:'这不是一场有明显强弱差的决赛。西班牙拥有更稳定的控制、防线和休息优势；阿根廷拥有更高的进球产量、梅西与逆风翻盘能力。主预测是 90 分钟 1–1；若必须选冠军，纯模型只微倾西班牙。预计首发、临场伤停、红牌与下方点球证据都未进入当前概率，本页为方法演示，非投注建议。',
  faSources:'数据截至 2026-07-16 · 赛程与场馆：<a href="https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/final-live-watch-teams-tickets" target="_blank" rel="noopener noreferrer">FIFA</a> · 赛况与球员数据：<a href="https://www.espn.co.uk/football/story/_/id/49370934/fifa-world-cup-final-argentina-vs-spain-need-know-predictions-odds" target="_blank" rel="noopener noreferrer">ESPN</a> / <a href="https://www.fotmob.com/matches/argentina-vs-spain/1ho257" target="_blank" rel="noopener noreferrer">FotMob</a> · 概率、比分分布与赛会统计：本项目可复现数据链。',
 s3t:'夺冠概率 · 全球版图',
 s3d:'48 支参赛队的夺冠概率立柱，矗立在各自国土之上（柱高 ∝ √概率，颜色 ∝ 概率）。最高的那道金光，就是此刻的头号热门。',
 s4t:'市场低估了谁 · 开赛前存证',
 s4d:'模型概率 − 锐利盘隐含概率（Pinnacle/Betfair/Polymarket/Kalshi 共识，overround 6.1%，幂法去水）。金色 = 被低估，灰蓝 = 被高估；图示正/负边际前 7/6 名，全量 48 队见 §08 表格。本节为 2026-06-11 开球前存证快照——模型与赔率取自同一时点，赛后不改。',
 s5t:'十二宫格 · 小组形势',s5d:'底条 = 小组头名概率 · 右侧百分比 = 晋级 32 强概率 · ★ = 东道主。',
 s6t:'逐场预测与结果 · 小组赛 72 场',
 s6d:'概率条为开球前存证预测（git + RFC3161 锚定，赛后不改）：蓝 = 左队胜，灰 = 平，金 = 右队胜。绿色为真实比分，B 为该场模型 Brier 分数（越低越好）。淘汰赛逐场预测另行存证：§06 对阵树 + predictions/ko_forecasts.csv（逐场于开球前追加入账）。',
  s7t:'48 队 · 赛前预测终场档案',s7d:'回到 6 月 11 日同一赛前窗口：模型夺冠概率、锐利盘隐含概率、价值偏差与最终成绩并列。点击表头排序。',
 sKOt:'淘汰赛对阵 · 完整终场版',
 sKOd:'全部 32 场淘汰赛均已结束。真实比分直接来自赛果库，金色为晋级方，P 表示点球决胜；赛前晋级概率仍保留在公开只增账本中。',
 bkR32:'32强',bkR16:'16强',bkQF:'八强',bkSF:'半决赛',bkF:'决赛',bk3:'季军战',bkPens:'P',
 navAlt:'另类',
 altT:'另类擂台 · 章鱼保罗的野路子',altBadge:'娱乐向 · 不进模型',
  altD:'纯为好玩：把这场决赛按几项<b>跟胜负无关</b>的另类数据比一比，金色 = 该项“赢家”。卡片末尾另附<b>主观战术看点</b>。这些<b>都不进预测模型</b>——保罗当年也就靠触手抓箱子。',
  altMapCap:'决赛赛城（金色） · 灰点为本届已用过的美国赛场',
 altHeight:'平均身高',altAge:'平均年龄',altClimate:'场地热适应',altFlight:'本届飞行',
 altHeightU:'cm',altAgeU:'岁',altFlightU:'km',
 altClimateHint:'场地气温 − 母国 7 月气温,越小越适应',
 altTally:'另类比分',altTie:'另类角度打平 · 保罗挠头',altPick:'另类角度更被看好',
 altVenue:'场地',
 altGCup:'本届赛事',altGSquad:'阵容 · 身体',altGNation:'国家 · 底蕴',altGCond:'客观条件',
 altGF:'本届进球',altGA:'本届失球',altGD:'净胜球',altPK:'点球大战',
 altValue:'阵容身价',altTitles:'历史夺冠',altBest:'历史最佳',altFifa:'FIFA 排名',altPop:'国家人口',altGdp:'人均 GDP',
 altFinC:'冠军',altFin3:'季军',altFinS:'四强',altFinQ:'八强',altFinR:'16强',
 altTactics:'战术看点',altTacSub:'主观 · 非模型',altCoach:'主帅',altStyle:'打法',altKey:'克制点',
 hintDrag:'拖拽旋转 · 滚轮缩放',hintTouch:'触摸拖拽旋转',hintGlobe:'拖拽旋转 · 自动巡航',hintTouch2:'触摸拖拽旋转',
 benchTitle:'模型阵营对照 · 西班牙 vs 德国夺冠概率（开赛前）',
 benchNote:'开赛前所有来源一致指向西班牙。德国行是模型阵营的分水岭：赛果系与市场都在 ~3–5%（本模型 2.9%、Opta 5.1%、市场 5.3%），而含球员身价协变量的模型给到 ~11%（红色条）——这是本届各模型最大的单队分歧。后记（7-02）：德国 32 强赛 1-1 点球不敌巴拉圭出局，赛果系阵营的低估计方向得到验证。',
 srcMine:'本模型（赛果系 · Elo）',srcSharp:'锐利盘市场（去水）',lblSpain:'西班牙',lblGermany:'德国',
 stChampion:'夺冠',stFinal:'决赛',stSF:'四强',stQF:'八强',stR16:'16强',stR32:'32强',prob:'概率',
  pmFinal:'决赛',pmSF:'四强',pmOdds:'赔率',pmRange:'纯模型区间',
 optAllDates:'全部日期',optAllGroups:'全部小组',optAllStates:'全部状态',optDone:'已完赛',optTodo:'未开赛',
 foldDone:'已收官',foldGroupsC:'展开 12 组最终形势',foldGroupsO:'收起小组形势',
 foldMatchesC:'展开 72 场逐场预测与结果',foldMatchesO:'收起逐场预测',koStagePrefix:'当前阶段',
 grp:'组',scoreNone:'首场完赛后开始逐场 Brier 计分',scored:'已计分',matchesUnit:'场',
 modelBrier:'模型 Brier',vsMarket:'vs 市场',matchEmpty:'没有符合筛选的比赛',
 pWinL:'左胜',pDraw:'平',pWinR:'右胜',brierTip:'模型 Brier（市场',
 thTeam:'队伍',thChampion:'夺冠·融合',thPreChampion:'赛前夺冠',thFinish:'最终成绩',thFinal:'决赛',thSF:'四强',thQF:'八强',thR16:'16强',thR32:'32强',
 thOdds:'锐利赔率',thImp:'市场隐含',thEdge:'边际pp',thEV:'EV',
 winProb:'夺冠概率',edgeWord:'边际',vsModel:'模型',vsMkt:'市场',
 foot1:'<b>方法链</b> · eloratings.net 评级 → 49,400 场历史重放重算逐场 Elo（vs 官方 corr 0.986）→ Dixon-Coles 在 8,103 场上 MLE 拟合（其中 1,309 场样本外验证，logloss 0.8325）→ σ=75 实力扰动（2018/2022 两届回测选定）→ <b>赛中评级滚动更新</b>（Round 4：eloratings 规则重放已完赛场次，两届回测淘汰赛 logloss −8%）→ 100,000 次全赛事蒙特卡洛（2026 新版规则完整实现）→ 锐利盘市场对照 → <b>淘汰赛头条与锐利盘市场融合（市场 WMKT）</b>（幂法去水；纯模型全程留档）',
 foot2:'<b>公开核验</b> · 全部 72 场小组赛逐场预测于揭幕战开球前 git 提交，提交哈希经 RFC3161 可信时间戳锚定（freetsa.org）；已完赛场次锁定真实结果条件重模拟，逐场 Brier 公开计分；淘汰赛逐场晋级概率逐场于开球前追加至公开账本（predictions/ko_forecasts.csv + ko_forecasts_r4.csv 双轨，只增不改，冻结/滚动评级头对头计分）',
 foot3:'本页面为静态数据快照，方法论演示，<b>非投注建议</b>。',
 brand:'🐙 <b>OpenPaul</b> — 2010 年章鱼保罗用触手挑选赢家，16 年后我们用 100,000 次蒙特卡洛。预测可以开源，章鱼只负责可爱。',
},en:{
  navTerrain:'3D Terrain',navGlobe:'Globe',navEdge:'Value',navBracket:'Full bracket',navMatches:'Matches',navPodium:'Final four',navFinal:'Forecast review',navTable:'Pre-tournament archive',
  overline:'Full time · 104 / 104 matches complete <span class="ol2">· FINAL REVIEW</span>',
  heroQ:'Spain are 2026 <em>world champions</em>',
  contenders:'FINAL FOUR · FINAL STANDINGS',
 snapPrefix:'Final snapshot',playedPrefix:'Played',genAt:'Snapshot generated',repoLink:'Code & data repository →',
 subProb:'Title probability (σ=75 main model) · sensitivity band',subFinal:'Final',subSF:'Semis',subDirect:'single-match model',
  subBlend:'Title probability · model × market blend',subModel:'model',subMarket:'QF board · final-conditioned',
  thModel:'model',thMktNow:'QF board · final-conditioned',
 statSimsV:'100k',statSims:'MONTE CARLO RUNS',statLL:'OUT-OF-SAMPLE LOGLOSS',statBrier:'PER-MATCH BRIER',statMatches:'MATCHES',statGoals:'GOALS',statGroupScore:'GROUP BRIER',statKoScore:'KO BRIER · FROZEN/ROLLED',
 statWait:'awaiting kickoff',statSigma:'BACKTEST-CHOSEN σ',
 s1t:'Probability Terrain',
 s1d:'A 3-D probability massif: top 24 teams × 6 knockout stages. The golden ridge up front is the road to the title; the blue wall behind is the round-of-32 entry probability.',
  s2t:'The Final Four · Before and After',s2d:'Final standings sit beside each team’s sealed 11 June title probability. Results never backfill or rewrite a pre-match number.',
  reviewT:'After 104 Matches · Forecast Review',reviewD:'The champion call landed, but the complete scorecard matters more than one result. Every comparison below uses the same scoring rule and sample.',
  outcomeFinal:'Final · World champions',outcomeThird:'Third-place match',afterExtra:'after extra time',placeChampion:'Champions',placeRunner:'Runners-up',placeThird:'Third',placeFourth:'Fourth',preTitleProb:'pre-tournament title chance',preRankLabel:'pre-tournament rank',
  reviewScoreTitle:'Public scorecard · like for like',reviewScoreSub:'Group matches use three-way 90-minute W/D/L scoring; knockout matches use binary advancement scoring. Lower is better in both, but the scales are not directly comparable.',groupScore:'Group stage · three-way',koScore:'Knockouts · binary',
  reviewLimit:'<b>Limits:</b> the market comparison covers only 16 group matches and the dual knockout ledgers share only 20 matches. One World Cup cannot establish that an update policy is superior in the long run. Confirmed line-ups, late injuries, red cards and match-specific tactics are outside the model, while shoot-outs remain 50/50. Final results never backfill a pre-match probability.',
  sFt:'The Championship Match · Spain vs Argentina',sFd:'One final, four layers: lift-the-cup probability, the 90-minute result, scoreline distribution and match plans. Numbers use rolling Elo + Dixon-Coles; tactics and predicted XIs are editorial and never enter the model.',
  sPt:'Penalty Shoot-out · Whom Can You Trust?',sPd:'Historical record, current goalkeepers and likely takers compared in separate layers. Every penalty figure is background evidence and does not rewrite the main model.',
  pkEvidenceTag:'historical evidence · not the model',pkVerdictT:'The shoot-out evidence clearly leans Argentina',pkVerdictD:'Argentina are 13–6 in major competitive shoot-outs, 6–1 at World Cups and 4–0 since 2021; Spain are 7–7, 1–4 and 2–3 respectively. Martínez has saved 8/18 shoot-out kicks faced against Simón’s 6/24, while Argentina also lead taker conversion, 71/91 to 48/67. Spain’s only edge is the broader model path after a 90-minute draw.',
  pkModelT:'After a 90-minute draw · title model',pkModelNote:'This path includes extra time and a default 50/50 shoot-out branch; it is not a shoot-out-specific forecast.',pkCompare:'like-for-like comparison',
  pkOverall:'senior competitive record',pkWorldCup:'World Cup',pkRecent:'since 2021',pkKeeperRate:'predicted starting keeper save rate',pkConversion:'taker conversion',pkRecentConversion:'team shoot-out conversion since 2021',pkRecord:'W–L',pkCurrent:'shoot-outs this tournament',pkNone:'both teams: 0',
  pkKeeperT:'Goalkeeper layer',pkKeeperSub:'senior international shoot-outs',pkFaced:'faced',pkSaved:'saved',pkOff:'off target / woodwork',pkGoals:'scored',pkSave:'save',pkGoal:'goal',
  pkRecentT:'Recent shoot-outs',pkTakersT:'Likely taker pool',pkTakersSub:'projected available players · not an official order',pkW:'W',pkL:'L',pkPens:'pens',
  pkCaveatT:'How to read this',pkCaveatD:'Shoot-outs are tiny samples and depend heavily on order, substitutions, fitness and match-night psychology. Goalkeeper save rate uses every kick faced as the denominator; an opponent miss or woodwork is listed separately and never credited as a save. Argentina\'s evidence edge is not a new precisely estimable probability.',pkAsOf:'Data through',pkScope:'Scope',pkSources:'Sources',
  faTime:'20 Jul · 03:00 China Standard Time',faPure:'single-match model',faBlend:'headline blend',faElo:'rolling Elo',fa90T:'90-minute result · model only',faScoreT:'Most likely scores · 90 minutes',
  faXg:'model goal mean',faTopScore:'top exact score',faH2H:'historical H2H',faH2HNote:'context only · excluded from model',faRecord:'tournament record',faRoute:'knockout route',faClean:'clean sheets',faGoals:'goals for / against',
  faW1:'Spain win',faDraw:'Draw',faW2:'Argentina win',faWin:'W',faDrawShort:'D',faLoss:'L',faPlayed:'played',
  faSpainT:'Spain’s match plan',faSpainD:'In possession, form a 2-3-5: Rodri secures the axis, Fabián and Olmo occupy the half-spaces, Yamal holds the right for isolation and Porro supplies width. The insurance policy is the immediate counter-press — Argentina must not be allowed to play the first pass straight through midfield.',
  faArgentinaT:'Argentina’s match plan',faArgentinaD:'Defend in a mid-block 4-4-2, with Tagliafico and the left midfielder doubling Yamal. On regain, find Messi in the right half-space first, then send Álvarez behind Spain’s high line. A slow possession contest would move the game onto Spain’s preferred terms.',
  faSwingT:'Three fault lines',faSwingD:'1) Spain’s first six seconds after losing the ball; 2) whether Messi can receive on the turn in the right half-space; 3) set pieces and second balls. With the 90-minute draw near 30%, fitness, substitutions and the goalkeeper layer may decide it.',
  faSpainXiT:'Spain predicted XI · 4-2-3-1',faSpainXi:'Unai Simón; Pedro Porro, Cubarsí, Laporte, Cucurella; Rodri, Fabián; Yamal, Olmo, Baena; Oyarzabal.',faSpainStars:'Based on the semi-final, not the official team sheet. Oyarzabal has five goals; Yamal has no serious injury, while Porro remains a minor muscular doubt pending the final check. Pedri, Nico Williams and Ferran Torres are the principal variations.',
  faArgentinaXiT:'Argentina predicted XI · 4-4-2',faArgentinaXi:'Emiliano Martínez; Montiel/Molina, Cristian Romero, Lisandro Martínez, Tagliafico; Simeone/De Paul, Paredes, Mac Allister, Enzo; Messi, Álvarez.',faArgentinaStars:'Based on the semi-final, not the official team sheet. Messi enters the final on eight goals and four assists; Lautaro is the biggest starting alternative or attacking substitute. Romero and Paredes have no confirmed absence or suspension.',
  faSc1T:'Spain score first',faSc1D:'Argentina must introduce De Paul or Lautaro earlier and concede more space; the path bends toward 2–0 or 2–1.',
  faSc2T:'Argentina score first',faSc2D:'Spain push both full-backs higher and add Pedri or Nico for control/depth; Messi gains more transition space.',
  faSc3T:'Still 0–0 at 60′',faSc3D:'Spain have roughly one extra recovery day and no extra time in this run; Argentina bring knockout resilience and a stronger goalkeeper narrative.',
  faSc4T:'Level after 90′',faSc4D:'The broader model path gives Spain roughly 51% to lift the trophy — effectively even — and includes extra time plus a default 50/50 shoot-out branch. The historical and personnel evidence in the next section leans Argentina.',
  faVerdictT:'Verdict',faVerdictD:'This final has no clear mismatch. Spain bring the steadier control, defence and recovery edge; Argentina bring greater scoring output, Messi and proven comeback capacity. The main 90-minute pick is 1–1; if forced to name the champion, the model only narrowly leans Spain. Predicted XIs, late fitness news, red cards and the penalty evidence below are outside the current probabilities. Methodology demo, not betting advice.',
  faSources:'Data through 16 Jul 2026 · Schedule and venue: <a href="https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/final-live-watch-teams-tickets" target="_blank" rel="noopener noreferrer">FIFA</a> · Match and player context: <a href="https://www.espn.co.uk/football/story/_/id/49370934/fifa-world-cup-final-argentina-vs-spain-need-know-predictions-odds" target="_blank" rel="noopener noreferrer">ESPN</a> / <a href="https://www.fotmob.com/matches/argentina-vs-spain/1ho257" target="_blank" rel="noopener noreferrer">FotMob</a> · Probabilities, scorelines and tournament records: this project’s reproducible data chain.',
 s3t:'Title Probability · World Map',
 s3d:'Championship-probability pillars rising from each of the 48 homelands (height ∝ √p, color ∝ p). The tallest golden beam marks the current favourite.',
 s4t:'Whom Did the Market Undervalue? · Sealed Pre-Kickoff',
 s4d:'Model probability − sharp-market implied (Pinnacle/Betfair/Polymarket/Kalshi consensus, 6.1% overround, power de-vig). Gold = undervalued, slate = overvalued; chart shows top 7/6 by ± edge — all 48 teams in §08. This section is the 2026-06-11 pre-kickoff sealed snapshot: model and odds from the same window, never edited after kickoff.',
 s5t:'The Twelve Groups',s5d:'Bottom bar = group-winner probability · right % = reach round-of-32 · ★ = host.',
 s6t:'Match-by-Match · 72 Group Games',
 s6d:'Probability bars are pre-kickoff sealed forecasts (git + RFC3161 anchored, never edited): blue = left win, grey = draw, gold = right win. Green chip = real score; B = per-match Brier (lower is better). Knockout forecasts are sealed separately: §06 bracket + predictions/ko_forecasts.csv (each row appended before kickoff).',
  s7t:'All 48 Teams · Final Pre-Tournament Archive',s7d:'Return to the same 11 June window: model title chance, sharp-market implied probability, value edge and final finish together. Click a header to sort.',
 sKOt:'Knockout Bracket · Complete',
 sKOd:'All 32 knockout matches are final. Scores come directly from the results ledger; gold marks the advancing side and P marks a shoot-out. Sealed pre-match advancement probabilities remain in the public append-only ledgers.',
 bkR32:'R32',bkR16:'R16',bkQF:'QF',bkSF:'SF',bkF:'FINAL',bk3:'3rd place',bkPens:'P',
 navAlt:'Fun',
 altT:'The Alt-Data Arena · Paul\'s Wild Guesses',altBadge:'for fun · not in the model',
  altD:'Purely for fun: the final compared on a few <b>result-irrelevant</b> quirky metrics, gold = that metric\'s “winner”, plus a <b>subjective tactical read</b>. None of it <b>touches the prediction model</b> — Paul the octopus just grabbed boxes with his tentacles, after all.',
  altMapCap:'Final host city (gold) · grey dots = US venues used earlier this tournament',
 altHeight:'Avg height',altAge:'Avg age',altClimate:'Heat adaptation',altFlight:'Km flown',
 altHeightU:'cm',altAgeU:'yr',altFlightU:'km',
 altClimateHint:'venue temp − home July temp; smaller = better adapted',
 altTally:'Alt-score',altTie:'a quirky-data tie · Paul scratches his head',altPick:'the quirky-data favourite',
 altVenue:'venue',
 altGCup:'This tournament',altGSquad:'Squad · body',altGNation:'Nation · pedigree',altGCond:'Conditions',
 altGF:'Goals for',altGA:'Goals against',altGD:'Goal diff',altPK:'Shootouts',
 altValue:'Squad value',altTitles:'WC titles',altBest:'Best finish',altFifa:'FIFA rank',altPop:'Population',altGdp:'GDP/capita',
 altFinC:'Champions',altFin3:'3rd',altFinS:'Semis',altFinQ:'QF',altFinR:'R16',
 altTactics:'Tactical read',altTacSub:'subjective · not the model',altCoach:'Coach',altStyle:'Style',altKey:'The key',
 hintDrag:'drag to rotate · scroll to zoom',hintTouch:'touch-drag to rotate',hintGlobe:'drag to rotate · auto-cruise',hintTouch2:'touch-drag to rotate',
 benchTitle:'Model Camps · Spain vs Germany title odds (pre-kickoff)',
 benchNote:'Before kickoff every source pointed to Spain. Germany was the fault line between camps: results-based models and the market sat at ~3–5% (this model 2.9%, Opta 5.1%, market 5.3%), while covariate models with squad value reached ~11% (red bar) — the single largest disagreement of this cup. Postscript (Jul 2): Germany went out of the round of 32, 1-1 (pens) to Paraguay — the low end of that range was the right one.',
 srcMine:'This model (results-based · Elo)',srcSharp:'Sharp market (de-vig)',lblSpain:'Spain',lblGermany:'Germany',
 stChampion:'Title',stFinal:'Final',stSF:'Semis',stQF:'Quarters',stR16:'R16',stR32:'R32',prob:'Probability',
  pmFinal:'Final',pmSF:'SF',pmOdds:'Odds',pmRange:'Model band',
 optAllDates:'All dates',optAllGroups:'All groups',optAllStates:'All states',optDone:'Finished',optTodo:'Upcoming',
 foldDone:'Complete',foldGroupsC:'Expand final group tables',foldGroupsO:'Collapse group tables',
 foldMatchesC:'Expand all 72 group-stage forecasts & results',foldMatchesO:'Collapse match list',koStagePrefix:'Current stage',
 grp:'',scoreNone:'Brier scoring starts after the first final whistle',scored:'Scored',matchesUnit:'',
 modelBrier:'model Brier',vsMarket:'vs market',matchEmpty:'No matches for this filter',
 pWinL:'left win',pDraw:'draw',pWinR:'right win',brierTip:'Model Brier (market',
 thTeam:'Team',thChampion:'Title·blend',thPreChampion:'Pre-title',thFinish:'Final finish',thFinal:'Final',thSF:'SF',thQF:'QF',thR16:'R16',thR32:'R32',
 thOdds:'Sharp odds',thImp:'Implied',thEdge:'Edge pp',thEV:'EV',
 winProb:'Title probability',edgeWord:'Edge',vsModel:'Model',vsMkt:'Market',
 foot1:'<b>Method chain</b> · eloratings.net ratings → 49,400-match historical replay of per-game Elo (corr 0.986 vs official) → Dixon-Coles MLE on 8,103 matches (1,309 held out, logloss 0.8325) → σ=75 strength noise (chosen by backtests on the 2018/2022 World Cups) → <b>in-tournament rating updates</b> (Round 4: eloratings rule replayed over finished matches, knockout logloss −8% across two backtested Cups) → 100,000 full-tournament Monte Carlo runs (complete 2026 ruleset) → sharp-market comparison → <b>knockout headline blended with the sharp market (WMKT market)</b> (power de-vig; model-only kept throughout)',
 foot2:'<b>Public verification</b> · all 72 group-stage forecasts committed to git before the opening kickoff, commit hashes anchored with RFC3161 trusted timestamps (freetsa.org); finished matches are locked into conditional re-simulation and Brier-scored in public; knockout advancement forecasts are appended per match to dual public pre-kickoff ledgers (predictions/ko_forecasts.csv + ko_forecasts_r4.csv, append-only, frozen vs rolled ratings scored head-to-head)',
 foot3:'This page is a static data snapshot and a methodology demo — <b>not betting advice</b>.',
 brand:'🐙 <b>OpenPaul</b> — in 2010, Paul the Octopus picked winners with tentacles; 16 years on, we use 100,000 Monte Carlo runs. The forecasts are open source — the octopus is just the mascot.',
}};
const BSRC_EN={'Opta 超算':'Opta supercomputer','Groll 等学术 ML':'Groll et al. (academic ML)','综合盘隐含':'Composite sportsbooks'};
let LANG=(function(){try{return localStorage.getItem('wc26lang')||'zh'}catch(e){return 'zh'}})();
const t=k=>(I18N[LANG]&&I18N[LANG][k])!=null?I18N[LANG][k]:(I18N.zh[k]!=null?I18N.zh[k]:k);
const nm=x=>LANG==='zh'?(ZH[x]||x):x;
function applyStatic(){
  document.documentElement.lang=LANG==='zh'?'zh-CN':'en';
  const b=document.getElementById('langBtn');if(b)b.textContent=LANG==='zh'?'EN':'中文';
  document.querySelectorAll('[data-i18n]').forEach(el=>{el.innerHTML=t(el.dataset.i18n)});
  // fill blend weights into disclosure copy — single source of truth is D.blend
  const wm=D.blend?Math.round(D.blend.weight_market*100):50;
  document.querySelectorAll('[data-i18n]').forEach(el=>{
    if(el.innerHTML.indexOf('WMKT')>=0||el.innerHTML.indexOf('WMDL')>=0)
      el.innerHTML=el.innerHTML.replace(/WMKT/g,wm+'%').replace(/WMDL/g,(100-wm)+'%');
  });
}
const fl=(t,w=80)=>FLAG[t]?`flags/${FLAG[t]}-w${w<=80?80:320}.png`:'';
const fimg=(t,w)=>FLAG[t]?`<img src="${fl(t,w||80)}" loading="lazy" alt="${nm(t)}">`:'';
const pct=(x,d=1)=>Number.isFinite(x)?(x*100).toFixed(d)+'%':'—';
const num=(x,d=2)=>x==null?'—':(+x).toFixed(d);
const sgn=x=>x==null?'—':(x>0?'+':'')+(+x).toFixed(1);
const hasGL=(()=>{try{const c=document.createElement('canvas');
  return !!(c.getContext('webgl')||c.getContext('experimental-webgl'))}catch(e){return false}})();
const hasEC=typeof echarts!=='undefined';
const DPR={devicePixelRatio:Math.min(window.devicePixelRatio||1,2)};
const MOBILE=innerWidth<760;
let charts={};

/* ---------- generic effects ---------- */
function countUp(el,target,dec,dur){
  const t0=performance.now();
  requestAnimationFrame(function step(now){
    const p=Math.min(1,(now-t0)/dur),e=1-Math.pow(1-p,3);
    el.textContent=(target*e).toFixed(dec);
    if(p<1)requestAnimationFrame(step)});
}
function flickAll(root){
  root.querySelectorAll('[data-flick]').forEach((el,i)=>{
    const fin=el.dataset.flick;delete el.dataset.flick;
    const t0=performance.now(),dur=480+(i%9)*70;
    requestAnimationFrame(function st(now){
      if(now-t0>=dur){el.textContent=fin;return}
      el.textContent=fin.replace(/\d/g,()=>Math.floor(Math.random()*10));
      requestAnimationFrame(st)});
  });
}
function growAll(root){
  root.querySelectorAll('[data-w]').forEach(el=>{el.style.width=el.dataset.w+'%'});
}
function onSee(id,fn,margin){
  const el=document.getElementById(id);if(!el)return;
  const o=new IntersectionObserver(es=>es.forEach(e=>{
    if(e.isIntersecting){o.disconnect();fn(el)}}),{threshold:.12,rootMargin:margin||'0px'});
  o.observe(el);
}

/* ---------- hero carousel ---------- */
const CONTENDERS=D.tournament_done
  ? [D.summary.find(r=>r.team===D.review.final.winner)].filter(Boolean)
  : D.summary.filter(r=>r.p_champion>0);
const TOPN=Math.min(6,CONTENDERS.length);
let heroIdx=0,heroTimer=null;
function setHero(i){
  heroIdx=i;const r=CONTENDERS[i];
  const line=document.getElementById('champLine');
  line.classList.remove('swap');void line.offsetWidth;line.classList.add('swap');
  const f=document.getElementById('champFlag');
  f.src=fl(r.team,320);f.alt=nm(r.team);
  if(D.tournament_done){
    const F=D.review.final;
    document.getElementById('champName').innerHTML=
      nm(r.team)+`<span class="en">2026 WORLD CHAMPIONS · ${r.team}</span>`;
    document.getElementById('champSub').innerHTML=LANG==='zh'
      ? `决赛加时 <b>${F.score1}–${F.score2}</b> ${nm(F.team2)} · 赛前夺冠概率 <b>${pct(+D.review.pre.champion.p_champion)}</b>`
      : `Final: <b>${F.score1}–${F.score2} AET</b> vs ${nm(F.team2)} · pre-tournament title chance <b>${pct(+D.review.pre.champion.p_champion)}</b>`;
    document.getElementById('champPct').textContent=`${F.score1}–${F.score2}`;
    document.getElementById('champUnit').textContent='AET';
    try{paulGo(0)}catch(e){}
    return;
  }
  document.getElementById('champName').innerHTML=
    nm(r.team)+`<span class="en">Nº${i+1} · ${r.team} · ELO ${Math.round(r.elo)}</span>`;
  const direct=D.final?(r.team===D.final.team1?D.final.p1_advance:D.final.p2_advance):null;
  document.getElementById('champSub').innerHTML = r.p_champion_model!=null
    ? `${t('subBlend')} · ${t('subModel')} <b>${pct(r.p_champion_model)}</b> · ${t('subMarket')} <b>${pct(r.p_market_champ)}</b>${direct!=null?` · ${t('subDirect')} <b>${pct(direct)}</b>`:` · ${t('subFinal')} <b>${pct(r.p_final)}</b>`}`
    : `${t('subProb')} [<b>${pct(r.p_s150)}</b>, <b>${pct(r.p_s0)}</b>] · ${t('subFinal')} <b>${pct(r.p_final)}</b> · ${t('subSF')} <b>${pct(r.p_sf)}</b>`;
  countUp(document.getElementById('champPct'),r.p_champion*100,1,900);
  document.querySelectorAll('#heroSide .rk').forEach((el,j)=>el.classList.toggle('active',j===i));
  try{paulGo(i)}catch(e){}   // mascot hops over on every switch, manual or auto
}
function nextHero(){if(TOPN>1)setHero((heroIdx+1)%TOPN)}
function restartHeroTimer(){clearInterval(heroTimer);if(TOPN>1)heroTimer=setInterval(nextHero,8000)}
function renderBadges(){
  document.getElementById('bSnap').textContent=t('snapPrefix')+' '+D.built_at;
  document.getElementById('bRes').textContent=t('playedPrefix')+' '+D.results_count+' / 104';
  document.getElementById('fSnap').textContent=t('genAt')+' '+D.built_at;
  if(REPO&&REPO.indexOf('github.com/')>0&&REPO.length>22){
    const a=document.getElementById('ghLink');a.style.display='';
    document.getElementById('fRepo').innerHTML=' · <a href="'+REPO+'" rel="noopener noreferrer">'+t('repoLink')+'</a>';
  }
}
function buildHeroSide(){
  const side=document.getElementById('heroSide');
  side.querySelectorAll('.rk').forEach(el=>el.remove());
  if(D.tournament_done){
    const labels={champion:t('placeChampion'),runner_up:t('placeRunner'),third:t('placeThird'),fourth:t('placeFourth')};
    D.review.podium.forEach((p,i)=>{
      const div=document.createElement('div');div.className='rk'+(i===0?' active':'');
      div.innerHTML=`<span class="no">0${p.place}</span>${fimg(p.team,80)}<span class="nm">${nm(p.team)}</span>
        <span class="p">${labels[p.code]}</span><span class="track"><i data-tw="${100-i*16}"></i></span>`;
      side.appendChild(div);
    });
    setTimeout(()=>side.querySelectorAll('.track i').forEach(el=>el.style.width=el.dataset.tw+'%'),300);
    return;
  }
  const top=CONTENDERS[0];
  CONTENDERS.slice(0,TOPN).forEach((r,i)=>{
    const div=document.createElement('div');div.className='rk'+(i===heroIdx?' active':'');
    div.setAttribute('role','button');div.setAttribute('tabindex','0');
    div.innerHTML=`<span class="no">0${i+1}</span>${fimg(r.team,80)}<span class="nm">${nm(r.team)}</span>
      <span class="p">${pct(r.p_champion)}</span>
      <span class="track"><i data-tw="${(r.p_champion/top.p_champion*100).toFixed(1)}"></i></span>`;
    div.addEventListener('click',()=>{setHero(i);restartHeroTimer()});
    side.appendChild(div);
  });
  setTimeout(()=>document.querySelectorAll('#heroSide .track i').forEach(el=>el.style.width=el.dataset.tw+'%'),300);
}
function renderHeroStats(){
  if(D.tournament_done){
    const S=D.review.stats,G=D.review.scoring.group,K=D.review.scoring.knockout;
    document.getElementById('heroStats').innerHTML=[
      [S.matches,t('statMatches')],
      [S.goals,t('statGoals')+' · '+S.goals_per_match.toFixed(2)],
      [G.model_brier.toFixed(3),t('statGroupScore')+' ×'+G.n],
      [K.v2_common_brier.toFixed(3)+' / '+K.r4_common_brier.toFixed(3),t('statKoScore')+' ×'+K.common_n],
    ].map(([v,k])=>`<div class="hstat"><div class="hv">${v}</div><div class="hk">${k}</div></div>`).join('');
    return;
  }
  const fit=D.meta.fit||{},bt=D.meta.backtest||{};
  const s=D.score_log||[];
  const bm=s.length?(s.reduce((a,x)=>a+x.brier_model,0)/s.length).toFixed(3):null;
  document.getElementById('heroStats').innerHTML=[
    [t('statSimsV'),t('statSims')],
    [fit.test_mle?fit.test_mle.logloss.toFixed(3):'—',t('statLL')],
    [bm!=null?bm:t('statWait'),t('statBrier')+(s.length?' ×'+s.length:'')],
    ['σ='+(bt.sigma_star!=null?bt.sigma_star:75),t('statSigma')],
  ].map(([v,k])=>`<div class="hstat"><div class="hv">${v}</div><div class="hk">${k}</div></div>`).join('');
}
function heroInit(){
  renderBadges();buildHeroSide();setHero(0);renderHeroStats();
  if(TOPN>1)heroTimer=setInterval(nextHero,5500);
  document.addEventListener('visibilitychange',()=>{
    clearInterval(heroTimer);
    if(!document.hidden&&TOPN>1)heroTimer=setInterval(nextHero,5500);
    if(charts.globe){try{charts.globe.setOption({globe:{viewControl:{autoRotate:!document.hidden&&globeVisible}}})}catch(e){}}
  });
}

/* ---------- final: Spain vs Argentina ---------- */
function finalAnalysis(){
  const F=D.final,sec=document.getElementById('final-analysis-sec');
  if(!F){if(sec)sec.style.display='none';return}
  const sum=Object.fromEntries(D.summary.map(r=>[r.team,r]));
  const A=sum[F.team1]||{},B=sum[F.team2]||{};
  const sa=F.teams[F.team1],sb=F.teams[F.team2];
  const record=s=>`${s.wins}-${s.draws}-${s.losses}`;
  document.getElementById('finalBoard').innerHTML=`<div class="final-matchline">
    <div class="final-team"><img src="${fl(F.team1,320)}" alt="${nm(F.team1)}"><div class="fn">${nm(F.team1)}</div><div class="fr">${record(sa)} · ${sa.gf}:${sa.ga} · ${t('faClean')} ${sa.clean_sheets}</div></div>
    <div class="final-mid"><div class="final-venue">#${F.match} · ${t('faTime')} · ${cityLabel(F.city)}</div>
      <div class="final-probs"><b>${pct(F.p1_advance)}</b><span>${t('faPure')}</span><b>${pct(F.p2_advance)}</b></div>
      <div class="final-bar" aria-label="${t('faPure')}"><i class="sp" style="width:${(F.p1_advance*100).toFixed(2)}%"></i><i class="ar" style="width:${(F.p2_advance*100).toFixed(2)}%"></i></div>
      <div class="final-sub">${t('faBlend')} · ${nm(F.team1)} <b>${pct(A.p_champion)}</b> — <b>${pct(B.p_champion)}</b> ${nm(F.team2)}</div></div>
    <div class="final-team"><img src="${fl(F.team2,320)}" alt="${nm(F.team2)}"><div class="fn">${nm(F.team2)}</div><div class="fr">${record(sb)} · ${sb.gf}:${sb.ga} · ${t('faClean')} ${sb.clean_sheets}</div></div>
  </div>`;
  const top=(F.scorelines||[])[0];
  const h=F.h2h;
  document.getElementById('finalKpis').innerHTML=[
    [F.xg1!=null?`${num(F.xg1,2)} – ${num(F.xg2,2)}`:'—',t('faXg')],
    [top?`${top.score1}–${top.score2} · ${pct(top.p)}`:'—',t('faTopScore')],
    [h?`${h.team1_wins}–${h.draws}–${h.team2_wins}`:'—',t('faH2H')+' · '+t('faH2HNote')]
  ].map(([v,l])=>`<div class="final-kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
  const seg=[['faW1','faW1',F.p1_win90],['faDr','faDraw',F.p_draw90],['faW2','faW2',F.p2_win90]];
  seg.forEach(([id,label,p])=>{const el=document.getElementById(id);el.style.width=(p*100).toFixed(2)+'%';el.textContent=pct(p,0)});
  document.getElementById('faWdlLabels').innerHTML=seg.map(([,label,p])=>`<span>${t(label)} <b>${pct(p)}</b></span>`).join('');
  const scores=F.scorelines||[],mx=scores.length?scores[0].p:1;
  document.getElementById('scoreList').innerHTML=scores.map(s=>`<div class="scoreline"><span class="sc">${s.score1}–${s.score2}</span><span class="sb"><i style="width:${(s.p/mx*100).toFixed(1)}%"></i></span><span class="sv">${pct(s.p)}</span></div>`).join('');
  const rlbl={R32:t('bkR32'),R16:t('bkR16'),QF:t('bkQF'),SF:t('bkSF')};
  document.getElementById('finalTeams').innerHTML=[F.team1,F.team2].map((team,idx)=>{
    const s=F.teams[team],elo=idx?F.elo2:F.elo1;
    const route=s.route.map(r=>`<span>${rlbl[r.round]||r.round} · ${nm(r.opponent)} <b>${r.gf}–${r.ga}</b></span>`).join('');
    return `<article class="panel team-detail"><div class="tdh">${fimg(team,80)}${nm(team)}</div>
      <div class="team-record"><div><b>${record(s)}</b><span>${t('faWin')}–${t('faDrawShort')}–${t('faLoss')}</span></div><div><b>${s.gf}–${s.ga}</b><span>${t('faGoals')}</span></div><div><b>${s.clean_sheets}</b><span>${t('faClean')}</span></div><div><b>${Math.round(elo)}</b><span>${t('faElo')}</span></div></div>
      <div class="sec-no">${t('faRoute')}</div><div class="route">${route}</div></article>`
  }).join('');
}

/* ---------- penalty shootout: historical evidence, isolated from model ---------- */
function penaltyAnalysis(){
  const sec=document.getElementById('penalty-sec');
  if(!sec)return;
  try{
    const F=D.final,P=F&&F.penalties;
    if(!P||!P.teams){sec.style.display='none';return}
    const teams=[F.team1,F.team2],A=P.teams[teams[0]],B=P.teams[teams[1]];
    if(!A||!B)throw new Error('missing penalty profile');
    sec.style.display='';
    const byId=id=>{const el=document.getElementById(id);if(!el)throw new Error('missing '+id);return el};
    const rec=r=>`${r.wins}-${r.losses}`;
    const compNames={
      'FIFA World Cup':{zh:'世界杯',en:'FIFA World Cup'},
      'Copa America':{zh:'美洲杯',en:'Copa America'},
      'UEFA EURO':{zh:'欧洲杯',en:'UEFA EURO'},
      'FIFA Confederations Cup':{zh:'联合会杯',en:'Confederations Cup'},
      'UEFA Nations League':{zh:'欧国联',en:'Nations League'},
      'Artemio Franchi Cup':{zh:'弗兰基杯',en:'Artemio Franchi Cup'}
    };
    const comp=x=>(compNames[x]&&compNames[x][LANG])||x;
    const model=P.model_after_draw||{},m0=model[teams[0]],m1=model[teams[1]];
    byId('penVerdict').innerHTML=`<div class="pen-call">
      <span class="pen-tag">${t('pkEvidenceTag')}</span><h3>${t('pkVerdictT')}</h3><p>${t('pkVerdictD')}</p>
    </div><div class="pen-model"><div class="pen-model-title">${t('pkModelT')}</div>
      <div class="pen-model-values"><span>${nm(teams[0])} <b>${pct(m0)}</b></span><span><b>${pct(m1)}</b> ${nm(teams[1])}</span></div>
      <div class="pen-modelbar" aria-label="${t('pkModelT')}"><i class="sp" style="width:${m0!=null?(m0*100).toFixed(2):0}%"></i><i class="ar" style="width:${m1!=null?(m1*100).toFixed(2):0}%"></i></div>
      <div class="pen-model-note">${t('pkModelNote')}</div></div>`;

    const metrics=[
      {label:t('pkOverall'),rateA:A.overall.win_pct,rateB:B.overall.win_pct,
       detailA:`${t('pkRecord')} ${rec(A.overall)}`,detailB:`${t('pkRecord')} ${rec(B.overall)}`},
      {label:t('pkWorldCup'),rateA:A.world_cup.win_pct,rateB:B.world_cup.win_pct,
       detailA:`${t('pkRecord')} ${rec(A.world_cup)}`,detailB:`${t('pkRecord')} ${rec(B.world_cup)}`},
      {label:t('pkRecent'),rateA:A.recent.win_pct,rateB:B.recent.win_pct,
       detailA:`${t('pkRecord')} ${rec(A.recent)}`,detailB:`${t('pkRecord')} ${rec(B.recent)}`},
      {label:t('pkKeeperRate'),rateA:A.keeper.save_pct,rateB:B.keeper.save_pct,
       detailA:`${A.keeper.saves}/${A.keeper.faced}`,detailB:`${B.keeper.saves}/${B.keeper.faced}`},
      {label:t('pkConversion'),rateA:A.kicks.conversion_pct,rateB:B.kicks.conversion_pct,
       detailA:`${A.kicks.scored}/${A.kicks.attempts}`,detailB:`${B.kicks.scored}/${B.kicks.attempts}`}
    ];
    const barWidth=x=>Number.isFinite(x)?(x*100).toFixed(1):'0';
    byId('penMetrics').innerHTML=`<div class="pen-teamheads">
      <div class="pen-teamhead">${fimg(teams[0],80)}<span>${nm(teams[0])}</span></div><div class="mid">${t('pkCompare')}</div>
      <div class="pen-teamhead right"><span>${nm(teams[1])}</span>${fimg(teams[1],80)}</div></div>
      ${metrics.map(m=>`<div class="pen-mrow" aria-label="${m.label}: ${nm(teams[0])} ${pct(m.rateA)}, ${nm(teams[1])} ${pct(m.rateB)}">
        <div class="pen-mval left">${pct(m.rateA)}</div><div class="pen-track left"><i data-w="${barWidth(m.rateA)}"></i></div>
        <div class="pen-mlabel"><b>${m.label}</b><span>${m.detailA} · ${m.detailB}</span></div>
        <div class="pen-track right"><i data-w="${barWidth(m.rateB)}"></i></div><div class="pen-mval right">${pct(m.rateB)}</div>
      </div>`).join('')}
      <div class="pen-model-note" style="margin-top:9px">${t('pkCurrent')} · ${nm(teams[0])} ${A.current_tournament_shootouts} · ${nm(teams[1])} ${B.current_tournament_shootouts}</div>`;

    const historyRows=(rows,keeper)=>((Array.isArray(rows)?rows:[]).map(r=>`<div class="pen-hrow">
      <span>${r.date.slice(0,4)}</span><span class="opp">${nm(r.opponent)}<small>${comp(r.competition)}</small></span>
      <span class="detail">${keeper?`${r.saves}/${r.faced}`:`${r.pens_for}–${r.pens_against}`}</span>
      <span class="res ${r.result==='W'?'win':'loss'}">${t(r.result==='W'?'pkW':'pkL')}</span></div>`).join(''));
    const keeperCard=(team,p)=>{const k=p.keeper,saveW=k.saves/k.faced*100,offW=k.off_target/k.faced*100,goalW=k.goals/k.faced*100;return `<article class="panel pen-card">
      <h3>${fimg(team,80)}${nm(team)} · ${k.name}</h3><div class="pen-sub">${t('pkKeeperT')} · ${t('pkKeeperSub')} · ${k.wins}-${k.losses}</div>
      <div class="pen-big"><b>${k.saves}/${k.faced}</b><span>${t('pkSaved')} · ${pct(k.save_pct)}</span></div>
      <div class="pen-kickbar" aria-label="${k.name}: ${t('pkSaved')} ${k.saves}, ${t('pkOff')} ${k.off_target}, ${t('pkGoals')} ${k.goals}"><i class="save" style="width:${saveW.toFixed(2)}%"></i><i class="off" style="width:${offW.toFixed(2)}%"></i><i class="goal" style="width:${goalW.toFixed(2)}%"></i></div>
      <div class="pen-legend"><span><i class="pen-dot save"></i>${t('pkSave')} ${k.saves}</span><span><i class="pen-dot off"></i>${t('pkOff')} ${k.off_target}</span><span><i class="pen-dot goal"></i>${t('pkGoal')} ${k.goals}</span></div>
      <div class="pen-history">${historyRows(k.history,true)}</div></article>`};
    byId('penKeepers').innerHTML=keeperCard(teams[0],A)+keeperCard(teams[1],B);

    const recentCard=(team,p)=>`<article class="panel pen-card"><h3>${fimg(team,80)}${nm(team)} · ${t('pkRecentT')}</h3>
      <div class="pen-sub">${t('pkRecent')} · ${rec(p.recent)} · ${p.recent_kicks.scored}/${p.recent_kicks.attempts} (${pct(p.recent_kicks.conversion_pct)})</div><div class="pen-history">${historyRows(p.recent_history,false)}</div></article>`;
    byId('penRecent').innerHTML=recentCard(teams[0],A)+recentCard(teams[1],B);

    const takerCard=(team,p)=>`<article class="panel pen-card"><h3>${fimg(team,80)}${nm(team)} · ${t('pkTakersT')}</h3>
      <div class="pen-sub">${t('pkTakersSub')}</div><div class="pen-taker-rate"><b>${p.recent_kicks.scored}/${p.recent_kicks.attempts}</b><span>${t('pkRecentConversion')} · ${pct(p.recent_kicks.conversion_pct)}</span></div><div class="pen-takers">${(p.taker_pool||[]).map(x=>`<span>${x}</span>`).join('')}</div>
      <p class="pen-note">${LANG==='zh'?p.taker_note_zh:p.taker_note_en}</p></article>`;
    byId('penTakers').innerHTML=takerCard(teams[0],A)+takerCard(teams[1],B);
    const definition=LANG==='zh'?P.definition_zh:P.definition_en;
    const links=(P.sources||[]).map(s=>`<a href="${s.url}" target="_blank" rel="noopener noreferrer">${s.label}</a>`).join(' · ');
    byId('penSources').innerHTML=`${t('pkAsOf')} <b>${P.as_of}</b> · ${t('pkScope')}: ${definition}<br>${t('pkSources')}: ${links}`;
  }catch(e){
    sec.style.display='none';
    try{console.warn('penalty module hidden',e)}catch(_){}
  }
}

/* ---------- OpenPaul mascot: a short-legged octopus stands beside the active
   contender's flag; on every switch (click OR auto-cycle) it hops over,
   picks the flag up overhead and flashes ❗; idles with head-scratch + ❓.
   rAF-driven, pauses offscreen / tab hidden; reduced-motion = static poses. */
const PAUL={flags:[],ok:false,vis:true,mode:'idle',x:380,tgt:-1,held:-1,
  h0:0,hx0:0,hx1:0,hn:1,hd:420,p0:0,kk:0,markT:0,carry:0,
  reduced:matchMedia('(prefers-reduced-motion: reduce)').matches};
const PAUL_GY=206,PAUL_OFF=-34,PAUL_FX=[90,205,320,435,550,665];
function paulShowMark(ch){
  const st=document.getElementById('paul');if(!PAUL.ok)return;
  document.getElementById('paulMark').textContent=ch;
  st.classList.remove('mark');void st.offsetWidth;st.classList.add('mark');
  clearTimeout(PAUL.markT);PAUL.markT=setTimeout(()=>st.classList.remove('mark'),1900);
}
function paulRender(T){
  let jy=0,sx=1,sy=1;
  if(PAUL.mode==='hop'){
    const u=Math.min(1,(T-PAUL.h0)/PAUL.hd);
    PAUL.x=PAUL.hx0+(PAUL.hx1-PAUL.hx0)*u;
    const air=Math.max(Math.abs(Math.sin(Math.PI*PAUL.hn*u)),
      PAUL.carry*Math.max(0,1-u*3));      // retarget mid-air: old height decays into the new arc
    jy=-38*air;sy=.86+.26*air;sx=2-sy;     // squash on the ground, stretch in the air
    if(u>=1){PAUL.mode='pick';PAUL.p0=T;PAUL.held=PAUL.tgt;
      PAUL.flags.forEach((f,j)=>f.g.classList.toggle('lift',j===PAUL.held));
      document.getElementById('paul').classList.add('held')}
  }else if(PAUL.mode==='pick'&&T-PAUL.p0>=330){PAUL.mode='idle';paulShowMark('❗')}
  document.getElementById('paulOcto').setAttribute('transform',
    `translate(${PAUL.x.toFixed(1)},${(PAUL_GY+jy).toFixed(1)}) scale(${sx.toFixed(3)},${sy.toFixed(3)})`);
  const sh=document.getElementById('paulShadow');
  sh.setAttribute('cx',PAUL.x.toFixed(1));
  sh.setAttribute('rx',(24*(1+jy/90)).toFixed(1));
  sh.setAttribute('opacity',(.3*(1+jy/60)).toFixed(2));
  PAUL.flags.forEach((f,i)=>{
    if(!PAUL.reduced)f.h+=((i===PAUL.held?1:0)-f.h)*PAUL.kk;
    const e=f.h*f.h*(3-2*f.h);
    const px=PAUL_FX[i]+(PAUL.x+24-PAUL_FX[i])*e,py=PAUL_GY-44*e,
      rot=Math.sin(T*12e-4+i*2)*2.5*(1-e)+(14+Math.sin(T*3e-3)*6)*e,
      sc=1+.08*e;
    f.g.setAttribute('transform',`translate(${px.toFixed(1)},${py.toFixed(1)}) rotate(${rot.toFixed(1)}) scale(${sc.toFixed(3)})`);
  });
}
function paulGo(i){ // the active contender changed -> hop over, pick up its flag, ❗
  if(!PAUL.ok||i==null||i<0||i>=PAUL.flags.length)return;
  if(i===PAUL.tgt&&(PAUL.mode!=='idle'||PAUL.held===i))return;
  const st=document.getElementById('paul');
  st.classList.remove('scratching');st.classList.remove('mark');clearTimeout(PAUL.markT);
  PAUL.tgt=i;
  const dest=PAUL_FX[i]+PAUL_OFF;
  if(PAUL.reduced){
    PAUL.x=dest;PAUL.held=i;PAUL.mode='idle';
    PAUL.flags.forEach((f,j)=>{f.h=j===i?1:0;f.g.classList.toggle('lift',j===i)});
    st.classList.add('held');
    paulRender(performance.now());paulShowMark('❗');return;
  }
  PAUL.carry=PAUL.mode==='hop'
    ?Math.abs(Math.sin(Math.PI*PAUL.hn*Math.min(1,(performance.now()-PAUL.h0)/PAUL.hd))):0;
  PAUL.held=-1;PAUL.flags.forEach(f=>f.g.classList.remove('lift'));
  st.classList.remove('held');
  PAUL.mode='hop';PAUL.hx0=PAUL.x;PAUL.hx1=dest;
  PAUL.hn=Math.max(1,Math.min(5,Math.round(Math.abs(dest-PAUL.x)/95)||1));
  PAUL.hd=PAUL.hn*380;PAUL.h0=performance.now();
}
function paulLang(){
  PAUL.flags.forEach((f,i)=>{const ti=f.g.querySelector('title');
    if(ti)ti.textContent=nm(CONTENDERS[i].team)});
}
function paulInit(){
  const st=document.getElementById('paul'),ground=document.getElementById('paulGround'),
    flayer=document.getElementById('paulFlags');
  if(!st||!ground||!flayer)return;
  const NS='http://www.w3.org/2000/svg';
  CONTENDERS.slice(0,TOPN).forEach((r,i)=>{
    const m=document.createElementNS(NS,'ellipse');
    m.setAttribute('class','pmound');m.setAttribute('cx',PAUL_FX[i]);m.setAttribute('cy',PAUL_GY+2);
    m.setAttribute('rx',9);m.setAttribute('ry',2.6);ground.appendChild(m);
    const fg=document.createElementNS(NS,'g');
    fg.setAttribute('class','pfg');fg.setAttribute('role','button');fg.setAttribute('tabindex','0');
    fg.innerHTML=`<title>${nm(r.team)}</title>
      <rect class="hit" x="-26" y="-66" width="92" height="78" rx="10"/>
      <line class="pole" x1="0" y1="0" x2="0" y2="-46"/>
      <circle class="fin" cx="0" cy="-46" r="2.4"/>
      <image href="${fl(r.team,80)}" x="2" y="-44" width="40" height="28" preserveAspectRatio="xMidYMid slice" clip-path="url(#pfClip)"/>
      <rect x="2" y="-44" width="40" height="28" rx="4"/>`;
    const act=()=>{setHero(i);restartHeroTimer()};   // paulGo rides along inside setHero
    fg.addEventListener('click',act);
    fg.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();act()}});
    flayer.appendChild(fg);
    PAUL.flags.push({g:fg,h:0});
  });
  PAUL.ok=true;
  if(PAUL.reduced){paulGo(heroIdx);return}   // static pose: no hop loop, no scratch timer
  new IntersectionObserver(es=>{PAUL.vis=es[es.length-1].isIntersecting}).observe(st);
  let last=performance.now();
  (function loop(now){
    if(PAUL.vis&&!document.hidden){
      PAUL.kk=1-Math.exp(-Math.min(50,now-last)/130);
      paulRender(now);
    }
    last=now;requestAnimationFrame(loop);
  })(last);
  (function scratch(){
    setTimeout(()=>{
      if(PAUL.vis&&!document.hidden&&PAUL.mode==='idle'){
        st.classList.add('scratching');paulShowMark('❓');
        setTimeout(()=>st.classList.remove('scratching'),1950);
      }
      scratch();
    },6500+Math.random()*7000);
  })();
  // entrance after first paint, so a cold load still shows the hop
  requestAnimationFrame(()=>setTimeout(()=>paulGo(heroIdx),300));
}

/* ---------- particle field (pauses when hero offscreen / tab hidden) ---------- */
let starsOn=true;
function stars(){
  const cv=document.getElementById('stars'),ctx=cv.getContext('2d');
  let W,H,ps;
  function init(){W=cv.width=cv.offsetWidth;H=cv.height=cv.offsetHeight;
    ps=Array.from({length:Math.min(90,W/14)},()=>({
      x:Math.random()*W,y:Math.random()*H,r:Math.random()*1.7+.4,
      vy:-(Math.random()*.25+.06),vx:(Math.random()-.5)*.08,
      a:Math.random()*.55+.1,gold:Math.random()<.3}))}
  init();addEventListener('resize',init);
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  new IntersectionObserver(es=>{starsOn=es[0].isIntersecting})
    .observe(document.getElementById('hero'));
  (function loop(){
    if(starsOn&&!document.hidden){
      ctx.clearRect(0,0,W,H);
      for(const p of ps){
        p.y+=p.vy;p.x+=p.vx;
        if(p.y<-4){p.y=H+4;p.x=Math.random()*W}
        ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,7);
        ctx.fillStyle=p.gold?`rgba(240,199,94,${p.a})`:`rgba(140,170,220,${p.a*.8})`;
        ctx.fill();}
    }
    requestAnimationFrame(loop)})();
}

/* ---------- 3D terrain: equalizer wave intro -> settle to real data ---------- */
function terrain(skipWave){
  const teams=D.summary.slice(0,MOBILE?12:24);
  const stages=[['p_champion',t('stChampion')],['p_final',t('stFinal')],['p_sf',t('stSF')],['p_qf',t('stQF')],['p_r16',t('stR16')],['p_r32',t('stR32')]];
  if(!hasGL||!hasEC){return terrainFallback(teams)}
  let c;
  try{c=echarts.init(document.getElementById('terrain'),null,DPR)}catch(e){return terrainFallback(teams)}
  charts.terrain=c;
  const real=[];teams.forEach((t,xi)=>stages.forEach((s,yi)=>real.push([xi,yi,t[s[0]]||0.0001])));
  const rowMax=stages.map(s=>Math.max.apply(null,teams.map(t=>t[s[0]]||0)));
  try{
  c.setOption({
    tooltip:{backgroundColor:'#101a30',borderColor:'#2a3b5d',textStyle:{color:'#e8eef9'},
      formatter:p=>`<b>${nm(teams[p.data[0]].team)}</b> · ${stages[p.data[1]][1]}<br>${t('prob')} <b style="color:#ffe9a8">${pct(p.data[2],2)}</b>`},
    visualMap:{show:false,min:0,max:5,dimension:1,
      inRange:{color:['#ffe9a8','#8fb0d8','#5f8fd0','#3a6fc0','#27508f','#1d3a6e']}},
    xAxis3D:{type:'category',data:teams.map(t=>nm(t.team)),
      axisLabel:{color:'#9fb2cd',fontSize:11,interval:MOBILE?1:2,rotate:40,margin:12},
      name:'',axisLine:{lineStyle:{color:'#16223c'}},splitLine:{show:false},axisTick:{show:false}},
    yAxis3D:{type:'category',data:stages.map(s=>s[1]),
      axisLabel:{color:'#cdd9ec',fontSize:11},name:'',
      axisLine:{lineStyle:{color:'#16223c'}},splitLine:{show:false},axisTick:{show:false}},
    zAxis3D:{type:'value',max:1,axisLabel:{show:false},name:'',
      splitLine:{show:false},axisLine:{lineStyle:{color:'#16223c'}}},
    grid3D:{boxWidth:210,boxDepth:110,boxHeight:55,
      environment:'#05070f',
      light:{main:{intensity:1.3,shadow:!MOBILE,shadowQuality:'medium',alpha:40,beta:20},
             ambient:{intensity:.35}},
      viewControl:{autoRotate:false,distance:200,alpha:16,beta:38,
        minDistance:110,maxDistance:420},
      postEffect:{enable:!MOBILE,bloom:{enable:true,bloomIntensity:.12}}},
    series:[{type:'bar3D',shading:'lambert',barSize:[7.4,12.4],
      data:skipWave?real:real.map(d=>[d[0],d[1],0.001]),
      emphasis:{itemStyle:{color:'#ffe9a8'}},
      itemStyle:{opacity:.97},
      animationDurationUpdate:140,animationEasingUpdate:'linear'}]
  });
  }catch(e){return terrainFallback(teams)}
  if(skipWave)return;
  // pure-random equalizer jitter for ~2.2s, then settle to real values
  let f=0;const FR=16;
  const iv=setInterval(()=>{
    f++;
    if(f>=FR){
      clearInterval(iv);
      c.setOption({series:[{animationDurationUpdate:1100,animationEasingUpdate:'cubicOut',data:real}]});
      return;
    }
    c.setOption({series:[{data:real.map(d=>[d[0],d[1],
      Math.max(.004,rowMax[d[1]]*(0.08+0.92*Math.random()))])}]});
  },140);
}
function terrainFallback(teams){
  document.getElementById('terrain').style.display='none';
  const h=document.getElementById('terrainHint');if(h)h.style.display='none';
  const el=document.getElementById('terrainFall');el.style.display='block';
  if(!hasEC){el.innerHTML='<div style="color:var(--dim);padding:60px;text-align:center">图表组件加载失败——完整数据见下方表格</div>';return}
  const c=echarts.init(el,null,{renderer:'svg'});charts.tf=c;
  const rows=[...teams].reverse();
  c.setOption({backgroundColor:'transparent',
    grid:{left:8,right:64,top:6,bottom:6,containLabel:true},
    xAxis:{type:'value',axisLabel:{color:'#76879f',formatter:v=>(v*100)+'%'},splitLine:{lineStyle:{color:'#15203a'}}},
    yAxis:{type:'category',data:rows.map(r=>nm(r.team)),axisLabel:{color:'#e8eef9'}},
    series:[{type:'bar',data:rows.map(r=>r.p_champion),barWidth:12,
      itemStyle:{color:'#4f8dff',borderRadius:[0,5,5,0]},
      label:{show:true,position:'right',color:'#e8eef9',fontSize:10,formatter:p=>pct(p.value)}}]});
}

/* ---------- globe (auto-cruise pauses offscreen) ---------- */
let globeVisible=false;
function globe(){
  if(!hasGL||!hasEC){hideGlobe();return}
  let c;
  try{c=echarts.init(document.getElementById('globe'),null,DPR)}catch(e){hideGlobe();return}
  charts.globe=c;
  const data=D.summary.filter(r=>GEO[r.team]).map((r,i)=>{
    const g=GEO[r.team];
    return {name:nm(r.team),value:[g[0],g[1],Math.sqrt(r.p_champion)*36,r.p_champion],p:r.p_champion,
      label:{show:i<8,formatter:'{b}',color:'#ffe9a8',fontSize:10,distance:3}};
  });
  const pmax=Math.max.apply(null,data.map(d=>d.p))*1.02;
  try{
  c.setOption({
    backgroundColor:'transparent',
    tooltip:{backgroundColor:'#101a30',borderColor:'#2a3b5d',textStyle:{color:'#e8eef9'},
      formatter:p=>`<b>${p.data.name}</b><br>${t('winProb')} <b style="color:#ffe9a8">${pct(p.data.p,2)}</b>`},
    visualMap:{show:false,min:0,max:pmax,dimension:3,seriesIndex:0,
      inRange:{color:['#2e5da8','#4f8dff','#9cc3ff','#e8c469','#ffe9a8']}},
    globe:{
      baseTexture:EARTH,
      shading:'lambert',
      environment:'#05070f',
      atmosphere:{show:true,color:'#2a4a8a',glowPower:5,innerGlowPower:3},
      light:{main:{intensity:1.7,shadow:false},ambient:{intensity:.62}},
      viewControl:{autoRotate:true,autoRotateSpeed:5,autoRotateAfterStill:3,
        distance:205,minDistance:140,maxDistance:380,targetCoord:[-15,32]},
      postEffect:{enable:!MOBILE,bloom:{enable:true,bloomIntensity:.18}}
    },
    series:[{type:'bar3D',coordinateSystem:'globe',data,
      barSize:2.4,minHeight:.8,shading:'lambert',
      itemStyle:{opacity:.95},
      emphasis:{itemStyle:{color:'#fff3cf'}}}]
  });
  }catch(e){hideGlobe();return}
  new IntersectionObserver(es=>{
    globeVisible=es[0].isIntersecting;
    try{c.setOption({globe:{viewControl:{autoRotate:globeVisible&&!document.hidden}}})}catch(e){}
  }).observe(document.getElementById('globePanel'));
}
function hideGlobe(){
  document.getElementById('globe-sec').style.display='none';
  document.getElementById('navGlobe').style.display='none';
}

/* ---------- podium ---------- */
function podium(){
  if(D.tournament_done){
    const cls=['p1','p2','p3','p4'],marks=['🏆','02','03','04'];
    const labels={champion:t('placeChampion'),runner_up:t('placeRunner'),third:t('placeThird'),fourth:t('placeFourth')};
    document.getElementById('podium').innerHTML=D.review.podium.map((p,i)=>`<div class="pcard ${cls[i]} reveal" style="transition-delay:${i*.1}s">
      <div class="medal">Nº${p.place} · ${labels[p.code]}</div>
      <img class="pflag" src="${fl(p.team,320)}" alt="${nm(p.team)}">
      <div class="pname">${nm(p.team)}</div>
      <div class="pper">${marks[i]}</div>
      <div style="color:var(--dim);font-size:11px;font-family:var(--num)">${t('preTitleProb')} <b style="color:var(--gold2)">${pct(p.pre_champion)}</b></div>
      <div class="pmeta"><span>${t('preRankLabel')} <b>Nº${p.pre_rank}</b></span><span>${labels[p.code]}</span></div>
    </div>`).join('');
    return;
  }
  const order=CONTENDERS.map((_,i)=>i),cls=['p1','p2','p3','p4'],medal=['Nº1 · LEADER','Nº2','Nº3','Nº4'];
  document.getElementById('podium').innerHTML=order.map((si,i)=>{
    const r=CONTENDERS[si];
    const direct=D.final?(r.team===D.final.team1?D.final.p1_advance:D.final.p2_advance):null;
    return `<div class="pcard ${cls[i]} reveal" style="transition-delay:${i*.1}s">
      <div class="medal">${medal[i]}</div>
      <img class="pflag" src="${fl(r.team,320)}" alt="${nm(r.team)}">
      <div class="pname">${nm(r.team)}</div>
      <div class="pper" data-flick="${pct(r.p_champion)}">${pct(r.p_champion)}</div>
      <div style="color:var(--dim);font-size:11px;font-family:var(--num)">${t('pmRange')} [${pct(r.p_s150)} – ${pct(r.p_s0)}]</div>
      <div class="pmeta">${direct!=null?`<span>${t('subDirect')} <b>${pct(direct)}</b></span>`:`<span>${t('pmFinal')} <b>${pct(r.p_final,0)}</b></span>`}<span>${t('subModel')} <b>${pct(r.p_champion_model,0)}</b></span><span>${t('subMarket')} <b>${pct(r.p_market_champ,0)}</b></span></div>
    </div>`}).join('');
}

/* ---------- completed tournament review ---------- */
function tournamentReview(){
  const sec=document.getElementById('review-sec');
  if(!D.tournament_done||!D.review){if(sec)sec.style.display='none';return}
  const R=D.review,F=R.final,T=R.third_place,z=LANG==='zh';
  const team=(name,side='')=>`<div class="outcome-team ${side}">${fimg(name,80)}<span>${nm(name)}</span></div>`;
  const aet=F.status==='STATUS_FINAL_AET';
  document.getElementById('outcomeBoard').innerHTML=`
    <div class="outcome-main"><div class="outcome-tag">${t('outcomeFinal')}</div><div class="outcome-row">
      ${team(F.team1)}<div class="outcome-score">${F.score1}–${F.score2}</div>${team(F.team2,'r')}</div>
      <div class="outcome-note">${aet?t('afterExtra'):''} · ${nm(F.winner)} ${t('placeChampion')}</div></div>
    <div class="outcome-third"><div class="outcome-tag">${t('outcomeThird')}</div><div class="outcome-row">
      ${team(T.team1)}<div class="outcome-score">${T.score1}–${T.score2}</div>${team(T.team2,'r')}</div>
      <div class="outcome-note">${nm(T.winner)} · ${t('placeThird')}</div></div>`;
  const S=R.stats;
  document.getElementById('reviewKpis').innerHTML=[
    [S.matches,z?'场比赛':'matches'],[S.goals,z?'粒进球（不含点球大战）':'goals (shoot-outs excluded)'],
    [S.goals_per_match.toFixed(2),z?'场均进球':'goals / match'],[S.draws,z?'记录比分相等':'level scores'],
    [S.shootouts,z?'场点球大战':'shoot-outs']
  ].map(([v,k])=>`<div class="panel review-kpi"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

  const C=R.pre.champion,V=R.pre.value_leader,FF=R.final_forecasts,TF=R.third_forecasts,K=R.scoring.knockout;
  const top4=(R.pre.top_four||[]).map(x=>nm(x.team)).join(z?'、':', ');
  const cards=z?[
    ['01 · 绝对概率','赛前头名最终夺冠',`西班牙以 <b>${pct(+C.p_champion)}</b> 位列揭幕战前冠军概率第一并最终夺冠；这是一条命中的强弱信号，不是确定性预言。`],
    ['02 · 最终四强','赛前前四完整会师',`赛前冠军概率前四为 <b>${top4}</b>，四队最终全部进入半决赛。名单完全重合，但不等于逐场命中了整条路径。`],
    ['03 · 价值判断','价值榜头名获得亚军',`阿根廷赛前夺冠概率 <b>${pct(+V.p_champion)}</b>，相对锐利盘为 <b>+${(+V.edge_sharp_pp).toFixed(2)}pp</b>，价值榜第一，最终打进决赛。`],
    ['04 · 决赛存证','判断正确，但优势很小',`滚动评级给西班牙 <b>${pct(FF.r4.winner_probability)}</b>，冻结评级为 <b>${pct(FF.v2.winner_probability)}</b>；两轨都只微倾冠军。`],
    ['05 · 明确失误','季军战两轨都选错',`滚动评级给法国 <b>${pct(1-TF.r4.winner_probability)}</b>，冻结评级为 <b>${pct(1-TF.v2.winner_probability)}</b>；英格兰最终 6–4 获胜。`],
    ['06 · 滚动更新','本届未击败冻结基线',`共同 ${K.common_n} 场，冻结版 Brier <b>${K.v2_common_brier.toFixed(3)}</b>，滚动版 <b>${K.r4_common_brier.toFixed(3)}</b>；命中率同为 85%，小样本下仅能说冻结版小幅胜出。`]
  ]:[
    ['01 · ABSOLUTE ODDS','The pre-tournament No. 1 won',`Spain led the opening-day title ranking at <b>${pct(+C.p_champion)}</b> and became champions. That is a successful strength signal, not a claim of certainty.`],
    ['02 · FINAL FOUR','The pre-tournament top four all made it',`The top four were <b>${top4}</b>; all four reached the semi-finals. The quartet matched, but that is not an exact prediction of every knockout path.`],
    ['03 · VALUE','The value leader finished second',`Argentina started at <b>${pct(+V.p_champion)}</b> with a <b>+${(+V.edge_sharp_pp).toFixed(2)}pp</b> edge over the sharp market, ranked first for value, and reached the final.`],
    ['04 · SEALED FINAL','Right call, narrow edge',`Spain had <b>${pct(FF.r4.winner_probability)}</b> with rolled ratings and <b>${pct(FF.v2.winner_probability)}</b> with frozen ratings. Both made the champion only a slight favourite.`],
    ['05 · CLEAR MISS','Both ledgers missed third place',`France had <b>${pct(1-TF.r4.winner_probability)}</b> with rolled ratings and <b>${pct(1-TF.v2.winner_probability)}</b> frozen; England won 6–4.`],
    ['06 · RATING UPDATES','Rolling did not beat the baseline here',`Across ${K.common_n} shared matches, frozen Brier was <b>${K.v2_common_brier.toFixed(3)}</b> versus <b>${K.r4_common_brier.toFixed(3)}</b> rolled. Accuracy was 85% for both; the sample supports only a modest frozen edge.`]
  ];
  document.getElementById('reviewCards').innerHTML=cards.map(([e,h,p])=>`<article class="panel review-card"><div class="eyebrow">${e}</div><h3>${h}</h3><p>${p}</p></article>`).join('');

  const G=R.scoring.group;
  document.getElementById('reviewScores').innerHTML=`<h3>${t('reviewScoreTitle')}</h3><p>${t('reviewScoreSub')}</p><div class="score-pair">
    <div class="score-box"><div class="sh">${t('groupScore')} · n=${G.n}</div><div class="sv">${G.model_brier.toFixed(4)}</div>
      <div class="sn">${z?'模型全 72 场 Brier':'model Brier, all 72'} · log-loss ${G.model_logloss.toFixed(4)}<br>
      ${z?'共同 16 场：模型':'shared 16: model'} <b>${G.model_common_brier.toFixed(4)}</b> · ${z?'市场':'market'} <b>${G.market_common_brier.toFixed(4)}</b></div></div>
    <div class="score-box"><div class="sh">${t('koScore')} · ${z?'共同':'shared'} n=${K.common_n}</div><div class="sv">${K.v2_common_brier.toFixed(4)} / ${K.r4_common_brier.toFixed(4)}</div>
      <div class="sn">${z?'冻结 / 滚动；两轨命中率均 85%':'frozen / rolled; both 85% accurate'}<br>
      ${z?'全账本：冻结':'all-ledger: frozen'} n=${K.v2_n} · ${K.v2_brier.toFixed(4)} / ${z?'滚动':'rolled'} n=${K.r4_n} · ${K.r4_brier.toFixed(4)}</div></div>
  </div>`;
}

/* ---------- edge chart (lazy, flags on axis) ---------- */
function edge(){
  if(!hasEC)return;
  const rows=[...D.summary].filter(x=>x.edge_sharp_pp!=null).sort((a,b)=>b.edge_sharp_pp-a.edge_sharp_pp);
  const sel=[...rows.slice(0,7),...rows.slice(-6)].sort((a,b)=>a.edge_sharp_pp-b.edge_sharp_pp);
  const c=echarts.init(document.getElementById('chEdge'),null,{renderer:'svg'});
  charts.edge=c;
  const rich={};
  sel.forEach((r,i)=>{rich['f'+i]={backgroundColor:{image:fl(r.team,80)},width:26,height:18,borderRadius:3}});
  c.setOption({backgroundColor:'transparent',
    grid:{left:8,right:58,top:6,bottom:6,containLabel:true},
    xAxis:{type:'value',axisLabel:{color:'#76879f',fontFamily:'Space Grotesk',formatter:v=>v+'pp'},
      splitLine:{lineStyle:{color:'#15203a'}}},
    yAxis:{type:'category',data:sel.map((r,i)=>`{f${i}|} ${nm(r.team)}`),
      axisLabel:{color:'#e8eef9',fontSize:12.5,rich},
      axisLine:{show:false},axisTick:{show:false}},
    tooltip:{backgroundColor:'#101a30',borderColor:'#2a3b5d',textStyle:{color:'#e8eef9'},
      formatter:p=>{const r=sel[p.dataIndex];
        return `<b>${nm(r.team)}</b><br>${t('vsModel')} ${pct(r.p_champion_model!=null?r.p_champion_model:r.p_champion)} − ${t('vsMkt')} ${pct(r.p_market_sharp)}<br>${t('edgeWord')} <b>${sgn(r.edge_sharp_pp)}pp</b> · EV ${sgn(r.ev_sharp*100)}%`}},
    series:[{type:'bar',barWidth:17,
      data:sel.map(r=>({value:+r.edge_sharp_pp.toFixed(2),
        itemStyle:{color:r.edge_sharp_pp>=0?
          new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#b8862e'},{offset:1,color:'#ffe9a8'}]):
          new echarts.graphic.LinearGradient(1,0,0,0,[{offset:0,color:'#33415e'},{offset:1,color:'#5d7299'}]),
        borderRadius:r.edge_sharp_pp>=0?[0,8,8,0]:[8,0,0,8]}})),
      label:{show:true,position:'right',color:'#9fb2cd',fontSize:11.5,fontFamily:'Space Grotesk',
        fontWeight:600,formatter:p=>sgn(p.value)},
      animationDuration:900,animationEasing:'cubicOut',animationDelay:i=>i*45}]});
}

/* ---------- bench comparison rows (pre-kickoff snapshot, frozen) ---------- */
function bench(){
  const mine=Object.fromEntries(D.summary.map(r=>[r.team,r]));
  const pre=Object.fromEntries((D.pre||D.summary).map(r=>[r.team,r]));
  const rows=[{src:t('srcMine'),mine:true,es:pre.Spain.p_champion*100,de:pre.Germany.p_champion*100}];
  for(const b of D.benchmarks){
    rows.push({src:LANG==='en'?(BSRC_EN[b.source]||b.source):b.source,es:b.probs?b.probs.Spain:null,de:b.probs?b.probs.Germany:null});
  }
  rows.push({src:t('srcSharp'),es:mine.Spain.p_market_sharp*100,de:mine.Germany.p_market_sharp*100});
  const SC=25; // shared scale max %
  document.getElementById('bench').innerHTML=rows.map(r=>`
    <div class="brow${r.mine?' mine':''}">
      <div class="bsrc">${r.src}</div>
      <div>
        <div class="bb es"><span class="bl">${t('lblSpain')}</span><span class="tr"><i data-w="${r.es!=null?Math.min(100,r.es/SC*100).toFixed(1):0}"></i></span><b data-flick="${r.es!=null?(+r.es).toFixed(1)+'%':'—'}">${r.es!=null?(+r.es).toFixed(1)+'%':'—'}</b></div>
        <div class="bb de${r.de!=null&&r.de>8?' hot':''}"><span class="bl">${t('lblGermany')}</span><span class="tr"><i data-w="${r.de!=null?Math.min(100,r.de/SC*100).toFixed(1):0}"></i></span><b data-flick="${r.de!=null?(+r.de).toFixed(1)+'%':'—'}">${r.de!=null?(+r.de).toFixed(1)+'%':'—'}</b></div>
      </div>
    </div>`).join('');
  document.getElementById('benchNote').textContent=t('benchNote');
}

/* ---------- groups ---------- */
function groupsSec(){
  document.getElementById('groups').innerHTML=Object.keys(D.groups).sort().map(g=>`
    <div class="gcard"><div class="glabel" aria-hidden="true">${g}</div><h4>GROUP ${g}</h4>
      ${D.groups[g].map(t=>`
      <div class="gteam">${fimg(t.team,80)}
        <span class="nm">${nm(t.team)}${t.is_host?'<span class="host">★</span>':''}</span>
        <span class="pq" data-flick="${pct(t.p_r32,0)}">${pct(t.p_r32,0)}</span>
        <span class="bar"><i data-w="${(t.p_group_winner*100).toFixed(0)}"></i></span>
      </div>`).join('')}
    </div>`).join('');
}

/* ---------- matches ---------- */
function matchTools(){
  const fd=document.getElementById('fDate'),fg=document.getElementById('fGroup');
  [...new Set(D.matches.map(m=>m.date))].forEach(d=>fd.insertAdjacentHTML('beforeend',`<option>${d}</option>`));
  [...new Set(D.matches.map(m=>m.group))].sort().forEach(g=>fg.insertAdjacentHTML('beforeend',`<option>${g}</option>`));
  ['fDate','fGroup','fState'].forEach(id=>document.getElementById(id).addEventListener('change',matches));
  const s=D.score_log||[];
  const el=document.getElementById('scoreSummary');
  if(!s.length){el.textContent=t('scoreNone')}
  else{
    const bm=s.reduce((a,x)=>a+x.brier_model,0)/s.length;
    const wm=s.filter(x=>x.brier_market!=null);
    const bk=wm.length?wm.reduce((a,x)=>a+x.brier_market,0)/wm.length:null;
    el.innerHTML=`${t('scored')} ${s.length} ${t('matchesUnit')} · ${t('modelBrier')} <b style="color:var(--gold)">${bm.toFixed(3)}</b>${bk!=null?` ${t('vsMarket')} ${bk.toFixed(3)}`:''}`;
  }
}
function matches(){
  const fd=document.getElementById('fDate').value,fg=document.getElementById('fGroup').value,fs=document.getElementById('fState').value;
  const list=D.matches.filter(m=>(!fd||m.date===fd)&&(!fg||m.group===fg)&&
    (!fs||(fs==='done'?m.score1!=null:m.score1==null)));
  document.getElementById('matchList').innerHTML=list.map((m,i)=>{
    const done=m.score1!=null;
    const wdl=m.p1!=null?`<div class="wdl" style="transition-delay:${(i%22)*30}ms" title="${t('pWinL')} ${pct(m.p1)} / ${t('pDraw')} ${pct(m.pd_)} / ${t('pWinR')} ${pct(m.p2)}">
      <i class="w" style="flex:${m.p1}">${m.p1>=.17?pct(m.p1,0):''}</i>
      <i class="d" style="flex:${m.pd_}">${m.pd_>=.17?pct(m.pd_,0):''}</i>
      <i class="l" style="flex:${m.p2}">${m.p2>=.17?pct(m.p2,0):''}</i></div>`:'<span class="mno">—</span>';
    const act=done?`<span class="chip res">${m.score1} : ${m.score2}</span>${m.brier!=null?`<span class="chip brier" title="${t('brierTip')} ${m.brier_mkt!=null?num(m.brier_mkt):'—'})">B ${num(m.brier)}</span>`:''}`
      :`<span class="chip todo">${m.date.slice(5)}</span>`;
    return `<div class="match">
      <span class="mno">#${m.match}<br>${m.group} ${t('grp')}</span>
      <div class="mt r"><span>${nm(m.team1)}${m.xg1!=null?`<span class="xg">xG ${num(m.xg1,2)}</span>`:''}</span>${fimg(m.team1,80)}</div>
      ${wdl}
      <div class="mt">${fimg(m.team2,80)}<span>${nm(m.team2)}${m.xg2!=null?`<span class="xg">xG ${num(m.xg2,2)}</span>`:''}</span></div>
      <div class="mact">${act}</div>
    </div>`;
  }).join('')||`<div style="color:var(--dim);padding:40px;text-align:center">${t('matchEmpty')}</div>`;
}

/* ---------- knockout bracket ---------- */
function bracket(){
  if(!D.ko||!D.ko.length)return;
  const bySlot={};D.ko.forEach(k=>bySlot[k.match]=k);
  const feeders=m=>[bySlot[m].src1,bySlot[m].src2]
    .map(s=>{const x=/^W(\d+)$/.exec(s);return x?+x[1]:null}).filter(x=>x);
  const fin=D.ko.find(k=>k.round==='FINAL'),third=D.ko.find(k=>k.round==='THIRD');
  const [sfL,sfR]=feeders(fin.match);
  const qfL=feeders(sfL),qfR=feeders(sfR);
  const r16L=qfL.flatMap(feeders),r16R=qfR.flatMap(feeders);
  const r32L=r16L.flatMap(feeders),r32R=r16R.flatMap(feeders);
  const row=(k,tm,src,sc,p,win)=>`<div class="bk-team${win===true?' win':''}${win===false?' out':''}">
      ${tm?fimg(tm,80):''}<span class="bk-nm">${tm?nm(tm):`<span class="bk-tbd">${src}</span>`}</span>
      <span class="bk-val">${sc!=null?sc+(win&&k.score1===k.score2?`<i class="bk-p">${t('bkPens')}</i>`:'')
        :(p!=null?pct(p,0):'')}</span></div>`;
  const card=m=>{const k=bySlot[m];if(!k)return'';
    const done=k.winner!=null,p1=k.p1!=null?+k.p1:null;
    return `<div class="bk-match${done?' done':''}" title="#${k.match} · ${k.date||''} · ${(k.city||'')}">
      <span class="bk-date">${(k.date||'').slice(5)}</span>
      ${row(k,k.team1,k.src1,done?k.score1:null,p1,done?k.winner===k.team1:null)}
      ${row(k,k.team2,k.src2,done?k.score2:null,p1!=null?1-p1:null,done?k.winner===k.team2:null)}
      ${!done&&p1!=null?`<div class="bk-bar"><i data-w="${(p1*100).toFixed(0)}"></i></div>`:''}
    </div>`};
  const col=(ms,lbl,cls)=>`<div class="bk-col ${cls||''}"><h5>${lbl}</h5>${ms.map(card).join('')}</div>`;
  // current stage = earliest round that still has an undecided fixture
  const ORDER=['R32','R16','QF','SF','FINAL','THIRD'];
  let cur=null;
  for(const r of ORDER){if(D.ko.some(k=>k.round===r&&k.winner==null)){cur=r;break}}
  const cc=r=>cur===r?'cur':'';
  const chip=document.getElementById('koStage');
  if(chip){
    const lbl={R32:'bkR32',R16:'bkR16',QF:'bkQF',SF:'bkSF',THIRD:'bk3',FINAL:'bkF'}[cur];
    if(cur&&lbl){chip.innerHTML=`${t('koStagePrefix')} · <b>${t(lbl)}</b>`;chip.hidden=false}
    else{chip.hidden=true}
  }
  const el=document.getElementById('bracket');
  const champ=fin&&fin.winner?`<div class="bk-cup">🏆 ${nm(fin.winner)}</div>`:'';
  const finalCol=`<div class="bk-col bk-final${cc('FINAL')?' cur':''}"><h5>${t('bkF')}</h5>${champ}${card(fin.match)}
      <div class="bk-third"><h5>${t('bk3')}</h5>${card(third.match)}</div></div>`;
  if(innerWidth<1000){
    el.className='stack';
    el.innerHTML=[
      col([...r32L,...r32R],t('bkR32'),cc('R32')),col([...r16L,...r16R],t('bkR16'),cc('R16')),
      col([...qfL,...qfR],t('bkQF'),cc('QF')),col([sfL,sfR],t('bkSF'),cc('SF')),finalCol].join('');
  }else{
    el.className='';
    el.innerHTML=[
      col(r32L,t('bkR32'),cc('R32')),col(r16L,t('bkR16'),cc('R16')),col(qfL,t('bkQF'),cc('QF')),col([sfL],t('bkSF'),cc('SF')),
      finalCol,
      col([sfR],t('bkSF'),cc('SF')),col(qfR,t('bkQF'),cc('QF')),col(r16R,t('bkR16'),cc('R16')),col(r32R,t('bkR32'),cc('R32'))].join('');
  }
}

/* ---------- alt-data arena (fun; never in the model) ---------- */
const cityLabel=c=>{const m=/\(([^)]+)\)/.exec(c||'');return m?m[1]:(c||'')};
function renderAlt(){
  const box=document.getElementById('altCards');
  if(!box||!D.alt||!D.alt.matchups)return;
  const bestTxt={1:t('altFinC'),3:t('altFin3'),4:t('altFinS'),8:t('altFinQ'),16:t('altFinR')};
  const valF=v=>v>=1000?'€'+(v/1000).toFixed(2)+'B':'€'+v+'M';
  const GROUPS=[
    {h:t('altGCup'),rows:[
      {k:'gf',lab:t('altGF'),better:'max',f:v=>v},
      {k:'ga',lab:t('altGA'),better:'min',f:v=>v},
      {k:'gd',lab:t('altGD'),better:'max',f:v=>(v>0?'+':'')+v},
      {k:'shootouts',lab:t('altPK'),f:v=>v}]},
    {h:t('altGSquad'),rows:[
      {k:'height',lab:t('altHeight'),better:'max',f:v=>v.toFixed(1)+' '+t('altHeightU')},
      {k:'age',lab:t('altAge'),better:'min',f:v=>v.toFixed(1)+' '+t('altAgeU')},
      {k:'value',lab:t('altValue'),better:'max',f:valF}]},
    {h:t('altGNation'),rows:[
      {k:'titles',lab:t('altTitles'),better:'max',f:v=>v},
      {k:'best_rank',lab:t('altBest'),better:'min',f:v=>bestTxt[v]||v},
      {k:'fifa',lab:t('altFifa'),better:'min',f:v=>'#'+v}]},
    {h:t('altGCond'),rows:[
      {k:'climate_gap',lab:t('altClimate'),better:'min',hint:t('altClimateHint'),f:v=>(v>0?'+':'')+v+' °C'},
      {k:'flight_km',lab:t('altFlight'),better:'min',f:v=>Math.round(v).toLocaleString()+' '+t('altFlightU')}]},
  ];
  box.innerHTML=D.alt.matchups.map(mu=>{
    const A=mu.team1,B=mu.team2;
    const groups=GROUPS.map(g=>{
      const rows=g.rows.map(mt=>{
        const va=A[mt.k],vb=B[mt.k];let wa=false,wb=false;
        if(mt.better&&va!=null&&vb!=null&&va!==vb){const mx=mt.better==='max';wa=mx?va>vb:va<vb;wb=!wa}
        return `<div class="altrow">
          <span class="l val ${wa?'win':''}">${va!=null?mt.f(va):'—'}</span>
          <span class="m"${mt.hint?` title="${mt.hint}"`:''}>${mt.lab}</span>
          <span class="r val ${wb?'win':''}">${vb!=null?mt.f(vb):'—'}</span></div>`;
      }).join('');
      return `<div class="altgrp"><div class="altgh">${g.h}</div>${rows}</div>`;
    }).join('');
    const L=LANG;
    const fx=mu.key?`<div class="altfx">
      <div class="altfxh">🎯 ${t('altTactics')}<span class="fxsub">${t('altTacSub')}</span></div>
      <div class="fxblock"><div class="fxlab">${t('altCoach')}</div>
        <div class="fxline">${fimg(A.team,80)}<b>${A.coach[L]}</b> · ${A.coach_level[L]}</div>
        <div class="fxline">${fimg(B.team,80)}<b>${B.coach[L]}</b> · ${B.coach_level[L]}</div></div>
      <div class="fxblock"><div class="fxlab">${t('altStyle')}</div>
        <div class="fxline">${fimg(A.team,80)}${A.style[L]}</div>
        <div class="fxline">${fimg(B.team,80)}${B.style[L]}</div></div>
      <div class="fxkey"><span class="fxkk">${t('altKey')}</span>${mu.key[L]}</div></div>`:'';
    return `<div class="altcard">
      <div class="ah"><span class="side">${fimg(A.team,80)}${nm(A.team)}</span>
        <span class="side r">${fimg(B.team,80)}${nm(B.team)}</span></div>
      <div class="avenue">${t('altVenue')}: ${cityLabel(mu.city)} · ${mu.venue_temp}°C · ${(mu.date||'').slice(5)}</div>
      ${groups}${fx}</div>`;
  }).join('');
}
function altMap(){
  const el=document.getElementById('altMap');
  if(!el||!hasEC||typeof USGEO==='undefined'||!USGEO||!D.alt||!D.alt.venues)return;
  try{echarts.registerMap('USA48',USGEO)}catch(e){return}
  if(charts.altmap){echarts.dispose(el);charts.altmap=null}
  charts.altmap=echarts.init(el);
  const V=D.alt.venues, featured=V.filter(v=>v.featured), past=V.filter(v=>!v.featured);
  charts.altmap.setOption({backgroundColor:'transparent',
    geo:{map:'USA48',roam:false,left:'2%',right:'2%',top:'4%',bottom:'4%',
      itemStyle:{areaColor:'#0f1a2e',borderColor:'#28395c',borderWidth:.9},
      emphasis:{itemStyle:{areaColor:'#14233d'},label:{show:false}}},
    tooltip:{trigger:'item',backgroundColor:'#101a30',borderColor:'#2a3b5d',textStyle:{color:'#e8eef9'},
      formatter:p=>{const v=p.data&&p.data.v;if(!v)return p.name;
        return v.featured?`<b>${cityLabel(v.city)}</b><br>${nm(v.team1)} vs ${nm(v.team2)}<br>${(v.date||'').slice(5)} · ${v.venue_temp}°C`:cityLabel(v.city)}},
    series:[
      {type:'scatter',coordinateSystem:'geo',data:past.map(v=>({name:v.city,value:[v.lng,v.lat],v})),
        symbolSize:6,itemStyle:{color:'#43567c',opacity:.85}},
      {type:'effectScatter',coordinateSystem:'geo',showEffectOn:'render',zlevel:2,
        rippleEffect:{scale:3.4,brushType:'stroke'},data:featured.map(v=>({name:v.city,value:[v.lng,v.lat],v})),
        symbolSize:12,itemStyle:{color:'#f0c75e',shadowBlur:10,shadowColor:'rgba(240,199,94,.6)'},
        label:{show:true,position:'right',color:'#eef3fb',fontSize:11,formatter:p=>`${nm(p.data.v.team1)}–${nm(p.data.v.team2)}`,
          backgroundColor:'rgba(5,8,16,.62)',padding:[2,5],borderRadius:4}}]});
}

/* ---------- full table ---------- */
let sortKey='p_champion',sortAsc=false,tableSeen=false;
const finishName=code=>{
  const zh={champion:'冠军',runner_up:'亚军',third:'季军',fourth:'第四',sf:'四强',qf:'八强',r16:'16强',r32:'32强',group:'小组赛'};
  const en={champion:'Champions',runner_up:'Runners-up',third:'Third',fourth:'Fourth',sf:'SF',qf:'QF',r16:'R16',r32:'R32',group:'Group stage'};
  return (LANG==='zh'?zh:en)[code]||code;
};
const CELL={
  team:r=>`<td><span class="tf">${fimg(r.team,80)}${nm(r.team)}</span></td>`,
  finish:r=>`<td><span class="${r.finish==='champion'?'goldc':''}">${finishName(r.finish)}</span></td>`,
  elo:r=>`<td class="num">${r.elo!=null?Math.round(r.elo):'—'}</td>`,
  p_champion:r=>`<td class="num goldc"${tableSeen?'':' data-flick="'+pct(r.p_champion,2)+'"'}>${pct(r.p_champion,2)}</td>`,
  p_champion_model:r=>`<td class="num dim">${r.p_champion_model!=null?pct(r.p_champion_model,2):'—'}</td>`,
  p_market_champ:r=>`<td class="num dim">${r.p_market_champ!=null?pct(r.p_market_champ,2):'—'}</td>`,
  p_final:r=>`<td class="num">${pct(r.p_final)}</td>`,p_sf:r=>`<td class="num">${pct(r.p_sf)}</td>`,
  p_qf:r=>`<td class="num">${pct(r.p_qf)}</td>`,p_r16:r=>`<td class="num">${pct(r.p_r16)}</td>`,
  p_r32:r=>`<td class="num">${pct(r.p_r32)}</td>`,
  decimal_odds_sharp:r=>`<td class="num">${num(r.decimal_odds_sharp)}</td>`,
  p_market_sharp:r=>`<td class="num">${pct(r.p_market_sharp,2)}</td>`,
  edge_sharp_pp:r=>`<td class="num ${r.edge_sharp_pp>0?'pos':'neg'}">${sgn(r.edge_sharp_pp)}</td>`,
  ev_sharp:r=>`<td class="num ${r.ev_sharp>0?'pos':'neg'}">${sgn(r.ev_sharp*100)}%</td>`};
// knockout stage: only teams still in, and drop the pre-tournament odds/edge/EV + trivial (=100%) reached-round columns
const COLS=()=>D.tournament_done
  ? [['team',t('thTeam')],['finish',t('thFinish')],['p_champion',t('thPreChampion')],['p_market_sharp',t('thImp')],['edge_sharp_pp',t('thEdge')],['p_final',t('thFinal')],['p_sf',t('thSF')]]
  : D.group_stage_done
  ? (D.final
    ? [['team',t('thTeam')],['elo','ELO'],['p_champion',t('thChampion')],['p_champion_model',t('thModel')],['p_market_champ',t('thMktNow')]]
    : [['team',t('thTeam')],['elo','ELO'],['p_champion',t('thChampion')],['p_champion_model',t('thModel')],['p_market_champ',t('thMktNow')],['p_final',t('thFinal')],['p_sf',t('thSF')]])
  : [['team',t('thTeam')],['elo','ELO'],['p_champion',t('thChampion')],['p_champion_model',t('thModel')],['p_market_champ',t('thMktNow')],['p_final',t('thFinal')],['p_sf',t('thSF')],
     ['p_qf',t('thQF')],['p_r16',t('thR16')],['p_r32',t('thR32')],['decimal_odds_sharp',t('thOdds')],['p_market_sharp',t('thImp')],['edge_sharp_pp',t('thEdge')],['ev_sharp',t('thEV')]];
function table(){
  const cols=COLS();
  const src=D.tournament_done?D.pre:(D.group_stage_done?D.summary.filter(r=>r.p_champion>0):D.summary);
  const rows=[...src].sort((a,b)=>{
    const x=a[sortKey],y=b[sortKey];
    if(x==null)return 1;if(y==null)return -1;
    return (x<y?-1:x>y?1:0)*(sortAsc?1:-1)});
  document.getElementById('fullTable').innerHTML=
    `<tr>${cols.map(([k,l])=>`<th data-k="${k}">${l}${sortKey===k?(sortAsc?' ▲':' ▼'):''}</th>`).join('')}</tr>`+
    rows.map(r=>`<tr>${cols.map(([k])=>CELL[k](r)).join('')}</tr>`).join('');
  document.querySelectorAll('#fullTable th').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k;
    if(sortKey===k)sortAsc=!sortAsc;else{sortKey=k;sortAsc=false}
    tableSeen=true;table()});
}

/* ---------- reveal ---------- */
function reveals(){
  const els=document.querySelectorAll('.reveal');
  els.forEach(el=>{if(el.getBoundingClientRect().top<innerHeight)el.classList.add('in')});
  const obs=new IntersectionObserver(es=>es.forEach(e=>{
    if(e.isIntersecting){e.target.classList.add('in');obs.unobserve(e.target)}}),
    {threshold:0,rootMargin:'0px 0px -8% 0px'});
  els.forEach(el=>obs.observe(el));
  setTimeout(()=>els.forEach(el=>el.classList.add('in')),2000);   // failsafe
}

/* ---------- lang toggle ---------- */
function langRefresh(){
  applyStatic();renderBadges();buildHeroSide();setHero(heroIdx);renderHeroStats();
  podium();tournamentReview();finalAnalysis();try{penaltyAnalysis()}catch(e){}bench();groupsSec();try{bracket()}catch(e){}matches();table();renderAlt();
  try{paulLang()}catch(e){}
  // strip one-shot effects instantly after re-render
  document.querySelectorAll('[data-flick]').forEach(el=>{el.textContent=el.dataset.flick;el.removeAttribute('data-flick')});
  growAll(document.body);document.body.classList.add('m-seen');
  if(charts.edge){echarts.dispose(document.getElementById('chEdge'));charts.edge=null;try{edge()}catch(e){}}
  if(charts.terrain){echarts.dispose(document.getElementById('terrain'));charts.terrain=null;try{terrain(true)}catch(e){}}
  if(charts.globe){echarts.dispose(document.getElementById('globe'));charts.globe=null;try{globe()}catch(e){}}
  if(charts.altmap){try{altMap()}catch(e){}}
}
document.getElementById('langBtn').addEventListener('click',e=>{
  e.preventDefault();
  LANG=LANG==='zh'?'en':'zh';
  try{localStorage.setItem('wc26lang',LANG)}catch(err){}
  langRefresh();
});

/* ---------- boot ---------- */
// knockout focus: body.ko (set server-side) hides the pre-tournament/group sections;
// renumber the sections that remain visible so they read 01,02,03... not 02,06,08
const penaltySec=document.getElementById('penalty-sec');
const penaltyData=D.final&&D.final.penalties;
if(penaltySec&&!(penaltyData&&penaltyData.teams&&
  penaltyData.teams[D.final.team1]&&penaltyData.teams[D.final.team2]))penaltySec.remove();
if(document.body.classList.contains('ko')){
  let n=0;
  document.querySelectorAll('.sec').forEach(s=>{
    if(s.offsetParent===null)return;                 // display:none -> skip
    const nn=('0'+(++n)).slice(-2);
    const no=s.querySelector('.sec-head .sec-no');if(no)no.textContent=nn;
    const gh=s.querySelector('.ghost');if(gh)gh.textContent=nn;
  });
}
applyStatic();heroInit();stars();podium();tournamentReview();finalAnalysis();try{penaltyAnalysis()}catch(e){}bench();groupsSec();try{bracket()}catch(e){}matchTools();matches();table();renderAlt();reveals();
try{paulInit()}catch(e){}
onSee('edge-sec',el=>{try{edge()}catch(e){} flickAll(el);growAll(el)},'0px 0px 200px 0px');
onSee('podium-sec',el=>flickAll(el));
onSee('final-analysis-sec',el=>{flickAll(el);growAll(el)});
onSee('penalty-sec',el=>{flickAll(el);growAll(el)});
onSee('groups-sec',el=>{flickAll(el);growAll(el)});
onSee('bracket-sec',el=>{flickAll(el);growAll(el)});
onSee('alt-sec',el=>{try{altMap()}catch(e){} flickAll(el);growAll(el)},'0px 0px 150px 0px');
onSee('matches-sec',()=>document.body.classList.add('m-seen'));
onSee('table-sec',el=>{flickAll(el);tableSeen=true});
addEventListener('resize',()=>Object.values(charts).forEach(c=>c&&c.resize()));
if(hasEC&&hasGL){
  const s=document.createElement('script');
  s.src='vendor/echarts-gl.min.js';
  s.onload=()=>{
    onSee('terrain-sec',()=>{try{terrain()}catch(e){terrainFallback(D.summary.slice(0,24))}},'0px 0px -20% 0px');
    onSee('globe-sec',()=>{try{globe()}catch(e){hideGlobe()}},'400px');
  };
  s.onerror=()=>{terrainFallback(D.summary.slice(0,24));hideGlobe()};
  document.head.appendChild(s);
}else{
  terrainFallback(D.summary.slice(0,24));
  hideGlobe();
}
</script>
</body>
</html>
"""


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    print("ensuring self-hosted assets ...")
    font_css = ensure_assets()
    payload = build_payload()
    with open(os.path.join(OUT, "assets", "earth-night.jpg"), "rb") as f:
        earth_b64 = base64.b64encode(f.read()).decode()
    ko = bool(payload.get("group_stage_done"))
    done = bool(payload.get("tournament_done"))
    fold = "" if ko else "open"   # collapsed once group stage is done
    usgeo_path = os.path.join(DATA, "us-states.geo.json")
    usgeo = open(usgeo_path, encoding="utf-8").read() if os.path.exists(usgeo_path) else "null"
    html = (TEMPLATE
            .replace("__BODYCLS__", "ko done" if done else ("ko" if ko else ""))
            .replace("__GROUPS_OPEN__", fold)
            .replace("__MATCHES_OPEN__", fold)
            .replace("__USGEO__", usgeo)
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__FLAGS__", json.dumps(ISO, ensure_ascii=False))
            .replace("__FONTCSS__", font_css)
            .replace("__EARTH__", earth_b64)
            .replace("__REPO__", REPO_URL or "https://github.com/"))
    out_path = os.path.join(OUT, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    size = os.path.getsize(out_path)
    n_assets = sum(len(files) for _, _, files in os.walk(OUT)) - 2
    print(f"built {out_path} ({size/1024:.0f} KB + {n_assets} self-hosted assets, "
          f"snapshot {payload['built_at']}, results={payload['results_count']})")
    if not REPO_URL:
        print("NOTE: WC26_REPO_URL not set — GitHub link hidden. "
              "Set it and rebuild once the public repo exists.")


if __name__ == "__main__":
    main()
