"""US-stock context from bitget-mcp-server (https://agent.bitget.com/mcp, free, no key):
earnings calendar and news. Spoken to directly over MCP's streamable-HTTP JSON-RPC.
"""
import html
import json
import os
import re
import urllib.request
from datetime import date, timedelta

from .data import STOCKS
from .sessions import is_trading_day

URL = "https://agent.bitget.com/mcp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EARNINGS_PATH = os.path.join(ROOT, "data", "earnings.json")
ETFS = {"SPY", "QQQ"}
HEAD = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "User-Agent": "pervigil/1.0"}


def _post(payload, sid=None):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={**HEAD, **({"Mcp-Session-Id": sid} if sid else {})})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.headers.get("mcp-session-id"), r.read().decode()


class Mcp:
    def __init__(self):
        self.sid, _ = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "pervigil", "version": "1.0"}}})
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.sid)
        self._id = 1

    def query(self, entry_id, params):
        """Run one catalog entry. -> list of result rows."""
        self._id += 1
        _, body = _post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call", "params": {
            "name": "do_query", "arguments": {"entry_id": entry_id, "params": params}}}, self.sid)
        for line in body.splitlines():
            if line.startswith("data: "):
                msg = json.loads(line[6:])
                text = msg["result"]["content"][0]["text"]
                data = json.loads(text).get("data") or {}
                return data.get("results", []) if isinstance(data, dict) else []
        return []


def _next_trading_day(d):
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def fetch_earnings(start="2026-06-01", end="2026-12-31"):
    """-> {ticker: {session_id: label}}. A session id is the date of the open that ends the closed window."""
    mcp, out = Mcp(), {}
    for tk in STOCKS:
        if tk in ETFS:
            continue
        out[tk] = {}
        for row in mcp.query("equity_calendar", {"symbol": tk, "start_date": start, "end_date": end}):
            day = row.get("perf_brief_dsclsr_date") or row.get("perf_report_dsclsr_date") \
                or row.get("perf_briefing_fore_dsclsr_date") or row.get("perf_report_fore_dsclsr_date")
            if not day:
                continue
            d = date.fromisoformat(day[:10])
            if row.get("is_trading_time") == "盘前":     # before the open
                if is_trading_day(d):
                    out[tk][d.isoformat()] = "earnings due before this open"
            else:                                        # after the close (or unspecified)
                # The feed dates an after-close release one trading day early: on all 8 releases in the
                # replay window the price gapped in the session after _next_trading_day(d), never in it.
                out[tk][_next_trading_day(_next_trading_day(d)).isoformat()] = "earnings released after the last close"
    os.makedirs(os.path.dirname(EARNINGS_PATH), exist_ok=True)
    json.dump(out, open(EARNINGS_PATH, "w"), indent=1, sort_keys=True)
    return out


def load_earnings():
    if not os.path.exists(EARNINGS_PATH):
        return fetch_earnings()
    return json.load(open(EARNINGS_PATH))


def _plain(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def headlines(limit=3, chars=700):
    """Latest items from Bitget's news feed, as plain text. Live use only."""
    rows = Mcp().query("news_label_search", {"label": 1, "page_size": limit})
    return [f"{_plain(r.get('title'))}: {_plain(r.get('content'))[:chars]}" for r in rows[:limit]]


# --- Per-stock news through Chainbase AgentKey (optional; needs AGENTKEY_API_KEY) -------------------------

AGENTKEY_URL = "https://api.agentkey.app/v1/mcp"
COMPANY = {"NVDA": "nvidia", "TSLA": "tesla", "AAPL": "apple", "MSFT": "microsoft", "AMZN": "amazon",
           "GOOGL": "alphabet|google", "META": "meta", "COIN": "coinbase"}


def _agentkey(key, payload, sid=None):
    head = {**HEAD, "Authorization": f"Bearer {key}", **({"Mcp-Session-Id": sid} if sid else {})}
    req = urllib.request.Request(AGENTKEY_URL, data=json.dumps(payload).encode(), headers=head)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.headers.get("mcp-session-id"), r.read().decode()


def stock_news(tickers, since_ms, until_ms, per=2, chars=220):
    """Headlines about each ticker published inside [since, until], newest first. -> ["NVDA (Yahoo): ...", ...]

    Finnhub company news via AgentKey. The feed tags loosely, so only items naming the company are kept.
    """
    key = os.environ.get("AGENTKEY_API_KEY")
    if not key:
        return []
    sid, _ = _agentkey(key, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "pervigil", "version": "1.0"}}})
    _agentkey(key, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    day = lambda ms: date.fromtimestamp(ms / 1000).isoformat()
    out = []
    for n, tk in enumerate(t for t in tickers if t in COMPANY):
        _, body = _agentkey(key, {"jsonrpc": "2.0", "id": 2 + n, "method": "tools/call", "params": {
            "name": "execute_tool", "arguments": {"name": "Finnhub/companyNews", "params": {
                "symbol": tk, "from": day(since_ms), "to": day(until_ms)}}}}, sid)
        rows = []
        for line in body.splitlines():
            line = line[6:] if line.startswith("data: ") else line
            if line.startswith("{") and "result" in line:
                rows = json.loads(json.loads(line)["result"]["content"][0]["text"]).get("data") or []
        named = re.compile(rf"\b({tk}|{COMPANY[tk]})\b", re.I)
        hits = [r for r in rows if since_ms <= r.get("datetime", 0) * 1000 <= until_ms
                and named.search(f"{r.get('headline')} {r.get('summary')}")]
        hits.sort(key=lambda r: -r["datetime"])
        out += [f"{tk} ({r.get('source')}): {_plain(r.get('headline'))}. {_plain(r.get('summary'))[:chars]}"
                for r in hits[:per]]
    return out


if __name__ == "__main__":
    e = fetch_earnings()
    print(json.dumps(e, indent=1))
    for h in headlines():
        print("-", h[:300])
