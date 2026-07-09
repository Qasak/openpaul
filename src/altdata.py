"""Fun 'alternative data' for the knockout matchups (ENTERTAINMENT ONLY — never
enters the prediction model). Four quirky metrics per quarter-final pairing:

  height   average squad height (cm)         — sourced, givemesport.com 2026
  age      average squad age                 — sourced, rotowire.com 2026
  climate  venue July temp vs home-country   — heat-shock gap (°C); smaller = better adapted
  flight   km flown between match cities so far — computed here from the real schedule

Also emits the US venue list (lng/lat) for the match-city map. All of it is
clearly labelled on the site as a fun sidebar, kept out of report_summary /
sim / the ledgers.
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

# average squad height (cm) and age — sourced, entertainment sidebar
HEIGHT = {"France": 184.9, "Spain": 181.7, "Argentina": 179.7, "England": 184.2,
          "Norway": 187.2, "Belgium": 185.8, "Switzerland": 185.2, "Morocco": 182.7}
AGE = {"France": 26.58, "Spain": 26.19, "Argentina": 28.62, "England": 26.62,
       "Norway": 26.35, "Belgium": 27.12, "Switzerland": 27.81, "Morocco": 25.92}
# July mean daily-high (°C): home country (capital/rep. city) — note S. hemisphere winter
HOME_TEMP = {"France": 25, "Spain": 33, "Argentina": 14, "England": 23,
             "Norway": 22, "Belgium": 23, "Switzerland": 25, "Morocco": 26}
# July mean daily-high (°C) at each quarter-final venue
VENUE_TEMP = {"Foxborough": 28, "Inglewood": 29, "Miami Gardens": 32, "Kansas City": 32}


def _coord(city: str):
    for k, v in COORDS.items():
        if city.startswith(k):
            return v
    return None


def _base(city: str) -> str:
    for k in COORDS:
        if city.startswith(k):
            return k
    return city


def _haversine(a, b) -> float:
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
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            rows.append((r["date"], int(r["match"]), r["team1"], r["team2"],
                         r["city"], r.get("group", "")))
    return rows


def build() -> dict:
    rows = _load_fixtures()

    flight = {}
    for t in QF_TEAMS:
        path = sorted([(d, m, c) for d, m, a, b, c, g in rows if t in (a, b)],
                      key=lambda x: (x[0], x[1]))
        cs = [c for _, _, c in path]
        km = 0.0
        for i in range(len(cs) - 1):
            ca, cb = _coord(cs[i]), _coord(cs[i + 1])
            if ca and cb:
                km += _haversine(ca, cb)
        flight[t] = round(km)

    teams = {}
    for t in QF_TEAMS:
        teams[t] = {"height": HEIGHT[t], "age": AGE[t], "flight_km": flight[t],
                    "home_temp": HOME_TEMP[t]}

    # the four quarter-finals, with each fixture's venue temp + climate gap per side
    qf = [(d, m, a, b, c, g) for d, m, a, b, c, g in rows if g == "QF"]
    matchups = []
    for d, m, a, b, c, g in sorted(qf, key=lambda x: x[1]):
        vtemp = VENUE_TEMP.get(_base(c))
        def side(t):
            gap = (vtemp - HOME_TEMP[t]) if vtemp is not None else None
            return {"team": t, "height": HEIGHT[t], "age": AGE[t],
                    "flight_km": flight[t], "home_temp": HOME_TEMP[t],
                    "climate_gap": gap}
        matchups.append({"match": m, "date": d, "city": c, "venue_temp": vtemp,
                         "team1": side(a), "team2": side(b)})

    # US venue markers for the match-city map (dedup, flag the QF hosts)
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
            "sources": {"height": "givemesport.com (2026 squads)",
                        "age": "rotowire.com (2026 squads)",
                        "flight": "great-circle between real match cities",
                        "climate": "July mean daily-high, venue vs home capital"}}


if __name__ == "__main__":
    import json
    print(json.dumps(build(), ensure_ascii=False, indent=2))
