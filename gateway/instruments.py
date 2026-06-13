"""Resolve an instrument's reference data for a run — real feed first, then fallback.

This is where real market data actually enters the simulation. For a **live** run we
ask the Market Data MCP for the name's real Alpha Vantage reference (price, ADV, free
float) and its recent realized volatility, plus the real date window the numbers cover.
When no key is set, a call is rate-limited, or the symbol is one of the curated demo
names, we fall back to the committed preset (deterministic, offline). An unknown symbol
with no real feed falls back to the MCP's synthetic reference.

Resolution order for a non-empty symbol:

1. **Alpha Vantage** (live runs only, when the MCP returns a real ``alphavantage``
   reference) — the real current data drives the engine.
2. **Curated preset** — the offline/deterministic reference for the demo tickers.
3. **Synthetic** — the MCP's seeded fallback, so a typed ticker still runs offline.

``build_run_config`` and ``/api/instrument`` both go through here, so the run and the
sourced-inputs panel always agree.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from engine.presets import get_preset

# Floor so a (possibly tiny or zero) realized vol never violates the engine's vol > 0.
_MIN_VOL = 0.005
_DEFAULT_VOL = 0.03
_WINDOW_DAYS = 120


def _mcp_reference(symbol: str) -> dict[str, Any]:
    """Ask the Market Data MCP for a reference (real Alpha Vantage if available)."""
    from mcp.market_data.data import get_historical_window, get_instrument_reference

    ref = get_instrument_reference(symbol)
    end = _dt.date.today()
    start = end - _dt.timedelta(days=_WINDOW_DAYS)
    hist = get_historical_window(symbol, start.isoformat(), end.isoformat())
    vol = hist.get("realized_vol_daily") or 0.0
    source = ref.get("source", "synthetic")
    window = (
        {"start": hist.get("start"), "end": hist.get("end")}
        if source == "alphavantage"
        else None
    )
    return {
        "symbol": ref["symbol"],
        "name": ref.get("name"),
        "reference_price": float(ref["reference_price"]),
        "adv": int(ref["adv"]),
        "free_float": int(ref["free_float"]),
        "volatility": max(_MIN_VOL, float(vol)) if vol else _DEFAULT_VOL,
        "source": source,
        "window": window,
        "bars": len(hist.get("bars", [])),
    }


def _synthetic_reference(symbol: str) -> dict[str, Any]:
    """A seeded synthetic reference that never touches the live feed.

    Used as the fallback for a non-live query of an unknown symbol, so a cached/
    offline lookup can never make a real Alpha Vantage call.
    """
    from mcp.market_data.data import _reference, _synthetic_historical

    ref = _reference(symbol)
    end = _dt.date.today()
    start = end - _dt.timedelta(days=_WINDOW_DAYS)
    hist = _synthetic_historical(symbol, start.isoformat(), end.isoformat())
    vol = hist.get("realized_vol_daily") or 0.0
    return {
        "symbol": symbol.strip().upper(),
        "name": ref.get("name"),
        "reference_price": float(ref["reference_price"]),
        "adv": int(ref["adv"]),
        "free_float": int(ref["free_float"]),
        "volatility": max(_MIN_VOL, float(vol)) if vol else _DEFAULT_VOL,
        "source": "synthetic",
        "window": None,
        "bars": len(hist.get("bars", [])),
    }


def _preset_reference(symbol: str) -> dict[str, Any] | None:
    preset = get_preset(symbol)
    if preset is None:
        return None
    return {
        "symbol": preset.symbol,
        "name": preset.name,
        "reference_price": preset.reference_price,
        "adv": preset.adv,
        "free_float": preset.free_float,
        "volatility": preset.volatility,
        "source": "curated",
        "window": None,
        "bars": 0,
    }


def resolve_instrument(symbol: str | None, *, live: bool) -> dict[str, Any] | None:
    """Reference data for ``symbol``; ``None`` only for an empty symbol.

    ``live`` enables the real Alpha Vantage path; offline/baseline runs pass
    ``live=False`` so they stay deterministic on the curated/synthetic fallback.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None

    if live:
        real = _mcp_reference(sym)
        if real["source"] == "alphavantage":
            return real

    preset = _preset_reference(sym)
    if preset is not None:
        return preset

    if live:
        # A typed, unknown symbol on a live run still gets to run on synthetic data.
        return _mcp_reference(sym)

    # Unknown symbol on an offline run: let the caller keep the flagship default.
    return None
