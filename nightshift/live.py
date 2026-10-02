"""Live paper trading. Run on a schedule (hourly); each run does at most one decision cycle.

    python -m nightshift.live

It uses the same snapshot, policy, risk layer, paper book and decision schedule as the proving ground,
plus live headlines. State lives in state/book.json; every action is appended to logs/paper_log.jsonl.
"""
import json
import os
import time
from datetime import datetime, timezone

from . import context, llm, policies, risk
from .data import STEP_MS, STOCKS, update_all
from .features import Market
from .paper import Book
from .replay import EXIT_AFTER_OPEN_MS, _prices, checkpoints, decide
from .sessions import ClosedSession, current_session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "book.json")
LOG = os.path.join(ROOT, "logs", "paper_log.jsonl")
NOTES = os.path.join(ROOT, "logs", "notes")


def iso(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state():
    if not os.path.exists(STATE):
        return {"book": Book().to_dict(), "session": None, "risk": None, "last_decision_ms": 0}
    return json.load(open(STATE))


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w"), indent=1)


def log(event, now_ms, **fields):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    rec = {"time": iso(now_ms), "event": event, **fields}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec)[:400])


def session_records(session_id):
    if not os.path.exists(LOG):
        return []
    return [r for r in map(json.loads, open(LOG)) if r.get("session") == session_id]


def morning_note(session_id, ret, equity):
    """Plain-English handoff for the human who wakes up. Falls back to a mechanical summary."""
    recs = session_records(session_id)
    facts = [{k: r.get(k) for k in ("time", "event", "note", "reasons", "approved", "violations", "stops", "fills")}
             for r in recs]
    summary = f"Session {session_id}: return {ret:+.2%}, equity {equity:,.2f} USDT, {len(recs)} log entries."
    try:
        body = llm.chat(
            "You are Night Shift's reporting voice. Write the morning handoff note for a human trader who was "
            "asleep: what moved while the US market was closed, what the agent did and why, what the risk layer "
            "blocked, and the result. Under 150 words, plain English, no hype. Use only the facts given.",
            summary + "\n\nLog:\n" + json.dumps(facts)[:12000], use_cache=False)
    except Exception as e:
        body = f"(LLM note unavailable: {e})"
    os.makedirs(NOTES, exist_ok=True)
    with open(os.path.join(NOTES, f"{session_id}.md"), "w") as f:
        f.write(f"# Morning note — {session_id} open\n\n{summary}\n\n{body}\n")


def headlines_fn(snap):
    try:
        return context.headlines()
    except Exception as e:
        print(f"(headlines unavailable: {e})")
        return None


def run(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    market = Market(update_all(days=45))
    state = load_state()
    book = Book.from_dict(state["book"])
    session = current_session(now_ms)

    # 1. A session we were trading has ended: flatten 15 minutes after the open.
    if state["session"] and (session is None or session.id != state["session"]["id"]):
        old = state["session"]
        exit_ms = old["open_ms"] + EXIT_AFTER_OPEN_MS
        if now_ms < exit_ms + STEP_MS:
            print("waiting for the first post-open candle to close")
            return
        prices = _prices(market, exit_ms, dict(old.get("last_prices", {})))
        fills = book.flatten(prices)
        equity = book.equity(prices)
        ret = equity / state["risk"]["start_equity"] - 1
        log("session_exit", now_ms, session=old["id"], fills=fills, equity=round(equity, 2), ret=ret)
        state.update(book=book.to_dict(), session=None, risk=None)
        save_state(state)
        morning_note(old["id"], ret, equity)

    if session is None:
        print("US cash market is open: Night Shift is off duty")
        return

    # 2. Closed hours: mark, check stops, decide if a scheduled decision time has passed.
    prices = _prices(market, now_ms, dict((state["session"] or {}).get("last_prices", {})))
    if state["session"] is None:
        state["session"] = {"id": session.id, "open_ms": session.open_ms, "close_ms": session.close_ms,
                            "kind": session.kind}
        state["risk"] = risk.SessionRisk(book.equity(prices)).to_dict()
        log("session_start", now_ms, session=session.id, kind=session.kind, equity=round(book.equity(prices), 2))
    srisk = risk.SessionRisk.from_dict(state["risk"])

    if book.qty:
        stops = risk.check_stops(book, prices, srisk)
        if stops:
            log("stops", now_ms, session=session.id, stops=stops, equity=round(book.equity(prices), 2))

    due = [c for c in checkpoints(session) if state["last_decision_ms"] < c <= now_ms]
    if due:
        try:
            earnings = context.load_earnings()
        except Exception:
            earnings = None
        try:
            rec = decide(policies.make_agent(headlines_fn=headlines_fn), market, session, now_ms, book, prices,
                         srisk, earnings)
            rec.pop("t")
            log("decision", now_ms, model=llm.config()["LLM_MODEL"], **rec)
        except Exception as e:
            log("decision_error", now_ms, session=session.id, error=str(e)[:300])
        state["last_decision_ms"] = now_ms
    else:
        print("no decision due this run")

    state["session"]["last_prices"] = prices
    state.update(book=book.to_dict(), risk=srisk.to_dict())
    save_state(state)


if __name__ == "__main__":
    run()
