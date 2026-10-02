"""Proving ground: replay past closed sessions through a policy, the risk layer and the paper book.

    python -m pervigil.replay rules            # the three fixed-rule baselines
    python -m pervigil.replay agent            # the LLM agent, full pass
    python -m pervigil.replay consistency [n]  # n repeat passes of each night's opening decision (default 2)

At each step the policy sees only candles that had closed by that moment (see features.Market.price_at).
"""
import json
import os
import sys

from . import policies, risk
from .data import STEP_MS, STOCKS, load_all, rtoken
from .features import Market, snapshot
from .paper import Book
from .sessions import closed_sessions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
HOUR = 3_600_000
FIRST_DECISION_H = 1      # first decision one hour after the cash close
STEP_H = 6                # then every 6 hours
EXIT_AFTER_OPEN_MS = STEP_MS   # flatten at the close of the first 15-minute candle after the open
WARMUP_DAYS = 21          # history needed before the first session, to size a "normal day"


def checkpoints(session, step_h=STEP_H):
    t, out = session.close_ms + FIRST_DECISION_H * HOUR, []
    while t <= session.open_ms - risk.NO_ENTRY_HOURS * HOUR:
        out.append(t)
        t += step_h * HOUR
    return out


def _prices(market, t_ms, last):
    for tk in STOCKS:
        p = market.price_at(rtoken(tk), t_ms)
        if p is not None:
            last[tk] = p
    return last


def decide(policy, market, session, t_ms, book, prices, state, earnings=None):
    """One decision cycle. Mutates the book. -> log record."""
    snap = snapshot(market, session, t_ms, STOCKS, earnings)
    weights = {t: round(book.weight(t, prices), 4) for t in book.qty}
    targets, reasons, note = policy(snap, weights)
    approved, violations = risk.apply(targets, book, prices, snap["hours_to_open"], state)
    fills = [f for t, w in approved.items() if (f := book.set_weight(t, w, prices))]
    return {
        "t": t_ms, "session": session.id, "hours_to_open": snap["hours_to_open"],
        "btc_move": snap["btc_move"],
        "seen": {t: {"move": round(f["move"], 5), "z": round(f["z"], 2)} for t, f in snap["names"].items()},
        "proposed": targets, "reasons": reasons, "note": note,
        "approved": approved, "violations": violations, "fills": fills,
        "equity": round(book.equity(prices), 2),
    }


def run_session(policy, market, session, book, step_h=STEP_H, earnings=None, first_only=False):
    prices = _prices(market, session.close_ms, {})
    state = risk.SessionRisk(book.equity(prices))
    marks = set(checkpoints(session, step_h)[:1] if first_only else checkpoints(session, step_h))
    decisions, stops = [], []
    t = session.close_ms + STEP_MS
    while t <= session.open_ms:
        _prices(market, t, prices)
        if book.qty:
            stops += [(t, *s) for s in risk.check_stops(book, prices, state)]
        if t in marks:
            decisions.append(decide(policy, market, session, t, book, prices, state, earnings))
        t += STEP_MS
    _prices(market, session.open_ms + EXIT_AFTER_OPEN_MS, prices)
    book.flatten(prices)
    end = book.equity(prices)
    return {
        "session": session.id, "kind": session.kind, "open_ms": session.open_ms,
        "start_equity": state.start_equity, "end_equity": end, "ret": end / state.start_equity - 1,
        "decisions": decisions, "stops": stops, "halted": state.halted,
    }


def sessions_for(candles):
    starts = [rows[0][0] for rows in candles.values() if rows]
    ends = [rows[-1][0] for rows in candles.values() if rows]
    lo = max(starts) + WARMUP_DAYS * 24 * HOUR
    hi = min(ends) + STEP_MS - EXIT_AFTER_OPEN_MS
    return [s for s in closed_sessions(lo, hi) if s.open_ms + EXIT_AFTER_OPEN_MS <= min(ends) + STEP_MS]


def run(policy, name, candles=None, step_h=STEP_H, earnings=None, progress=False, first_only=False, every=1):
    """first_only: only the opening decision of each session. every: take every n-th session."""
    candles = candles or load_all()
    market, book, out = Market(candles), Book(), []
    for s in sessions_for(candles)[::every]:
        if first_only:
            book = Book()   # each opening decision starts flat at the same equity, as in the full pass
        out.append(run_session(policy, market, s, book, step_h, earnings, first_only))
        if progress:
            print(f"{name} {s.id} {s.kind:9} ret={out[-1]['ret']:+.3%} equity={out[-1]['end_equity']:.0f}", flush=True)
    result = {"policy": name, "step_h": step_h, "traded": book.traded, "costs": book.costs, "sessions": out}
    os.makedirs(RESULTS, exist_ok=True)
    json.dump(result, open(os.path.join(RESULTS, f"{name}.json"), "w"))
    return result


def main(argv):
    what = argv[1] if len(argv) > 1 else "rules"
    candles = load_all()
    earnings = None
    try:
        from .context import load_earnings
        earnings = load_earnings()
    except Exception as e:
        print(f"(no earnings calendar: {e})")
    if what == "rules":
        for name, pol in [("do_nothing", policies.do_nothing), ("always_fade", policies.always_fade),
                          ("always_follow", policies.always_follow)]:
            r = run(pol, name, candles, earnings=earnings)
            print(name, len(r["sessions"]), "sessions, final equity", round(r["sessions"][-1]["end_equity"], 2))
    elif what == "agent":
        run(policies.make_agent(run=0), "agent_run0", candles, earnings=earnings, progress=True)
    elif what == "consistency":
        # Repeat passes of the opening decision on every 3rd night, to measure how stable the agent's call is.
        for i in range(1, 1 + (int(argv[2]) if len(argv) > 2 else 2)):
            run(policies.make_agent(run=i), f"consistency_run{i}", candles, earnings=earnings, progress=True,
                first_only=True, every=3)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
