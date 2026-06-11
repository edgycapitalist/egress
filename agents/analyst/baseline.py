"""Deterministic template analyst — the offline/baseline explanation.

In baseline mode the whole system runs with zero LLM calls, so the analyst, like
the archetypes, has a deterministic stand-in. It composes a plain-language summary
straight from the engine's metrics — no model, no cloud — so an end-to-end baseline
run still produces the narrative panel the product shows. The live Gemini analyst
(``agent.py``) is the product path; this is its swappable fallback.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.common.state import ANALYSIS, RUN_METRICS, SCENARIO_CONFIG


def render_summary(scenario: dict, metrics: dict) -> str:
    """Plain-language summary of a run from its metrics (deterministic)."""
    inst = scenario.get("instrument", {})
    pos = scenario.get("position", {})
    symbol = inst.get("symbol", "the instrument")
    qty = pos.get("quantity", 0)

    fill = metrics.get("fill_rate", 0.0)
    stuck = metrics.get("pct_stuck", 0.0)
    stuck_qty = metrics.get("stuck_qty", 0)
    shortfall = metrics.get("implementation_shortfall_bps", 0.0)
    slippage = metrics.get("slippage_bps", 0.0)
    drawdown = metrics.get("max_drawdown_pct", 0.0)
    vwap = metrics.get("vwap_sold")
    arrival = metrics.get("arrival_price", 0.0)
    final = metrics.get("final_price", 0.0)
    halts = metrics.get("halt_count", 0)
    tte = metrics.get("time_to_exit_ticks")

    closed = fill < 0.999
    verdict = (
        f"The exit did not fully close: only {fill:.0%} of the {qty:,}-share position "
        f"in {symbol} could be sold, leaving {stuck_qty:,} shares ({stuck:.0%}) stuck."
        if closed
        else f"The full {qty:,}-share position in {symbol} was sold ({fill:.0%} filled)."
    )
    vwap_str = f"{vwap:.2f}" if vwap is not None else "n/a"
    price_line = (
        f"Selling pushed the price from an arrival of {arrival:.2f} to {final:.2f} "
        f"(max drawdown {drawdown:.0%}); the position sold at a VWAP of {vwap_str}. "
        f"That cost {shortfall:.0f} bps of implementation shortfall and "
        f"{slippage:.0f} bps of slippage versus arrival."
    )
    halt_line = (
        f"A volatility halt triggered {halts} time(s), pausing trading and worsening "
        "the stuck position."
        if halts
        else "No volatility halt triggered."
    )
    exit_line = (
        f"Full exit took {tte} ticks." if tte is not None else "The position never fully exited."
    )
    mechanism = (
        "The cascade came from forced and panic sellers overwhelming thin "
        "bargain-hunter and market-maker support as the shocks landed: each break in "
        "the price armed the next tranche of sellers, draining the book faster than "
        "buyers replenished it."
    )
    return "  ".join([verdict, price_line, halt_line, exit_line, mechanism])


class BaselineAnalystAgent(BaseAgent):
    """Writes the ``analysis`` key from a deterministic template (no LLM)."""

    def __init__(self, name: str = "BaselineAnalyst") -> None:
        super().__init__(name=name)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        scenario = state.get(SCENARIO_CONFIG) or {}
        metrics = state.get(RUN_METRICS) or {}
        summary = render_summary(scenario, metrics)
        state[ANALYSIS] = summary
        yield Event(author=self.name, actions=EventActions(state_delta={ANALYSIS: summary}))
