<img src="docs/logo.svg" width="72" alt="Pervigil logo">

# Pervigil

*Vigilat dum dormis* — it keeps watch while you sleep.

**Live page:** https://milliebanned.github.io/pervigil/

An LLM agent that trades tokenized US stocks (Bitget rTokens) only while the US cash market is closed,
plus a proving ground that measures whether the agent beats a fixed rule. Paper trading only.

Bitget AI Base Camp Hackathon S2 · Agentic Trading · Open Theme.

## How it works

1. **Sense** — every closed-hours decision time, the agent sees each name's move since the cash close, the size of
   that name's normal day, their ratio (z), Bitcoin's move over the same window, earnings dates and headlines.
   Prices come from Bitget public market data; earnings and news from `bitget-mcp-server`.
2. **Decide** — the LLM returns target weights with a one-line reason per name. Staying out is allowed.
3. **Risk layer** — hard limits the LLM cannot override: 20% per name, 60% gross, no new risk in the last
   30 minutes before the open, -3% position stop, -2% session stop. Every overruled proposal is logged.
4. **Execute** — paper fills at Bitget prices with 0.10% fee and 0.05% slippage per side.
5. **Flatten** — everything is closed 15 minutes after the open, and a morning note is written for the human.

## Run it

Python 3.12, standard library only.

```
cp .env.example .env            # any OpenAI-compatible endpoint
python -m pervigil.data       # download / refresh candles
python -m unittest discover tests
python -m pervigil.replay rules
python -m pervigil.replay agent 3
python -m pervigil.scorecard
python -m pervigil.live       # one live cycle (scheduled hourly by GitHub Actions)
python -m pervigil.site       # build docs/index.html
```

## Where things are

- `logs/paper_log.jsonl` — live paper-trading log, appended by the scheduled run.
- `logs/notes/` — morning notes.
- `results/` — replay output per policy and `scorecard.json`.
- `data/candles/` — the frozen 15-minute candles the replay uses.
