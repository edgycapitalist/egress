"""Calibration backtest — offline tests for the generator-critic loop.

The loop runs the deterministic pipeline, so these are fast, free, and reproducible.
"""

from __future__ import annotations

import pytest
from engine.scenarios import flagship_scenario
from eval.backtest import render_report, run_calibration_backtest


@pytest.mark.asyncio
async def test_loop_converges_from_an_over_rational_crowd() -> None:
    result = await run_calibration_backtest(start="calm", max_iterations=5)
    assert result.converged
    assert result.final.report.plausible
    # It started too calm and ended plausible: the loop actually did work.
    assert result.iterations[0].report.verdict == "too_calm"
    # The cascade deepened across the loop — the calibrated crowd forces the price
    # much harder than the over-rational one it started from.
    assert (
        result.final.metrics["max_drawdown_pct"]
        > result.iterations[0].metrics["max_drawdown_pct"]
    )
    assert result.final.metrics["pct_stuck"] > result.iterations[0].metrics["pct_stuck"]


@pytest.mark.asyncio
async def test_shipped_crowd_is_already_plausible() -> None:
    result = await run_calibration_backtest(start="default", max_iterations=3)
    assert result.converged
    assert len(result.iterations) == 1  # no correction needed
    assert result.final.report.verdict == "plausible"


@pytest.mark.asyncio
async def test_backtest_is_deterministic() -> None:
    a = await run_calibration_backtest(start="calm", max_iterations=5)
    b = await run_calibration_backtest(start="calm", max_iterations=5)
    assert [it.metrics for it in a.iterations] == [it.metrics for it in b.iterations]


@pytest.mark.asyncio
async def test_render_report_reads_cleanly() -> None:
    result = await run_calibration_backtest(start="calm", max_iterations=5)
    text = render_report(result, start="calm")
    assert "calibration backtest" in text.lower()
    assert "CVNA" in text or "Carvana" in text
    assert "Converged" in text


@pytest.mark.asyncio
async def test_backtest_refuses_without_a_reference_episode() -> None:
    cfg = flagship_scenario()
    cfg = cfg.model_copy(
        update={"instrument": cfg.instrument.model_copy(update={"symbol": "ZZZZ"})}
    )
    with pytest.raises(ValueError, match="no curated episode"):
        await run_calibration_backtest(config=cfg)
