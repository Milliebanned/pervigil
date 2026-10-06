"""Trade logs as CSV: one row per fill, with time, instrument, direction, price, quantity and account balance.

    python -m pervigil.trades            # both files below
    python -m pervigil.trades replay     # results/trades.csv from the agent's replay (no model calls)
    python -m pervigil.trades live       # logs/trades.csv from the live paper log

The replay file is rebuilt by running the replay engine again with the agent's recorded decisions
(results/agent_run0.json) instead of the model, and every night's result is checked against the saved one.
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone

from .data import load_all, rtoken
from .replay import RESULTS, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "paper_log.jsonl")
LIVE_CSV = os.path.join(ROOT, "logs", "trades.csv")
REPLAY_CSV = os.path.join(RESULTS, "trades.csv")
AGENT = os.path.join(RESULTS, "agent_run0.json")
COLUMNS = ["time_utc", "session", "instrument", "ticker", "direction", "action", "why", "quantity", "price",
           "notional_usdt", "fee_usdt", "realized_pnl_usdt", "balance_after_usdt", "balance_change_usdt"]


def iso(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(events, start_balance):
    """events: (time_utc, session, why, fill) in time order. -> CSV rows, tracking each position's size."""
    held, last, out = {}, start_balance, []
    for time_utc, session, why, f in events:
        t, signed = f["ticker"], f["qty"] if f["side"] == "buy" else -f["qty"]
        old = held.get(t, 0.0)
        new = old + signed
        held[t] = 0.0 if abs(new) < 1e-9 else new
        if abs(old) < 1e-9:
            action = "open long" if signed > 0 else "open short"
        elif abs(new) < 1e-9:
            action = "close long" if old > 0 else "close short"
        elif old * new < 0:
            action = "flip to long" if new > 0 else "flip to short"
        else:
            action = "add" if abs(new) > abs(old) else "reduce"
        bal = f.get("equity_after")
        out.append([time_utc, session, rtoken(t), t, f["side"], action, why, f"{f['qty']:.6f}", f"{f['price']:.4f}",
                    f"{f['notional']:.2f}", f"{f['fee']:.4f}",
                    "" if f.get("realized_pnl") is None else f"{f['realized_pnl']:.2f}",
                    "" if bal is None else f"{bal:.2f}", "" if bal is None else f"{bal - last:+.2f}"])
        if bal is not None:
            last = bal
    return out


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(data)


def write_replay():
    saved = json.load(open(AGENT))
    recorded = {d["t"]: (d["proposed"], d["reasons"], d["note"]) for s in saved["sessions"] for d in s["decisions"]}
    ids = {s["session"] for s in saved["sessions"]}

    def policy(snap, weights):
        return recorded[snap["t"]]

    from .context import load_earnings
    again = run(policy, "agent_run0_trades", load_all(), earnings=load_earnings(), only=ids, save=False)
    for a, b in zip(saved["sessions"], again["sessions"]):
        assert a["session"] == b["session"] and abs(a["end_equity"] - b["end_equity"]) < 1e-6, \
            f"replay drifted on {a['session']}: {a['end_equity']} vs {b['end_equity']}"
    events = []
    for s in again["sessions"]:
        timed = [(d["t"], "agent decision", f) for d in s["decisions"] for f in d["fills"]]
        timed += [(f["t"], f["rule"].replace("_", " "), f) for f in s["stop_fills"]]
        timed += [(s["exit_ms"], "flat 15 min after the open", f) for f in s["exit_fills"]]
        events += [(iso(t), s["session"], why, f) for t, why, f in sorted(timed, key=lambda x: x[0])]
    data = rows(events, again["sessions"][0]["start_equity"])
    write(REPLAY_CSV, data)
    return len(data)


def write_live():
    recs = [json.loads(line) for line in open(LOG) if line.strip()] if os.path.exists(LOG) else []
    events, start = [], None
    for r in recs:
        if start is None and r.get("event") == "session_start":
            start = r.get("equity")
        why = {"decision": "agent decision", "stops": "stop", "session_exit": "flat 15 min after the open"}.get(r.get("event"))
        for f in (r.get("fills") or []) if why else []:
            events.append((r["time"], r.get("session"), f.get("rule", why).replace("_", " "), f))
    data = rows(events, start or 10_000.0)
    write(LIVE_CSV, data)
    return len(data)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "both"
    if what in ("replay", "both"):
        print("replay trades:", write_replay(), "->", os.path.relpath(REPLAY_CSV, ROOT))
    if what in ("live", "both"):
        print("live trades:", write_live(), "->", os.path.relpath(LIVE_CSV, ROOT))
