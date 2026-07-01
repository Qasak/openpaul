"""Local dashboard server for the World Cup prediction project.

Zero extra dependencies (stdlib http.server). Serves dashboard/index.html and
a small JSON API; result submission appends to data/results.csv (validated)
and triggers conditional re-simulation + market/value refresh + Brier scoring
in a background thread.

Usage:  python3 -m src.webapp [port]      (default port 8765)
Then open http://localhost:8765
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pandas as pd

from .load_data import DATA, canon, load_all

ROOT = os.path.dirname(DATA)
DASH = os.path.join(ROOT, "dashboard")
PRED = os.path.join(ROOT, "predictions")
FORECAST_FILE = os.path.join(PRED, "2026-06-11_round2_matches.csv")

STATUS = {
    "running": False, "step": "", "progress": "", "error": "",
    "started_at": "", "finished_at": "", "mode": "",
}
_LOCK = threading.Lock()


# --------------------------------------------------------------- recalc job

def _recalc(mode: str) -> None:
    from .simulate import run as sim_run
    from .market import run as market_run
    from .report import build_summary
    from . import score as score_mod

    n = 100_000 if mode == "full" else 20_000
    steps = [
        ("模拟 σ=75（主模型）", lambda: sim_run(n, 42, 75.0, "")),
        ("模拟 σ=0（敏感性）", lambda: sim_run(n, 42, 0.0, "_sigma0")),
        ("模拟 σ=150（敏感性）", lambda: sim_run(n, 42, 150.0, "_sigma150")),
        ("市场价值分析", market_run),
        ("汇总表", build_summary),
        ("逐场 Brier 计分", score_mod.main),
    ]
    try:
        for i, (name, fn) in enumerate(steps, 1):
            STATUS.update(step=name, progress=f"{i}/{len(steps)}")
            fn()
        # dated archive of the conditional snapshot (rolling protocol)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
        shutil.copy(os.path.join(DATA, "sim_probs.csv"),
                    os.path.join(DATA, "archive", f"sim_probs_{stamp}.csv"))
        STATUS.update(step="完成", error="")
    except Exception:
        STATUS.update(error=traceback.format_exc()[-1500:])
    finally:
        STATUS.update(running=False,
                      finished_at=datetime.now(timezone.utc).isoformat())


def start_recalc(mode: str) -> bool:
    with _LOCK:
        if STATUS["running"]:
            return False
        STATUS.update(running=True, step="启动", progress="0", error="",
                      mode=mode, started_at=datetime.now(timezone.utc).isoformat(),
                      finished_at="")
    threading.Thread(target=_recalc, args=(mode,), daemon=True).start()
    return True


# --------------------------------------------------------------- data shape

def _csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path)
    return json.loads(df.replace({np.nan: None}).to_json(orient="records"))


def _json(path: str):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _ko_block() -> list[dict]:
    """Knockout bracket state: all 32 slots (matches 73-104) with teams once
    determined (schedule_ko.csv), model advancement probability for the first
    listed team (ko_forecast.csv, sigma=75-averaged), and real results."""
    from .tournament import parse_source

    bracket = _json(os.path.join(DATA, "bracket.json")) or {}
    sched = {int(r["match"]): r for r in _csv(os.path.join(DATA, "schedule_ko.csv"))}
    fc = {int(r["match"]): r for r in _csv(os.path.join(DATA, "ko_forecast.csv"))}
    res = {int(r["match"]): r for r in _csv(os.path.join(DATA, "results.csv"))
           if int(r["match"]) >= 73}

    def short(src: str) -> str:
        kind, val = parse_source(src)
        return {"W": f"1{val}", "R": f"2{val}", "T": "3rd",
                "M": f"W{val}", "L": f"L{val}"}[kind]

    out = []
    for rnd in bracket.get("rounds", []):
        for s in rnd["slots"]:
            m = int(s["match"])
            row = {"match": m, "round": rnd["round"], "date": s.get("date"),
                   "city": s.get("city"),
                   "src1": short(s["home_source"]), "src2": short(s["away_source"])}
            if m in sched:
                row.update(team1=sched[m]["team1"], team2=sched[m]["team2"])
            if m in fc:
                row["p1"] = fc[m]["p1_advance"]
            if m in res:
                row.update(score1=res[m]["score1"], score2=res[m]["score2"],
                           winner=res[m]["winner"])
            out.append(row)
    return out


def api_all() -> dict:
    summary = _csv(os.path.join(DATA, "report_summary.csv"))
    teams = pd.read_csv(os.path.join(DATA, "teams.csv"))
    sim = {r["team"]: r for r in _csv(os.path.join(DATA, "sim_probs.csv"))}

    groups = {}
    for _, t in teams.iterrows():
        s = sim.get(t["team"], {})
        groups.setdefault(t["group"], []).append({
            "team": t["team"], "is_host": bool(t["is_host"]),
            "p_r32": s.get("p_r32"), "p_group_winner": s.get("p_group_winner"),
            "elo": s.get("elo"),
        })
    for g in groups.values():
        g.sort(key=lambda x: -(x["p_group_winner"] or 0))

    sched = pd.read_csv(os.path.join(DATA, "schedule_group.csv"))
    fc = pd.read_csv(FORECAST_FILE) if os.path.exists(FORECAST_FILE) else pd.DataFrame()
    res = pd.read_csv(os.path.join(DATA, "results.csv"))
    market_files = [os.path.join(PRED, f) for f in sorted(os.listdir(PRED))
                    if f.startswith("market_1x2")]
    mk = pd.concat([pd.read_csv(p) for p in market_files], ignore_index=True) \
        if market_files else pd.DataFrame()
    score_log = {r["match"]: r for r in _csv(os.path.join(DATA, "score_log.csv"))}

    matches = []
    for _, r in sched.sort_values("match").iterrows():
        m = {"match": int(r["match"]), "date": r["date"], "group": r["group"],
             "team1": r["team1"], "team2": r["team2"],
             "city": str(r.get("city") or "")}
        if len(fc):
            f = fc[fc["match"] == r["match"]]
            if len(f):
                f = f.iloc[0]
                m.update(p1=float(f["p_team1_win"]), pd_=float(f["p_draw"]),
                         p2=float(f["p_team2_win"]),
                         xg1=float(f["xg_team1"]), xg2=float(f["xg_team2"]))
        if len(res):
            rr = res[res["match"] == r["match"]]
            if len(rr):
                rr = rr.iloc[0]
                m.update(score1=int(rr["score1"]), score2=int(rr["score2"]))
        if len(mk):
            q = mk[mk["match"] == r["match"]]
            if len(q):
                q = q.iloc[0]
                m.update(mk1=float(q["odds_team1"]), mkd=float(q["odds_draw"]),
                         mk2=float(q["odds_team2"]), mkbook=str(q["book"]))
        if m["match"] in score_log:
            sl = score_log[m["match"]]
            m.update(brier=sl.get("brier_model"), brier_mkt=sl.get("brier_market"))
        matches.append(m)

    score_rows = _csv(os.path.join(DATA, "score_log.csv"))
    bench = _json(os.path.join(DATA, "benchmarks.json")) or {}
    short = {"Opta": "Opta 超算", "Academic": "Groll 等学术 ML",
             "Bookmaker": "综合盘隐含", "EA": "EA Sports", "Silver": "Silver Bulletin"}
    bench_rows = []
    for p in bench.get("predictions", []):
        probs = {x["team"]: x["champion_prob_pct"] for x in (p.get("probabilities") or [])}
        if probs:
            name = p["source_name"]
            name = next((v for k, v in short.items() if name.startswith(k)), name[:24])
            bench_rows.append({"source": name,
                               "pick": p.get("top_pick"), "probs": probs})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "groups": groups,
        "matches": matches,
        "score_log": score_rows,
        "meta": {
            "sim": _json(os.path.join(DATA, "sim_meta.json")),
            "fit": _json(os.path.join(DATA, "fit_validation.json")),
            "backtest": _json(os.path.join(DATA, "backtest_sigma.json")),
            "conmebol": _json(os.path.join(DATA, "conmebol_check.json")),
        },
        "benchmarks": bench_rows,
        "status": dict(STATUS),
        "results_count": int(len(res)),
        "ko": _ko_block(),
    }


def submit_result(payload: dict) -> dict:
    match = int(payload["match"])
    s1, s2 = int(payload["score1"]), int(payload["score2"])
    winner = canon(str(payload.get("winner") or "").strip()) or ""
    if not (0 <= s1 <= 20 and 0 <= s2 <= 20):
        raise ValueError("比分超出合理范围")
    sched = pd.read_csv(os.path.join(DATA, "schedule_group.csv"))
    row = sched[sched["match"] == match]
    if match >= 73:
        if not winner:
            raise ValueError("淘汰赛必须填写晋级方 winner")
        date, group, t1, t2 = "", "", canon(payload["team1"]), canon(payload["team2"])
    else:
        if not len(row):
            raise ValueError(f"场次 {match} 不在小组赛赛程中")
        r = row.iloc[0]
        date, group, t1, t2 = r["date"], r["group"], r["team1"], r["team2"]
        winner = ""

    res_path = os.path.join(DATA, "results.csv")
    res = pd.read_csv(res_path)
    res = res[res["match"] != match]
    res = pd.concat([res, pd.DataFrame([{
        "match": match, "date": date, "group": group, "team1": t1, "team2": t2,
        "score1": s1, "score2": s2, "winner": winner,
    }])], ignore_index=True).sort_values("match")
    backup = res.copy()
    res.to_csv(res_path, index=False)
    try:
        load_all()   # full validation incl. winner rules
    except Exception:
        backup[backup["match"] != match].to_csv(res_path, index=False)
        raise
    return {"ok": True, "match": match}


# ----------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_out(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                with open(os.path.join(DASH, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path == "/api/all":
                self._json_out(api_all())
            elif self.path == "/api/status":
                self._json_out(dict(STATUS))
            else:
                self._send(404, b"not found", "text/plain")
        except Exception:
            self._json_out({"error": traceback.format_exc()[-800:]}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/result":
                out = submit_result(payload)
                if payload.get("recalc", True):
                    out["recalc_started"] = start_recalc(
                        payload.get("mode", "quick"))
                self._json_out(out)
            elif self.path == "/api/recalc":
                ok = start_recalc(payload.get("mode", "quick"))
                self._json_out({"ok": ok,
                                "note": "" if ok else "已有重算任务在运行"})
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._json_out({"error": str(e)}, 400)

    def log_message(self, fmt, *args):
        pass   # keep the console quiet


def main(port: int = 8765) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard: http://localhost:{port}  (Ctrl-C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
