"""Build the public, read-only showcase site (docs/index.html).

"决赛之夜" edition v2 — cinematic dark-stadium data showcase.
  - hero carousel over the top-6 contenders (auto-cycle + click-to-switch)
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
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

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


def build_payload() -> dict:
    d = api_all()
    d.pop("status", None)
    d["benchmarks"] = d.get("benchmarks", [])
    if d.get("meta", {}).get("sim"):
        d["meta"]["sim"].pop("params_path", None)   # no local paths in public blob
    d["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return d


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenPaul 🐙 · 2026 世界杯冠军预测</title>
<meta name="description" content="Elo 驱动 Dixon-Coles + 100,000 次蒙特卡洛的 2026 世界杯预测：3D 概率地形、全球夺冠版图、市场价值偏差、逐场预测与公开核验。数据/代码/参数全开源可复现。">
<meta name="theme-color" content="#05070f">
<meta name="twitter:card" content="summary_large_image">
<meta property="og:title" content="OpenPaul 🐙 · 2026 世界杯冠军预测">
<meta property="og:description" content="章鱼保罗的开源转世：100,000 次蒙特卡洛 · 3D 概率地形与全球夺冠版图 · 开球前存证，赛后逐场公开核验">
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
@media(max-width:760px){nav .links a:not(#ghLink){display:none}.badge{display:none}}

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

/* podium */
.podium{display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:20px;align-items:end}
@media(max-width:900px){.podium{grid-template-columns:1fr;align-items:stretch}}
.pcard{position:relative;border-radius:20px;padding:30px 26px 24px;text-align:center;overflow:hidden;
  background:linear-gradient(180deg,#101a30,#0a111f);border:1px solid var(--line);
  transition:transform .35s cubic-bezier(.2,.8,.2,1),box-shadow .35s}
.pcard:hover{transform:translateY(-10px) scale(1.015);box-shadow:0 26px 60px rgba(0,0,0,.55)}
.pcard.p1{border:1px solid rgba(240,199,94,.55);padding-top:44px;
  background:linear-gradient(180deg,rgba(56,42,12,.5),#0c1322 60%);
  box-shadow:0 0 70px rgba(240,199,94,.12) inset,0 18px 50px rgba(0,0,0,.5)}
.pcard.p2{border-color:rgba(190,205,225,.4)}
.pcard.p3{border-color:rgba(205,137,80,.4)}
.medal{position:absolute;top:14px;left:18px;font-family:var(--num);font-weight:700;font-size:15px;letter-spacing:.18em}
.p1 .medal{color:var(--gold)}.p2 .medal{color:#c9d6e8}.p3 .medal{color:#d99a62}
.pflag{width:108px;height:78px;border-radius:12px;object-fit:cover;margin-bottom:16px;
  box-shadow:0 10px 30px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.12)}
.p1 .pflag{width:132px;height:96px;box-shadow:0 14px 40px rgba(0,0,0,.6),0 0 0 1px rgba(240,199,94,.5),0 0 50px rgba(240,199,94,.2)}
.pname{font-size:20px;font-weight:800}
.pper{font-family:var(--num);font-weight:700;font-size:46px;margin:6px 0 2px;color:var(--gold2)}
.p2 .pper{color:#dde7f5}.p3 .pper{color:#eab68b}
.pmeta{font-size:11.5px;color:var(--dim);display:flex;justify-content:center;gap:14px;margin-top:10px}
.pmeta b{color:var(--txt);font-family:var(--num)}

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
.tablebox{max-height:600px;overflow:auto;border-radius:12px}

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
<body>
<script>document.documentElement.classList.add('js')</script>
<div id="grain" aria-hidden="true"></div>
<div class="aurora a1" aria-hidden="true"></div><div class="aurora a2" aria-hidden="true"></div><div class="aurora a3" aria-hidden="true"></div>

<nav>
  <span class="wordmark">🐙 OPENPAUL<span> · 世界杯预测引擎</span></span>
  <span class="badge" id="bSnap"></span>
  <span class="badge live" id="bRes"></span>
  <span class="links">
    <a href="#terrain-sec" data-i18n="navTerrain">3D 地形</a><a href="#globe-sec" id="navGlobe" data-i18n="navGlobe">地球</a><a href="#edge-sec" data-i18n="navEdge">价值</a>
    <a href="#matches-sec" data-i18n="navMatches">赛程</a>
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
      <div class="overline" data-i18n="overline">Monte Carlo ×100,000 <span class="ol2">· 49,400 场历史重放</span> · 开球前存证</div>
      <h1 class="hero-q" data-i18n="heroQ">这个夏天，谁举起<em>大力神杯</em>？</h1>
      <div class="champ-line" id="champLine">
        <img class="champ-flag" id="champFlag" alt="">
        <div class="champ-name" id="champName"></div>
        <div class="champ-pct"><span id="champPct">0</span><span style="font-size:.42em">%</span></div>
      </div>
      <div class="champ-sub" id="champSub"></div>
      <div class="hero-stats" id="heroStats"></div>
    </div>
    <div class="hero-side" id="heroSide"><h3 data-i18n="contenders">Contenders · 候选人（点击切换）</h3></div>
  </div>
  <div class="scroll-cue" aria-hidden="true">SCROLL</div>
</section>

<section class="sec reveal" id="terrain-sec">
  <div class="ghost" aria-hidden="true">01</div>
  <div class="sec-head"><span class="sec-no">01</span><h2 class="sec-h" data-i18n="s1t">晋级概率地形</h2></div>
  <div class="sec-d" data-i18n="s1d">24 支最强球队 × 6 个晋级阶段的三维概率山脉——最前排的金色山脊就是夺冠之路，身后的蓝色高墙是 32 强的入场概率。</div>
  <div class="panel"><span class="hint" id="terrainHint"><span class="fine" data-i18n="hintDrag">拖拽旋转 · 滚轮缩放</span><span class="coarse" data-i18n="hintTouch">触摸拖拽旋转</span></span><div id="terrain"></div><div id="terrainFall" class="gl-fall"></div></div>
</section>

<section class="sec reveal" id="podium-sec">
  <div class="ghost" aria-hidden="true">02</div>
  <div class="sec-head"><span class="sec-no">02</span><h2 class="sec-h" data-i18n="s2t">领奖台</h2></div>
  <div class="sec-d" data-i18n="s2d">绝对概率层前三名（σ=75 主模型，区间为 [σ=150, σ=0] 敏感性边界）。</div>
  <div class="podium" id="podium"></div>
</section>

<section class="sec reveal" id="globe-sec">
  <div class="ghost" aria-hidden="true">03</div>
  <div class="sec-head"><span class="sec-no">03</span><h2 class="sec-h" data-i18n="s3t">夺冠概率 · 全球版图</h2></div>
  <div class="sec-d" data-i18n="s3d">48 支参赛队的夺冠概率立柱，矗立在各自国土之上（柱高 ∝ √概率，颜色 ∝ 概率）。伊比利亚半岛上那道金光，就是本届最高的山。</div>
  <div class="panel" id="globePanel"><span class="hint"><span class="fine" data-i18n="hintGlobe">拖拽旋转 · 自动巡航</span><span class="coarse" data-i18n="hintTouch2">触摸拖拽旋转</span></span><div id="globe"></div></div>
</section>

<section class="sec reveal" id="edge-sec">
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

<section class="sec reveal" id="groups-sec">
  <div class="ghost" aria-hidden="true">05</div>
  <div class="sec-head"><span class="sec-no">05</span><h2 class="sec-h" data-i18n="s5t">十二宫格 · 小组形势</h2></div>
  <div class="sec-d" data-i18n="s5d">底条 = 小组头名概率 · 右侧百分比 = 晋级 32 强概率 · ★ = 东道主。</div>
  <div class="groups" id="groups"></div>
</section>

<section class="sec reveal" id="matches-sec">
  <div class="ghost" aria-hidden="true">06</div>
  <div class="sec-head"><span class="sec-no">06</span><h2 class="sec-h" data-i18n="s6t">逐场预测与结果 · 小组赛 72 场</h2></div>
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
</section>

<section class="sec reveal" id="table-sec">
  <div class="ghost" aria-hidden="true">07</div>
  <div class="sec-head"><span class="sec-no">07</span><h2 class="sec-h" data-i18n="s7t">48 队全量数据</h2></div>
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
'South Africa':'南非',Iraq:'伊拉克','Bosnia and Herzegovina':'波黑','Curaçao':'库拉索'};
const I18N={zh:{
 navTerrain:'3D 地形',navGlobe:'地球',navEdge:'价值',navMatches:'赛程',
 overline:'OpenPaul · Monte Carlo ×100,000 <span class="ol2">· 49,400 场历史重放</span> · 开球前存证',
 heroQ:'这个夏天，谁举起<em>大力神杯</em>？',
 contenders:'Contenders · 候选人（点击切换）',
 snapPrefix:'快照',playedPrefix:'已完赛',genAt:'快照生成',repoLink:'代码与数据仓库 →',
 subProb:'夺冠概率（σ=75 主模型）· 敏感区间',subFinal:'进决赛',subSF:'进四强',
 statSimsV:'10万',statSims:'蒙特卡洛模拟',statLL:'样本外 LOGLOSS',statBrier:'逐场 BRIER',
 statWait:'待开赛',statSigma:'回测选定扰动',
 s1t:'晋级概率地形',
 s1d:'24 支最强球队 × 6 个晋级阶段的三维概率山脉——最前排的金色山脊就是夺冠之路，身后的蓝色高墙是 32 强的入场概率。',
 s2t:'领奖台',s2d:'绝对概率层前三名（σ=75 主模型，区间为 [σ=150, σ=0] 敏感性边界）。',
 s3t:'夺冠概率 · 全球版图',
 s3d:'48 支参赛队的夺冠概率立柱，矗立在各自国土之上（柱高 ∝ √概率，颜色 ∝ 概率）。伊比利亚半岛上那道金光，就是本届最高的山。',
 s4t:'市场低估了谁',
 s4d:'模型概率 − 锐利盘隐含概率（Pinnacle/Betfair/Polymarket/Kalshi 共识，overround 6.1%，幂法去水）。金色 = 被低估，灰蓝 = 被高估；图示正/负边际前 7/6 名，全量 48 队见 §07 表格。',
 s5t:'十二宫格 · 小组形势',s5d:'底条 = 小组头名概率 · 右侧百分比 = 晋级 32 强概率 · ★ = 东道主。',
 s6t:'逐场预测与结果 · 小组赛 72 场',
 s6d:'概率条为开球前存证预测（git + RFC3161 锚定，赛后不改）：蓝 = 左队胜，灰 = 平，金 = 右队胜。绿色为真实比分，B 为该场模型 Brier 分数（越低越好）。淘汰赛对阵确定后另行存证。',
 s7t:'48 队全量数据',s7d:'点击表头排序。边际与 EV 以锐利盘共识为基准。',
 hintDrag:'拖拽旋转 · 滚轮缩放',hintTouch:'触摸拖拽旋转',hintGlobe:'拖拽旋转 · 自动巡航',hintTouch2:'触摸拖拽旋转',
 benchTitle:'模型阵营对照 · 西班牙 vs 德国夺冠概率',
 benchNote:'所有来源一致指向西班牙。德国行是模型阵营的分水岭：赛果系与市场都在 ~3–5%（本模型 2.9%、Opta 5.1%、市场 5.3%），而含球员身价协变量的模型给到 ~11%（红色条）——这是本届各模型最大的单队分歧。',
 srcMine:'本模型（赛果系 · Elo）',srcSharp:'锐利盘市场（去水）',lblSpain:'西班牙',lblGermany:'德国',
 stChampion:'夺冠',stFinal:'决赛',stSF:'四强',stQF:'八强',stR16:'16强',stR32:'32强',prob:'概率',
 pmFinal:'决赛',pmSF:'四强',pmOdds:'赔率',pmRange:'区间',
 optAllDates:'全部日期',optAllGroups:'全部小组',optAllStates:'全部状态',optDone:'已完赛',optTodo:'未开赛',
 grp:'组',scoreNone:'首场完赛后开始逐场 Brier 计分',scored:'已计分',matchesUnit:'场',
 modelBrier:'模型 Brier',vsMarket:'vs 市场',matchEmpty:'没有符合筛选的比赛',
 pWinL:'左胜',pDraw:'平',pWinR:'右胜',brierTip:'模型 Brier（市场',
 thTeam:'队伍',thChampion:'夺冠',thFinal:'决赛',thSF:'四强',thQF:'八强',thR16:'16强',thR32:'32强',
 thOdds:'锐利赔率',thImp:'市场隐含',thEdge:'边际pp',thEV:'EV',
 winProb:'夺冠概率',edgeWord:'边际',vsModel:'模型',vsMkt:'市场',
 foot1:'<b>方法链</b> · eloratings.net 评级 → 49,400 场历史重放重算逐场 Elo（vs 官方 corr 0.986）→ Dixon-Coles 在 8,103 场上 MLE 拟合（其中 1,309 场样本外验证，logloss 0.8325）→ σ=75 实力扰动（2018/2022 两届回测选定）→ 100,000 次全赛事蒙特卡洛（2026 新版规则完整实现）→ 锐利盘市场对照',
 foot2:'<b>公开核验</b> · 全部 72 场小组赛逐场预测于揭幕战开球前 git 提交，提交哈希经 RFC3161 可信时间戳锚定（freetsa.org）；已完赛场次锁定真实结果条件重模拟，逐场 Brier 公开计分',
 foot3:'本页面为静态数据快照，方法论演示，<b>非投注建议</b>。',
 brand:'🐙 <b>OpenPaul</b> — 2010 年章鱼保罗用触手挑选赢家，16 年后我们用 100,000 次蒙特卡洛。预测可以开源，章鱼只负责可爱。',
},en:{
 navTerrain:'3D Terrain',navGlobe:'Globe',navEdge:'Value',navMatches:'Matches',
 overline:'OpenPaul · Monte Carlo ×100,000 <span class="ol2">· 49,400-match Elo replay</span> · sealed before kickoff',
 heroQ:'This summer, who lifts <em>the World Cup</em>?',
 contenders:'Contenders · click to switch',
 snapPrefix:'Snapshot',playedPrefix:'Played',genAt:'Snapshot generated',repoLink:'Code & data repository →',
 subProb:'Title probability (σ=75 main model) · sensitivity band',subFinal:'Final',subSF:'Semis',
 statSimsV:'100k',statSims:'MONTE CARLO RUNS',statLL:'OUT-OF-SAMPLE LOGLOSS',statBrier:'PER-MATCH BRIER',
 statWait:'awaiting kickoff',statSigma:'BACKTEST-CHOSEN σ',
 s1t:'Probability Terrain',
 s1d:'A 3-D probability massif: top 24 teams × 6 knockout stages. The golden ridge up front is the road to the title; the blue wall behind is the round-of-32 entry probability.',
 s2t:'The Podium',s2d:'Top three by absolute probability (σ=75 main model; band = [σ=150, σ=0] sensitivity bounds).',
 s3t:'Title Probability · World Map',
 s3d:'Championship-probability pillars rising from each of the 48 homelands (height ∝ √p, color ∝ p). That golden beam over Iberia is the tallest mountain of this cup.',
 s4t:'Whom Does the Market Undervalue?',
 s4d:'Model probability − sharp-market implied (Pinnacle/Betfair/Polymarket/Kalshi consensus, 6.1% overround, power de-vig). Gold = undervalued, slate = overvalued; chart shows top 7/6 by ± edge — all 48 teams in §07.',
 s5t:'The Twelve Groups',s5d:'Bottom bar = group-winner probability · right % = reach round-of-32 · ★ = host.',
 s6t:'Match-by-Match · 72 Group Games',
 s6d:'Probability bars are pre-kickoff sealed forecasts (git + RFC3161 anchored, never edited): blue = left win, grey = draw, gold = right win. Green chip = real score; B = per-match Brier (lower is better). Knockout forecasts will be sealed once the bracket is set.',
 s7t:'All 48 Teams',s7d:'Click headers to sort. Edge & EV vs the sharp-book consensus.',
 hintDrag:'drag to rotate · scroll to zoom',hintTouch:'touch-drag to rotate',hintGlobe:'drag to rotate · auto-cruise',hintTouch2:'touch-drag to rotate',
 benchTitle:'Model Camps · Spain vs Germany title odds',
 benchNote:'Every source points to Spain. Germany is the fault line between camps: results-based models and the market sit at ~3–5% (this model 2.9%, Opta 5.1%, market 5.3%), while covariate models with squad value reach ~11% (red bar) — the single largest disagreement of this cup.',
 srcMine:'This model (results-based · Elo)',srcSharp:'Sharp market (de-vig)',lblSpain:'Spain',lblGermany:'Germany',
 stChampion:'Title',stFinal:'Final',stSF:'Semis',stQF:'Quarters',stR16:'R16',stR32:'R32',prob:'Probability',
 pmFinal:'Final',pmSF:'SF',pmOdds:'Odds',pmRange:'Band',
 optAllDates:'All dates',optAllGroups:'All groups',optAllStates:'All states',optDone:'Finished',optTodo:'Upcoming',
 grp:'',scoreNone:'Brier scoring starts after the first final whistle',scored:'Scored',matchesUnit:'',
 modelBrier:'model Brier',vsMarket:'vs market',matchEmpty:'No matches for this filter',
 pWinL:'left win',pDraw:'draw',pWinR:'right win',brierTip:'Model Brier (market',
 thTeam:'Team',thChampion:'Title',thFinal:'Final',thSF:'SF',thQF:'QF',thR16:'R16',thR32:'R32',
 thOdds:'Sharp odds',thImp:'Implied',thEdge:'Edge pp',thEV:'EV',
 winProb:'Title probability',edgeWord:'Edge',vsModel:'Model',vsMkt:'Market',
 foot1:'<b>Method chain</b> · eloratings.net ratings → 49,400-match historical replay of per-game Elo (corr 0.986 vs official) → Dixon-Coles MLE on 8,103 matches (1,309 held out, logloss 0.8325) → σ=75 strength noise (chosen by backtests on the 2018/2022 World Cups) → 100,000 full-tournament Monte Carlo runs (complete 2026 ruleset) → sharp-market comparison',
 foot2:'<b>Public verification</b> · all 72 group-stage forecasts committed to git before the opening kickoff, commit hashes anchored with RFC3161 trusted timestamps (freetsa.org); finished matches are locked into conditional re-simulation and Brier-scored in public',
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
}
const fl=(t,w=80)=>FLAG[t]?`flags/${FLAG[t]}-w${w<=80?80:320}.png`:'';
const fimg=(t,w)=>FLAG[t]?`<img src="${fl(t,w||80)}" loading="lazy" alt="${nm(t)}">`:'';
const pct=(x,d=1)=>x==null?'—':(x*100).toFixed(d)+'%';
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
const TOPN=Math.min(6,D.summary.length);
let heroIdx=0,heroTimer=null;
function setHero(i){
  heroIdx=i;const r=D.summary[i];
  const line=document.getElementById('champLine');
  line.classList.remove('swap');void line.offsetWidth;line.classList.add('swap');
  const f=document.getElementById('champFlag');
  f.src=fl(r.team,320);f.alt=nm(r.team);
  document.getElementById('champName').innerHTML=
    nm(r.team)+`<span class="en">Nº${i+1} · ${r.team} · ELO ${r.elo}</span>`;
  document.getElementById('champSub').innerHTML=
    `${t('subProb')} [<b>${pct(r.p_s150)}</b>, <b>${pct(r.p_s0)}</b>] · ${t('subFinal')} <b>${pct(r.p_final)}</b> · ${t('subSF')} <b>${pct(r.p_sf)}</b>`;
  countUp(document.getElementById('champPct'),r.p_champion*100,1,900);
  document.querySelectorAll('#heroSide .rk').forEach((el,j)=>el.classList.toggle('active',j===i));
  try{paulGo(i)}catch(e){}   // mascot hops over on every switch, manual or auto
}
function nextHero(){setHero((heroIdx+1)%TOPN)}
function restartHeroTimer(){clearInterval(heroTimer);heroTimer=setInterval(nextHero,8000)}
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
  const top=D.summary[0];
  D.summary.slice(0,TOPN).forEach((r,i)=>{
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
  heroTimer=setInterval(nextHero,5500);
  document.addEventListener('visibilitychange',()=>{
    clearInterval(heroTimer);
    if(!document.hidden)heroTimer=setInterval(nextHero,5500);
    if(charts.globe){try{charts.globe.setOption({globe:{viewControl:{autoRotate:!document.hidden&&globeVisible}}})}catch(e){}}
  });
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
    if(ti)ti.textContent=nm(D.summary[i].team)});
}
function paulInit(){
  const st=document.getElementById('paul'),ground=document.getElementById('paulGround'),
    flayer=document.getElementById('paulFlags');
  if(!st||!ground||!flayer)return;
  const NS='http://www.w3.org/2000/svg';
  D.summary.slice(0,TOPN).forEach((r,i)=>{
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
  const order=[1,0,2],cls=['p2','p1','p3'],medal=['Nº2 · SILVER','Nº1 · GOLD','Nº3 · BRONZE'];
  document.getElementById('podium').innerHTML=order.map((si,i)=>{
    const r=D.summary[si];
    return `<div class="pcard ${cls[i]} reveal" style="transition-delay:${i*.1}s">
      <div class="medal">${medal[i]}</div>
      <img class="pflag" src="${fl(r.team,320)}" alt="${nm(r.team)}">
      <div class="pname">${nm(r.team)}</div>
      <div class="pper" data-flick="${pct(r.p_champion)}">${pct(r.p_champion)}</div>
      <div style="color:var(--dim);font-size:11px;font-family:var(--num)">${t('pmRange')} [${pct(r.p_s150)} – ${pct(r.p_s0)}]</div>
      <div class="pmeta"><span>${t('pmFinal')} <b>${pct(r.p_final,0)}</b></span><span>${t('pmSF')} <b>${pct(r.p_sf,0)}</b></span><span>${t('pmOdds')} <b>${num(r.decimal_odds_sharp,1)}</b></span></div>
    </div>`}).join('');
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
        return `<b>${nm(r.team)}</b><br>${t('vsModel')} ${pct(r.p_champion)} − ${t('vsMkt')} ${pct(r.p_market_sharp)}<br>${t('edgeWord')} <b>${sgn(r.edge_sharp_pp)}pp</b> · EV ${sgn(r.ev_sharp*100)}%`}},
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

/* ---------- bench comparison rows ---------- */
function bench(){
  const mine=Object.fromEntries(D.summary.map(r=>[r.team,r]));
  const rows=[{src:t('srcMine'),mine:true,es:mine.Spain.p_champion*100,de:mine.Germany.p_champion*100}];
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

/* ---------- full table ---------- */
let sortKey='p_champion',sortAsc=false,tableSeen=false;
const COLS=()=>[['team',t('thTeam')],['elo','ELO'],['p_champion',t('thChampion')],['p_final',t('thFinal')],['p_sf',t('thSF')],
  ['p_qf',t('thQF')],['p_r16',t('thR16')],['p_r32',t('thR32')],['decimal_odds_sharp',t('thOdds')],
  ['p_market_sharp',t('thImp')],['edge_sharp_pp',t('thEdge')],['ev_sharp',t('thEV')]];
function table(){
  const rows=[...D.summary].sort((a,b)=>{
    const x=a[sortKey],y=b[sortKey];
    if(x==null)return 1;if(y==null)return -1;
    return (x<y?-1:x>y?1:0)*(sortAsc?1:-1)});
  document.getElementById('fullTable').innerHTML=
    `<tr>${COLS().map(([k,l])=>`<th data-k="${k}">${l}${sortKey===k?(sortAsc?' ▲':' ▼'):''}</th>`).join('')}</tr>`+
    rows.map(r=>`<tr>
      <td><span class="tf">${fimg(r.team,80)}${nm(r.team)}</span></td>
      <td class="num">${r.elo!=null?r.elo:'—'}</td>
      <td class="num goldc"${tableSeen?'':' data-flick="'+pct(r.p_champion,2)+'"'}>${pct(r.p_champion,2)}</td>
      <td class="num">${pct(r.p_final)}</td><td class="num">${pct(r.p_sf)}</td><td class="num">${pct(r.p_qf)}</td>
      <td class="num">${pct(r.p_r16)}</td><td class="num">${pct(r.p_r32)}</td>
      <td class="num">${num(r.decimal_odds_sharp)}</td><td class="num">${pct(r.p_market_sharp,2)}</td>
      <td class="num ${r.edge_sharp_pp>0?'pos':'neg'}">${sgn(r.edge_sharp_pp)}</td>
      <td class="num ${r.ev_sharp>0?'pos':'neg'}">${sgn(r.ev_sharp*100)}%</td></tr>`).join('');
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
  podium();bench();groupsSec();matches();table();
  try{paulLang()}catch(e){}
  // strip one-shot effects instantly after re-render
  document.querySelectorAll('[data-flick]').forEach(el=>{el.textContent=el.dataset.flick;el.removeAttribute('data-flick')});
  growAll(document.body);document.body.classList.add('m-seen');
  if(charts.edge){echarts.dispose(document.getElementById('chEdge'));charts.edge=null;try{edge()}catch(e){}}
  if(charts.terrain){echarts.dispose(document.getElementById('terrain'));charts.terrain=null;try{terrain(true)}catch(e){}}
  if(charts.globe){echarts.dispose(document.getElementById('globe'));charts.globe=null;try{globe()}catch(e){}}
}
document.getElementById('langBtn').addEventListener('click',e=>{
  e.preventDefault();
  LANG=LANG==='zh'?'en':'zh';
  try{localStorage.setItem('wc26lang',LANG)}catch(err){}
  langRefresh();
});

/* ---------- boot ---------- */
applyStatic();heroInit();stars();podium();bench();groupsSec();matchTools();matches();table();reveals();
try{paulInit()}catch(e){}
onSee('edge-sec',el=>{try{edge()}catch(e){} flickAll(el);growAll(el)},'0px 0px 200px 0px');
onSee('podium-sec',el=>flickAll(el));
onSee('groups-sec',el=>{flickAll(el);growAll(el)});
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
    html = (TEMPLATE
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
