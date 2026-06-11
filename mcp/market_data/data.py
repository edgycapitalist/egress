"""Market-data backend for the Market Data MCP.

Two layers, one boundary:

* **Real feed** — Alpha Vantage ``TIME_SERIES_DAILY`` for OHLCV + reference data,
  read with the key in ``ALPHAVANTAGE_API_KEY``. Every response is cached in
  Postgres (and an in-process memo) keyed by symbol, so a whole run makes at most
  one real call per symbol and serves every later window from cache — essential on
  the free ~25-calls/day tier (fetch-once, reuse-many).
* **Synthetic fallback** — a deterministic synthesiser used automatically whenever
  the API key is missing or the call is rate-limited/errors. This keeps the offline
  test suite and the deterministic baseline running with **zero network**.

The functions here are plain (no MCP/ADK/cloud) and shared by the FastMCP server
and the in-process ADK ``FunctionTool`` wrappers. The HTTP call uses only the
standard library; the Postgres cache imports ``psycopg`` lazily and degrades to a
no-op if the DB is unreachable, so nothing here is ever a hard dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

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

    Alpha Vantage signals a rate-limit or error with an HTTP 200 plus a ``Note`` /
    ``Information`` / ``Error Message`` envelope; we treat all of those as a miss so
    the caller falls back to the synthesiser.
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


def _av_daily(symbol: str) -> dict | None:
    """Cached, normalised TIME_SERIES_DAILY for ``symbol`` — one real call per run.

    Shared by the reference, history, and liquidity tools so a run spends a single
    Alpha Vantage call on prices regardless of how many windows/agents ask.
    """
    cached = _cache_get("av_daily", symbol)
    if cached is not None:
        return cached
    raw = _av_get({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "full"})
    series_raw = (raw or {}).get("Time Series (Daily)")
    if not series_raw:
        return None
    series = [
        {
            "date": d,
            "open": float(v["1. open"]),
            "high": float(v["2. high"]),
            "low": float(v["3. low"]),
            "close": float(v["4. close"]),
            "volume": int(float(v["5. volume"])),
        }
        for d, v in sorted(series_raw.items())
    ]
    normalised = {"symbol": symbol, "series": series}
    _cache_put("av_daily", symbol, normalised)
    return normalised


# --------------------------------------------------------------------------- #
# Synthetic fallback (deterministic; the offline / no-key path)
# --------------------------------------------------------------------------- #
# Curated reference fixtures. These mirror the flagship scenario so the scenario
# author resolves the flagship ticker to exactly the instrument the engine
# simulates even with no API key (offline). CVNA is the flagship; ACME is kept as
# a generic fictional fixture for tests.
_FIXTURES: dict[str, dict] = {
    "CVNA": {
        "name": "Carvana Co.",
        "sector": "Consumer Discretionary",
        "currency": "USD",
        "reference_price": 15.0,
        "tick_size": 0.01,
        "adv": 12_000_000,
        "free_float": 90_000_000,
        "halt_tier": 1,
    },
    "ACME": {
        "name": "Acme Industrial Corp",
        "sector": "Industrials",
        "currency": "USD",
        "reference_price": 100.0,
        "tick_size": 0.01,
        "adv": 5_000_000,
        "free_float": 120_000_000,
        "halt_tier": 1,
    },
}


def _symbol_seed(symbol: str) -> int:
    """Stable per-symbol seed (hashlib, not the salted built-in ``hash``)."""
    digest = hashlib.sha256(symbol.upper().encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _rng(symbol: str, salt: str = "") -> np.random.Generator:
    return np.random.default_rng(_symbol_seed(symbol + salt))


def _reference(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol in _FIXTURES:
        return dict(_FIXTURES[symbol])
    # Synthesise a plausible mid-cap for any unknown symbol, deterministically.
    rng = _rng(symbol, "ref")
    price = round(float(rng.uniform(20.0, 250.0)), 2)
    adv = int(rng.integers(500_000, 12_000_000))
    free_float = int(rng.integers(30_000_000, 400_000_000))
    halt_tier = 1 if price >= 3.0 else 2
    return {
        "name": f"{symbol} Holdings",
        "sector": "Unknown",
        "currency": "USD",
        "reference_price": price,
        "tick_size": 0.01,
        "adv": adv,
        "free_float": free_float,
        "halt_tier": halt_tier,
    }


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return default


def _synthetic_historical(symbol: str, start: str, end: str) -> dict:
    ref = _reference(symbol)
    end_d = _parse_date(end, date(2025, 1, 31))
    start_d = _parse_date(start, end_d - timedelta(days=60))
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    n_days = (end_d - start_d).days + 1
    n_days = max(1, min(n_days, 500))  # guard absurd ranges

    rng = _rng(symbol, f"hist:{start_d}:{end_d}")
    daily_vol = 0.015  # ~1.5% daily sigma
    shocks = rng.normal(0.0, daily_vol, size=n_days)
    price = ref["reference_price"]
    bars: list[dict] = []
    cur = start_d
    for r in shocks:
        prev = price
        price = round(price * float(np.exp(r)), 2)
        hi = round(max(prev, price) * (1.0 + abs(float(rng.normal(0, 0.004)))), 2)
        lo = round(min(prev, price) * (1.0 - abs(float(rng.normal(0, 0.004)))), 2)
        vol = int(ref["adv"] * float(rng.uniform(0.6, 1.6)))
        bars.append(
            {"date": cur.isoformat(), "open": prev, "high": hi, "low": lo,
             "close": price, "volume": vol}
        )
        cur += timedelta(days=1)
    return _window_payload(symbol, start_d.isoformat(), end_d.isoformat(), bars, source="synthetic")


# --------------------------------------------------------------------------- #
# Shared shapers (contract §) — identical output whether real or synthetic
# --------------------------------------------------------------------------- #
def _window_payload(symbol: str, start: str, end: str, bars: list[dict], source: str) -> dict:
    closes = [b["close"] for b in bars]
    returns = [round((closes[i] / closes[i - 1]) - 1.0, 6) for i in range(1, len(closes))]
    return {
        "symbol": symbol.upper(),
        "start": start,
        "end": end,
        "bars": bars,
        "returns": returns,
        "realized_vol_daily": round(float(np.std(returns)) if returns else 0.0, 6),
        "source": source,
    }


def _reference_from_series(symbol: str, series: list[dict]) -> dict:
    """Build contract reference data from a real daily series (latest snapshot)."""
    latest = series[-1]
    recent = series[-30:]
    adv = int(np.mean([b["volume"] for b in recent])) if recent else 0
    return {
        "symbol": symbol.upper(),
        "name": symbol.upper(),
        "sector": "Unknown",
        "currency": "USD",
        "reference_price": round(float(latest["close"]), 2),
        "tick_size": 0.01,
        "adv": max(1, adv),
        # Free float is not on the free TIME_SERIES tier; derive a proxy from ADV.
        "free_float": max(1, adv * 30),
        "halt_tier": 1,
        "source": "alphavantage",
    }


# --------------------------------------------------------------------------- #
# Public tools — real feed first (cached), synthetic fallback
# --------------------------------------------------------------------------- #
def get_instrument_reference(instrument: str) -> dict:
    """Reference data for ``instrument`` (drives the engine ``Instrument``).

    Real Alpha Vantage data when a key is set (cached); otherwise the curated
    fixture / deterministic synthesiser. Returns symbol, name, sector, currency,
    reference price, tick size, ADV, free float, and halt tier.
    """
    sym = instrument.upper()
    if _api_key():
        daily = _av_daily(sym)
        if daily and daily.get("series"):
            return _reference_from_series(sym, daily["series"])
    return {"symbol": sym, "source": "synthetic", **_reference(sym)}


def get_historical_window(instrument: str, start: str, end: str) -> dict:
    """Daily OHLCV bars for ``instrument`` over ``[start, end]`` (inclusive).

    Real Alpha Vantage daily bars when a key is set (cached, sliced to the window);
    otherwise a deterministic random walk. Dates are ``YYYY-MM-DD``.
    """
    sym = instrument.upper()
    if _api_key():
        daily = _av_daily(sym)
        if daily and daily.get("series"):
            start_d = _parse_date(start, date(2025, 1, 1))
            end_d = _parse_date(end, date(2025, 1, 31))
            if start_d > end_d:
                start_d, end_d = end_d, start_d
            lo, hi = start_d.isoformat(), end_d.isoformat()
            bars = [b for b in daily["series"] if lo <= b["date"] <= hi]
            if bars:
                return _window_payload(sym, lo, hi, bars, source="alphavantage")
    return _synthetic_historical(sym, start, end)


def get_liquidity_profile(instrument: str) -> dict:
    """Liquidity summary used to reason about how hard the position is to exit.

    ADV / free float come from the (real or synthetic) reference; spread and
    resting depth are derived deterministically from ADV.
    """
    sym = instrument.upper()
    ref = get_instrument_reference(sym)
    adv = ref["adv"]
    free_float = ref["free_float"]
    rng = _rng(sym, "liq")
    spread_bps = round(float(rng.uniform(2.0, 18.0)), 1)
    depth_at_touch = int(adv * float(rng.uniform(0.0008, 0.0025)))
    return {
        "symbol": sym,
        "adv": adv,
        "free_float": free_float,
        "spread_bps": spread_bps,
        "depth_at_touch": depth_at_touch,
        "turnover_ratio": round(adv / free_float, 5) if free_float else 0.0,
        "halt_tier": ref["halt_tier"],
        "source": ref.get("source", "synthetic"),
    }
