"""Deterministic market-data backend for the Market Data MCP.

The tool *logic* lives here as plain functions with no MCP, ADK, or cloud
dependency, so it is trivially unit-testable offline and is shared by both the
FastMCP server (``server.py``, the deployment surface) and the in-process ADK
``FunctionTool`` wrappers (``tools.py``, the path the agents use today).

Data source: for the build we serve a small curated fixture for known instruments
(the flagship ``ACME``) plus a deterministic synthesiser for any other symbol,
seeded from the symbol so results are reproducible. Historical data is sufficient
for the build; a live, authorised market-data feed is a later upgrade and will be
declared in the submission's data-sources field (AGENTS.md §6).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import numpy as np

# Curated reference fixtures. These mirror the flagship scenario so the scenario
# author resolves ``ACME`` to exactly the instrument the engine simulates.
_FIXTURES: dict[str, dict] = {
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


def get_instrument_reference(instrument: str) -> dict:
    """Reference data for ``instrument`` (contract: drives engine ``Instrument``).

    Returns symbol, name, sector, quote currency, pre-shock reference price, tick
    size, average daily volume (shares), free float (shares), and exchange halt
    tier.
    """
    ref = _reference(instrument)
    return {"symbol": instrument.upper(), **ref}


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return default


def get_historical_window(instrument: str, start: str, end: str) -> dict:
    """Daily OHLCV bars for ``instrument`` over ``[start, end]`` (inclusive).

    A deterministic geometric random walk around the instrument's reference price.
    Used by the scenario author to sanity-check the instrument and, in a later
    phase, by the engine's statistical fitting. Dates are ``YYYY-MM-DD``.
    """
    ref = _reference(instrument)
    end_d = _parse_date(end, date(2025, 1, 31))
    start_d = _parse_date(start, end_d - timedelta(days=60))
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    n_days = (end_d - start_d).days + 1
    n_days = max(1, min(n_days, 500))  # guard absurd ranges

    rng = _rng(instrument, f"hist:{start_d}:{end_d}")
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
            {
                "date": cur.isoformat(),
                "open": prev,
                "high": hi,
                "low": lo,
                "close": price,
                "volume": vol,
            }
        )
        cur += timedelta(days=1)

    closes = [b["close"] for b in bars]
    returns = [round((closes[i] / closes[i - 1]) - 1.0, 6) for i in range(1, len(closes))]
    return {
        "symbol": instrument.upper(),
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "bars": bars,
        "returns": returns,
        "realized_vol_daily": round(float(np.std(returns)) if returns else 0.0, 6),
    }


def get_liquidity_profile(instrument: str) -> dict:
    """Liquidity summary used to reason about how hard the position is to exit.

    Average daily volume, typical touch spread (bps), resting depth at the touch,
    free float, and the position's days-to-trade at a 10% participation rate. All
    derived deterministically from the instrument's reference data.
    """
    ref = _reference(instrument)
    rng = _rng(instrument, "liq")
    spread_bps = round(float(rng.uniform(2.0, 18.0)), 1)
    depth_at_touch = int(ref["adv"] * float(rng.uniform(0.0008, 0.0025)))
    return {
        "symbol": instrument.upper(),
        "adv": ref["adv"],
        "free_float": ref["free_float"],
        "spread_bps": spread_bps,
        "depth_at_touch": depth_at_touch,
        "turnover_ratio": round(ref["adv"] / ref["free_float"], 5),
        "halt_tier": ref["halt_tier"],
    }
