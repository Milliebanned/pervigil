"""Mirror the paper book onto Bitget's demo exchange, so every position is a real order with an order id.

The paper book stays the source of truth (it covers all ten names and is what the proving ground scores).
After each live cycle the demo account is reconciled to it: for every name that has a stock perpetual on
the demo exchange, a market order is placed for the difference. Names without one are left to the paper book.

    python -m pervigil.exchange      # connection check: prints demo equity and open positions

Needs a Bitget *Demo* API key (unified account, read-write) in the environment or .env:
    BITGET_DEMO_API_KEY, BITGET_DEMO_SECRET, BITGET_DEMO_PASSPHRASE
Without them the mirror is skipped and the agent runs on the paper book alone.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .data import BASE
from .llm import load_env

PRODUCT = "USDT-FUTURES"
# Stock perpetuals listed on the demo exchange. MSFT, SPY and QQQ are not, so they stay paper-only.
MIRRORED = ("NVDA", "TSLA", "AAPL", "AMZN", "GOOGL", "META", "COIN")
STEP = 0.01          # contract size step, in shares
MIN_USDT = 5.0       # exchange minimum order value


def _keys():
    load_env()
    k = [os.environ.get(n, "") for n in ("BITGET_DEMO_API_KEY", "BITGET_DEMO_SECRET", "BITGET_DEMO_PASSPHRASE")]
    return k if all(k) else None


def configured():
    return _keys() is not None


def _request(method, path, params=None, body=None):
    key, secret, phrase = _keys()
    query = "?" + urllib.parse.urlencode(params) if params else ""
    payload = json.dumps(body) if body is not None else ""
    ts = str(int(time.time() * 1000))
    sign = base64.b64encode(hmac.new(secret.encode(), (ts + method + path + query + payload).encode(),
                                     hashlib.sha256).digest()).decode()
    req = urllib.request.Request(BASE + path + query, data=payload.encode() or None, method=method, headers={
        "ACCESS-KEY": key, "ACCESS-SIGN": sign, "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": phrase,
        "paptrading": "1",          # routes the call to the demo environment
        "Content-Type": "application/json", "locale": "en-US", "User-Agent": "pervigil/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Bitget HTTP {e.code}: {e.read()[:300]!r}")
    if out.get("code") != "00000":
        raise RuntimeError(f"Bitget {out.get('code')}: {out.get('msg')}")
    return out.get("data")


def equity():
    return float(_request("GET", "/api/v3/account/assets").get("usdtEquity") or 0)


def positions():
    """-> {ticker: signed shares} for the mirrored names."""
    rows = (_request("GET", "/api/v3/position/current-position", {"category": PRODUCT}) or {}).get("list") or []
    out = {}
    for r in rows:
        tk = r["symbol"][:-4]
        size = float(r.get("total") or r.get("size") or 0)
        if tk in MIRRORED and size:
            out[tk] = out.get(tk, 0.0) + (size if (r.get("posSide") or r.get("holdSide")) == "long" else -size)
    return out


def _order(ticker, delta, reduce_only):
    """Market order for `delta` shares (signed). -> record for the log."""
    symbol = ticker + "USDT"
    rec = {"ticker": ticker, "symbol": symbol, "side": "buy" if delta > 0 else "sell",
           "size": f"{abs(delta):.2f}", "reduce_only": reduce_only}
    try:
        placed = _request("POST", "/api/v3/trade/place-order", body={
            "category": PRODUCT, "symbol": symbol, "qty": rec["size"], "side": rec["side"], "orderType": "market",
            "reduceOnly": "yes" if reduce_only else "no", "clientOid": f"pervigil-{int(time.time() * 1000)}-{ticker}"})
        rec["order_id"] = placed["orderId"]
        time.sleep(1)   # let the market order fill before reading it back
        d = _request("GET", "/api/v3/trade/order-info", {"orderId": rec["order_id"]})
        rec.update(status=d.get("orderStatus"), price=float(d.get("avgPrice") or 0),
                   filled=d.get("cumExecQty"))
    except Exception as e:
        rec["error"] = str(e)[:300]
    return rec


def plan(target, held, prices):
    """Orders that take `held` to `target` (both {ticker: signed shares}). -> [(ticker, delta, reduce_only)]

    A flip is split in two so the closing leg can be reduce-only. Differences worth less than the
    exchange minimum are left alone, except when closing a position completely.
    """
    orders = []
    for tk in MIRRORED:
        want = round(target.get(tk, 0.0) / STEP) * STEP
        have = held.get(tk, 0.0)
        if have and want * have <= 0:                      # close, or the first leg of a flip
            orders.append((tk, -have, True))
            have = 0.0
        delta = round((want - have) / STEP) * STEP
        if abs(delta) >= STEP and abs(delta) * prices.get(tk, 0.0) >= MIN_USDT:
            orders.append((tk, delta, abs(want) < abs(have)))
    return orders


def sync(book_qty, prices):
    """Reconcile the demo account to the paper book. -> order records ([] if nothing to do or not configured)."""
    if not configured():
        return []
    try:   # one position per name, so a signed target maps to one position; refused while positions are open
        _request("POST", "/api/v3/account/set-hold-mode", body={"holdMode": "one_way_mode"})
    except RuntimeError:
        pass
    return [_order(tk, delta, ro) for tk, delta, ro in plan(book_qty, positions(), prices)]


if __name__ == "__main__":
    if not configured():
        raise SystemExit("No demo key: set BITGET_DEMO_API_KEY, BITGET_DEMO_SECRET, BITGET_DEMO_PASSPHRASE in .env")
    print("demo account equity:", equity(), "USDT")
    print("open positions:", positions() or "none")
