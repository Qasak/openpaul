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
FIFA = {"Argentina": 1, "Spain": 2, "France": 3, "England": 4,
        "Morocco": 7, "Belgium": 9, "Switzerland": 19, "Norway": 31}
HOME_TEMP = {"France": 25, "Spain": 33, "Argentina": 14, "England": 23,       # July high °C
             "Norway": 22, "Belgium": 23, "Switzerland": 25, "Morocco": 26}
VENUE_TEMP = {"Foxborough": 28, "Inglewood": 29, "Miami Gardens": 32, "Kansas City": 32}

# ---- qualitative tactical read (subjective/editorial; bilingual) ----
COACH = {
  "France":      {"name": {"zh": "德尚", "en": "Deschamps"},        "level": {"zh": "2018世界杯冠军·稳健", "en": "2018 WC winner · pragmatic"}},
  "Spain":       {"name": {"zh": "德拉富恩特", "en": "de la Fuente"}, "level": {"zh": "2024欧洲杯冠军·传控", "en": "Euro 2024 winner · possession"}},
  "Argentina":   {"name": {"zh": "斯卡洛尼", "en": "Scaloni"},        "level": {"zh": "2022世界杯冠军·善用梅西", "en": "2022 WC winner · builds around Messi"}},
  "England":     {"name": {"zh": "图赫尔", "en": "Tuchel"},          "level": {"zh": "欧冠冠军·结构化", "en": "UCL winner · structured"}},
  "Norway":      {"name": {"zh": "索尔巴肯", "en": "Solbakken"},      "level": {"zh": "务实经验型", "en": "pragmatic veteran"}},
  "Belgium":     {"name": {"zh": "加西亚", "en": "Garcia"},          "level": {"zh": "经验型", "en": "experienced"}},
  "Switzerland": {"name": {"zh": "亚金", "en": "Yakin"},            "level": {"zh": "组织防反", "en": "organised, counter"}},
  "Morocco":     {"name": {"zh": "瓦希比", "en": "Ouahbi"},          "level": {"zh": "接棒新帅", "en": "new appointment"}},
}
STYLE = {
  "France":      {"zh": "务实反击 + 球星单点(姆巴佩速度、登贝莱/奥利塞边路)", "en": "pragmatic counters + star quality (Mbappé pace, Dembélé/Olise wings)"},
  "Spain":       {"zh": "极致传控 + 高位逼抢(本届零封)", "en": "relentless possession + high press (no goal conceded)"},
  "Argentina":   {"zh": "梅西调度 + 大赛抗压 + 定位球", "en": "Messi-orchestrated + big-game nous + set pieces"},
  "England":     {"zh": "均衡全面(创造多、失球少) + 边路个人质量", "en": "most balanced profile + wide individual quality"},
  "Norway":      {"zh": "直接 + 哈兰德支点 + 定位球(后防偏漏)", "en": "direct + Haaland focal point + set pieces (leaky at the back)"},
  "Belgium":     {"zh": "直接强攻(德凯特拉雷 + 卢卡库)", "en": "direct firepower (De Ketelaere + Lukaku)"},
  "Switzerland": {"zh": "严密低位 + 快速反击(大赛超常发挥)", "en": "compact block + counters (tournament over-achievers)"},
  "Morocco":     {"zh": "纪律密集防守 + 快速反击(2022四强底子)", "en": "disciplined low block + quick transitions (2022 semi-finalists)"},
}
MATCHUP_KEY = {
  97: {"zh": "全场最强火力 vs 最铁的防守。摩洛哥的低位铁桶最擅长冻结法国的中场传导、逼其打阵地战;法国得靠姆巴佩冲身后、边路(登贝莱/奥利塞)与定位球撬开。摩洛哥反击一旦犀利就是爆冷剧本——但法国这届 14 球的火力全场最猛。",
       "en": "The tournament's hottest attack vs its most disciplined defence. Morocco's low block is exactly what frustrates France's build-up and forces a grind; France must beat it with Mbappé's runs in behind, the Dembélé/Olise wings and set pieces. Sharp Moroccan counters are the upset script — but France's 14 goals are the most of the eight."},
  98: {"zh": "控球 vs 直接。西班牙的传控会把比利时压在半场疯狂回追;比利时的活路是德凯特拉雷/卢卡库直接冲西班牙高位防线的身后空当。西班牙本届一球未失,比利时想破门得靠一次单点爆发。",
       "en": "Control vs directness. Spain's possession pins Belgium back and drains them; Belgium's route is hitting the space behind Spain's high line with De Ketelaere/Lukaku. Spain haven't conceded all tournament, so Belgium need a moment of individual quality."},
  99: {"zh": "挪威后防是这组最大破绽(本届丢 9 球全场最多),英格兰均衡的进攻足以打穿;反过来哈兰德的支点与定位球正好去咬英格兰自己的定位球软肋。迈阿密湿热是双方共同考验,但英格兰整体更完整,挪威押哈兰德搏冷。",
       "en": "Norway's defence is the tie's soft spot (9 conceded, most of the eight); England's balanced attack should get through, while Haaland's aerial/set-piece threat targets England's own weak spot. Miami's heat tests both, but England are the more complete side; Norway ride Haaland."},
  100: {"zh": "瑞士的严密低位 + 反击,正是阿根廷最难受的对手类型(本届多是 3-2 险胜、已丢 5 球);阿根廷要靠耐心和梅西的灵光、定位球撬开,拖得越久越危险。瑞士淘汰赛已靠点球过关,再拖进点球又是爆冷候选。堪萨斯城酷暑对刚从南半球冬训过来的阿根廷是额外考验。",
        "en": "Switzerland's compact block-and-counter is the profile that troubles Argentina most (a run of 3-2 wins, five conceded); Argentina need patience and a Messi moment, and the longer it stays level the more dangerous it gets. The Swiss already won one shootout — another and it's upset territory. Kansas City's heat is an extra test for an Argentina side arriving from a southern-hemisphere winter."},
}


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
                "best_rank": BEST_RANK[t], "fifa": FIFA[t], "flight_km": flight[t],
                "home_temp": HOME_TEMP[t], "coach": COACH[t]["name"],
                "coach_level": COACH[t]["level"], "style": STYLE[t],
                "climate_gap": (venue_temp - HOME_TEMP[t]) if venue_temp is not None else None}

    teams = {t: facts(t) for t in QF_TEAMS}

    qf = [(d, m, a, b, c, g) for d, m, a, b, c, g in rows if g == "QF"]
    matchups = []
    for d, m, a, b, c, g in sorted(qf, key=lambda x: x[1]):
        vtemp = VENUE_TEMP.get(_base(c))
        matchups.append({"match": m, "date": d, "city": c, "venue_temp": vtemp,
                         "team1": facts(a, vtemp), "team2": facts(b, vtemp),
                         "key": MATCHUP_KEY.get(m)})

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
