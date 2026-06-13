"""Offline tests for the gateway's curated ticker presets (live-baseline path).

The picker swaps real instrument data into one fixed configuration and sizes the
position at a fixed %ADV, so picking a liquid name keeps the exit open and a crowded
one closes it — with no per-episode tuning. These guard that wiring offline.
"""

from __future__ import annotations

import pytest
from agents.orchestrator.driver import run_baseline_simulation
from engine.presets import DEFAULT_POSITION_FRAC, PRESETS
from gateway.run_config import build_run_config


def test_symbol_preset_overrides_instrument_and_sizes_by_adv() -> None:
    cfg = build_run_config({"symbol": "AAPL", "position_size": 250_000})
    inst = cfg.instrument
    assert inst.symbol == "AAPL"
    assert inst.adv == PRESETS["AAPL"].adv
    assert inst.volatility == PRESETS["AAPL"].volatility
    # Position is a fixed %ADV, not the manual share count.
    assert cfg.position.quantity == round(DEFAULT_POSITION_FRAC * PRESETS["AAPL"].adv)
    assert cfg.position.arrival_price == PRESETS["AAPL"].reference_price


def test_no_symbol_keeps_flagship_and_manual_size() -> None:
    cfg = build_run_config({"symbol": "", "position_size": 250_000})
    assert cfg.instrument.symbol == "CVNA"
    assert cfg.position.quantity == 250_000  # manual size preserved


def test_unknown_symbol_falls_back_without_crashing() -> None:
    cfg = build_run_config({"symbol": "ZZZZ", "position_size": 300_000})
    assert cfg.instrument.symbol == "CVNA"
    assert cfg.position.quantity == 300_000


@pytest.mark.asyncio
async def test_picker_separates_liquid_from_illiquid() -> None:
    """The acceptance check, guarded: one fixed config, only the ticker changes."""
    illiquid = await run_baseline_simulation(build_run_config({"symbol": "CVNA"}))
    liquid = await run_baseline_simulation(build_run_config({"symbol": "AAPL"}))
    # CVNA: the exit closes — most of the position cannot be sold and it halts.
    assert illiquid["run_metrics"]["fill_rate"] < 0.5
    assert illiquid["run_metrics"]["halt_count"] > 0
    # AAPL: the exit stays open — it fills cleanly with no halt.
    assert liquid["run_metrics"]["fill_rate"] > 0.9
    assert liquid["run_metrics"]["halt_count"] == 0
