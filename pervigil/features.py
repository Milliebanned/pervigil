"""What the agent is allowed to see at time t. Every number uses only candles that closed at or before t."""
import bisect
import statistics

from .data import BTC, STEP_MS, rtoken
from .sessions import cash_closes

STALE_MS = 12 * 3_600_000  # rTokens can go hours without a trade on weekends; older than this is missing
VOL_DAYS = 20


class Market:
    def __init__(self, candles):
        self.candles = candles
        self._ts = {s: [r[0] for r in rows] for s, rows in candles.items()}

    def price_at(self, symbol, t_ms):
        """Close of the last candle that finished at or before t_ms."""
        ts = self._ts.get(symbol)
        if not ts:
            return None
        i = bisect.bisect_right(ts, t_ms - STEP_MS) - 1
        if i < 0 or t_ms - (ts[i] + STEP_MS) > STALE_MS:
            return None
        return self.candles[symbol][i][4]

    def last_ts(self, symbol):
        ts = self._ts.get(symbol)
        return ts[-1] + STEP_MS if ts else None


def normal_day(market, symbol, before_ms):
    """Std-dev of the last VOL_DAYS cash close-to-close returns: the size of this name's ordinary day."""
    closes = cash_closes(before_ms - 45 * 86_400_000, before_ms)[-(VOL_DAYS + 1):]
    px = [market.price_at(symbol, c) for c in closes]
    rets = [b / a - 1 for a, b in zip(px, px[1:]) if a and b]
    return statistics.pstdev(rets) if len(rets) >= 10 else None


def _pct(now, ref):
    return None if not now or not ref else now / ref - 1


def snapshot(market, session, t_ms, tickers, earnings=None):
    """-> {"t", "session", "hours_since_close", "hours_to_open", "btc_move", "names": {ticker: {...}}}"""
    names = {}
    for tk in tickers:
        sym = rtoken(tk)
        px = market.price_at(sym, t_ms)
        ref = market.price_at(sym, session.close_ms)
        vol = normal_day(market, sym, session.close_ms)
        move = _pct(px, ref)
        if px is None or move is None or not vol:
            continue
        names[tk] = {
            "price": px,
            "move": move,                                  # since the cash close
            "normal_day": vol,
            "z": move / vol,                               # move in units of this name's normal day
            "last_hour": _pct(px, market.price_at(sym, t_ms - 3_600_000)),
            "earnings": (earnings or {}).get(tk, {}).get(session.id),
        }
    return {
        "t": t_ms,
        "session": session.id,
        "kind": session.kind,
        "hours_since_close": round((t_ms - session.close_ms) / 3_600_000, 2),
        "hours_to_open": round((session.open_ms - t_ms) / 3_600_000, 2),
        "btc_move": _pct(market.price_at(BTC, t_ms), market.price_at(BTC, session.close_ms)),
        "names": names,
    }
