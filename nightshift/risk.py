"""Hard risk limits. The policy proposes target weights; this layer has the final say.

Every time a limit changes or blocks a proposal it is recorded, so the scorecard can report how often
the decision-maker had to be overruled.
"""
from dataclasses import dataclass, field

MAX_WEIGHT = 0.20        # per name, of equity
MAX_GROSS = 0.60         # sum of |weights|
NO_ENTRY_HOURS = 0.5     # no new or larger exposure this close to the open
POSITION_STOP = -0.03    # close a position at -3%
SESSION_STOP = -0.02     # flatten and halt for the session at -2% of equity
MIN_CHANGE = 0.01        # ignore weight changes smaller than this


@dataclass
class SessionRisk:
    start_equity: float
    halted: bool = False
    blocked: set = field(default_factory=set)   # names stopped out this session

    def to_dict(self):
        return {"start_equity": self.start_equity, "halted": self.halted, "blocked": sorted(self.blocked)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["start_equity"], d["halted"], set(d["blocked"]))


def check_stops(book, prices, state):
    """Mechanical stops, run every candle. -> list of (ticker_or_"*", rule) that fired."""
    fired = []
    if state.halted:
        return fired
    if book.equity(prices) / state.start_equity - 1 <= SESSION_STOP:
        state.halted = True
        book.flatten(prices)
        return [("*", "session_stop")]
    for t in list(book.qty):
        if book.pnl_pct(t, prices[t]) <= POSITION_STOP:
            book.set_weight(t, 0.0, prices)
            state.blocked.add(t)
            fired.append((t, "position_stop"))
    return fired


def apply(proposals, book, prices, hours_to_open, state):
    """proposals: {ticker: target_weight}. -> (approved {ticker: weight}, violations [(ticker, rule)])."""
    violations, approved = [], {}
    for t, w in proposals.items():
        if t not in prices:
            violations.append((t, "unknown_or_unpriced"))
            continue
        try:
            w = float(w)
        except (TypeError, ValueError):
            violations.append((t, "invalid_weight"))
            continue
        cur = book.weight(t, prices)
        adds_risk = abs(w) > abs(cur) + 1e-9 or w * cur < 0
        if adds_risk and state.halted:
            violations.append((t, "session_halted"))
            continue
        if adds_risk and t in state.blocked:
            violations.append((t, "stopped_out_name"))
            continue
        if adds_risk and hours_to_open < NO_ENTRY_HOURS:
            violations.append((t, "too_close_to_open"))
            continue
        if abs(w) > MAX_WEIGHT + 1e-9:
            violations.append((t, "size_clamped"))
            w = MAX_WEIGHT if w > 0 else -MAX_WEIGHT
        approved[t] = w

    # Gross limit: scale down only the names whose exposure is being raised.
    after = {t: book.weight(t, prices) for t in book.qty}
    after.update(approved)
    gross = sum(abs(w) for w in after.values())
    if gross > MAX_GROSS + 1e-9:
        kept = sum(abs(w) for t, w in after.items() if t not in approved)
        room = max(MAX_GROSS - kept, 0.0)
        asked = sum(abs(w) for w in approved.values())
        scale = min(room / asked, 1.0) if asked else 1.0
        for t in approved:
            approved[t] *= scale
            violations.append((t, "gross_scaled"))

    approved = {t: w for t, w in approved.items() if abs(w - book.weight(t, prices)) >= MIN_CHANGE}
    return approved, violations
