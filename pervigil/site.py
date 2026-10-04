"""Build the public page (docs/index.html, served by GitHub Pages) from the scorecard and live log.

    python -m pervigil.site

The look lives in page.css; this file only fills it with numbers.
"""
import glob
import html
import json
import math
import os
import re
import time
from datetime import datetime, timezone

from . import scorecard
from .replay import checkpoints
from .sessions import NY, current_session, from_ms

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
LOG = os.path.join(ROOT, "logs", "paper_log.jsonl")
NOTES = os.path.join(ROOT, "logs", "notes")
ARCHIVE = os.path.join(scorecard.RESULTS, "archive")
CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.css")
REPO = "https://github.com/Milliebanned/pervigil"
AGENT = "agent_run0"
FEED_LIMIT = 30

# policy -> (name, what it does, css suffix). Drawn in this order, so the agent's line sits on top.
POLICY = {
    "do_nothing": ("Do nothing", "stay in cash", "none"),
    "always_follow": ("Always follow", "bet a big overnight move continues", "follow"),
    "always_fade": ("Always fade", "bet a big overnight move reverses", "fade"),
    AGENT: ("Pervigil", "the agent", "agent"),
}

LOGO_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" role="img" aria-label="Pervigil">'
            '<rect width="48" height="48" rx="12" fill="#5a58f2"/><g fill="#ffffff"><path d="M6 24A20 20 0 0 1 42 24A20 20 0 0 1 6 24Z"/></g>'
            '<g fill="#5a58f2"><circle cx="24" cy="24" r="8"/></g><g fill="#ffffff"><circle cx="26.6" cy="22.4" r="6.4"/></g>'
            '<g fill="#3ff0a6"><circle cx="26.6" cy="22.4" r="2.7"/></g></svg>')
# The same mark as a reusable symbol whose colours follow the theme.
LOGO_SYMBOL = """<svg width="0" height="0" style="position:absolute" aria-hidden="true"><symbol id="logo" viewBox="0 0 48 48">
<rect class="logo-bg" width="48" height="48" rx="12"/><g class="logo-fg"><path d="M6 24A20 20 0 0 1 42 24A20 20 0 0 1 6 24Z"/></g>
<g class="logo-bg"><circle cx="24" cy="24" r="8"/></g><g class="logo-fg"><circle cx="26.6" cy="22.4" r="6.4"/></g>
<g class="logo-dot"><circle cx="26.6" cy="22.4" r="2.7"/></g></symbol></svg>"""
USE_LOGO = '<svg aria-hidden="true"><use href="#logo"/></svg>'

NAV = [("top", "Overview"), ("numbers", "Numbers"), ("how", "How it works"), ("proving", "Proving ground"),
       ("lab", "Beat the agent"), ("history", "How it got here"), ("hardest", "Hardest nights"), ("log", "Live log"), ("notes", "Morning notes")]

STEPS = [
    ("Sense", "Each stock's move since the close, sized against its normal day, plus Bitcoin, earnings and news."),
    ("Decide", "Qwen sets a target per stock with a one-sentence reason. Staying out is allowed."),
    ("Risk layer", "Hard limits it cannot override: 20% per stock, 60% in total, stop-losses, no late entries."),
    ("Execute", "Paper fills at Bitget prices with fees and slippage, mirrored as orders on Bitget's demo exchange."),
    ("Flatten", "Everything is closed 15 minutes after the open, and a morning note is written."),
]

# Earlier runs kept in results/archive: (scorecard file or None for the current one, label, title, what changed).
STAGES = [
    ("scorecard_calendar_bug.json", "Run 1", "First run",
     "The agent as first written. It bet that big overnight moves would reverse, and finished behind doing nothing."),
    ("scorecard_calendar_fixed_no_desk_note.json", "Run 2", "After fixing a data bug",
     "The earnings calendar was one night early, so on the night a company reported, the agent was told there was "
     "no news. Fixed, with no change to its instructions."),
    (None, "Run 3 · current", "After adding one rule",
     "On a night a company has just reported earnings, do not bet against the move. The whole gain comes from four "
     "earnings nights, and the rule was written after seeing them, so it is not yet proven on new nights."),
]

NAV_JS = """(function () {
  var links = [].slice.call(document.querySelectorAll('.nav a'));
  if (!('IntersectionObserver' in window)) return;
  var io = new IntersectionObserver(function (es) {
    es.forEach(function (e) {
      if (e.isIntersecting) links.forEach(function (l) { l.classList.toggle('on', l.hash === '#' + e.target.id); });
    });
  }, { rootMargin: '-25% 0px -65% 0px' });
  links.forEach(function (l) { var t = document.querySelector(l.hash); if (t) io.observe(t); });
})();"""


