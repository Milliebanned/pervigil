"""US cash-market hours, and the closed windows between them where only rTokens trade."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)
# NYSE full-day holidays, 2026.
HOLIDAYS = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
            date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}


def ms(dt):
    return int(dt.timestamp() * 1000)


def from_ms(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc)


def is_trading_day(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def _at(d, t):
    return ms(datetime.combine(d, t, NY))


def is_market_open(t_ms):
    local = from_ms(t_ms).astimezone(NY)
    return is_trading_day(local.date()) and OPEN <= local.time() < CLOSE


@dataclass(frozen=True)
class ClosedSession:
    close_ms: int   # cash market closes (16:00 New York)
    open_ms: int    # next cash open (09:30 New York)
    kind: str       # "overnight" or "weekend" (weekend also covers holiday gaps)

    @property
    def id(self):
        return from_ms(self.open_ms).astimezone(NY).strftime("%Y-%m-%d")

    @property
    def hours(self):
        return (self.open_ms - self.close_ms) / 3_600_000


def closed_sessions(start_ms, end_ms):
    """Every closed window whose cash close is >= start_ms and whose next open is <= end_ms."""
    out = []
    d = from_ms(start_ms).astimezone(NY).date() - timedelta(days=1)
    last = from_ms(end_ms).astimezone(NY).date()
    prev_close = None
    while d <= last:
        if is_trading_day(d):
            if prev_close is not None:
                o = _at(d, OPEN)
                if prev_close >= start_ms and o <= end_ms:
                    kind = "weekend" if o - prev_close > 24 * 3_600_000 else "overnight"
                    out.append(ClosedSession(prev_close, o, kind))
            prev_close = _at(d, CLOSE)
        d += timedelta(days=1)
    return out


def current_session(now_ms):
    """The closed window containing now_ms, or None while the cash market is open."""
    if is_market_open(now_ms):
        return None
    for s in closed_sessions(now_ms - 6 * 86_400_000, now_ms + 6 * 86_400_000):
        if s.close_ms <= now_ms < s.open_ms:
            return s
    return None


def cash_closes(start_ms, end_ms):
    """Timestamps of each 16:00 New York close in range (for daily close-to-close returns)."""
    out = []
    d = from_ms(start_ms).astimezone(NY).date()
    last = from_ms(end_ms).astimezone(NY).date()
    while d <= last:
        if is_trading_day(d):
            c = _at(d, CLOSE)
            if start_ms <= c <= end_ms:
                out.append(c)
        d += timedelta(days=1)
    return out
