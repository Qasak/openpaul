"""Derive data/schedule_ko.csv from real results (rolling update, step 0).

R32 pairings follow from the final group tables — real results ranked with
the 2026 tiebreakers plus the Annex-C third-place allocation implemented in
src/tournament.py. Our allocation is a constraint-respecting BACKTRACKING
APPROXIMATION of FIFA's official lookup table, so for R32 fixtures without a
recorded result yet, the announced pairings on the ESPN feed are authority:
third-place slots are corrected to match them (loudly), and any disagreement
on a winner/runner-up slot — which IS exactly derivable — is a hard error.
(Reality check 2026-07-02: the approximation put Sweden/Paraguay and
Senegal/Algeria each in the other's slot; ESPN reconciliation caught both.)

Later rounds fill in automatically as knockout winners land in
data/results.csv (Winner/Loser Match N references — no allocation ambiguity
there). Only fixtures with both teams determined are written; dates and
cities come from data/bracket.json.

ingest_results.py reads schedule_ko.csv to recognize finished knockout
matches, so daily_update.sh regenerates this file before every ingest —
after each round completes, the next round's fixtures appear on their own
and the chain stays automatic through the final.

Safety: refuses to change the teams of any fixture that already has a
recorded result (that would mean the bracket derivation and reality
disagree — a data problem that must be looked at, never papered over).

Usage:  python3 -m src.build_schedule_ko
"""
from __future__ import annotations

import csv
import os
import sys

from .load_data import CITY_COUNTRY, DATA, load_all
from .tournament import Bracket, allocate_thirds, group_table, parse_source, rank_thirds

OUT = os.path.join(DATA, "schedule_ko.csv")
COLS = ["match", "date", "group", "team1", "team2", "city", "country"]


def derive_fixtures(data: dict) -> tuple[list[dict], dict[str, str]]:
    """All knockout fixtures whose two teams are already determined.

    Returns (fixtures, third_team). Each fixture carries an internal
    "_t_side" key (0/1/None): which side is a third-place slot — the only
    kind our derivation cannot pin down exactly (see module docstring)."""
    results = data["results"]
    fifa_rank = dict(zip(data["fifa"]["team"], data["fifa"]["rank"]))

    group_res = results[results["match"] <= 72]
    if len(group_res) < 72:
        print(f"group stage incomplete ({len(group_res)}/72 results) — "
              f"knockout pairings not derivable yet")
        return [], {}

    group_teams = data["teams"].groupby("group")["team"].apply(list).to_dict()
    winners, runners, third_team, thirds_stats = {}, {}, {}, []
    for grp, tlist in group_teams.items():
        rows = group_res[group_res["group"] == grp]
        if len(rows) != 6:
            raise ValueError(f"group {grp}: expected 6 results, got {len(rows)}")
        res = [(r["team1"], r["team2"], int(r["score1"]), int(r["score2"]))
               for _, r in rows.iterrows()]
        ranked, pts, gd, gf = group_table(tlist, res, fifa_rank)
        winners[grp], runners[grp] = ranked[0], ranked[1]
        third = ranked[2]
        third_team[grp] = third
        thirds_stats.append((grp, third, pts[third], gd[third], gf[third]))

    qualified = rank_thirds(thirds_stats, fifa_rank)
    bracket = Bracket.from_data(data["bracket"]["rounds"], CITY_COUNTRY)
    alloc, used_fallback = allocate_thirds(qualified, bracket.third_slots())
    if used_fallback:
        raise ValueError(
            "third-place allocation hit the unconstrained fallback for the real "
            f"qualified groups {qualified} — the Annex-C constraint set needs review")

    ko_winner = {}      # match no -> advancing team (real)
    recorded_pair = {}  # match no -> (team1, team2) as recorded (pair-checked at ingest)
    for _, r in results[results["match"] >= 73].iterrows():
        ko_winner[int(r["match"])] = str(r["winner"])
        recorded_pair[int(r["match"])] = (str(r["team1"]), str(r["team2"]))

    match_teams: dict[int, tuple[str, str]] = {}
    fixtures = []
    for rnd in data["bracket"]["rounds"]:
        for slot in rnd["slots"]:
            match = int(slot["match"])
            sides, t_side = [], None
            for i, key in enumerate(("home_source", "away_source")):
                kind, val = parse_source(slot[key])
                if kind == "W":
                    sides.append(winners[val])
                elif kind == "R":
                    sides.append(runners[val])
                elif kind == "T":
                    sides.append(third_team[alloc[match]])
                    t_side = i
                elif kind in ("M", "L") and val in ko_winner and val in match_teams:
                    w = ko_winner[val]
                    t1, t2 = match_teams[val]
                    if w not in (t1, t2):
                        raise ValueError(
                            f"match {val}: recorded winner {w!r} is neither "
                            f"{t1!r} nor {t2!r} — results vs bracket mismatch")
                    sides.append(w if kind == "M" else (t2 if w == t1 else t1))
                else:
                    sides.append(None)
            rec = recorded_pair.get(match)
            if rec is not None:
                # a recorded result is authority for the pairing; the exactly
                # derivable (non-third) side must still agree with derivation
                for i in (0, 1):
                    if i != t_side and sides[i] is not None and sides[i] != rec[i]:
                        raise ValueError(
                            f"match {match}: recorded as {rec[0]} vs {rec[1]} "
                            f"but bracket derives {sides[i]!r} on side {i + 1} "
                            f"— results vs bracket mismatch")
                sides = list(rec)
            if None not in sides:
                match_teams[match] = (sides[0], sides[1])
            city = str(slot.get("city") or "")
            fixtures.append({
                "match": match, "date": slot["date"], "group": rnd["round"],
                "team1": sides[0], "team2": sides[1], "city": city,
                "country": CITY_COUNTRY.get(city.split(" (")[0].strip(), ""),
                "_t_side": t_side,
            })
    return [f for f in fixtures if f["team1"] and f["team2"]], third_team