def e(s):
    return html.escape(str(s))


def sign(x):
    return "pos" if x > 0 else "neg" if x < 0 else "flat"


def pct_text(x, digits=2):
    if abs(x) < 0.5 * 10 ** -(digits + 2):
        return f"{0:.{digits}%}"
    return f"{x:+.{digits}%}".replace("-", "−")


def pct(x):
    return f'<span class="num {sign(x)}">{pct_text(x)}</span>' if x is not None else '<span class="num flat">—</span>'


def ratio(x):
    return f"{x:.2f}".replace("-", "−")


def day(d):
    return f"{d:%a} {d.day} {d:%b}"


# ---------- status and hero ----------

def status_bar(now_ms):
    s = current_session(now_ms)
    if s is None:
        cls, pill, line = "status off", "Off duty", "US market open"
    else:
        ahead = [c for c in checkpoints(s) if c > now_ms]
        nxt = f"next decision in {max(1, math.ceil((ahead[0] - now_ms) / 3_600_000))}h" if ahead \
            else "last decision made, flat 15 minutes after the open"
        cls, pill, line = "status", "On watch", f"US market closed, {nxt}"
    asof = from_ms(now_ms).astimezone(NY)
    return (f'<div class="top panel"><p class="{cls}"><span class="pill"><i aria-hidden="true"></i>{pill}</span>'
            f'<span>{line} · updated {asof:%H:%M} ET, {day(asof)}</span></p>'
            f'<label class="theme"><input type="checkbox" id="theme-switch" aria-label="Use light theme">'
            f'<span class="label">Light</span></label></div>')


