"""Turn replay results into the benchmark scorecard.

    python -m pervigil.scorecard      # reads results/*.json, writes results/scorecard.json and SCORECARD.md
"""
import glob
import json
import math
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
PER_YEAR = 252   # one closed session per trading day


def sharpe(rets):
    if len(rets) < 2 or statistics.pstdev(rets) == 0:
        return 0.0
    return statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(PER_YEAR)


def sortino(rets):
    if len(rets) < 2:
        return 0.0
    down = math.sqrt(sum(min(r, 0) ** 2 for r in rets) / len(rets))
    return statistics.mean(rets) / down * math.sqrt(PER_YEAR) if down else 0.0


def max_drawdown(rets):
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return dd


def total_return(rets):
    return math.prod(1 + r for r in rets) - 1


def metrics(result):
    ss = result["sessions"]
    rets = [s["ret"] for s in ss]
    active = [s["ret"] for s in ss if any(d["fills"] for d in s["decisions"])]
    decisions = [d for s in ss for d in s["decisions"]]
    proposals = sum(len(d["proposed"]) for d in decisions)
    violations = [v for d in decisions for v in d["violations"]]
    cut = len(rets) * 2 // 3
    start_eq = ss[0]["start_equity"] if ss else 1.0
    return {
        "sessions": len(rets),
        "sessions_traded": len(active),
        "total_return": total_return(rets),
        "sharpe": sharpe(rets),
        "sortino": sortino(rets),
        "max_drawdown": max_drawdown(rets),
        "win_rate": sum(r > 0 for r in active) / len(active) if active else None,
        "worst_session": min(rets) if rets else 0.0,
        "turnover_per_session": result["traded"] / start_eq / len(rets) if rets else 0.0,
        "costs_pct": result["costs"] / start_eq,
        "sharpe_first_two_thirds": sharpe(rets[:cut]),
        "sharpe_last_third": sharpe(rets[cut:]),
        "decisions": len(decisions),
        "proposals": proposals,
        "risk_violations": len(violations),
        "risk_violation_rate": len(violations) / proposals if proposals else 0.0,
        "violations_by_rule": {r: sum(v[1] == r for v in violations) for r in sorted({v[1] for v in violations})},
        "invalid_outputs": sum(d["note"] == "INVALID_OUTPUT" for d in decisions),
        "stops_fired": sum(len(s["stops"]) for s in ss),
        "sessions_halted": sum(s["halted"] for s in ss),
    }


def _direction(w):
    try:
        w = float(w)
    except (TypeError, ValueError):
        return 0
    return (w > 0.005) - (w < -0.005)


def consistency(full, repeats):
    """Same nights, independent passes: how often does the agent make the same call?

    Compared at the opening decision of a night, where every pass is flat and sees identical inputs.
    """
    if not full or not repeats:
        return None
    first = {s["session"]: s["decisions"][0] for s in full["sessions"] if s["decisions"]}
    same = total = same_active = total_active = nights = 0
    common = set(first)
    for r in repeats:
        common &= {s["session"] for s in r["sessions"] if s["decisions"]}
    by = [{s["session"]: s["decisions"][0] for s in r["sessions"] if s["decisions"]} for r in repeats]
    for sid in sorted(common):
        nights += 1
        calls = [first[sid]] + [b[sid] for b in by]
        for t in first[sid]["seen"]:
            dirs = [_direction(d["proposed"].get(t, 0)) for d in calls]
            total += 1
            same += len(set(dirs)) == 1
            if any(dirs):
                total_active += 1
                same_active += len(set(dirs)) == 1
    return {
        "passes": 1 + len(repeats),
        "nights_compared": nights,
        "name_decisions_compared": total,
        "agreement": same / total if total else None,
        "agreement_when_any_pass_traded": same_active / total_active if total_active else None,
    }


def stress(results, n=5):
    """The n sessions where the market moved most while closed (largest average |z|), and what each policy did."""
    ref = results[next(iter(results))]["sessions"]
    size = {}
    for s in ref:
        zs = [abs(f["z"]) for d in s["decisions"][-1:] for f in d["seen"].values()]
        size[s["session"]] = statistics.mean(zs) if zs else 0.0
    worst = sorted(size, key=size.get, reverse=True)[:n]
    by = {name: {s["session"]: s for s in r["sessions"]} for name, r in results.items()}
    return [{"session": sid, "avg_abs_z": size[sid],
             **{name: by[name][sid]["ret"] for name in results if sid in by[name]}} for sid in worst]


def build():
    results = {}
    for p in sorted(glob.glob(os.path.join(RESULTS, "*.json"))):
        name = os.path.basename(p)[:-5]
        if name not in ("scorecard", "lab"):
            results[name] = json.load(open(p))
    repeats = [results.pop(n) for n in sorted(results) if n.startswith("consistency_run")]
    card = {"policies": {n: metrics(r) for n, r in results.items()}}
    card["consistency"] = consistency(results.get("agent_run0"), repeats)
    card["stress"] = stress(results) if results else []
    if "agent_run0" in results:
        a = card["policies"]["agent_run0"]
        card["value_added"] = {
            n: {"total_return": a["total_return"] - m["total_return"], "sharpe": a["sharpe"] - m["sharpe"],
                "max_drawdown": a["max_drawdown"] - m["max_drawdown"]}
            for n, m in card["policies"].items() if not n.startswith("agent_run")}
    json.dump(card, open(os.path.join(RESULTS, "scorecard.json"), "w"), indent=1)
    return card


def fmt(card):
    rows = ["| Policy | Sessions | Traded | Return | Sharpe | Sortino | Max DD | Win rate | Worst night | Turnover/night |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for n, m in card["policies"].items():
        win = f"{m['win_rate']:.0%}" if m["win_rate"] is not None else "-"
        rows.append(f"| {n} | {m['sessions']} | {m['sessions_traded']} | {m['total_return']:+.2%} | {m['sharpe']:.2f} | "
                    f"{m['sortino']:.2f} | {m['max_drawdown']:.2%} | {win} | {m['worst_session']:+.2%} | "
                    f"{m['turnover_per_session']:.2f}x |")
    return "\n".join(rows)


if __name__ == "__main__":
    c = build()
    print(fmt(c))
    if c["consistency"]:
        print(json.dumps(c["consistency"], indent=1))
