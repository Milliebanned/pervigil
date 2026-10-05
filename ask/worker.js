// Ask Pervigil: a Cloudflare Worker that answers questions about the agent's decisions.
// It keeps the LLM key off the public page. Each answer is grounded in the agent's own log on GitHub,
// plus the live numbers the page shows for the selected stock.
//
//   cd ask && npx wrangler deploy
//   npx wrangler secret put LLM_API_KEY      (LLM_BASE_URL and LLM_MODEL are in wrangler.toml)

const REPO_RAW = "https://raw.githubusercontent.com/Milliebanned/pervigil/main/";
const STOCKS = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ", "COIN"];
const ORIGINS = ["https://milliebanned.github.io", "http://localhost:8000", "null"];
const RECENT = 8;              // decisions to show the model
const PER_MINUTE = 6;          // questions per visitor per minute
const seen = new Map();        // ip -> recent request times (per isolate; a soft limit, not a wall)

const SYSTEM = `You are Pervigil, an AI agent that paper-trades ten tokenized US stocks (Bitget rTokens) only while the US cash market is closed, and is flat 15 minutes after the open.
You answer visitors' questions about your own decisions. Use only the facts in the context: your decision log, your replay scorecard and the live numbers. Never invent news, prices or trades. If headlines_read says "not recorded", say the headlines you read then were not saved, rather than guessing them. If it says "none were available", say you had no news then.
How you decide: every six hours while the market is closed you see each stock's move since the 4pm ET close, that move divided by the stock's normal day (the spread of its last 20 daily returns; "1.0x" means one normal day), Bitcoin's move over the same window, earnings dates and headlines. You set a target weight with a one-line reason, and staying out is allowed. A round trip costs about 0.30% (0.10% fee and 0.05% slippage per side), so small moves are not worth trading.
Hard limits you cannot override: 20% per stock, 60% in total, no new risk in the last 30 minutes before the open, -3% stop per position, -2% stop per session.
Answer in plain English, under 130 words, first person. Name the specific numbers and news behind a decision. This is paper trading and not financial advice; if asked what someone should buy, explain what you would look for instead.`;

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function reply(body, status, origin) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...cors(origin) } });
}

async function cached(url, ctx, seconds) {
  const cache = caches.default, key = new Request(url);
  let res = await cache.match(key);
  if (!res) {
    res = await fetch(url, { headers: { "User-Agent": "pervigil-ask" } });
    if (!res.ok) throw new Error(`${url}: ${res.status}`);
    res = new Response(res.body, res);
    res.headers.set("Cache-Control", `max-age=${seconds}`);
    ctx.waitUntil(cache.put(key, res.clone()));
  }
  return res;
}

const pct = (x) => (x == null ? null : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`);

// The decision log, cut down to what matters for one stock.
async function history(ticker, ctx) {
  const text = await (await cached(REPO_RAW + "logs/paper_log.jsonl", ctx, 300)).text();
  const recs = text.split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const decisions = recs.filter((r) => r.event === "decision").slice(-RECENT);
  const other = recs.filter((r) => ["session_exit", "stops"].includes(r.event)).slice(-4);
  return {
    decisions: decisions.map((r) => ({
      time_utc: r.time,
      hours_to_open: r.hours_to_open,
      [ticker + "_seen"]: r.seen?.[ticker] ? { move_since_close: pct(r.seen[ticker].move), size_x_normal_day: r.seen[ticker].z } : "no price",
      biggest_movers: Object.entries(r.seen || {}).sort((a, b) => Math.abs(b[1].z) - Math.abs(a[1].z)).slice(0, 3)
        .map(([t, f]) => `${t} ${pct(f.move)} (${f.z}x)`),
      bitcoin_move: pct(r.btc_move),
      approved_weights: r.approved || {},
      reason_for_this_stock: r.reasons?.[ticker] || null,
      note: r.note,
      blocked_by_risk_layer: r.violations || [],
      headlines_read: r.headlines === undefined ? "not recorded (the log only keeps headlines from 5 Oct 2026 on)"
        : r.headlines.length ? r.headlines.map((h) => h.slice(0, 260)) : "none were available",
    })),
    session_results: other.map((r) => ({ time_utc: r.time, event: r.event, ret: pct(r.ret), stops: r.stops })),
  };
}

async function scorecard(ctx) {
  try {
    const c = await (await cached(REPO_RAW + "results/scorecard.json", ctx, 3600)).json();
    const a = c.policies.agent_run0, f = c.policies.always_follow, d = c.policies.always_fade;
    return `Replay over ${a.sessions} past closed sessions after costs: agent ${pct(a.total_return)} (Sharpe ${a.sharpe.toFixed(2)}, max drawdown ${pct(a.max_drawdown)}, traded ${a.sessions_traded} sessions); always-follow rule ${pct(f.total_return)}; always-fade rule ${pct(d.total_return)}; doing nothing 0%.`;
  } catch {
    return "";
  }
}

function num(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(origin) });
    if (request.method !== "POST") return reply({ error: "POST a question" }, 405, origin);

    const ip = request.headers.get("CF-Connecting-IP") || "?", now = Date.now();
    const times = (seen.get(ip) || []).filter((t) => now - t < 60_000);
    if (times.length >= PER_MINUTE) return reply({ error: "Too many questions. Try again in a minute." }, 429, origin);
    seen.set(ip, [...times, now]);

    let body;
    try { body = await request.json(); } catch { return reply({ error: "Bad request" }, 400, origin); }
    const ticker = String(body.ticker || "").toUpperCase();
    const question = String(body.question || "").trim().slice(0, 400);
    if (!STOCKS.includes(ticker) || !question) return reply({ error: "Pick a stock and ask a question" }, 400, origin);
    const now_view = body.view || {};
    const live = {
      stock: ticker,
      price: num(now_view.price),
      move_since_us_close: pct(num(now_view.move)),
      size_x_normal_day: num(now_view.z) == null ? null : Number(num(now_view.z).toFixed(2)),
      bitcoin_since_close: pct(num(now_view.btc)),
      us_market: now_view.open ? "open (you are off duty)" : "closed (you are on watch)",
      your_position_now: num(now_view.held) ? pct(num(now_view.held)) + " of the account" : "none",
    };
    const turns = (Array.isArray(body.history) ? body.history : []).slice(-4)
      .filter((m) => m && ["user", "assistant"].includes(m.role))
      .map((m) => ({ role: m.role, content: String(m.content).slice(0, 600) }));

    let context;
    try {
      context = { now_utc: new Date().toISOString(), live, ...(await history(ticker, ctx)), scorecard: await scorecard(ctx) };
    } catch (e) {
      return reply({ error: "Could not read the decision log right now." }, 502, origin);
    }

    const res = await fetch(env.LLM_BASE_URL.replace(/\/$/, "") + "/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.LLM_API_KEY}` },
      body: JSON.stringify({
        model: env.LLM_MODEL,
        temperature: 0.3,
        max_tokens: 600,
        messages: [
          { role: "system", content: SYSTEM },
          { role: "system", content: "Context (JSON):\n" + JSON.stringify(context) },
          ...turns,
          { role: "user", content: `[About ${ticker}] ${question}` },
        ],
      }),
    });
    if (!res.ok) return reply({ error: "The model is busy. Try again shortly." }, 502, origin);
    const out = await res.json();
    let answer = out.choices?.[0]?.message?.content || "";
    answer = answer.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
    return reply({ answer: answer || "No answer came back. Try asking again.", model: env.LLM_MODEL }, 200, origin);
  },
};
