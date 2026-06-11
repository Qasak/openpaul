"""Tournament structure simulation for the 48-team / 104-match 2026 format.

12 groups (A-L) of 4; top 2 of each group + 8 best third-placed teams advance
to a Round of 32, then single-elimination to the final.

Group tiebreakers (FIFA 2026 regulations — NOTE: changed vs earlier World
Cups): points, then among tied teams head-to-head points, head-to-head GD,
head-to-head goals, then OVERALL goal difference, overall goals scored, then
fair play (not modelled — documented limitation), then FIFA ranking (no
drawing of lots at this edition).

Third-place allocation: FIFA's official allocation table maps each
combination of qualified third-place groups to fixed bracket slots. We
implement it as constraint matching: every third-place R32 slot carries the
set of groups it may legally receive; we assign the 8 ranked thirds to slots
by backtracking (slots in match order, candidates in ranking order), which
respects all constraints and is deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------- group stage

def group_table(teams: list[str], results: list[tuple[str, str, int, int]],
                fifa_rank: dict[str, int],
                order_rule: str = "h2h_first") -> tuple[list[str], dict, dict, dict]:
    """Rank 4 teams given the 6 group results. Returns (names 1st..4th, pts, gd, gf).

    order_rule="h2h_first" (2026 rules): points; then among teams level on
    points: head-to-head points, h2h GD, h2h goals; then overall GD, overall
    goals; (fair play skipped — not modelled); then FIFA ranking.
    order_rule="overall_first" (2018/2022 rules, used for backtests): points,
    overall GD, overall goals, then head-to-head, then FIFA ranking proxy.
    Deterministic either way.
    """
    pts = {t: 0 for t in teams}
    gd = {t: 0 for t in teams}
    gf = {t: 0 for t in teams}
    for t1, t2, s1, s2 in results:
        gd[t1] += s1 - s2; gd[t2] += s2 - s1
        gf[t1] += s1; gf[t2] += s2
        if s1 > s2:
            pts[t1] += 3
        elif s2 > s1:
            pts[t2] += 3
        else:
            pts[t1] += 1; pts[t2] += 1

    if order_rule == "h2h_first":
        def pre_key(t):
            return (pts[t],)
    else:
        def pre_key(t):
            return (pts[t], gd[t], gf[t])

    order = sorted(teams, key=pre_key, reverse=True)
    ranked: list[str] = []
    i = 0
    while i < len(order):
        cluster = [t for t in order if pre_key(t) == pre_key(order[i])]
        if len(cluster) > 1:
            sub = [r for r in results if r[0] in cluster and r[1] in cluster]
            h2h_pts = {t: 0 for t in cluster}
            h2h_gd = {t: 0 for t in cluster}
            h2h_gf = {t: 0 for t in cluster}
            for t1, t2, s1, s2 in sub:
                h2h_gd[t1] += s1 - s2; h2h_gd[t2] += s2 - s1
                h2h_gf[t1] += s1; h2h_gf[t2] += s2
                if s1 > s2:
                    h2h_pts[t1] += 3
                elif s2 > s1:
                    h2h_pts[t2] += 3
                else:
                    h2h_pts[t1] += 1; h2h_pts[t2] += 1
            if order_rule == "h2h_first":
                key = lambda t: (h2h_pts[t], h2h_gd[t], h2h_gf[t],
                                 gd[t], gf[t], -fifa_rank.get(t, 999))
            else:
                key = lambda t: (h2h_pts[t], h2h_gd[t], h2h_gf[t],
                                 -fifa_rank.get(t, 999))
            cluster.sort(key=key, reverse=True)
        ranked.extend(cluster)
        i += len(cluster)
    return ranked, pts, gd, gf


def rank_thirds(third_stats: list[tuple[str, str, int, int, int]],
                fifa_rank: dict[str, int]) -> list[str]:
    """Rank the 12 third-placed teams; input rows (group, team, pts, gd, gf).
    Criteria: points, GD, goals, (fair play skipped), FIFA ranking.
    Returns the groups (letters) of the 8 qualified thirds, best first."""
    rows = sorted(third_stats,
                  key=lambda r: (r[2], r[3], r[4], -fifa_rank.get(r[1], 999)),
                  reverse=True)
    return [r[0] for r in rows[:8]]


# ------------------------------------------------------------------- bracket

SOURCE_RE = {
    "winner_group": re.compile(r"winner.*group\s+([A-L])", re.I),
    "runner_up": re.compile(r"runners?[- ]?up.*group\s+([A-L])", re.I),
    "third": re.compile(r"(?:3rd|third).*?((?:[A-L]\s*/\s*)+[A-L])", re.I),
    "winner_match": re.compile(r"winner.*match\s+(\d+)", re.I),
    "loser_match": re.compile(r"loser.*match\s+(\d+)", re.I),
}


def parse_source(text: str):
    """'Winner Group E' -> ('W','E'); 'Runner-up Group A' -> ('R','A');
    '3rd Group C/D/F/G' -> ('T', frozenset('CDFG'));
    'Winner Match 74' -> ('M', 74); 'Loser Match 101' -> ('L', 101)."""
    m = SOURCE_RE["winner_match"].search(text)
    if m:
        return ("M", int(m.group(1)))
    m = SOURCE_RE["loser_match"].search(text)
    if m:
        return ("L", int(m.group(1)))
    m = SOURCE_RE["third"].search(text)
    if m:
        return ("T", frozenset(g.strip().upper() for g in m.group(1).split("/")))
    m = SOURCE_RE["winner_group"].search(text)
    if m:
        return ("W", m.group(1).upper())
    m = SOURCE_RE["runner_up"].search(text)
    if m:
        return ("R", m.group(1).upper())
    raise ValueError(f"unparseable bracket source: {text!r}")


def allocate_thirds(qualified_groups: list[str],
                    third_slots: list[tuple[int, frozenset]]
                    ) -> tuple[dict[int, str], bool]:
    """Assign qualified third-place groups (ranked best-first) to R32 slots.

    third_slots: [(match_no, allowed_group_set)], processed in match order.
    Backtracking guarantees a legal perfect matching if one exists (verified
    exhaustively for all 495 C(12,8) combinations in the test suite).
    Returns ({match_no: group_letter}, used_fallback).
    """
    slots = sorted(third_slots)
    assignment: dict[int, str] = {}

    def bt(i: int, remaining: list[str]) -> bool:
        if i == len(slots):
            return True
        match_no, allowed = slots[i]
        for g in remaining:                       # ranking order preference
            if g in allowed:
                assignment[match_no] = g
                if bt(i + 1, [x for x in remaining if x != g]):
                    return True
                del assignment[match_no]
        return False

    if bt(0, list(qualified_groups)):
        return assignment, False
    # fall back: ignore constraints rather than crash (counted by caller)
    return {s[0]: g for s, g in zip(slots, qualified_groups)}, True


@dataclass
class Bracket:
    """Knockout bracket as parsed slot definitions, in match-number order."""
    slots: list[dict] = field(default_factory=list)  # {match, round, home, away, country}

    @classmethod
    def from_data(cls, rounds_data: list[dict], city_country: dict[str, str]):
        b = cls()
        for rnd in rounds_data:
            for s in rnd["slots"]:
                b.slots.append({
                    "match": int(s["match"]),
                    "round": rnd["round"],
                    "home": parse_source(s["home_source"]),
                    "away": parse_source(s["away_source"]),
                    "country": city_country.get(
                        (s.get("city") or "").split(" (")[0].strip(), None),
                })
        b.slots.sort(key=lambda s: s["match"])
        return b

    def third_slots(self) -> list[tuple[int, frozenset]]:
        out = []
        for s in self.slots:
            for side in ("home", "away"):
                if s[side][0] == "T":
                    out.append((s["match"], s[side][1]))
        return out
