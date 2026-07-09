"""Fun 'alternative data' for the knockout matchups (ENTERTAINMENT ONLY — never
enters the prediction model). Per quarter-final pairing we compare a bunch of
result-irrelevant quirky metrics, grouped on the site:

  This tournament (computed here from results.csv):
    gf/ga/gd  goals for / against / difference so far
    shootouts penalty shootouts survived
  Squad & body:
    height    average squad height (cm)   — givemesport.com 2026
    age       average squad age           — rotowire.com 2026
    value     total squad market value €M — transfermarkt via planetfootball 2026
  Nation & pedigree:
    titles    World Cup titles won
    best_rank best-ever WC finish (1=champ,3=3rd,4=SF,8=QF,16=R16) — pre-2026
    pop       country population (millions)
    gdp_pc    GDP per capita (k USD)
    fifa      FIFA world ranking (Jun 2026)
  Conditions:
    climate_gap  venue July temp − home July temp (°C); smaller = better adapted
    flight_km    km flown between match cities so far (great-circle from schedule)

Also emits the US venue list (lng/lat) for the match-city map. All clearly
labelled on the site as a fun sidebar; kept out of report_summary/sim/ledgers.
"""
from __future__ import annotations

import csv
import math
import os

DATA = os.path.dirname(os.path.abspath(__file__)).replace("src", "data")
QF_TEAMS = ["France", "Spain", "Argentina", "England",
            "Norway", "Belgium", "Switzerland", "Morocco"]

# (lat, lng) of every 2026 host venue, keyed by the prefix used in the schedule
COORDS = {
    "Foxborough": (42.09, -71.26), "Inglewood": (33.95, -118.34),
    "Miami Gardens": (25.96, -80.24), "Kansas City": (39.10, -94.58),
    "Atlanta": (33.76, -84.40), "Arlington": (32.75, -97.08),
    "East Rutherford": (40.81, -74.08), "Philadelphia": (39.90, -75.17),
    "Santa Clara": (37.40, -121.97), "Seattle": (47.59, -122.33),
    "Houston": (29.68, -95.41), "Zapopan": (20.72, -103.42),
    "Guadalupe": (25.68, -100.26), "Mexico City": (19.30, -99.15),
    "Toronto": (43.63, -79.42), "Vancouver": (49.28, -123.11),
}
US_VENUES = {"Foxborough", "Inglewood", "Miami Gardens", "Kansas City", "Atlanta",
             "Arlington", "East Rutherford", "Philadelphia", "Santa Clara",
             "Seattle", "Houston"}

# ---- sourced constants (fun sidebar, hand-maintained) ----
HEIGHT = {"France": 184.9, "Spain": 181.7, "Argentina": 179.7, "England": 184.2,
          "Norway": 187.2, "Belgium": 185.8, "Switzerland": 185.2, "Morocco": 182.7}
AGE = {"France": 26.58, "Spain": 26.19, "Argentina": 28.62, "England": 26.62,
       "Norway": 26.35, "Belgium": 27.12, "Switzerland": 27.81, "Morocco": 25.92}
VALUE = {"France": 1520, "England": 1360, "Spain": 1220, "Argentina": 807,   # €M
         "Norway": 590, "Belgium": 548, "Morocco": 448, "Switzerland": 333}
TITLES = {"Argentina": 3, "France": 2, "England": 1, "Spain": 1,
          "Norway": 0, "Belgium": 0, "Switzerland": 0, "Morocco": 0}
BEST_RANK = {"France": 1, "Spain": 1, "Argentina": 1, "England": 1,           # pre-2026 best
             "Belgium": 3, "Morocco": 4, "Switzerland": 8, "Norway": 16}
POP = {"France": 68.4, "Spain": 48.4, "Argentina": 46.0, "England": 57.1,     # millions
       "Norway": 5.5, "Belgium": 11.8, "Switzerland": 8.9, "Morocco": 37.8}
GDP_PC = {"Switzerland": 100, "Norway": 87, "Belgium": 55, "England": 49,     # k USD
          "France": 46, "Spain": 34, "Argentina": 14, "Morocco": 4}
FIFA = {"Argentina": 1, "Spain": 2, "France": 3, "England": 4,
        "Morocco": 7, "Belgium": 9, "Switzerland": 19, "Norway": 31}
HOME_TEMP = {"France": 25, "Spain": 33, "Argentina": 14, "England": 23,       # July high °C
             "Norway": 22, "Belgium": 23, "Switzerland": 25, "Morocco": 26}
