"""Offline tests for the gateway's instrument resolution on the live-baseline path.

A typed ticker swaps its real instrument data (price, ADV, free float, volatility) into
one fixed configuration; the position is the user's own free, editable share count (not
auto-sized to ADV). With a material position and a moderate crisis, a deep name keeps the
exit open and a crowded one closes it — no per-episode tuning. These guard that wiring
offline.
"""

from __future__ import annotations

import pytest
from agents.orchestrator.driver import run_baseline_simulation
from engine.presets import DEFAULT_POSITION_FRAC, PRESETS
from gateway.run_config import build_run_config


def test_symbol_overrides_instrument_keeps_manual_size() -> None:
    cfg = build_run_config({"symbol": "AAPL", "position_size": 250_000})
    inst = cfg.instrument
    assert inst.symbol == "AAPL"
    assert inst.adv == PRESETS["AAPL"].adv
    assert inst.volatility == PRESETS["AAPL"].volatility
    # Position is the user's own free, editable share count — not auto-sized to %ADV.
    assert cfg.position.quantity == 250_000
    assert cfg.position.arrival_price == PRESETS["AAPL"].reference_price


def test_no_symbol_keeps_flagship_and_manual_size() -> None:
    cfg = build_run_config({"symbol": "", "position_size": 250_000})
    assert cfg.instrument.symbol == "CVNA"
    assert cfg.position.quantity == 250_000  # manual size preserved


def test_unknown_symbol_falls_back_without_crashing() -> None:
    cfg = build_run_config({"symbol": "ZZZZ", "position_size": 300_000})
    assert cfg.instrument.symbol == "CVNA"
    assert cfg.position.quantity == 300_000


def test_crisis_intensity_param_is_threaded() -> None:
    cfg = build_run_config({"symbol": "AAPL"}, crisis_intensity=1.3)
    assert cfg.crisis_intensity == 1.3
    # Omitted -> the engine's neutral default.
    assert build_run_config({"symbol": "AAPL"}).crisis_intensity == 1.0


@pytest.mark.asyncio
async def test_picker_separates_liquid_from_illiquid() -> None:
    """One fixed config, a material 20%-ADV block and a moderate crisis on each name."""

    def cfg(sym: str):
        qty = round(DEFAULT_POSITION_FRAC * PRESETS[sym].adv)
        return build_run_config({"symbol": sym, "position_size": qty}, crisis_intensity=0.4)

    illiquid = await run_baseline_simulation(cfg("CVNA"))
    liquid = await run_baseline_simulation(cfg("AAPL"))
    # CVNA: the exit closes — most of the position cannot be sold and it halts.
    assert illiquid["run_metrics"]["fill_rate"] < 0.5
    assert illiquid["run_metrics"]["halt_count"] > 0
    # AAPL: the exit stays open — it fills cleanly with no halt.
    assert liquid["run_metrics"]["fill_rate"] > 0.9
    assert liquid["run_metrics"]["halt_count"] == 0
