"""The docs page (docs/guide.html): a short plain-English explanation of the project. Built by site.build()."""
from . import paper, risk
from .data import STOCKS
from .replay import EXIT_AFTER_OPEN_MS, STEP_H

NAV = [("top", "What it is"), ("problem", "The problem"), ("answer", "The answer"), ("cycle", "One decision"),
       ("rules", "The rule book"), ("proof", "Is it any good"), ("run", "Run your own"), ("limits", "Limits")]


def section(i, title, body):
    return (f'<section class="panel" id="{i}"><div class="sec-head"><h2>{title}</h2></div>'
            f'<div class="prose">{body}</div></section>')


def build(card):
    from . import site   # site imports this module; its helpers are only needed once the page is being built
    pct, logo, repo = site.pct_text, site.USE_LOGO, site.REPO
    agent = card["policies"].get(site.AGENT) or {}
    nights = agent.get("sessions", 0)
    follow = card["policies"].get("always_follow", {}).get("total_return", 0.0)
    result = (f"Over {nights} replayed nights the current agent returned <b>{pct(agent['total_return'])}</b> after "
              f"costs, against {pct(follow)} for the best fixed rule and 0.00% for doing nothing."
              if agent else "The agent replay has not finished yet.")

    problem = """
    <p class="big">US stocks used to sleep when Wall Street slept. They don't any more.</p>
    <p>On Bitget, tokenised copies of Apple, Tesla, Nvidia and other US stocks trade all day, every day. The real
    market is only open 32.5 hours a week. For the other 80% of the week, prices keep moving while nobody is at the desk.</p>
    <ul>
      <li>Earnings come out after the close. News breaks on Saturday. Bitcoin moves on Sunday.</li>
      <li>A person cannot watch ten stocks through every night and weekend.</li>
      <li>Plenty of AI trading agents claim they can. Almost none of them show whether they beat a rule a child could write.</li>
    </ul>"""

    answer = f"""
    <p class="big">An agent that works the night shift, and a scorecard that says how good it is.</p>
    <div class="pair">
      <div class="card"><h3>The watchman</h3><p>An AI model (Qwen) that looks at the market every {STEP_H} hours while
      it is closed, chooses to buy, sell or stay out, and writes down why. Hard limits it cannot override keep it from
      betting too much.</p></div>
      <div class="card"><h3>The proving ground</h3><p>The same agent, replayed over {nights} past nights and marked
      against three fixed rules: do nothing, always follow the move, always bet against it. The results are published,
      including the runs where it lost.</p></div>
    </div>"""

    cycle = f"""
    <ol>
      <li><b>Sense.</b> For each of ten stocks ({", ".join(STOCKS)}) it reads how far the price has moved since the
      market closed, and compares that with the size of that stock's normal day. It also reads Bitcoin's move, whether
      the company has just reported earnings, and recent headlines.</li>
      <li><b>Decide.</b> Qwen answers with a target for each stock and a one-sentence reason. Staying out is a valid
      answer, and it is the usual one.</li>
      <li><b>Risk layer.</b> Plain code checks the answer against the rule book below and cuts or blocks anything
      outside it. Every block is logged.</li>
      <li><b>Execute.</b> What survives is filled in a paper account at Bitget prices, with fees and slippage, and
      placed as a real order on Bitget's demo exchange, so there is an order number to check.</li>
      <li><b>Flatten.</b> {EXIT_AFTER_OPEN_MS // 60000} minutes after the market opens, everything is closed and a
      short morning note is written for the human.</li>
    </ol>"""

    rules = f"""
    <p>These limits are ordinary code, not instructions to the AI. The model cannot talk its way past them.</p>
    <dl class="facts">
      <div class="card"><dt class="label">Per stock</dt><dd><b>{risk.MAX_WEIGHT:.0%}</b>of the account, at most</dd></div>
      <div class="card"><dt class="label">All positions</dt><dd><b>{risk.MAX_GROSS:.0%}</b>of the account in total, at most</dd></div>
      <div class="card"><dt class="label">Position stop</dt><dd><b>{pct(risk.POSITION_STOP, 0)}</b>on one stock closes it and bars it for the night</dd></div>
      <div class="card"><dt class="label">Night stop</dt><dd><b>{pct(risk.SESSION_STOP, 0)}</b>on the account closes everything until the next night</dd></div>
      <div class="card"><dt class="label">Late entries</dt><dd><b>{risk.NO_ENTRY_HOURS * 60:.0f} min</b>before the open, no new risk is allowed</dd></div>
      <div class="card"><dt class="label">Costs charged</dt><dd><b>{paper.FEE + paper.SLIPPAGE:.2%}</b>per side: {paper.FEE:.2%} fee plus {paper.SLIPPAGE:.2%} slippage</dd></div>
    </dl>"""

    proof = f"""
    <p class="big">{result}</p>
    <p>That number needs three honest notes, and the <a href="index.html#history">live page</a> shows all of them:</p>
    <ul>
      <li><b>The first run lost.</b> The agent bet that big overnight moves would reverse. They mostly continued.</li>
      <li><b>A data bug was part of the story.</b> The earnings calendar was one night early, so the agent was told
      "no news" on the very night a company reported.</li>
      <li><b>The gain is concentrated.</b> Almost all of it comes from four earnings nights, after one rule was added:
      do not bet against a move on the night a company reports. That rule was written after seeing those nights, so it
      is not yet proven on new ones.</li>
    </ul>
    <p>You can test this yourself. <a href="index.html#lab">Beat the agent</a> lets you build a rule with two sliders
    and replays it over the same nights. A few rules do beat it.</p>"""

    run = f"""
    <p>Watching costs nothing: open the <a href="index.html">live page</a>. To run your own copy you need to be
    comfortable with GitHub. It takes about twenty minutes.</p>
    <ol>
      <li>Fork <a href="{repo}">the repository</a> to your own GitHub account.</li>
      <li>Get an AI key for any OpenAI-compatible model, and a Bitget demo API key if you want demo orders.</li>
      <li>Add them as repository secrets: <code>LLM_BASE_URL</code>, <code>LLM_API_KEY</code>, <code>LLM_MODEL</code>,
      and optionally <code>BITGET_DEMO_API_KEY</code>, <code>BITGET_DEMO_SECRET</code>, <code>BITGET_DEMO_PASSPHRASE</code>.</li>
      <li>Enable GitHub Actions and GitHub Pages. The agent then wakes every hour, decides when a decision is due,
      and rebuilds your page.</li>
    </ol>
    <p>It is plain Python 3.12 with no extra packages. The README lists the commands to replay the proving ground on
    your own machine.</p>"""

    limits = f"""
    <ul>
      <li><b>Paper trading only.</b> No real money is at risk, and it cannot connect to anyone's live account.</li>
      <li><b>Not a product yet.</b> There is no sign-up or one-click start. Running your own copy needs a developer.</li>
      <li><b>A short record.</b> {nights} replayed nights and a few days of live log. The replay sees prices and
      earnings dates, not the news the live agent reads.</li>
      <li><b>Three stocks are paper only.</b> Bitget's demo exchange does not list MSFT, SPY or QQQ, so those positions
      exist in the paper account alone.</li>
      <li><b>Not financial advice.</b></li>
    </ul>"""

    main = f"""<div class="top panel"><p class="status off"><span class="pill"><i aria-hidden="true"></i>Docs</span><span>A five-minute read</span></p>
    <label class="theme"><input type="checkbox" id="theme-switch" aria-label="Use light theme"><span class="label">Light</span></label></div>
  <section class="panel hero" id="top">
    <div class="hero-id">{logo}<h1>Pervigil</h1></div>
    <p class="motto" lang="la">Vigilat dum dormis<span lang="en">It keeps watch while you sleep.</span></p>
    <p class="lede">Pervigil is a night watchman for US stocks. While the real stock market is closed and you are asleep, it watches the prices, decides whether to trade, explains itself in one sentence, and hands everything back flat in the morning.</p>
    <p class="cta"><a class="btn" href="index.html">See it live</a><a class="btn ghost" href="index.html#lab">Beat the agent</a></p>
  </section>
  {section("problem", "The problem", problem)}
  {section("answer", "The answer", answer)}
  {section("cycle", "One decision, step by step", cycle)}
  {section("rules", "The rule book", rules)}
  {section("proof", "Is it any good", proof)}
  {section("run", "Run your own", run)}
  {section("limits", "What it is not", limits)}"""
    return site.shell(
        "Pervigil docs · how the night-shift agent works",
        "What Pervigil is, the problem it answers, how one decision works, its risk rules and its honest results.",
        NAV, '<a class="btn docs" href="index.html">See it live</a>', main, site.NAV_JS, home="index.html")
