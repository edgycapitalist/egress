"""End-to-end baseline orchestration through the real ADK Runner — no LLM, no cloud.

This is the proof of the firewall and of the deterministic baseline: the full
lifecycle (setup → simulate loop → finalize → analyst) runs with the archetype and
analyst LLMs swapped for their deterministic stand-ins, producing a cascade, valid
metrics, a replay file, and a narrative — all offline.
"""

from pathlib import Path

import pytest
from agents.analyst.baseline import render_summary
from agents.common.state import MARKET_STATE
from agents.orchestrator.driver import run_baseline_simulation
from agents.orchestrator.engine_bridge import _coerce_stances
from engine.scenarios import flagship_scenario
from engine.schema import INVESTOR_TYPES, Stance


@pytest.mark.asyncio
async def test_baseline_pipeline_runs_end_to_end() -> None:
    res = await run_baseline_simulation()
    assert res["error"] is None
    assert res["run_id"] == "flagship-42"

    m = res["run_metrics"]
    assert m is not None
    # A genuine cascade: price fell hard and the exit did not fully clear.
    assert m["final_price"] < 90.0
    assert m["max_drawdown_pct"] > 0.1
    assert 0.0 < m["fill_rate"] < 1.0
    assert m["filled_qty"] + m["stuck_qty"] == flagship_scenario().position.quantity

    # Contract outputs are all present.
    assert res["market_state"] is not None
    assert res["analysis"] and "ACME" in res["analysis"]
    assert Path(res["replay_ref"]).exists()


@pytest.mark.asyncio
async def test_baseline_pipeline_is_deterministic() -> None:
    a = await run_baseline_simulation()
    b = await run_baseline_simulation()
    assert a["run_metrics"] == b["run_metrics"]


@pytest.mark.asyncio
async def test_loop_terminates_before_the_window_cap() -> None:
    res = await run_baseline_simulation()
    # Stall/exit/halt logic ends the run well before max_ticks (300).
    assert res["run_metrics"]["ticks_run"] < 300
    assert res["market_state"]["tick"] == res["run_metrics"]["ticks_run"]


def test_coerce_stances_falls_back_on_bad_input() -> None:
    config = flagship_scenario()
    state = {
        MARKET_STATE: {"last_price": 80.0, "tick": 20, "halted": False},
        # one valid stance, one malformed, the rest missing
        "forced_seller_stance": Stance(
            aggressiveness=0.9, sell_threshold_pct=0.03, participation=0.8
        ).model_dump(),
        "panic_seller_stance": {"aggressiveness": "not-a-number"},
    }
    stances = _coerce_stances(state, config)
    assert set(stances) == set(INVESTOR_TYPES)
    assert all(isinstance(s, Stance) for s in stances.values())
    # The valid one is preserved; the malformed/missing ones fell back deterministically.
    assert stances["forced_seller"].aggressiveness == 0.9


def test_render_summary_reads_metrics() -> None:
    scenario = flagship_scenario().model_dump()
    metrics = {
        "fill_rate": 0.3,
        "pct_stuck": 0.7,
        "stuck_qty": 175_000,
        "implementation_shortfall_bps": 400,
        "slippage_bps": 400,
        "max_drawdown_pct": 0.5,
        "vwap_sold": 96.0,
        "arrival_price": 100.0,
        "final_price": 50.0,
        "halt_count": 3,
        "time_to_exit_ticks": None,
    }
    text = render_summary(scenario, metrics)
    assert "ACME" in text and "30%" in text and "halt" in text.lower()
