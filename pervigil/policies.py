"""Decision-makers. Each takes a snapshot plus current weights and returns
({ticker: target_weight}, {ticker: reason}, note). The LLM agent and the fixed rules share this
interface, so the proving ground can run them through the identical risk layer and paper book.
"""
from . import llm

RULE_Z = 1.0       # fixed rules act on moves of at least one normal day
RULE_SIZE = 0.10


def do_nothing(snap, weights):
    return {}, {}, ""


def _rule(sign):
    def policy(snap, weights):
        targets, reasons = {}, {}
        for t, f in snap["names"].items():
            if abs(f["z"]) >= RULE_Z:
                targets[t] = sign * RULE_SIZE * (1 if f["z"] > 0 else -1)
                reasons[t] = f"z={f['z']:+.2f}"
            elif weights.get(t):
                targets[t] = 0.0
                reasons[t] = "move back inside one normal day"
        return targets, reasons, ""
    return policy


always_fade = _rule(-1)     # bet the closed-hours move reverses
always_follow = _rule(+1)   # bet the closed-hours move continues

SYSTEM = """You are Pervigil, an autonomous trading agent for tokenized US stocks (rTokens) on Bitget.
You only act while the US cash market is CLOSED. During these hours the rToken price is the market's
guess at where the real stock will open. Your job: decide, per name, whether that guess looks right,
overdone, or not enough, and take a position only when you have a real reason.

You see, per name: the move since the cash close, the size of that name's normal day, and
z = move / normal day. You also see Bitcoin's move over the same window (a 24/7 risk-appetite gauge),
hours until the open, any earnings due, your current positions, and (when available) headlines.

Rules of the desk:
- Weights are fractions of equity. Positive = long, negative = short, 0 = flat. Max 0.20 per name.
- Every position is closed automatically 15 minutes after the open. You are trading the open, nothing longer.
- Trading costs about 0.15% per side. A small or ordinary move is not worth trading. Staying out is a valid decision.
- A separate risk layer will clamp or block anything outside the limits. Do not rely on it; size sensibly.
- Only list names you want to change. Names you leave out keep their current weight.

Reply with ONE JSON object and nothing else:
{"decisions": [{"ticker": "NVDA", "weight": -0.10, "reason": "one short sentence"}],
 "note": "one or two sentences on how you read this session"}"""


def render(snap, weights, headlines=None):
    lines = [
        f"Session: {snap['kind']} window ending at the {snap['session']} open.",
        f"Hours since close: {snap['hours_since_close']}. Hours to open: {snap['hours_to_open']}.",
        "Bitcoin since the close: " + (f"{snap['btc_move']:+.2%}" if snap["btc_move"] is not None else "n/a"),
        "",
        "ticker | move since close | normal day | z | last hour | earnings | your weight",
    ]
    for t, f in sorted(snap["names"].items()):
        last = f"{f['last_hour']:+.2%}" if f["last_hour"] is not None else "n/a"
        lines.append(f"{t} | {f['move']:+.2%} | {f['normal_day']:.2%} | {f['z']:+.2f} | {last} | "
                     f"{f['earnings'] or '-'} | {weights.get(t, 0.0):+.2f}")
    if headlines:
        lines += ["", "Headlines:"] + [f"- {h}" for h in headlines]
    return "\n".join(lines)


def make_agent(run=0, headlines_fn=None):
    """LLM policy. Unparseable output means no action, flagged in the note so it is counted."""
    def policy(snap, weights):
        heads = headlines_fn(snap) if headlines_fn else None
        text = llm.chat(SYSTEM, render(snap, weights, heads), run=run)
        obj = llm.extract_json(text)
        if not isinstance(obj, dict) or not isinstance(obj.get("decisions"), list):
            return {}, {}, "INVALID_OUTPUT"
        targets, reasons = {}, {}
        for d in obj["decisions"]:
            if isinstance(d, dict) and "ticker" in d:
                t = str(d["ticker"]).upper()
                targets[t] = d.get("weight")
                reasons[t] = str(d.get("reason", ""))[:300]
        return targets, reasons, str(obj.get("note", ""))[:600]
    return policy
