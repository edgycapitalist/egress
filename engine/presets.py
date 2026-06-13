"""Curated instrument presets — real reference data for a few named episodes.

Representative public values (price, ADV, free float, daily realized volatility) for
each name over its episode window — the same honest offline-reference approach as
``agents/critic/episodes/``. They let one fixed engine configuration run against
genuinely different real liquidity, so the liquid-vs-illiquid difference shows up with
no per-episode tuning, no API key, and no live model.

Living in the engine keeps them dependency-light and available wherever the engine is
(the gateway container included). The discrimination harness and the gateway's ticker
picker both source the numbers from here, so there is one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass

# A comparison position sized as this fraction of a name's ADV, so the exit is
# comparable in liquidity terms across names rather than a fixed share count (which
# would be trivially small for a mega-cap and huge for a thin name).
DEFAULT_POSITION_FRAC = 0.20


@dataclass(frozen=True)
class InstrumentPreset:
    symbol: str
    name: str  # short human label for the UI
    group: str  # "illiquid" | "liquid"
    reference_price: float
    adv: int  # average daily volume, shares
    free_float: int
    volatility: float  # real daily realized volatility over the episode window


#: Curated presets, in display order: two crowded names whose exit closed, two deep
#: names that fell sharply but stayed tradeable.
PRESETS: dict[str, InstrumentPreset] = {
    "CVNA": InstrumentPreset(
        "CVNA", "Carvana — late-2022", "illiquid", 15.00, 12_000_000, 90_000_000, 0.090
    ),
    "SIVB": InstrumentPreset(
        "SIVB", "SVB Financial — Mar-2023", "illiquid", 267.00, 1_300_000, 59_000_000, 0.100
    ),
    "AAPL": InstrumentPreset(
        "AAPL", "Apple — bad-earnings day", "liquid", 180.00, 55_000_000, 15_300_000_000, 0.018
    ),
    "SPY": InstrumentPreset(
        "SPY", "S&P 500 ETF — drawdown", "liquid", 450.00, 75_000_000, 900_000_000, 0.010
    ),
}


def get_preset(symbol: str | None) -> InstrumentPreset | None:
    """Return the curated preset for ``symbol`` (case-insensitive), or ``None``."""
    if not symbol:
        return None
    return PRESETS.get(symbol.strip().upper())
