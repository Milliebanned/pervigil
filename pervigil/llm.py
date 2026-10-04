"""One LLM setting for the whole project: any OpenAI-compatible chat endpoint.

Set in the environment or in a .env file at the repo root:
    LLM_BASE_URL   e.g. https://hackathon.bitgetops.com/v1  (Qwen credits) or a free-tier provider
    LLM_API_KEY
    LLM_MODEL      e.g. qwen3.8-max
Responses are cached on disk so a replay can be re-scored without calling the model again.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "llm_cache")


def load_env():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def config():
    load_env()
    cfg = {k: os.environ.get(k, "") for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise RuntimeError(f"LLM not configured, missing: {', '.join(missing)} (see .env.example)")
    return cfg


_last_call = [0.0]


def _pace():
    """LLM_MIN_GAP (seconds) spaces out uncached calls, so a long replay stays inside a daily token quota."""
    gap = float(os.environ.get("LLM_MIN_GAP", 0))
    wait = _last_call[0] + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def chat(system, user, run=0, temperature=0.2, use_cache=True):
    """-> assistant text. `run` separates repeated calls on the same prompt (consistency test)."""
    cfg = config()
    key = hashlib.sha256(json.dumps([cfg["LLM_MODEL"], system, user, run, temperature]).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key[:2], key + ".json")
    if use_cache and os.path.exists(path):
        return json.load(open(path))["text"]
    body = json.dumps({
        "model": cfg["LLM_MODEL"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(cfg["LLM_BASE_URL"].rstrip("/") + "/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {cfg['LLM_API_KEY']}",
        "User-Agent": "pervigil/1.0"})   # some providers reject the default urllib agent
    _pace()
    err = None
    for i in range(30):   # rides out rate limits and network drops of up to ~25 minutes
        try:
            with urllib.request.urlopen(req, timeout=300) as r:   # reasoning models can think for minutes
                reply = json.load(r)
            text = reply["choices"][0]["message"]["content"]
            if use_cache:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                json.dump({"model": cfg["LLM_MODEL"], "run": run, "text": text, "usage": reply.get("usage")},
                          open(path, "w"))
            return text
        except urllib.error.HTTPError as e:
            err = RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}")
            if e.code not in (408, 429, 500, 502, 503, 504):
                break
            wait = e.headers.get("Retry-After")
            if wait:   # free tiers say exactly how long to wait; do that instead of guessing
                time.sleep(min(float(wait) + 2, 900))
                continue
        except Exception as e:
            err = e
        time.sleep(min(5 * 2 ** i, 60))   # free tiers rate-limit; back off
    raise RuntimeError(f"LLM call failed: {err}")


def extract_json(text):
    """Pull the first JSON object out of a model reply (handles code fences and stray prose)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None