def week_strip(now_ms):
    """One week, hour by hour, Monday to Sunday in New York time: 32.5 hours open of 168."""
    local = from_ms(now_ms).astimezone(NY)
    now = local.weekday() * 24 + local.hour + local.minute / 60
    opens = "".join(f'<rect class="open" x="{d * 24 + 9.5}" width="6.5" height="10"/>' for d in range(5))
    days = "".join(f"<li>{d}</li>" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    return (f'<div class="week card"><svg viewBox="0 0 168 10" preserveAspectRatio="none" role="img" aria-label="One week, '
            f'hour by hour. The US market is open for five short sessions; the rest of the week it is closed.">'
            f'<rect class="closed" width="168" height="10"/>{opens}<line class="now" x1="{now:.2f}" x2="{now:.2f}" y1="-3" y2="13"/></svg>'
            f'<ol class="label" aria-hidden="true">{days}</ol>'
            f"<p>Purple is when the US stock market is open: 32.5 hours of 168. <b>The other 80% of the week is "
            f"Pervigil's watch.</b> The green marker is now.</p></div>")


# ---------- headline numbers ----------

def wins(m):
    return round((m["win_rate"] or 0) * m["sessions_traded"])


def figures(agent):
    if not agent:
        return '<p class="figures-note">The agent replay has not finished yet; the fixed rules are below.</p>'
    r, dd = agent["total_return"], agent["max_drawdown"]
    out_nights = agent["sessions"] - agent["sessions_traded"]
    big_pct = lambda x: f'<dd class="big {sign(x)}">{pct_text(x)[:-1]}<small>%</small></dd>'
    cards = [
        ("Return", big_pct(r), "after fees and slippage"),
        ("Sharpe", f'<dd class="big">{ratio(agent["sharpe"])}</dd>', "return per unit of risk, annualised"),
        ("Worst drawdown", big_pct(dd), "deepest fall from a peak"),
        ("Winning nights", f'<dd class="big">{wins(agent)}<small>of {agent["sessions_traded"]}</small></dd>',
         f"it stayed out the other {out_nights}"),
        ("Risk-rule blocks", f'<dd class="big">{agent["risk_violations"]}<small>of {agent["proposals"]}</small></dd>',
         "proposals the hard limits overruled"),
    ]
    body = "".join(f'<div class="figure card"><dt class="label">{k}</dt>{v}<dd class="sub">{s}</dd></div>' for k, v, s in cards)
    return (f'<dl class="figures">{body}</dl><p class="figures-note">A replay of {agent["sessions"]} past nights and weekends '
            f"on real Bitget prices. Paper trading: no real money was at risk.</p>")


# ---------- proving ground ----------

def curves(results):
    """policy -> cumulative return after each night, starting at 0."""
    out = {}
    for k in POLICY:
        if k in results:
            eq, vals = 1.0, [0.0]
            for s in results[k]["sessions"]:
                eq *= 1 + s["ret"]
                vals.append(eq - 1)
            out[k] = vals
    return out


def chart(results):
    series = curves(results)
    if not series:
        return ""
    n = len(next(iter(series.values()))) - 1
    lo = min(min(v) for v in series.values())
    hi = max(max(v) for v in series.values())
    step = next(st for st in (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5) if (hi - lo) / st <= 7)
    lo, hi = math.floor(lo / step - 1e-9) * step, math.ceil(hi / step + 1e-9) * step
    frac = lambda v: (hi - v) / (hi - lo)
    W, H = n * 10, 200
    ticks = [lo + i * step for i in range(round((hi - lo) / step) + 1)]
    digits = 0 if step >= 0.01 else 1
    ylab = "".join(f'<span style="top:{frac(v) * 100:.2f}%">{pct_text(v, digits)}</span>' for v in ticks)
    grid = "".join(f'<line class="{"zero" if abs(v) < 1e-9 else "grid"}" x1="0" x2="{W}" y1="{frac(v) * H:.1f}" y2="{frac(v) * H:.1f}"/>'
                   for v in ticks)
    lines = "".join(f'<polyline class="ln ln-{POLICY[k][2]}" points="'
                    + " ".join(f"{i * 10},{frac(v) * H:.1f}" for i, v in enumerate(vals)) + '"/>'
                    for k, vals in series.items())
    marks = [i for i in range(10, n, 10) if n - i >= 5]
    xlab = '<span style="left:0%">Start</span>' + "".join(f'<span style="left:{i / n * 100:.2f}%">{i}</span>' for i in marks) \
        + f'<span style="left:100%">Night {n}</span>'
    order = sorted(series, key=lambda k: -series[k][-1])
    legend = "".join(f'<li><span class="key key-{POLICY[k][2]}"></span>{POLICY[k][0]} {pct(series[k][-1])}</li>' for k in order)
    return (f'<div class="chart-card card"><ul class="legend">{legend}</ul><div class="chart">'
            f'<div class="y" aria-hidden="true">{ylab}</div>'
            f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="Account value over {n} nights for '
            f'the agent and three fixed rules. The figures are in the table below.">{grid}{lines}</svg>'
            f'<div class="x" aria-hidden="true">{xlab}</div></div>'
            f'<p class="chart-cap">Account value after each night, as a change from the starting balance, after costs.</p></div>')


def policy_table(card):
    pol = {k: m for k, m in card["policies"].items() if k in POLICY}
    rows = []
    for k in sorted(pol, key=lambda k: -pol[k]["total_return"]):
        m = pol[k]
        name, what, css = POLICY[k]
        traded = m["sessions_traded"]
        cells = [("Return", pct(m["total_return"])),
                 ("Sharpe", f'<span class="num {sign(m["sharpe"])}">{ratio(m["sharpe"])}</span>' if traded else pct(None)),
                 ("Max drawdown", pct(m["max_drawdown"])),
                 ("Winning nights", f'<span class="num">{wins(m)} of {traded}</span>' if traded else pct(None)),
                 ("Worst night", pct(m["worst_session"])),
                 ("Risk-rule blocks", f'<span class="num">{m["risk_violations"]} of {m["proposals"]}</span>' if m["proposals"] else pct(None))]
        rows.append(f'<tr class="row-{css}"><th scope="row"><span class="key key-{css}" aria-hidden="true"></span>{name}'
                    f"<small>{what}</small></th>" + "".join(f'<td data-label="{h}">{v}</td>' for h, v in cells) + "</tr>")
    nights = next(iter(pol.values()))["sessions"] if pol else 0
    head = "".join(f'<th scope="col">{h}</th>' for h in
                   ("Strategy", "Return", "Sharpe", "Max drawdown", "Winning nights", "Worst night", "Risk-rule blocks"))
    return (f'<table class="tbl"><caption class="label">Scorecard, {nights} nights</caption><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def consistency_line(card):
    c = card.get("consistency")
    if not c or c.get("agreement") is None:
        return ""
    return (f" Asked the same opening question {c['passes']} times on {c['nights_compared']} nights, it made the same call on "
            f"{c['agreement']:.0%} of stock decisions, nearly all of them agreeing to stay out.")


# ---------- beat the agent ----------

LAB_JS = """(function () {
  var el = document.getElementById('lab-data');
  if (!el) return;
  var D = JSON.parse(el.textContent), form = document.getElementById('lab-form'), n = D.agent.length;
  var $ = function (id) { return document.getElementById(id); };
  function curve(r) { var eq = 1, out = [0]; r.forEach(function (x) { eq *= 1 + x / 1e6; out.push(eq - 1); }); return out; }
  function pct(v, d) { d = d == null ? 2 : d; var t = (Math.abs(v) * 100).toFixed(d) + '%';
    return Math.abs(v) * 100 < 0.5 / Math.pow(10, d) ? t : (v > 0 ? '+' : '\u2212') + t; }
  function cls(v) { return Math.abs(v) < 5e-5 ? 'flat' : v > 0 ? 'pos' : 'neg'; }
  function stats(r) {
    var c = curve(r), xs = r.map(function (x) { return x / 1e6; }), m = xs.reduce(function (a, b) { return a + b; }, 0) / n;
    var sd = Math.sqrt(xs.reduce(function (a, b) { return a + (b - m) * (b - m); }, 0) / (n - 1));
    var peak = 0, dd = 0; c.forEach(function (v) { peak = Math.max(peak, v); dd = Math.min(dd, (1 + v) / (1 + peak) - 1); });
    var traded = xs.filter(function (x) { return x !== 0; });
    return { c: c, total: c[n], sharpe: sd ? m / sd * Math.sqrt(252) : null, dd: dd, traded: traded.length,
             wins: traded.filter(function (x) { return x > 0; }).length };
  }
  var A = stats(D.agent);
  function draw(you) {
    var lo = Math.min.apply(null, you.c.concat(A.c)), hi = Math.max.apply(null, you.c.concat(A.c));
    var step = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5].filter(function (s) { return (hi - lo) / s <= 7; })[0];
    lo = Math.floor(lo / step - 1e-9) * step; hi = Math.ceil(hi / step + 1e-9) * step;
    var f = function (v) { return (hi - v) / (hi - lo); }, W = n * 10, y = '', g = '';
    for (var v = lo; v <= hi + 1e-9; v += step) {
      y += '<span style="top:' + (f(v) * 100).toFixed(2) + '%">' + pct(v, step >= 0.01 ? 0 : 1) + '</span>';
      g += '<line class="' + (Math.abs(v) < 1e-9 ? 'zero' : 'grid') + '" x1="0" x2="' + W + '" y1="' + (f(v) * 200).toFixed(1) + '" y2="' + (f(v) * 200).toFixed(1) + '"/>';
    }
    var line = function (c, k) { return '<polyline class="ln ln-' + k + '" points="' + c.map(function (v, i) { return i * 10 + ',' + (f(v) * 200).toFixed(1); }).join(' ') + '"/>'; };
    $('lab-y').innerHTML = y;
    $('lab-svg').innerHTML = g + line(A.c, 'agent') + line(you.c, 'you');
  }
  function update() {
    var sign = form.elements.sign.value, nights = form.elements.nights.value;
    var z = D.z[form.elements.z.value], size = D.size[form.elements.size.value];
    $('lab-z').textContent = Number(z).toFixed(2) + '\u00d7';
    $('lab-size').textContent = Math.round(size * 100) + '%';
    var r = D.runs[sign + '|' + z + '|' + size + '|' + nights], Y = stats(r), gap = Y.total - A.total;
    $('lab-you').textContent = pct(Y.total); $('lab-you').className = cls(Y.total);
    $('lab-legend-you').textContent = pct(Y.total); $('lab-legend-you').className = 'num ' + cls(Y.total);
    $('lab-you-box').className = gap > 5e-5 ? 'win' : ''; $('lab-agent-box').className = gap < -5e-5 ? 'win' : '';
    var better = 0, worse = 0; r.forEach(function (x, i) { if (x > D.agent[i]) better++; else if (x < D.agent[i]) worse++; });
    $('lab-verdict').textContent = !Y.traded ? 'Your rule never traded, so it finished flat. Pervigil stays ahead.'
      : Math.abs(gap) <= 5e-5 ? 'A tie with Pervigil.'
      : gap > 0 ? 'Your rule beats Pervigil by ' + (gap * 100).toFixed(2) + ' points. ' + D.beat + ' of the ' + D.count + ' rules these controls can make do that.'
      : 'Pervigil beats your rule by ' + (-gap * 100).toFixed(2) + ' points.';
    $('lab-sharpe').textContent = Y.sharpe == null ? '\u2014' : Y.sharpe.toFixed(2).replace('-', '\u2212');
    $('lab-dd').textContent = pct(Y.dd);
    $('lab-traded').textContent = Y.traded + ' of ' + n;
    $('lab-wins').textContent = Y.traded ? Y.wins + ' of ' + Y.traded : '\u2014';
    $('lab-nights').textContent = better + ' better, ' + worse + ' worse, ' + (n - better - worse) + ' same';
    draw(Y);
  }
  form.addEventListener('input', update); form.addEventListener('submit', function (e) { e.preventDefault(); });
  update();
})();"""


def lab_section(agent):
    path = os.path.join(scorecard.RESULTS, "lab.json")
    if not agent or not os.path.exists(path):
        return ""
    lab = json.load(open(path))
    total = lambda r: math.prod(1 + x / 1e6 for x in r) - 1
    target = total(lab["agent"])
    beat = sum(total(r) > target + 5e-5 for r in lab["runs"].values())
    zs, sizes = lab["grid"]["z"], lab["grid"]["size"]
    n = len(lab["agent"])
    data = {"agent": lab["agent"], "runs": lab["runs"], "z": [str(z) for z in zs], "size": [str(x) for x in sizes],
            "beat": beat, "count": len(lab["runs"])}
    marks = [i for i in range(10, n, 10) if n - i >= 5]
    xlab = '<span style="left:0%">Start</span>' + "".join(f'<span style="left:{i / n * 100:.2f}%">{i}</span>' for i in marks) \
        + f'<span style="left:100%">Night {n}</span>'
    seg = lambda name, opts: "".join(
        f'<label><input type="radio" name="{name}" value="{v}"{" checked" if i == 0 else ""}><span>{t}</span></label>'
        for i, (v, t) in enumerate(opts))
    return f"""<section class="panel" id="lab">
    <div class="sec-head"><h2>Beat the agent</h2>
    <p>Write your own rule and replay it over the same {n} nights, with the same prices, costs and risk limits the agent faced. These controls can make {len(lab["runs"])} different rules. {beat} of them beat Pervigil.</p></div>
    <div class="lab">
      <form class="lab-controls card" id="lab-form">
        <fieldset><legend>When a stock makes a big move overnight</legend>
          <div class="seg">{seg("sign", [("1", "Follow the move"), ("-1", "Bet against it")])}</div></fieldset>
        <label><span class="q">Act when it has moved at least <output id="lab-z"></output> a normal day</span>
          <input type="range" name="z" min="0" max="{len(zs) - 1}" step="1" value="{zs.index(1.0)}">
          <span class="ends"><span>{zs[0]}&times;</span><span>{zs[-1]}&times;</span></span></label>
        <label><span class="q">Put <output id="lab-size"></output> of the account on each stock</span>
          <input type="range" name="size" min="0" max="{len(sizes) - 1}" step="1" value="{sizes.index(0.1)}">
          <span class="ends"><span>{sizes[0]:.0%}</span><span>{sizes[-1]:.0%}</span></span></label>
        <fieldset><legend>Which stocks</legend>
          <div class="seg">{seg("nights", [("all", "Any stock"), ("earnings", "Only ones that just reported earnings"), ("quiet", "Never ones that just reported")])}</div></fieldset>
      </form>
      <div class="lab-out card" aria-live="polite">
        <div class="duel">
          <div id="lab-you-box"><span class="label">Your rule</span><strong id="lab-you">&nbsp;</strong></div>
          <div id="lab-agent-box"><span class="label">Pervigil</span><strong class="{sign(target)}">{pct_text(target)}</strong></div>
        </div>
        <p class="verdict" id="lab-verdict">Turn on JavaScript to run your rule.</p>
        <dl class="lab-stats">
          <div><dt>Sharpe</dt><dd id="lab-sharpe">&nbsp;</dd></div>
          <div><dt>Worst drawdown</dt><dd id="lab-dd">&nbsp;</dd></div>
          <div><dt>Nights it traded</dt><dd id="lab-traded">&nbsp;</dd></div>
          <div><dt>Winning nights</dt><dd id="lab-wins">&nbsp;</dd></div>
          <div style="grid-column:1/-1"><dt>Night by night against Pervigil</dt><dd id="lab-nights">&nbsp;</dd></div>
        </dl>
      </div>
    </div>
    <div class="chart-card card lab-chart">
      <ul class="legend"><li><span class="key key-you"></span>Your rule <span class="num" id="lab-legend-you"></span></li>
      <li><span class="key key-agent"></span>Pervigil {pct(target)}</li></ul>
      <div class="chart"><div class="y" aria-hidden="true" id="lab-y"></div>
        <svg viewBox="0 0 {n * 10} 200" preserveAspectRatio="none" role="img" aria-label="Account value over {n} nights for your rule and for the agent." id="lab-svg"></svg>
        <div class="x" aria-hidden="true">{xlab}</div></div>
    </div>
    <p class="lab-note">Every rule here was replayed in advance by the same code that scores the agent, so the numbers are the real backtest, not an estimate. A rule that wins was picked after seeing these {n} nights, so it is not proven on new ones. The same caution applies to the agent's own earnings rule.</p>
    <script type="application/json" id="lab-data">{json.dumps(data, separators=(",", ":"))}</script>
  </section>"""


# ---------- how it got here ----------

def stages(card):
    rows = []
    for fname, label, title, text in STAGES:
        src = card if fname is None else None
        if fname and os.path.exists(os.path.join(ARCHIVE, fname)):
            src = json.load(open(os.path.join(ARCHIVE, fname)))
        if src and AGENT in src["policies"]:
            rows.append((label, title, text, src["policies"][AGENT]["total_return"]))
    if len(rows) < 2:
        return ""
    lo, hi = min(0.0, *(r[3] for r in rows)), max(0.0, *(r[3] for r in rows))
    at = lambda v: (v - lo) / (hi - lo) * 100 if hi > lo else 0.0
    items, prev = [], None
    for label, title, text, v in rows:
        left, width = min(at(0), at(v)), abs(at(v) - at(0))
        before = f'<span class="before num">{pct_text(prev)}</span><span class="arrow" aria-label="to">&rarr;</span>' if prev is not None else ""
        items.append(f'<li class="stage card"><div><span class="label">{label}</span><h3>{title}</h3><p>{text}</p></div>'
                     f'<div><div class="delta">{before}<span class="after num {sign(v)}">{pct_text(v)}</span></div>'
                     f'<div class="track" aria-hidden="true"><i class="{"neg" if v < 0 else "pos"}-bar" '
                     f'style="left:{left:.2f}%;width:{width:.2f}%"></i></div></div></li>')
        prev = v
    return (f'<section class="panel" id="history"><div class="sec-head"><h2>How it got here</h2>'
            f"<p>The first version lost money. The earlier results stay on this page because a scorecard that only shows "
            f'the final run is not a scorecard. Every run is in <a href="{REPO}/tree/main/results">the results folder</a>.</p></div>'
            f'<ol class="stages">{"".join(items)}</ol></section>')


# ---------- the hardest nights ----------

def stress_table(card):
    if not card["stress"]:
        return '<p class="figures-note">No replay results yet.</p>'
    cols = [k for k in (AGENT, "always_follow", "always_fade", "do_nothing") if k in card["stress"][0]]
    head = '<th scope="col">Night</th><th scope="col">Size of move</th>' + "".join(f'<th scope="col">{POLICY[c][0]}</th>' for c in cols)
    rows = []
    for r in card["stress"]:
        d = datetime.strptime(r["session"], "%Y-%m-%d")
        rows.append(f'<tr><th scope="row">{day(d)}<small>the night before this open</small></th>'
                    f'<td data-label="Size of move"><span class="num">{r["avg_abs_z"]:.2f}&times; a normal day</span></td>'
                    + "".join(f'<td data-label="{POLICY[c][0]}">{pct(r.get(c))}</td>' for c in cols) + "</tr>")
    return f'<table class="tbl" style="margin-top:0"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


# ---------- live log ----------

def when(rec):
    return datetime.strptime(rec["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(NY)


def entry(rec, action, tag, reason, extra=""):
    t = when(rec)
    return (f'<li class="entry" data-action="{action}"><time datetime="{t.isoformat()}">{t:%H:%M} ET<span>{day(t)}</span></time>'
            f'<div><span class="tag">{e(tag)}</span><p class="reason">{reason}</p>{extra}</div></li>')


def decision_entry(rec, orders):
    fills = rec.get("fills") or []
    sides = {f["side"] for f in fills}
    if not fills:
        action, tag = "hold", "Stayed out"
    elif len(fills) == 1:
        action, tag = fills[0]["side"], f'{"Bought" if fills[0]["side"] == "buy" else "Sold"} {fills[0]["ticker"]}'
    else:
        action, tag = (sides.pop() if len(sides) == 1 else "mixed"), f"Traded {len(fills)} stocks"
    seen = sorted(rec.get("seen", {}).items(), key=lambda kv: -abs(kv[1]["z"]))[:4]
    saw = '<ul class="saw" aria-label="What it saw">' + "".join(
        f'<li><b>{e(t)}</b><span class="{sign(round(f["move"], 4))}">{pct_text(f["move"])}</span></li>' for t, f in seen) + "</ul>"
    meta = [f"<div><dt>{e(t)} to {pct_text(w, 0)}</dt><dd>{e(rec['reasons'].get(t, ''))}</dd></div>"
            for t, w in rec.get("approved", {}).items()]
    blocked = ", ".join(f"{e(t)} ({e(rule.replace('_', ' '))})" for t, rule in rec.get("violations", []))
    meta.append(f'<div><dt>Risk layer</dt><dd class="blocked">Blocked: {blocked}</dd></div>' if blocked
                else "<div><dt>Risk layer</dt><dd>Nothing blocked</dd></div>")
    ids = [o["order_id"] for o in orders if o.get("order_id")]
    if ids:
        meta.append(f'<div><dt>Bitget demo order{"s" if len(ids) > 1 else ""}</dt><dd class="id">{", ".join(map(e, ids))}</dd></div>')
    if any(o.get("error") for o in orders):
        meta.append('<div><dt>Bitget demo</dt><dd class="blocked">An order was not accepted; the paper book still holds the position</dd></div>')
    reason = e(rec.get("note") or "No note.") if rec.get("note") != "INVALID_OUTPUT" \
        else "The model's answer could not be read, so no action was taken."
    return entry(rec, action, tag, reason, saw + f'<dl class="meta">{"".join(meta)}</dl>')


def live_feed():
    recs = [json.loads(l) for l in open(LOG) if l.strip()] if os.path.exists(LOG) else []
    orders = {r["time"]: r["orders"] for r in recs if r["event"] == "exchange_orders"}
    items = []
    for r in recs:
        if r["event"] == "decision":
            items.append(decision_entry(r, orders.get(r["time"], [])))
        elif r["event"] == "session_exit":
            items.append(entry(r, "hold", "Flattened", f"Closed everything after the open. Night result {pct(r['ret'])}."))
        elif r["event"] == "stops":
            fired = ", ".join(f"{e(t)} ({e(rule.replace('_', ' '))})" for t, rule in r["stops"])
            items.append(entry(r, "sell", "Stop fired", f"The risk layer closed positions without asking the agent: {fired}."))
        elif r["event"] == "decision_error":
            items.append(entry(r, "hold", "No decision", "The model could not be reached, so nothing was changed."))
    decisions = sum(r["event"] == "decision" for r in recs)
    since = f" since {day(when(recs[0]))}" if recs else ""
    head = (f"{decisions} decisions{since}, newest first. Most of them are decisions not to trade. "
            f'<a href="{REPO}/blob/main/logs/paper_log.jsonl">Raw log</a>')
    empty = '<li class="empty">No decisions yet tonight<span>The first one appears here after the next check.</span></li>'
    return head, f'<ol class="feed" reversed>{empty}{"".join(reversed(items[-FEED_LIMIT:]))}</ol>'


# ---------- morning notes ----------

def notes():
    items = []
    for path in sorted(glob.glob(os.path.join(NOTES, "*.md")), reverse=True)[:6]:
        sid = os.path.basename(path)[:-3]
        parts = [p.strip() for p in open(path).read().split("\n\n") if p.strip() and not p.startswith("#")]
        got = re.search(r"return ([+-][\d.]+)%", parts[0]) if parts else None
        ret = float(got.group(1)) / 100 if got else None
        result = f'<span class="note-result">Night result {pct(ret)}</span>' if ret is not None else ""
        body = " ".join(parts[1:]) or (parts[0] if parts else "")
        d = datetime.strptime(sid, "%Y-%m-%d")
        items.append(f'<li class="note"><header><time datetime="{sid}">Before the open, {day(d)}</time>{result}</header>'
                     f"<p>{e(body)}</p></li>")
    empty = '<li class="empty">No notes yet<span>The first note is written after the market next opens.</span></li>'
    return f'<ul class="notes">{empty}{"".join(items)}</ul>'


# ---------- page ----------

def build(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    card = scorecard.build()
    results = {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(os.path.join(scorecard.RESULTS, "*.json"))
               if os.path.basename(p) not in ("scorecard.json", "lab.json") and "consistency_run" not in p}
    agent = card["policies"].get(AGENT)
    nights = agent["sessions"] if agent else 0
    promo = (f'<div class="promo card"><span class="label">{nights} nights</span>'
             f'<strong class="{sign(agent["total_return"])}">{pct_text(agent["total_return"])}</strong>'
             f'<p>Scored against three fixed rules, earlier losing runs included.</p>'
             f'<a class="btn" href="#proving">Scorecard</a></div>') if agent else ""
    nav = "".join(f'<a{" class=\"on\"" if i == 0 else ""} href="#{k}">{v}</a>' for i, (k, v) in enumerate(NAV))
    steps = "".join(f'<li class="step card"><h3>{t}</h3><p>{d}</p></li>' for t, d in STEPS)
    log_head, feed = live_feed()
    icon = "data:image/svg+xml," + LOGO_SVG.replace("#", "%23").replace('"', "'")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>Pervigil · the night-shift trading agent</title>
<meta name="description" content="Pervigil is an AI agent that trades tokenised US stocks only while the real market is closed, and publishes its scorecard.">
<link rel="icon" type="image/svg+xml" href="{icon}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Michroma&display=swap" rel="stylesheet">
<style>
{open(CSS).read()}</style>
</head>
<body>
{LOGO_SYMBOL}
<div class="shell">
<aside class="side panel">
  <a class="brand" href="#top">{USE_LOGO}Pervigil</a>
  <nav class="nav" aria-label="Sections">{nav}</nav>
  {promo}
  <p class="side-foot"><a href="{REPO}">Code on GitHub</a><br>Paper trading only.</p>
</aside>
<main>
  {status_bar(now_ms)}
  <section class="panel hero" id="top">
    <div class="hero-id">{USE_LOGO}<h1>Pervigil</h1></div>
    <p class="motto" lang="la">Vigilat dum dormis<span lang="en">It keeps watch while you sleep.</span></p>
    <p class="lede">An AI agent that trades ten big US stocks on Bitget only while the real market is closed, explains every decision in one sentence, and is flat again shortly after the opening bell.</p>
    {week_strip(now_ms)}
  </section>
  <section class="panel" id="numbers">
    <div class="sec-head"><h2>{nights or "Past"} nights, replayed</h2></div>
    {figures(agent)}
  </section>
  <section class="panel" id="how">
    <div class="sec-head"><h2>How the watch works</h2><p>The same five steps, every six hours, for as long as the market is closed.</p></div>
    <ol class="steps">{steps}</ol>
  </section>
  <section class="panel" id="proving">
    <div class="sec-head"><h2>Proving ground</h2>
    <p>The agent was replayed over {nights} past nights and weekends and scored against three fixed rules that need no intelligence at all. Every strategy sees the same prices, pays the same costs and passes through the same risk limits. The fixed rules act when a stock has moved at least one normal day.{consistency_line(card)}</p></div>
    {chart(results)}
    {policy_table(card)}
  </section>
  {lab_section(agent)}
  {stages(card)}
  <section class="panel" id="hardest">
    <div class="sec-head"><h2>The hardest nights</h2><p>The five nights the market moved most while closed, and what each strategy made or lost on them.</p></div>
    {stress_table(card)}
  </section>
  <section class="panel" id="log">
    <div class="sec-head"><h2>Live log</h2><p>{log_head}</p></div>
    {feed}
  </section>
  <section class="panel" id="notes">
    <div class="sec-head"><h2>Morning notes</h2><p>What the agent leaves for whoever was asleep. One per night.</p></div>
    {notes()}
  </section>
  <footer class="panel">
    <p class="foot-id">{USE_LOGO}<span><a href="{REPO}">Code, data and logs on GitHub</a> &nbsp;·&nbsp; Built for the Bitget AI Base Camp Hackathon S2</span></p>
    <p><strong>Paper trading only. Not financial advice.</strong></p>
  </footer>
</main>
</div>
<script>
{NAV_JS}
{LAB_JS}
</script>
</body>
</html>
"""
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, "index.html"), "w").write(page)
    open(os.path.join(DOCS, "logo.svg"), "w").write(LOGO_SVG + "\n")
    return os.path.join(DOCS, "index.html")


if __name__ == "__main__":
    print(build())
