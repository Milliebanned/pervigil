"""Paper book: positions are signed weights of equity in rTokens, marked to Bitget prices.

Shorts are assumed to be taken through the matching stock perpetual; costs use the (higher) spot taker fee.
"""
from dataclasses import dataclass, field

FEE = 0.0010       # Bitget rToken spot taker fee, per side
SLIPPAGE = 0.0005  # assumed, per side
START_EQUITY = 10_000.0


@dataclass
class Book:
    cash: float = START_EQUITY
    qty: dict = field(default_factory=dict)      # ticker -> signed units
    entry: dict = field(default_factory=dict)    # ticker -> average entry price
    traded: float = 0.0                          # cumulative notional traded
    costs: float = 0.0                           # cumulative fees + slippage

    def equity(self, prices):
        return self.cash + sum(q * prices[t] for t, q in self.qty.items())

    def weight(self, ticker, prices):
        q = self.qty.get(ticker, 0.0)
        return q * prices[ticker] / self.equity(prices) if q else 0.0

    def pnl_pct(self, ticker, price):
        """Unrealised return on the open position in `ticker`."""
        q = self.qty.get(ticker, 0.0)
        if not q:
            return 0.0
        return (price / self.entry[ticker] - 1) * (1 if q > 0 else -1)

    def set_weight(self, ticker, target, prices):
        """Trade `ticker` to `target` weight of current equity. Returns the fill record, or None if no trade."""
        px = prices[ticker]
        eq = self.equity(prices)
        old = self.qty.get(ticker, 0.0)
        new = target * eq / px
        delta = new - old
        if abs(delta * px) < 1e-9:
            return None
        fill = px * (1 + SLIPPAGE if delta > 0 else 1 - SLIPPAGE)
        notional = abs(delta) * fill
        fee = notional * FEE
        closed = min(abs(delta), abs(old)) if old * delta < 0 else 0.0
        realized = closed * (fill - self.entry[ticker]) * (1 if old > 0 else -1) if closed else 0.0
        self.cash -= delta * fill + fee
        self.traded += notional
        self.costs += fee + abs(delta) * abs(fill - px)
        if abs(new) < 1e-12:
            self.qty.pop(ticker, None)
            self.entry.pop(ticker, None)
        else:
            same_side = old * new > 0
            if same_side and abs(new) > abs(old):      # adding: average the entry
                self.entry[ticker] = (self.entry[ticker] * abs(old) + fill * abs(delta)) / abs(new)
            elif not same_side:                         # new or flipped position
                self.entry[ticker] = fill
            self.qty[ticker] = new
        return {"ticker": ticker, "side": "buy" if delta > 0 else "sell", "qty": abs(delta),
                "price": fill, "notional": notional, "fee": fee, "target_weight": target,
                "realized_pnl": realized, "equity_after": self.equity(prices)}

    def flatten(self, prices):
        return [f for t in list(self.qty) if (f := self.set_weight(t, 0.0, prices))]

    def to_dict(self):
        return {"cash": self.cash, "qty": self.qty, "entry": self.entry, "traded": self.traded, "costs": self.costs}

    @classmethod
    def from_dict(cls, d):
        return cls(**d)
