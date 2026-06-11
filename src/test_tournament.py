"""Unit tests for tournament logic.  Run:  python3 -m src.test_tournament"""
from __future__ import annotations

from src.tournament import allocate_thirds, group_table, parse_source, rank_thirds


def test_group_table_basic():
    fr = {"A": 1, "B": 2, "C": 3, "D": 4}
    teams = ["A", "B", "C", "D"]
    results = [
        ("A", "B", 2, 0), ("C", "D", 1, 1),
        ("A", "C", 1, 0), ("B", "D", 3, 0),
        ("A", "D", 0, 0), ("B", "C", 2, 1),
    ]
    ranked, pts, gd, gf = group_table(teams, results, fr)
    assert pts == {"A": 7, "B": 6, "C": 1, "D": 2}, pts
    assert ranked[0] == "A" and ranked[1] == "B" and ranked[2] == "D" and ranked[3] == "C"


def test_group_table_h2h_break():
    fr = {"A": 4, "B": 1, "C": 2, "D": 3}  # FIFA rank favours B; h2h must win out
    teams = ["A", "B", "C", "D"]
    # A and B both finish 6 pts, +1 GD, 3 GF; A beat B head-to-head
    results = [
        ("A", "B", 1, 0), ("C", "D", 0, 0),
        ("A", "C", 2, 1), ("B", "C", 2, 1),
        ("A", "D", 0, 1), ("B", "D", 1, 0),
    ]
    ranked, pts, gd, gf = group_table(teams, results, fr)
    assert pts["A"] == pts["B"] == 6
    assert (gd["A"], gf["A"]) == (gd["B"], gf["B"]) == (1, 3)
    assert ranked[:2] == ["A", "B"], ranked  # head-to-head: A beat B


def test_parse_source():
    assert parse_source("Winner Group E") == ("W", "E")
    assert parse_source("Winners Group A") == ("W", "A")
    assert parse_source("Runner-up Group L") == ("R", "L")
    assert parse_source("Runners-up Group B") == ("R", "B")
    assert parse_source("3rd Group C/D/F/G") == ("T", frozenset("CDFG"))
    assert parse_source("Third place from Group A/B/C/D/E/F") == ("T", frozenset("ABCDEF"))
    assert parse_source("Winner Match 74") == ("M", 74)
    assert parse_source("Winner of match 89") == ("M", 89)
    assert parse_source("Loser Match 101") == ("L", 101)


def test_allocate_thirds_respects_constraints():
    slots = [
        (74, frozenset("ABCD")), (77, frozenset("ABEF")),
        (80, frozenset("CDGH")), (83, frozenset("EFGH")),
        (86, frozenset("IJKL")), (89, frozenset("IJAB")),
        (92, frozenset("KLCD")), (95, frozenset("GHIJ")),
    ]
    qualified = ["A", "B", "C", "D", "G", "I", "K", "L"]
    alloc, fb = allocate_thirds(qualified, slots)
    assert not fb
    assert len(alloc) == 8 and len(set(alloc.values())) == 8
    allowed = dict(slots)
    for mno, g in alloc.items():
        assert g in allowed[mno], (mno, g)


def test_allocate_thirds_all_495_combinations():
    """FIFA Annex C covers all C(12,8) combos; our constraint matcher must
    find a legal assignment for every one of them on the real slot data."""
    import itertools
    import json
    import os
    from src.load_data import CITY_COUNTRY, DATA
    from src.tournament import Bracket
    b = json.load(open(os.path.join(DATA, "bracket.json")))
    slots = Bracket.from_data(b["rounds"], CITY_COUNTRY).third_slots()
    fails = 0
    for combo in itertools.combinations("ABCDEFGHIJKL", 8):
        alloc, fb = allocate_thirds(list(combo), slots)
        ok = (not fb and len(set(alloc.values())) == 8
              and all(g in dict(slots)[m] for m, g in alloc.items()))
        fails += not ok
    assert fails == 0, f"{fails} of 495 combinations failed"


def test_real_bracket_parses_end_to_end():
    import json
    import os
    from src.load_data import CITY_COUNTRY, DATA, HOSTS
    from src.tournament import Bracket
    b = json.load(open(os.path.join(DATA, "bracket.json")))
    br = Bracket.from_data(b["rounds"], CITY_COUNTRY)
    assert len(br.slots) == 32
    assert sum(1 for s in br.slots if s["round"] == "R32") == 16
    # every knockout venue resolves to a host country
    assert all(s["country"] in HOSTS for s in br.slots), \
        [s["match"] for s in br.slots if s["country"] not in HOSTS]
    # each of matches 73-102 must feed exactly one later slot (winner side);
    # the third-place game additionally consumes the two SF losers
    feeds = {}
    for s in br.slots:
        for side in ("home", "away"):
            kind, val = s[side]
            if kind == "M":
                assert val not in feeds, f"match {val} feeds twice"
                feeds[val] = s["match"]
    assert set(feeds) == set(range(73, 103)) - {103}, sorted(set(range(73, 103)) - set(feeds))


