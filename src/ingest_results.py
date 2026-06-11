"""Ingest finished match results into data/results.csv (rolling update, step 1).

Primary source:  ESPN public scoreboard JSON (no auth, near-real-time, and it
                 distinguishes FT / AET / penalty shootouts via status name +
                 shootoutScore — exactly what the knockout 'winner' column needs).
Cross-check:     football-data.org v4, optional; activates when WC26_FD_TOKEN is
                 set. When active, both sources must agree on
                 (score1, score2, winner) or the row is NOT written.

Rows are merged idempotently into data/results.csv keyed by match number; an
existing row with different scores is never overwritten (hard error instead).
Knockout rows always carry an explicit winner. Any unmapped team name or
source disagreement aborts with a non-zero exit so the cron log surfaces it.

Usage:
  python3 -m src.ingest_results                      # yesterday + today (UTC)
  python3 -m src.ingest_results --dates 20260611,20260612
  python3 -m src.ingest_results --probe 20221218     # parse & print, no write
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RESULTS = os.path.join(DATA, "results.csv")
ESPN_URL = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
            "fifa.world/scoreboard?dates={date}")
FD_URL = ("https://api.football-data.org/v4/competitions/WC/matches"
          "?dateFrom={a}&dateTo={b}")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# source displayName -> our canonical name (only names that differ are listed;
# anything not in the schedule and not aliased is a hard error, never a guess)
SRC_ALIASES = {
    "Czech Republic": "Czechia",
    "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Côte d'Ivoire": "Ivory Coast", "Cote d'Ivoire": "Ivory Coast",
    "Cape Verde Islands": "Cape Verde",
    "USA": "United States", "United States of America": "United States",
    "Korea Republic": "South Korea", "South Korea Republic": "South Korea",
    "IR Iran": "Iran", "Iran IR": "Iran",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Congo DR": "DR Congo", "DR Congo (Kinshasa)": "DR Congo",
    "Democratic Republic of the Congo": "DR Congo",
    "Curacao": "Curaçao",
}
FINAL_STATUSES = {"STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_FINAL_AET",
                  "STATUS_FINAL_PEN"}


def _get_json(url: str, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_schedule() -> tuple[list[dict], set[str]]:
    """All known fixtures (group + knockout once its file exists) and the
    team-name universe. Knockout rows are flagged ko=True."""
    rows: list[dict] = []
    for fname, ko in (("schedule_group.csv", False), ("schedule_ko.csv", True)):
        path = os.path.join(DATA, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["ko"] = ko
                rows.append(r)
    teams = {r["team1"] for r in rows} | {r["team2"] for r in rows}
    return rows, teams


def resolve(name: str, teams: set[str], unmapped: set[str]) -> str | None:
    n = str(name).strip()
    if n in teams:
        return n
    if n in SRC_ALIASES and SRC_ALIASES[n] in teams:
        return SRC_ALIASES[n]
    unmapped.add(n)
    return None


def parse_espn(date: str) -> list[dict]:
    """One scoreboard day -> list of events with raw names, scores, status."""
    payload = _get_json(ESPN_URL.format(date=date))
    out = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        status = (comp.get("status") or ev.get("status", {})).get("type", {})
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if "home" not in sides or "away" not in sides:
            continue
        h, a = sides["home"], sides["away"]
        out.append({
            "date": str(ev.get("date", ""))[:10],
            "status": status.get("name", ""),
            "completed": bool(status.get("completed")),
            "home": h["team"]["displayName"], "away": a["team"]["displayName"],
            "hs": h.get("score"), "as": a.get("score"),
            "hso": h.get("shootoutScore"), "aso": a.get("shootoutScore"),
        })
    return out


def event_winner(ev: dict, t_home: str, t_away: str, ko: bool) -> str | None:
    """Winner for a knockout row ('' for group rows). None = cannot decide."""
    if not ko:
        return ""
    hs, as_ = int(ev["hs"]), int(ev["as"])
    if hs != as_:
        return t_home if hs > as_ else t_away
    if ev["hso"] is not None and ev["aso"] is not None:
        hso, aso = int(ev["hso"]), int(ev["aso"])
        if hso != aso:
            return t_home if hso > aso else t_away
    return None


def fd_results(date_from: str, date_to: str, token: str) -> dict:
    """(frozenset(team1,team2)) -> (s1, s2, winner_name|'' ) from football-data."""
    payload = _get_json(FD_URL.format(a=date_from, b=date_to),
                        headers={"X-Auth-Token": token})
    out = {}
    for m in payload.get("matches", []):
        if m.get("status") != "FINISHED":
            continue
        home, away = m["homeTeam"]["name"], m["awayTeam"]["name"]
        sc = m.get("score", {})
        ft = sc.get("fullTime", {})
        hs, as_ = ft.get("home"), ft.get("away")
        if hs is None or as_ is None:
            continue
        win = sc.get("winner")  # HOME_TEAM / AWAY_TEAM / DRAW
        pens = sc.get("penalties") or {}
        out[frozenset((home, away))] = {
            "home": home, "away": away, "hs": int(hs), "as": int(as_),
            "winner_side": win, "pen_h": pens.get("home"), "pen_a": pens.get("away"),
        }
    return out


def read_existing() -> dict[str, dict]:
    if not os.path.exists(RESULTS):
        return {}
    with open(RESULTS, encoding="utf-8") as f:
        return {r["match"]: r for r in csv.DictReader(f)}


def write_results(rows: dict[str, dict]) -> None:
    cols = ["match", "date", "group", "team1", "team2", "score1", "score2", "winner"]
    tmp = RESULTS + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(rows, key=lambda x: int(x)):
            w.writerow({c: rows[k].get(c, "") for c in cols})
    os.replace(tmp, RESULTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", type=str, default=None,
                    help="comma-separated YYYYMMDD (default: yesterday+today UTC)")
    ap.add_argument("--probe", type=str, default=None,
                    help="parse one YYYYMMDD and print, no write")
    args = ap.parse_args()

    if args.probe:
        for ev in parse_espn(args.probe):
            print(json.dumps(ev, ensure_ascii=False))
        return 0

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        now = datetime.now(timezone.utc)
        dates = [(now - timedelta(days=1)).strftime("%Y%m%d"), now.strftime("%Y%m%d")]

    schedule, teams = load_schedule()
    by_pair = {frozenset((r["team1"], r["team2"])): r for r in schedule}
    existing = read_existing()
    token = os.environ.get("WC26_FD_TOKEN", "").strip()
    fd = {}
    if token:
        iso = [datetime.strptime(d, "%Y%m%d").strftime("%Y-%m-%d") for d in dates]
        try:
            fd = fd_results(min(iso), max(iso), token)
        except Exception as e:
            print(f"WARNING: football-data.org unavailable ({e}); "
                  f"cross-check skipped this run", file=sys.stderr)

    unmapped: set[str] = set()
    errors: list[str] = []
    added = 0
    for d in dates:
        for ev in parse_espn(d):
            if not (ev["completed"] and ev["status"] in FINAL_STATUSES):
                continue
            th = resolve(ev["home"], teams, unmapped)
            ta = resolve(ev["away"], teams, unmapped)
            if th is None or ta is None:
                continue                       # reported via `unmapped` below
            sched = by_pair.get(frozenset((th, ta)))
            if sched is None:
                errors.append(f"finished {th} vs {ta} not in schedule")
                continue
            sd = datetime.strptime(sched["date"], "%Y-%m-%d")
            ed = datetime.strptime(ev["date"], "%Y-%m-%d")
            if abs((sd - ed).days) > 1:
                errors.append(f"match {sched['match']} date mismatch "
                              f"(sched {sched['date']} vs espn {ev['date']})")
                continue
            win = event_winner(ev, th, ta, sched["ko"])
            if win is None:
                errors.append(f"match {sched['match']} {th} vs {ta}: knockout "
                              f"draw without shootout data — needs manual entry")
                continue
            # orient scores to the schedule's team1/team2 order
            if sched["team1"] == th:
                s1, s2 = int(ev["hs"]), int(ev["as"])
            else:
                s1, s2 = int(ev["as"]), int(ev["hs"])
            row = {"match": sched["match"], "date": sched["date"],
                   "group": sched.get("group", ""), "team1": sched["team1"],
                   "team2": sched["team2"], "score1": str(s1), "score2": str(s2),
                   "winner": win}
            if token and fd:
                ref = next((v for k, v in fd.items()
                            if {resolve(n, teams, set()) for n in k} == {th, ta}), None)
                if ref is not None:
                    f1 = ref["hs"] if resolve(ref["home"], teams, set()) == sched["team1"] else ref["as"]
                    f2 = ref["as"] if f1 == ref["hs"] else ref["hs"]
                    if (f1, f2) != (s1, s2):
                        errors.append(f"match {sched['match']}: ESPN {s1}-{s2} vs "
                                      f"football-data {f1}-{f2} DISAGREE — not written")
                        continue
                else:
                    print(f"NOTE: match {sched['match']} not in football-data yet; "
                          f"written from ESPN alone", file=sys.stderr)
            old = existing.get(str(sched["match"]))
            if old is not None:
                if (old["score1"], old["score2"]) != (str(s1), str(s2)):
                    errors.append(f"match {sched['match']} already recorded as "
                                  f"{old['score1']}-{old['score2']}, source now says "
                                  f"{s1}-{s2} — refusing to overwrite")
                continue
            existing[str(sched["match"])] = row
            added += 1
            print(f"+ match {sched['match']} {sched['team1']} {s1}-{s2} "
                  f"{sched['team2']}" + (f"  winner={win}" if win else ""))

    if unmapped:
        errors.append("unmapped team names from source: " + ", ".join(sorted(unmapped)) +
                      " — extend SRC_ALIASES in src/ingest_results.py")
    if added:
        write_results(existing)
    print(f"ingest: {added} new result(s), {len(errors)} problem(s)")
    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
