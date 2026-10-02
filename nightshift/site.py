"""Build the public results page (docs/index.html, served by GitHub Pages) from the scorecard and live log.

    python -m nightshift.site
"""
import glob
import html
import json
import os

from . import scorecard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "index.html")
LOG = os.path.join(ROOT, "logs", "paper_log.jsonl")
NOTES = os.path.join(ROOT, "logs", "notes")
LABEL = {"do_nothing": "Do nothing", "always_fade": "Always fade", "always_follow": "Always follow"}

CSS = """
:root{--bg:#f7f7f4;--card:#fff;--ink:#16181d;--mute:#5d6470;--line:#e2e2dc;--good:#0a7d4f;--bad:#b3261e;--accent:#0b6b74}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#101214;--card:#181b1f;--ink:#eceef1;--mute:#9aa2ad;--line:#2a2e34;--good:#4cc38a;--bad:#f2867d;--accent:#5cc8d2}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:30px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 10px}
p{margin:8px 0}.sub{color:var(--mute)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th:first-child,td:first-child{text-align:left}
th{color:var(--mute);font-weight:600}.pos{color:var(--good)}.neg{color:var(--bad)}.agent td{font-weight:650}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.tile b{display:block;font-size:24px}
.tile span{color:var(--mute);font-size:13px}.log{font-size:14px}.log .t{color:var(--mute);font-size:12px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 6px;font-size:12px;margin-right:4px}
pre{white-space:pre-wrap;font:14px/1.5 inherit;margin:0}a{color:var(--accent)}
"""


def e(s):
    return html.escape(str(s))


def pct(x, signed=True, cls=True):
    if x is None:
        return "-"
    s = f"{x:+.2%}" if signed else f"{x:.2%}"
    c = (" class='pos'" if x > 0 else " class='neg'" if x < 0 else "") if cls else ""
    return f"<span{c}>{s}</span>"


def name(n):
    return LABEL.get(n, n.replace("agent_run", "Night Shift agent, pass "))


def policy_table(card):
    rows = ["<tr><th>Policy</th><th>Nights</th><th>Nights traded</th><th>Return</th><th>Sharpe</th><th>Sortino</th>"
            "<th>Max drawdown</th><th>Win rate</th><th>Worst night</th><th>Risk blocks</th></tr>"]
    for n, m in card["policies"].items():
        win = f"{m['win_rate']:.0%}" if m["win_rate"] is not None else "-"
        rows.append(f"<tr class='{'agent' if n.startswith('agent') else ''}'><td>{e(name(n))}</td><td>{m['sessions']}</td>"
                    f"<td>{m['sessions_traded']}</td><td>{pct(m['total_return'])}</td><td>{m['sharpe']:.2f}</td>"
                    f"<td>{m['sortino']:.2f}</td><td>{pct(m['max_drawdown'])}</td><td>{win}</td>"
                    f"<td>{pct(m['worst_session'])}</td><td>{m['risk_violations']} / {m['proposals']}</td></tr>")
    return "<div class='scroll'><table>" + "".join(rows) + "</table></div>"


def stress_table(card):
    if not card["stress"]:
        return ""
    cols = [k for k in card["stress"][0] if k not in ("session", "avg_abs_z")]
    head = "<tr><th>Night (open date)</th><th>Avg move, in normal days</th>" + "".join(f"<th>{e(name(c))}</th>" for c in cols) + "</tr>"
    body = "".join(f"<tr><td>{e(r['session'])}</td><td>{r['avg_abs_z']:.2f}</td>" + "".join(f"<td>{pct(r.get(c))}</td>" for c in cols) + "</tr>"
                   for r in card["stress"])
    return "<div class='scroll'><table>" + head + body + "</table></div>"


def live_section():
    if not os.path.exists(LOG):
        return "<p class='sub'>The live paper log has not started yet.</p>"
    recs = [json.loads(l) for l in open(LOG)]
    out = [f"<p class='sub'>{len(recs)} log entries, {recs[0]['time']} to {recs[-1]['time']} (UTC). Newest first.</p>"]
    for r in reversed(recs[-60:]):
        bits = [f"<span class='tag'>{e(r['event'])}</span>"]
        if r.get("session"):
            bits.append(f"<span class='tag'>open {e(r['session'])}</span>")
        if "equity" in r:
            bits.append(f"equity {r['equity']:,.2f}")
        body = ""
        if r["event"] == "decision":
            acts = ", ".join(f"{e(t)} → {w:+.2f} ({e(r['reasons'].get(t, ''))})" for t, w in r["approved"].items()) or "no trade"
            blocked = "; blocked: " + ", ".join(f"{e(t)} ({e(rule)})" for t, rule in r["violations"]) if r["violations"] else ""
            body = f"<div>{acts}{blocked}</div><div class='sub'>{e(r.get('note', ''))}</div>"
        elif r["event"] == "session_exit":
            body = f"<div>Flattened after the open. Night return {pct(r['ret'])}</div>"
        elif r["event"] == "stops":
            body = "<div>Stops fired: " + ", ".join(f"{e(t)} ({e(rule)})" for t, rule in r["stops"]) + "</div>"
        elif r["event"] == "decision_error":
            body = f"<div class='neg'>{e(r['error'])}</div>"
        out.append(f"<div class='card log'><div class='t'>{e(r['time'])}</div>{' '.join(bits)}{body}</div>")
    return "".join(out)


def notes_section():
    files = sorted(glob.glob(os.path.join(NOTES, "*.md")), reverse=True)[:5]
    return "".join(f"<div class='card'><pre>{e(open(f).read())}</pre></div>" for f in files) or \
        "<p class='sub'>The first morning note appears after the first completed night.</p>"


def build():
    card = scorecard.build()
    pol = card["policies"]
    agent = pol.get("agent_run0")
    tiles = ""
    if agent:
        va = card.get("value_added", {})
        con = card.get("consistency") or {}
        best_rule = max((n for n in pol if not n.startswith("agent")), key=lambda n: pol[n]["total_return"])
        tiles = "<div class='tiles'>" + "".join(f"<div class='card tile'><b>{v}</b><span>{e(k)}</span></div>" for k, v in [
            ("Nights replayed", agent["sessions"]),
            ("Agent return, after costs", pct(agent["total_return"])),
            (f"vs best fixed rule ({name(best_rule)})", pct(va[best_rule]["total_return"])),
            ("Proposals the risk layer overruled", f"{agent['risk_violation_rate']:.1%}"),
            ("Same call across repeat passes", f"{con['agreement']:.0%}" if con.get("agreement") is not None else "-"),
            ("Max drawdown", pct(agent["max_drawdown"])),
        ]) + "</div>"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Night Shift</title><style>{CSS}</style></head><body><main>
<h1>Night Shift</h1>
<p class="sub">An LLM agent that trades tokenized US stocks on Bitget only while the US market is closed, and a proving ground
that measures whether it beats a fixed rule. Paper trading only. Bitget AI Base Camp Hackathon S2, Agentic Trading.</p>
{tiles}
<h2>Proving ground: the agent against three fixed rules</h2>
<p>Every policy replays the same past nights and weekends on real Bitget rToken prices, through the same risk layer and
paper book, with 0.10% fee and 0.05% slippage per side. Each decision sees only candles that had already closed.</p>
<div class="card">{policy_table(card)}</div>
<h2>Stress: the nights the market moved most</h2>
<div class="card">{stress_table(card)}</div>
<h2>Morning notes</h2>{notes_section()}
<h2>Live paper log</h2>{live_section()}
</main></body></html>"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(page)
    return OUT


if __name__ == "__main__":
    print(build())