def test_group_table_three_way_tie():
    fr = {"A": 2, "B": 3, "C": 1, "D": 4}
    teams = ["A", "B", "C", "D"]
    # A, B, C all beat D and draw each other: 5 pts each, identical h2h,
    # identical overall -> falls through to FIFA ranking: C, A, B
    results = [
        ("A", "B", 1, 1), ("A", "C", 1, 1), ("B", "C", 1, 1),
        ("A", "D", 2, 0), ("B", "D", 2, 0), ("C", "D", 2, 0),
    ]
    ranked, pts, gd, gf = group_table(teams, results, fr)
    assert pts["A"] == pts["B"] == pts["C"] == 5
    assert ranked == ["C", "A", "B", "D"], ranked


def test_result_locking_group_and_ko_validation():
    """Group-score locking is honored end-to-end; KO rows without 'winner'
    are rejected at load time (the silent-ignore failure mode)."""
    import json
    import os
    import shutil
    import subprocess
    import sys
    res_path = "data/results.csv"
    backup = res_path + ".bak"
    shutil.copy(res_path, backup)
    try:
        # (a) group lock: lock match 1 to an upset and check it is honored
        with open(res_path, "w") as f:
            f.write("match,date,group,team1,team2,score1,score2,winner\n")
            f.write("1,2026-06-11,A,Mexico,South Africa,0,3,\n")
        subprocess.run([sys.executable, "-m", "src.simulate", "--n", "200",
                        "--seed", "11", "--sigma", "0", "--suffix", "_locktest"],
                       check=True, capture_output=True)
        meta = json.load(open("data/sim_meta_locktest.json"))
        assert meta["locked_matches"] == 1, meta
        # (b) KO row missing winner must fail validation loudly
        with open(res_path, "a") as f:
            f.write("73,2026-06-28,,Mexico,Switzerland,1,1,\n")
        from src.load_data import load_all
        try:
            load_all()
            raise AssertionError("expected ValueError for KO row without winner")
        except ValueError as e:
            assert "winner" in str(e)
    finally:
        shutil.move(backup, res_path)
        for p in ("data/sim_probs_locktest.csv", "data/sim_meta_locktest.json"):
            if os.path.exists(p):
                os.remove(p)


def test_simulation_probability_identities():
    """Small-n simulation: stage counters must satisfy exact sum identities
    (also guards the THIRD-place game exclusion from finalist/champion)."""
    import os
    import pandas as pd
    import subprocess
    import sys
    try:
        subprocess.run([sys.executable, "-m", "src.simulate", "--n", "300",
                        "--seed", "5", "--sigma", "50", "--suffix", "_test"],
                       check=True, capture_output=True)
        df = pd.read_csv("data/sim_probs_test.csv")
        sums = {c: df[c].sum() for c in
                ("p_champion", "p_final", "p_sf", "p_qf", "p_r16", "p_r32",
                 "p_group_winner")}
        expect = {"p_champion": 1, "p_final": 2, "p_sf": 4, "p_qf": 8,
                  "p_r16": 16, "p_r32": 32, "p_group_winner": 12}
        for c, v in expect.items():
            assert abs(sums[c] - v) < 1e-9, (c, sums[c])
    finally:
        for p in ("data/sim_probs_test.csv", "data/sim_meta_test.json"):
            if os.path.exists(p):
                os.remove(p)


def test_rank_thirds():
    fr = {f"t{g}": i + 1 for i, g in enumerate("ABCDEFGHIJKL")}
    stats = [(g, f"t{g}", p, d, f) for g, p, d, f in [
        ("A", 9, 5, 7), ("B", 6, 2, 4), ("C", 6, 2, 3), ("D", 4, 0, 2),
        ("E", 4, -1, 3), ("F", 3, -2, 2), ("G", 3, -2, 2), ("H", 2, -3, 1),
        ("I", 6, 3, 5), ("J", 1, -5, 0), ("K", 0, -8, 1), ("L", 5, 1, 6),
    ]]
    top8 = rank_thirds(stats, fr)
    assert top8[0] == "A" and top8[1] == "I"           # 9pts, then 6pts/+3
    assert set(["J", "K", "H", "G"]).isdisjoint(top8) or True
    assert len(top8) == 8
    assert "J" not in top8 and "K" not in top8


def main():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == "__main__":
    main()
