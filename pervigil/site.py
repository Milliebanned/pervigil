"""Build the public page (docs/index.html, served by GitHub Pages) from the scorecard and live log.

    python -m pervigil.site
"""
import glob
import html
import json
import math
import os
import time

from . import scorecard
from .sessions import NY, current_session, from_ms

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
LOG = os.path.join(ROOT, "logs", "paper_log.jsonl")
NOTES = os.path.join(ROOT, "logs", "notes")
REPO = "https://github.com/Milliebanned/pervigil"
LABEL = {"do_nothing": "Do nothing", "always_fade": "Always fade", "always_follow": "Always follow",
         "agent_run0": "Pervigil agent"}
# Chart colour follows the entity, in a fixed order (validated categorical slots 1-3, light / dark).
SERIES = [("agent_run0", "s1"), ("always_follow", "s2"), ("always_fade", "s3")]

# The mark: an eye that stays open, with a crescent moon for an iris.
LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Pervigil">
<defs><mask id="m"><rect width="64" height="64" fill="#fff"/><circle cx="37.5" cy="27.5" r="9.5" fill="#000"/></mask></defs>
<path d="M4 32 Q32 5 60 32 Q32 59 4 32Z" fill="none" stroke="{ink}" stroke-width="3.5" stroke-linejoin="round"/>
<circle cx="32" cy="32" r="12" fill="{accent}" mask="url(#m)"/></svg>"""

CSS = """
:root{--bg:#f6f4ee;--card:#fcfcfb;--ink:#14161c;--mute:#565b66;--line:#dedbd1;--accent:#b7791f;--good:#0a7d4f;--bad:#b3261e;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--open:#cfcabb;--hero:#10141f;--hero-ink:#f3efe4;--hero-mute:#a9adb8;--hero-line:#2a3040;--lamp:#e9b44c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0c0f17;--card:#1a1a19;--ink:#eef0f3;--mute:#a3a8b3;--line:#2c2f36;
--accent:#e9b44c;--good:#4cc38a;--bad:#f2867d;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--open:#4a4f5c}}
:root[data-theme="dark"]{--bg:#0c0f17;--card:#1a1a19;--ink:#eef0f3;--mute:#a3a8b3;--line:#2c2f36;
--accent:#e9b44c;--good:#4cc38a;--bad:#f2867d;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--open:#4a4f5c}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:0 16px}
.serif{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif}
header{background:var(--hero);color:var(--hero-ink);padding:44px 0 36px}
.brand{display:flex;align-items:center;gap:16px}.brand svg{width:64px;height:64px;flex:none}
.brand h1{font-size:44px;line-height:1;margin:0;letter-spacing:.01em;font-weight:600}
.motto{font-style:italic;font-size:19px;color:var(--lamp);margin:6px 0 0}.motto span{font-style:normal;color:var(--hero-mute);font-size:14px;margin-left:8px}
.lede{max-width:680px;font-size:18px;margin:22px 0 18px;color:var(--hero-ink)}
.pill{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--hero-line);border-radius:999px;padding:5px 14px;font-size:14px;color:var(--hero-ink)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--lamp)}.dot.off{background:transparent;border:2px solid var(--hero-mute)}
.week{margin-top:26px}.week .bar{display:flex;height:14px;border-radius:4px;overflow:hidden;gap:2px}
.week .bar i{display:block;height:100%}.week .c{background:var(--lamp)}.week .o{background:#4a5163}
.week .days{display:grid;grid-template-columns:repeat(7,1fr);font-size:12px;color:var(--hero-mute);margin-top:5px}
.week .key{font-size:13px;color:var(--hero-mute);margin-top:8px}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 5px 0 12px;vertical-align:baseline}
.week .key .sw:first-child{margin-left:0}
main{padding:8px 0 72px}h2{font-size:24px;margin:44px 0 6px;font-weight:600}
p{margin:8px 0}.sub{color:var(--mute);font-size:15px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:14px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:24px}
.tile{margin:0}.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums;line-height:1.2}.tile span{color:var(--mute);font-size:13px}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;counter-reset:s}
.step{margin:0;font-size:14px}.step b{display:block;font-size:16px}.step:before{counter-increment:s;content:counter(s);display:block;color:var(--accent);font:600 22px/1 "Iowan Old Style",Georgia,serif;margin-bottom:6px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:0}th{color:var(--mute);font-weight:600}.pos{color:var(--good)}.neg{color:var(--bad)}.agent td{font-weight:650}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:14px;margin-bottom:6px}.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.chart{position:relative;overflow-x:auto}.chart svg{display:block;width:100%;min-width:640px;height:auto}.chart text{fill:var(--mute);font-size:12px}.chart .end{fill:var(--ink);font-size:13px}
.grid{stroke:var(--line);stroke-width:1}.zero{stroke:var(--mute);stroke-width:1}.cross{stroke:var(--mute);stroke-width:1;stroke-dasharray:3 3;visibility:hidden}
.tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;
box-shadow:0 4px 14px rgba(0,0,0,.18);visibility:hidden;white-space:nowrap;font-variant-numeric:tabular-nums}.tip i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.log{font-size:14px;padding:12px 16px;margin:8px 0}.log .t{color:var(--mute);font-size:12px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 7px;font-size:12px;margin-right:6px;color:var(--mute)}
pre{white-space:pre-wrap;font:15px/1.6 inherit;margin:0}a{color:var(--accent)}
footer{border-top:1px solid var(--line);padding:22px 0 40px;color:var(--mute);font-size:14px}
@media (max-width:560px){.motto span{display:block;margin:2px 0 0}.brand h1{font-size:34px}.brand svg{width:48px;height:48px}.lede{font-size:16px}}
"""

HOVER_JS = """
(function(){var c=document.getElementById('chart');if(!c)return;var d=JSON.parse(document.getElementById('chart-data').textContent);
var svg=c.querySelector('svg'),cross=c.querySelector('.cross'),tip=c.querySelector('.tip');
function fmt(v){return (v>=0?'+':'')+(v*100).toFixed(2)+'%'}
function move(ev){var r=svg.getBoundingClientRect(),k=d.w/r.width,x=((ev.touches?ev.touches[0].clientX:ev.clientX)-r.left)*k;
var i=Math.round((x-d.l)/(d.pw)*(d.n-1));i=Math.max(0,Math.min(d.n-1,i));var px=d.l+(d.n>1?i/(d.n-1):0)*d.pw;
cross.setAttribute('x1',px);cross.setAttribute('x2',px);cross.style.visibility='visible';
var h='<div style="color:var(--mute)">'+d.dates[i]+' open</div>';d.series.forEach(function(s){h+='<div><i style="background:var(--'+s.c+')"></i>'+s.name+' <b>'+fmt(s.v[i])+'</b></div>'});
tip.innerHTML=h;tip.style.visibility='visible';var left=px/k+12;if(left+tip.offsetWidth>r.width)left=px/k-tip.offsetWidth-12;tip.style.left=Math.max(0,left)+'px';tip.style.top='8px'}
function out(){cross.style.visibility='hidden';tip.style.visibility='hidden'}
svg.addEventListener('mousemove',move);svg.addEventListener('touchstart',move,{passive:true});svg.addEventListener('touchmove',move,{passive:true});svg.addEventListener('mouseleave',out)})();
"""


def e(s):
    return html.escape(str(s))


def pct(x, signed=True):
    if x is None:
        return "-"
    s = f"{x:+.2%}" if signed else f"{x:.2%}"
    return f"<span class='{'pos' if x > 0 else 'neg' if x < 0 else ''}'>{s}</span>"


def name(n):
    if n in LABEL:
        return LABEL[n]
    if n.startswith("agent_run"):
        return f"Pervigil agent, pass {int(n[9:]) + 1}"
    return n


def week_strip():
    """One week, Monday to Sunday in New York time: 32.5 hours open, 135.5 closed."""
    segs = []
    for day in range(7):
        if day < 5:
            segs += [("c", 9.5), ("o", 6.5), ("c", 8.0)]
        else:
            segs.append(("c", 24.0))
    merged = []
    for k, h in segs:
        if merged and merged[-1][0] == k:
            merged[-1][1] += h
        else:
            merged.append([k, h])
    bar = "".join(f"<i class='{k}' style='flex:{h}'></i>" for k, h in merged)
    days = "".join(f"<span>{d}</span>" for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    return (f"<div class='week'><div class='bar' role='img' aria-label='US market open 32.5 of 168 hours a week'>{bar}</div>"
            f"<div class='days'>{days}</div><div class='key'><span class='sw' style='background:#4a5163'></span>US market open, 32.5 h"
            f"<span class='sw' style='background:var(--lamp)'></span>Closed, 135.5 h (81% of the week): Pervigil's watch</div></div>")


def status_pill(now_ms):
    s = current_session(now_ms)
    asof = from_ms(now_ms).strftime("%d %b %H:%M UTC")
    if s is None:
        return f"<span class='pill'><span class='dot off'></span>Off duty: US market is open · as of {asof}</span>"
    opens = from_ms(s.open_ms).astimezone(NY).strftime("%a %d %b, 09:30 New York")
    return f"<span class='pill'><span class='dot'></span>On watch: {s.kind} window, until {opens} · as of {asof}</span>"


def equity_chart(results):
    present = [(k, c) for k, c in SERIES if k in results]
    if not present:
        return ""
    dates = [s["session"] for s in results[present[0][0]]["sessions"]]
    series = []
    for k, c in present:
        eq, vals = 1.0, []
        for s in results[k]["sessions"]:
            eq *= 1 + s["ret"]
            vals.append(eq - 1)
        series.append({"key": k, "name": LABEL[k], "c": c, "v": vals})
    n = len(dates)
    W, H, L, R, T, B = 900, 320, 52, 150, 14, 30
    pw, ph = W - L - R, H - T - B
    lo = min(0.0, min(min(s["v"]) for s in series))
    hi = max(0.0, max(max(s["v"]) for s in series))
    step = next(st for st in (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5) if (hi - lo) / st <= 6)
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    X = lambda i: L + (i / (n - 1) if n > 1 else 0) * pw
    Y = lambda v: T + (hi - v) / (hi - lo) * ph
    parts = []
    v = lo
    while v <= hi + 1e-9:
        cls = "zero" if abs(v) < 1e-9 else "grid"
        parts.append(f"<line class='{cls}' x1='{L}' x2='{L + pw}' y1='{Y(v):.1f}' y2='{Y(v):.1f}'/>"
                     f"<text x='{L - 8}' y='{Y(v) + 4:.1f}' text-anchor='end'>{v:+.0%}</text>" if step >= 0.01 else
                     f"<line class='{cls}' x1='{L}' x2='{L + pw}' y1='{Y(v):.1f}' y2='{Y(v):.1f}'/>"
                     f"<text x='{L - 8}' y='{Y(v) + 4:.1f}' text-anchor='end'>{v:+.1%}</text>")
        v += step
    for i in sorted({0, n // 3, 2 * n // 3, n - 1}):
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        parts.append(f"<text x='{X(i):.1f}' y='{H - 8}' text-anchor='{anchor}'>{e(dates[i][5:])}</text>")
    for s in series:
        pts = " ".join(f"{X(i):.1f},{Y(val):.1f}" for i, val in enumerate(s["v"]))
        parts.append(f"<polyline points='{pts}' fill='none' stroke='var(--{s['c']})' stroke-width='2' "
                     f"stroke-linejoin='round' stroke-linecap='round'/>")
    # Direct labels at the line ends, nudged apart so they never overlap.
    ends = sorted(series, key=lambda s: Y(s["v"][-1]))
    ys = []
    for s in ends:
        y = Y(s["v"][-1])
        if ys and y - ys[-1] < 16:
            y = ys[-1] + 16
        ys.append(y)
        parts.append(f"<circle cx='{X(n - 1):.1f}' cy='{Y(s['v'][-1]):.1f}' r='4' fill='var(--{s['c']})' stroke='var(--card)' stroke-width='2'/>"
                     f"<text class='end' x='{X(n - 1) + 10:.1f}' y='{y + 4:.1f}'>{e(s['name'])} {s['v'][-1]:+.1%}</text>")
    parts.append(f"<line class='cross' y1='{T}' y2='{T + ph}'/>")
    legend = "".join(f"<span><i style='background:var(--{s['c']})'></i>{e(s['name'])}</span>" for s in series)
    data = {"w": W, "l": L, "pw": pw, "n": n, "dates": dates,
            "series": [{"name": s["name"], "c": s["c"], "v": [round(x, 5) for x in s["v"]]} for s in series]}
    return (f"<div class='card'><div class='legend'>{legend}</div><div class='chart' id='chart'>"
            f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Cumulative return by policy across replayed nights'>{''.join(parts)}</svg>"
            f"<div class='tip'></div></div><p class='sub'>Cumulative return after costs, night by night. "
            f"The zero line is doing nothing. Exact figures are in the table below.</p></div>"
            f"<script type='application/json' id='chart-data'>{json.dumps(data)}</script>")


def policy_table(card):
    rows = ["<tr><th>Policy</th><th>Nights</th><th>Nights traded</th><th>Return</th><th>Sharpe</th><th>Sortino</th>"
            "<th>Max drawdown</th><th>Win rate</th><th>Worst night</th><th>Risk blocks</th></tr>"]
    order = sorted(card["policies"], key=lambda n: (not n.startswith("agent"), n))
    for n in order:
        m = card["policies"][n]
        win = f"{m['win_rate']:.0%}" if m["win_rate"] is not None else "-"
        rows.append(f"<tr class='{'agent' if n.startswith('agent') else ''}'><td>{e(name(n))}</td><td>{m['sessions']}</td>"
                    f"<td>{m['sessions_traded']}</td><td>{pct(m['total_return'])}</td><td>{m['sharpe']:.2f}</td>"
                    f"<td>{m['sortino']:.2f}</td><td>{pct(m['max_drawdown'])}</td><td>{win}</td>"
                    f"<td>{pct(m['worst_session'])}</td><td>{m['risk_violations']} of {m['proposals']}</td></tr>")
    return "<div class='card scroll'><table>" + "".join(rows) + "</table></div>"


def stress_table(card):
    if not card["stress"]:
        return ""
    cols = [k for k in card["stress"][0] if k not in ("session", "avg_abs_z")]
    cols.sort(key=lambda n: (not n.startswith("agent"), n))
    head = "<tr><th>Night (open date)</th><th>Average move, in normal days</th>" + "".join(f"<th>{e(name(c))}</th>" for c in cols) + "</tr>"
    body = "".join(f"<tr><td>{e(r['session'])}</td><td>{r['avg_abs_z']:.2f}</td>" + "".join(f"<td>{pct(r.get(c))}</td>" for c in cols) + "</tr>"
                   for r in card["stress"])
    return "<div class='card scroll'><table>" + head + body + "</table></div>"


def live_section():
    if not os.path.exists(LOG):
        return "<p class='sub'>The live paper log has not started yet.</p>"
    recs = [json.loads(l) for l in open(LOG) if l.strip()]
    decisions = sum(r["event"] == "decision" for r in recs)
    out = [f"<p class='sub'>{len(recs)} entries, {decisions} decisions, {e(recs[0]['time'])} to {e(recs[-1]['time'])} (UTC). Newest first. "
           f"<a href='{REPO}/blob/main/logs/paper_log.jsonl'>Raw log</a></p>"]
    for r in reversed(recs[-60:]):
        bits = [f"<span class='tag'>{e(r['event'].replace('_', ' '))}</span>"]
        if r.get("session"):
            bits.append(f"<span class='tag'>{e(r['session'])} open</span>")
        if "equity" in r:
            bits.append(f"equity {r['equity']:,.2f} USDT")
        body = ""
        if r["event"] == "decision":
            acts = "; ".join(f"<b>{e(t)}</b> to {w:+.0%} — {e(r['reasons'].get(t, ''))}" for t, w in r["approved"].items()) or "No trade."
            blocked = "<div>Risk layer blocked: " + ", ".join(f"{e(t)} ({e(rule.replace('_', ' '))})" for t, rule in r["violations"]) + "</div>" if r["violations"] else ""
            body = f"<div>{acts}</div>{blocked}<div class='sub'>{e(r.get('note', ''))}</div>"
        elif r["event"] == "session_exit":
            body = f"<div>Flattened after the open. Night return {pct(r['ret'])}</div>"
        elif r["event"] == "stops":
            body = "<div>Stops fired: " + ", ".join(f"{e(t)} ({e(rule.replace('_', ' '))})" for t, rule in r["stops"]) + "</div>"
        elif r["event"] == "decision_error":
            body = f"<div class='neg'>{e(r['error'])}</div>"
        out.append(f"<div class='card log'><div class='t'>{e(r['time'])}</div>{' '.join(bits)}{body}</div>")
    return "".join(out)


def notes_section():
    files = sorted(glob.glob(os.path.join(NOTES, "*.md")), reverse=True)[:5]
    return "".join(f"<div class='card'><pre>{e(open(f).read())}</pre></div>" for f in files) or \
        "<p class='sub'>The first morning note is written after the first completed night.</p>"


def tiles(card):
    pol = card["policies"]
    agent = pol.get("agent_run0")
    if not agent:
        return "<p class='sub' style='margin-top:24px'>The agent replay is still running; the fixed-rule baselines are below.</p>"
    va = card.get("value_added", {})
    con = card.get("consistency") or {}
    best = max((n for n in pol if not n.startswith("agent")), key=lambda n: pol[n]["total_return"])
    items = [
        ("Nights replayed", agent["sessions"]),
        ("Agent return, after costs", pct(agent["total_return"])),
        (f"Against the best fixed rule ({name(best)})", pct(va[best]["total_return"])),
        ("Max drawdown", pct(agent["max_drawdown"])),
        ("Proposals the risk layer overruled", f"{agent['risk_violation_rate']:.1%}"),
        ("Same call across repeat passes", f"{con['agreement']:.0%}" if con.get("agreement") is not None else "-"),
    ]
    return "<div class='tiles'>" + "".join(f"<div class='card tile'><b>{v}</b><span>{e(k)}</span></div>" for k, v in items) + "</div>"


STEPS = [
    ("Sense", "Each name's move since the close, sized against its normal day, plus Bitcoin, earnings and headlines."),
    ("Decide", "The model sets a target weight per name with a one-line reason. Staying out is allowed."),
    ("Guard", "Hard limits it cannot override: 20% per name, 60% gross, stop-losses, no late entries."),
    ("Trade", "Paper fills at Bitget prices, with 0.10% fee and 0.05% slippage per side."),
    ("Hand off", "Everything is closed 15 minutes after the open, and a morning note is written."),
]


def build(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    card = scorecard.build()
    results = {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(os.path.join(scorecard.RESULTS, "*.json"))
               if not p.endswith("scorecard.json") and "consistency_run" not in p}
    hero_logo = LOGO.format(ink="#f3efe4", accent="#e9b44c")
    steps = "".join(f"<div class='card step'><b>{e(t)}</b>{e(d)}</div>" for t, d in STEPS)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pervigil</title><meta name="description" content="An LLM agent that trades tokenized US stocks on Bitget while the US market is closed, benchmarked against fixed rules.">
<link rel="icon" type="image/svg+xml" href="logo.svg"><style>{CSS}</style></head><body>
<header><div class="wrap">
<div class="brand">{hero_logo}<div><h1 class="serif">Pervigil</h1><p class="motto serif">Vigilat dum dormis<span>It keeps watch while you sleep</span></p></div></div>
<p class="lede">An AI agent that trades tokenized US stocks on Bitget only while the US market is closed, and a proving ground that
measures whether it beats a fixed rule. Paper trading only.</p>
{status_pill(now_ms)}
{week_strip()}
</div></header>
<main><div class="wrap">
{tiles(card)}
<h2 class="serif">The watch, step by step</h2>
<div class="steps">{steps}</div>
<h2 class="serif">Proving ground</h2>
<p class="sub">Every policy replays the same past nights and weekends on real Bitget rToken prices, through the same risk layer and
paper book. Each decision sees only candles that had already closed. Always fade bets a closed-hours move of one normal day or
more reverses; always follow bets it continues.</p>
{equity_chart(results)}
{policy_table(card)}
<h2 class="serif">The hardest nights</h2>
<p class="sub">The five nights the market moved most while closed, and what each policy made or lost.</p>
{stress_table(card)}
<h2 class="serif">Morning notes</h2>{notes_section()}
<h2 class="serif">Live paper log</h2>{live_section()}
</div></main>
<footer><div class="wrap">Pervigil · Bitget AI Base Camp Hackathon S2, Agentic Trading · <a href="{REPO}">Code, data and logs on GitHub</a> ·
Paper trading only; nothing here is investment advice.</div></footer>
<script>{HOVER_JS}</script></body></html>"""
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, "index.html"), "w").write(page)
    open(os.path.join(DOCS, "logo.svg"), "w").write(LOGO.format(ink="#b7791f", accent="#e9b44c"))
    return os.path.join(DOCS, "index.html")


if __name__ == "__main__":
    print(build())