VENUE_TEMP = {"Foxborough": 28, "Inglewood": 29, "Miami Gardens": 32, "Kansas City": 32}


def _coord(city):
    for k, v in COORDS.items():
        if city.startswith(k):
            return v
    return None


def _base(city):
    for k in COORDS:
        if city.startswith(k):
            return k
    return city


def _haversine(a, b):
    R = 6371.0
    la1, lo1 = map(math.radians, a)
    la2, lo2 = map(math.radians, b)
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * \
        math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _load_fixtures():
    rows = []
    for fn in ("schedule_group.csv", "schedule_ko.csv"):
        p = os.path.join(DATA, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                rows.append((r["date"], int(r["match"]), r["team1"], r["team2"],
                             r["city"], r.get("group", "")))
    return rows


def _tournament_stats():
    """gf/ga/gd/shootouts per team, computed from real results."""
    st = {t: {"gf": 0, "ga": 0, "gd": 0, "shootouts": 0} for t in QF_TEAMS}
    p = os.path.join(DATA, "results.csv")
    if not os.path.exists(p):
        return st
    for r in csv.DictReader(open(p, encoding="utf-8")):
        if r.get("score1") in (None, ""):
            continue
        s1, s2 = int(r["score1"]), int(r["score2"])
        ko = int(r["match"]) >= 73
        for t, gfor, gag in ((r["team1"], s1, s2), (r["team2"], s2, s1)):
            if t in st:
                st[t]["gf"] += gfor
                st[t]["ga"] += gag
                if ko and s1 == s2:
                    st[t]["shootouts"] += 1
    for t in st:
        st[t]["gd"] = st[t]["gf"] - st[t]["ga"]
    return st


def build() -> dict:
    rows = _load_fixtures()
    stats = _tournament_stats()

    flight = {}
    for t in QF_TEAMS:
        cs = [c for _, _, c in sorted(
            [(d, m, c) for d, m, a, b, c, g in rows if t in (a, b)],
            key=lambda x: (x[0], x[1]))]
        km = 0.0
        for i in range(len(cs) - 1):
            ca, cb = _coord(cs[i]), _coord(cs[i + 1])
            if ca and cb:
                km += _haversine(ca, cb)
        flight[t] = round(km)

    def facts(t, venue_temp=None):
        return {"team": t, "height": HEIGHT[t], "age": AGE[t], "value": VALUE[t],
                "gf": stats[t]["gf"], "ga": stats[t]["ga"], "gd": stats[t]["gd"],
                "shootouts": stats[t]["shootouts"], "titles": TITLES[t],
                "best_rank": BEST_RANK[t], "pop": POP[t], "gdp_pc": GDP_PC[t],
                "fifa": FIFA[t], "flight_km": flight[t], "home_temp": HOME_TEMP[t],
                "climate_gap": (venue_temp - HOME_TEMP[t]) if venue_temp is not None else None}

    teams = {t: facts(t) for t in QF_TEAMS}

    qf = [(d, m, a, b, c, g) for d, m, a, b, c, g in rows if g == "QF"]
    matchups = []
    for d, m, a, b, c, g in sorted(qf, key=lambda x: x[1]):
        vtemp = VENUE_TEMP.get(_base(c))
        matchups.append({"match": m, "date": d, "city": c, "venue_temp": vtemp,
                         "team1": facts(a, vtemp), "team2": facts(b, vtemp)})

    qf_cities = {_base(c): (m, a, b, d) for d, m, a, b, c, g in qf}
    seen, venues = set(), []
    for d, m, a, b, c, g in rows:
        base = _base(c)
        if base not in US_VENUES or base in seen:
            continue
        seen.add(base)
        lat, lng = COORDS[base]
        v = {"city": base, "lng": lng, "lat": lat, "qf": base in qf_cities}
        if base in qf_cities:
            mm, t1, t2, dt = qf_cities[base]
            v.update(match=mm, team1=t1, team2=t2, date=dt,
                     venue_temp=VENUE_TEMP.get(base))
        venues.append(v)

    return {"teams": teams, "matchups": matchups, "venues": venues,
            "note": "entertainment only — not part of the prediction model",
            "sources": {"height": "givemesport.com 2026", "age": "rotowire.com 2026",
                        "value": "transfermarkt via planetfootball 2026",
                        "fifa": "FIFA ranking Jun 2026",
                        "goals/shootouts": "computed from real results",
                        "flight": "great-circle between real match cities",
                        "climate": "July mean high, venue vs home capital"}}


if __name__ == "__main__":
    import json
    print(json.dumps(build(), ensure_ascii=False, indent=2))