def reconcile_with_espn(fixtures: list[dict], third_team: dict[str, str],
                        data: dict) -> bool:
    """Correct third-place slots of result-less R32 fixtures against the
    announced pairings on the ESPN feed. Returns False on unfixable
    disagreement (winner/runner-up slot wrong, duplicate third, ...).

    Skipped silently once every R32 fixture has a recorded result; keeps the
    derivation (with a warning) when the feed is unreachable — the pair
    lookup in ingest_results still blocks any wrong pairing from being
    recorded."""
    from .ingest_results import parse_espn, resolve

    results = data["results"]
    recorded = set(int(m) for m in results[results["match"] >= 73]["match"])
    pending = [f for f in fixtures if f["group"] == "R32"
               and f["match"] not in recorded]
    if not pending:
        return True

    events = []
    try:
        for d in sorted({f["date"] for f in pending}):
            events.extend(parse_espn(d.replace("-", "")))
    except Exception as e:
        print(f"WARNING: ESPN unreachable ({e}) — R32 third-place slots kept "
              f"from Annex-C approximation, unconfirmed", file=sys.stderr)
        return True

    universe = set(data["teams"]["team"])
    opp: dict[str, str] = {}
    for ev in events:
        th = resolve(ev["home"], universe, set())
        ta = resolve(ev["away"], universe, set())
        if th and ta:
            opp[th] = ta
            opp[ta] = th

    thirds = set(third_team.values())
    ok = True
    for f in pending:
        if f["_t_side"] is None:
            for side in ("team1", "team2"):
                other = "team2" if side == "team1" else "team1"
                real = opp.get(f[side])
                if real is not None and real != f[other]:
                    print(f"ERROR: match {f['match']} derived as {f['team1']} "
                          f"vs {f['team2']} but source says {f[side]} vs "
                          f"{real} — non-third slot, not correctable",
                          file=sys.stderr)
                    ok = False
            continue
        t_key = "team1" if f["_t_side"] == 0 else "team2"
        a_key = "team2" if f["_t_side"] == 0 else "team1"
        real = opp.get(f[a_key])
        if real is None:
            print(f"NOTE: match {f['match']} {f['team1']} vs {f['team2']} not "
                  f"on the feed yet — third slot unconfirmed")
            continue
        if real == f[t_key]:
            continue
        if real not in thirds:
            print(f"ERROR: match {f['match']}: source pairs {f[a_key]} with "
                  f"{real}, which is not a third-placed team "
                  f"({sorted(thirds)})", file=sys.stderr)
            ok = False
            continue
        print(f"CORRECTED match {f['match']}: third-place slot "
              f"{f[t_key]} -> {real} (announced pairing beats Annex-C "
              f"approximation)")
        f[t_key] = real

    seen: dict[str, int] = {}
    for f in fixtures:
        if f["group"] == "R32" and f["_t_side"] is not None:
            t = f["team1"] if f["_t_side"] == 0 else f["team2"]
            if t in seen:
                print(f"ERROR: third-placed {t} assigned to both match "
                      f"{seen[t]} and match {f['match']}", file=sys.stderr)
                ok = False
            seen[t] = f["match"]
    return ok


def main() -> int:
    data = load_all()
    fixtures, third_team = derive_fixtures(data)
    if not fixtures:
        return 0
    if not reconcile_with_espn(fixtures, third_team, data):
        return 1

    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = {int(r["match"]): r for r in csv.DictReader(f)}
        recorded = set(int(r["match"]) for _, r in
                       data["results"][data["results"]["match"] >= 73].iterrows())
        new_by_match = {f["match"]: f for f in fixtures}
        for m, r in old.items():
            cur = new_by_match.get(m)
            pair_old = {r["team1"], r["team2"]}
            if m in recorded and (cur is None or
                                  {cur["team1"], cur["team2"]} != pair_old):
                print(f"ERROR: match {m} has a recorded result for "
                      f"{sorted(pair_old)} but the bracket now derives "
                      f"{sorted({cur['team1'], cur['team2']}) if cur else 'nothing'}"
                      f" — refusing to rewrite schedule_ko.csv", file=sys.stderr)
                return 1
    else:
        old = {}

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for fx in sorted(fixtures, key=lambda x: x["match"]):
            w.writerow({c: fx[c] for c in COLS})
    os.replace(tmp, OUT)

    added = [f["match"] for f in fixtures if f["match"] not in old]
    print(f"schedule_ko.csv: {len(fixtures)} fixture(s) with both teams known"
          + (f", new: {added}" if added else ""))
    for fx in fixtures:
        if fx["match"] in added:
            print(f"  match {fx['match']} ({fx['group']}, {fx['date']}): "
                  f"{fx['team1']} vs {fx['team2']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
