"""Bitget public market data (no API key). 15-minute candles, cached on disk as CSV."""
import csv
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

BASE = "https://api.bitget.com"
STEP_MS = 15 * 60 * 1000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDLE_DIR = os.path.join(ROOT, "data", "candles")

# Liquid rTokens (tokenized US stocks / ETFs) plus BTC as the cross-asset reference.
STOCKS = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ", "COIN"]
BTC = "BTCUSDT"


def rtoken(ticker):
    return f"R{ticker}USDT"


def http_json(path, params=None, tries=10):
    """GET a Bitget public endpoint. The API resets connections now and then, so retry,
    falling back to curl when urllib keeps failing."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    err = None
    for i in range(tries):
        try:
            if i % 2 == 0:
                req = urllib.request.Request(url, headers={"User-Agent": "pervigil/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = json.load(r)
            else:
                out = subprocess.run(["curl", "-s", "-m", "20", url], capture_output=True, text=True).stdout
                body = json.loads(out)
            if body.get("code") == "00000":
                return body["data"]
            err = RuntimeError(f"bitget {body.get('code')}: {body.get('msg')}")
        except Exception as e:  # network reset, timeout, bad JSON
            err = e
        time.sleep(min(1.0 * (i + 1), 8))
    raise RuntimeError(f"GET {url} failed: {err}")


def _path(symbol):
    return os.path.join(CANDLE_DIR, f"{symbol}.csv")


def load_cached(symbol):
    """-> list of (ts_ms, open, high, low, close, quote_volume), ascending."""
    p = _path(symbol)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in csv.reader(f)]


def _save(symbol, rows):
    os.makedirs(CANDLE_DIR, exist_ok=True)
    with open(_path(symbol), "w", newline="") as f:
        csv.writer(f).writerows(rows)


def _fetch_page(symbol, end_ms):
    data = http_json("/api/v2/spot/market/history-candles",
                     {"symbol": symbol, "granularity": "15min", "endTime": end_ms, "limit": 200})
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])) for r in data]


def update(symbol, days=100, now_ms=None):
    """Bring the cache for `symbol` up to date, back-filling to `days` ago. Only closed candles are kept."""
    now_ms = now_ms or int(time.time() * 1000)
    last_closed = (now_ms // STEP_MS) * STEP_MS - STEP_MS
    have = {r[0]: r for r in load_cached(symbol)}
    want_from = now_ms - days * 86_400_000
    def fill(end, stop_at):
        while end > stop_at:
            page = _fetch_page(symbol, end)
            if not page:
                break
            for r in page:
                if r[0] <= last_closed:
                    have[r[0]] = r
            _save(symbol, sorted(have.values()))  # keep progress if a later page fails
            first = min(r[0] for r in page)
            if first >= end:
                break
            end = first
            time.sleep(0.35)

    if have:
        fill(now_ms, max(have))            # newer candles since the last run
        fill(min(have), want_from)         # older candles not yet back-filled
    else:
        fill(now_ms, want_from)
    rows = sorted(have.values())   # never trim: older history stays in the cache
    _save(symbol, rows)
    return rows


def update_all(days=100, pause=45, attempts=6):
    """Update every symbol. Bitget throttles bursts with empty replies, so pause and resume."""
    out = {}
    for sym in [rtoken(t) for t in STOCKS] + [BTC]:
        for i in range(attempts):
            try:
                out[sym] = update(sym, days)
                break
            except RuntimeError:
                if i == attempts - 1:
                    raise
                time.sleep(pause)
    return out


def load_all():
    return {sym: load_cached(sym) for sym in [rtoken(t) for t in STOCKS] + [BTC]}


if __name__ == "__main__":
    for sym, rows in update_all().items():
        print(sym, len(rows))
