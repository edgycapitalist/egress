"""Curated instrument presets loaded from the Phase 6 eval episode corpus.

Representative public values (price, ADV, free float, daily realized volatility) for
each name over its episode window live in ``eval/episodes/*.json``. Keeping the
fixtures there lets the validation harness, the gateway ticker picker, and the
deterministic cached path share one source of truth.

This module stays dependency-light and imports only the standard library so the engine
remains free of ADK, Gemini, and cloud packages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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


def _episode_dirs() -> tuple[Path, ...]:
    """Candidate corpus locations for source-tree and container layouts."""
    repo_root = Path(__file__).resolve().parents[1]
    cwd = Path.cwd()
    return (
        repo_root / "eval" / "episodes",
        cwd / "eval" / "episodes",
    )


def _load_presets() -> dict[str, InstrumentPreset]:
    for directory in _episode_dirs():
        if not directory.exists():
            continue
        presets: dict[str, InstrumentPreset] = {}
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            instrument = data["instrument"]
            symbol = str(data["symbol"]).upper()
            # The first record for a symbol wins, keeping display order stable if a
            # future corpus adds more than one episode for the same ticker.
            presets.setdefault(
                symbol,
                InstrumentPreset(
                    symbol=symbol,
                    name=str(data["title"]),
                    group=str(data["group"]),
                    reference_price=float(instrument["reference_price"]),
                    adv=int(instrument["adv"]),
                    free_float=int(instrument["free_float"]),
                    volatility=float(instrument["volatility"]),
                ),
            )
        if presets:
            return presets
    return {}


#: Curated presets, in corpus display order.
PRESETS: dict[str, InstrumentPreset] = _load_presets()


def get_preset(symbol: str | None) -> InstrumentPreset | None:
    """Return the curated preset for ``symbol`` (case-insensitive), or ``None``."""
    if not symbol:
        return None
    return PRESETS.get(symbol.strip().upper())
