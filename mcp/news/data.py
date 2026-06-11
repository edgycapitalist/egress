"""News backend for the News MCP.

Two layers, one boundary:

* **Real feed** — Alpha Vantage ``NEWS_SENTIMENT``: real headlines and real
  per-article sentiment for a ticker over a period, read with the key in
  ``ALPHAVANTAGE_API_KEY``. Every response is cached in Postgres (and an
  in-process memo) keyed by symbol+period, so a whole run makes at most one real
  call per symbol+period and serves every later window from cache — essential on
  the free ~25-calls/day tier (fetch-once, reuse-many).
* **Synthetic fallback** — a deterministic crisis-tape synthesiser used
  automatically whenever the API key is missing or the call is rate-limited, so
  the offline test suite and the deterministic baseline run with **zero network**.

``get_sentiment(text)`` stays a deterministic lexicon scorer: Alpha Vantage scores
*tickers*, not arbitrary text, and this tool is used to score free text (e.g. a
scenario description), so there is no real-feed equivalent to swap in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request

import numpy as np

# --------------------------------------------------------------------------- #
# Alpha Vantage client + cache (gated on the API key; never required offline)
# --------------------------------------------------------------------------- #
_AV_BASE = "https://www.alphavantage.co/query"
_MEMO: dict[str, dict] = {}  # process-lifetime cache: one real call per key per run


def _api_key() -> str | None:
    return (os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip() or None


def _av_get(params: dict) -> dict | None:
    """One Alpha Vantage call. Returns parsed JSON, or ``None`` on any failure.

    AV signals a rate-limit/error with HTTP 200 + a ``Note`` / ``Information`` /
    ``Error Message`` envelope; all are treated as a miss so the caller falls back.
    """
    key = _api_key()
    if not key:
        return None
    url = _AV_BASE + "?" + urllib.parse.urlencode({**params, "apikey": key})
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 (https only)
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or any(
        k in payload for k in ("Note", "Information", "Error Message")
    ):
        return None
    return payload


def _ensure_cache_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mcp_cache ("
        "provider text NOT NULL, cache_key text NOT NULL, "
        "payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(), "
        "PRIMARY KEY (provider, cache_key))"
    )


def _cache_get(provider: str, key: str) -> dict | None:
    """Best-effort cache read: in-process memo, then Postgres. Never raises."""
    memo = _MEMO.get(f"{provider}|{key}")
    if memo is not None:
        return memo
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            _ensure_cache_table(conn)
            row = conn.execute(
                "SELECT payload FROM mcp_cache WHERE provider = %s AND cache_key = %s",
                (provider, key),
            ).fetchone()
        if row:
            _MEMO[f"{provider}|{key}"] = row[0]
            return row[0]
    except Exception:
        return None
    return None


def _cache_put(provider: str, key: str, payload: dict) -> None:
    """Best-effort cache write to the memo and Postgres. Never raises."""
    _MEMO[f"{provider}|{key}"] = payload
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return
    try:
        import psycopg
        from psycopg.types.json import Json

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            _ensure_cache_table(conn)
            conn.execute(
                "INSERT INTO mcp_cache (provider, cache_key, payload, fetched_at) "
                "VALUES (%s, %s, %s, now()) "
                "ON CONFLICT (provider, cache_key) DO UPDATE "
                "SET payload = EXCLUDED.payload, fetched_at = now()",
                (provider, key, Json(payload)),
            )
            conn.commit()
    except Exception:
        return


def _label(score: float) -> str:
    return "negative" if score < -0.15 else "positive" if score > 0.15 else "neutral"


def _av_time_range(period: str) -> tuple[str | None, str | None]:
    """Map a free-form period to Alpha Vantage ``time_from``/``time_to`` (YYYYMMDDTHHMM)."""
    p = (period or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", p)
    if m:
        y, mo, d = m.groups()
        return f"{y}{mo}{d}T0000", f"{y}{mo}{d}T2359"
    m = re.match(r"(\d{4})-Q([1-4])", p, re.IGNORECASE)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        start_mo = (q - 1) * 3 + 1
        end_mo = start_mo + 2
        last = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][end_mo - 1]
        return f"{y}{start_mo:02d}01T0000", f"{y}{end_mo:02d}{last:02d}T2359"
    m = re.match(r"(\d{4})-(\d{2})", p)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        last = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
        return f"{y}{mo:02d}01T0000", f"{y}{mo:02d}{last:02d}T2359"
    m = re.match(r"(\d{4})$", p)
    if m:
        return f"{m.group(1)}0101T0000", f"{m.group(1)}1231T2359"
    return None, None


def _av_news(symbol: str, period: str) -> dict | None:
    """Cached, contract-shaped real headlines for ``symbol`` over ``period``."""
    cache_key = f"{symbol}:{period}"
    cached = _cache_get("av_news", cache_key)
    if cached is not None:
        return cached

    params = {"function": "NEWS_SENTIMENT", "tickers": symbol, "sort": "RELEVANCE", "limit": "50"}
    time_from, time_to = _av_time_range(period)
    if time_from:
        params["time_from"] = time_from
    if time_to:
        params["time_to"] = time_to

    raw = _av_get(params)
    feed = (raw or {}).get("feed")
    if not feed:
        return None

    headlines: list[dict] = []
    scores: list[float] = []
    for offset, art in enumerate(feed[:12]):
        # Prefer the ticker-specific sentiment, fall back to the article overall.
        score = None
        for ts in art.get("ticker_sentiment", []):
            if ts.get("ticker", "").upper() == symbol:
                try:
                    score = float(ts["ticker_sentiment_score"])
                except (KeyError, TypeError, ValueError):
                    score = None
                break
        if score is None:
            try:
                score = float(art.get("overall_sentiment_score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
        score = round(max(-1.0, min(1.0, score)), 3)
        scores.append(score)
        headlines.append(
            {
                "day": offset,
                "headline": art.get("title", "").strip(),
                "source": art.get("source", "Alpha Vantage"),
                "sentiment": score,
            }
        )
    overall = round(sum(scores) / len(scores), 3) if scores else 0.0
    payload = {
        "symbol": symbol,
        "period": period,
        "headlines": headlines,
        "overall_sentiment": overall,
        "sentiment_label": _label(overall),
        "headline_count": len(headlines),
        "source": "alphavantage",
    }
    _cache_put("av_news", cache_key, payload)
    return payload


# --------------------------------------------------------------------------- #
# Synthetic fallback (deterministic crisis tape; the offline / no-key path)
# --------------------------------------------------------------------------- #
_NEGATIVE = {
    "downgrade", "cut", "plunge", "plummet", "selloff", "sell-off", "crash",
    "default", "bankruptcy", "fraud", "probe", "lawsuit", "withdrawal", "margin",
    "liquidation", "fear", "panic", "collapse", "halt", "loss", "losses", "miss",
    "warning", "slump", "fall", "falling", "weak", "risk", "downturn", "recession",
    "contagion", "distress", "redemptions",
}
_POSITIVE = {
    "upgrade", "beat", "surge", "rally", "rebound", "growth", "profit", "gain",
    "gains", "strong", "record", "support", "stabilise", "stabilize", "recovery",
    "inflow", "inflows", "optimism", "calm",
}

# Curated negative headline templates for a credible crisis tape (fallback only).
_TEMPLATES = [
    ("{sym} cut to junk by major rating agency", "Ratings Wire", -0.8),
    ("Funds rush to trim {sym} as risk limits breach", "Market Desk", -0.7),
    ("{sym} gaps lower on heavy volume amid forced selling", "Tape Report", -0.75),
    ("Analysts warn of crowded positioning in {sym}", "Street Research", -0.5),
    ("Liquidity thins in {sym} as market makers step back", "Microstructure Daily", -0.6),
    ("{sym} slides as redemptions hit holders", "Flows Monitor", -0.65),
    ("Bargain hunters eye {sym} after steep drop", "Value Watch", 0.2),
]


def _symbol_seed(symbol: str, period: str) -> int:
    digest = hashlib.sha256(f"{symbol.upper()}|{period}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _synthetic_event_news(symbol: str, period: str) -> dict:
    sym = symbol.upper()
    rng = np.random.default_rng(_symbol_seed(sym, period))
    n = int(rng.integers(4, len(_TEMPLATES) + 1))
    chosen = rng.choice(len(_TEMPLATES), size=n, replace=False)
    headlines: list[dict] = []
    scores: list[float] = []
    for offset, i in enumerate(sorted(chosen)):
        template, source, base = _TEMPLATES[i]
        jitter = float(rng.normal(0.0, 0.08))
        s = round(max(-1.0, min(1.0, base + jitter)), 3)
        scores.append(s)
        headlines.append(
            {"day": offset, "headline": template.format(sym=sym), "source": source, "sentiment": s}
        )
    overall = round(sum(scores) / len(scores), 3) if scores else 0.0
    return {
        "symbol": sym,
        "period": period,
        "headlines": headlines,
        "overall_sentiment": overall,
        "sentiment_label": _label(overall),
        "headline_count": len(headlines),
        "source": "synthetic",
    }


# --------------------------------------------------------------------------- #
# Public tools — real feed first (cached), synthetic fallback
# --------------------------------------------------------------------------- #
def get_event_news(instrument: str, period: str) -> dict:
    """Headlines and an aggregate sentiment for ``instrument`` over ``period``.

    Real Alpha Vantage ``NEWS_SENTIMENT`` when a key is set (cached by
    symbol+period); otherwise a deterministic crisis tape. Returns dated headlines
    (each with a source and sentiment) and an overall sentiment the archetypes use.
    """
    sym = instrument.upper()
    if _api_key():
        real = _av_news(sym, period)
        if real is not None:
            return real
    return _synthetic_event_news(sym, period)


def get_sentiment(text: str) -> dict:
    """Score the sentiment of arbitrary ``text`` in ``[-1, 1]`` (lexicon, no model).

    Kept deterministic on purpose: Alpha Vantage scores tickers, not free text, and
    this tool scores arbitrary text (e.g. a scenario description), so there is no
    real-feed substitute. ``label`` is negative/neutral/positive.
    """
    tokens = re.findall(r"[a-zA-Z'-]+", text.lower())
    if not tokens:
        return {"score": 0.0, "label": "neutral", "magnitude": 0.0, "tokens": 0}
    neg = sum(1 for t in tokens if t in _NEGATIVE)
    pos = sum(1 for t in tokens if t in _POSITIVE)
    raw = (pos - neg) / max(1, neg + pos)
    score = round(max(-1.0, min(1.0, raw)), 3)
    magnitude = round((neg + pos) / len(tokens), 3)
    return {"score": score, "label": _label(score), "magnitude": magnitude, "tokens": len(tokens)}
