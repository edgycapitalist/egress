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
from agents.common.timing import after_agent, before_agent


def _pct(value: float | None, dp: int = 0) -> str:
    return "n/a" if value is None else f"{value:.{dp}%}"


def _bps(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} bps"


def _band(ensemble: dict, key: str) -> dict:
    return dict((ensemble.get("bands") or {}).get(key) or {})


def _case_metrics(ensemble: dict, case: str) -> dict:
    for summary in ensemble.get("cases") or []:
        if summary.get("case") == case:
            return dict(summary.get("metrics") or {})
    return {}


def render_ensemble_summary(scenario: dict, ensemble: dict) -> str:
    """Plain-language summary of an ensemble from deterministic result bands."""
    inst = scenario.get("instrument", {})
    pos = scenario.get("position", {})
    symbol = inst.get("symbol", "the instrument")
    qty = pos.get("quantity", 0)
    evidence = ensemble.get("evidence_summary") or scenario.get("evidence_summary") or {}
    evidence_summary = str(evidence.get("summary") or "").strip()

    fill = _band(ensemble, "fill_rate")
    stuck = _band(ensemble, "pct_stuck")
    slippage = _band(ensemble, "slippage_bps")
    drawdown = _band(ensemble, "max_drawdown_pct")
    halts = _band(ensemble, "halt_probability")
    base = _case_metrics(ensemble, "base")
    low = _case_metrics(ensemble, "low")
    high = _case_metrics(ensemble, "high")

    verdict = (
        f"Under this scenario, the low/base/high peer-crowding ensemble sold "
        f"{_pct(fill.get('low'))} to {_pct(fill.get('high'))} of the {qty:,}-share "
        f"{symbol} position. The stuck range was {_pct(stuck.get('low'))} to "
        f"{_pct(stuck.get('high'))}, so this is an assumption-based stress range, "
        "not a single-point forecast."
    )
    cost = (
        f"Across the ensemble, slippage ranged from {_bps(slippage.get('low'))} to "
        f"{_bps(slippage.get('high'))}, with worst price drawdown from "
        f"{_pct(drawdown.get('low'))} to {_pct(drawdown.get('high'))}. "
        f"The halt probability across deterministic seeds was {_pct(halts.get('median'))}."
    )

    low_stuck = float(low.get("pct_stuck") or 0.0)
    high_stuck = float(high.get("pct_stuck") or 0.0)
    driver = (
        "The main assumption moving the result is peer overlap and shared triggers: "
        "the high-crowding case leaves materially more stock stuck than the low-crowding case."
        if high_stuck - low_stuck >= 0.05
        else (
            "The range is less sensitive to peer overlap here; exit speed, shock severity, "
            "and available buyer depth carry more of the outcome."
        )
    )

    impact = dict(base.get("impact_attribution") or {})
    counterfactual = dict(base.get("counterfactual_attribution") or {})
    if counterfactual:
        attribution = (
            "The representative base path includes paired counterfactual impact estimates: "
            f"exogenous shocks {_bps(counterfactual.get('exogenous_shock_bps'))}, "
            f"peer cascade {_bps(counterfactual.get('peer_cascade_bps'))}, own exit "
            f"{_bps(counterfactual.get('own_exit_bps'))}, and residual market behaviour "
            f"{_bps(counterfactual.get('residual_market_behavior_bps'))}. These are "
            "approximate deltas, not exact causal proof."
        )
    elif impact:
        attribution = (
            "The representative base path reports heuristic impact estimates for "
            "scheduled crisis shocks "
            f"({_bps(impact.get('exogenous_shock_bps'))}), trading impact "
            f"({_bps(impact.get('endogenous_trading_bps'))}), and liquidity withdrawal "
            f"({_bps(impact.get('liquidity_withdrawal_bps'))})."
        )
    else:
        attribution = (
            "The representative path should be read as one replay inside the "
            "ensemble range."
        )

    source = (
        f"Assumption evidence: {evidence_summary}"
        if evidence_summary
        else "Assumption evidence is labelled in the evidence panel."
    )
    language = (
        "Read this as an institutional stress result: under the stated assumptions, the exit "
        "gets harder as shared holders sell together, but it is not an absolute prediction."
    )
    return "  ".join([verdict, cost, driver, attribution, source, language])


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
    impact = dict(metrics.get("impact_attribution") or {})
    counterfactual = dict(metrics.get("counterfactual_attribution") or {})

    closed = fill < 0.999
    verdict = (
        f"Under this scenario, the exit did not fully close: only {fill:.0%} of the "
        f"{qty:,}-share position in {symbol} could be sold, leaving {stuck_qty:,} "
        f"shares ({stuck:.0%}) stuck."
        if closed
        else (
            f"Under this scenario, the full {qty:,}-share position in {symbol} was "
            f"sold ({fill:.0%} filled)."
        )
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
    if counterfactual:
        impact_line = (
            "Paired counterfactual impact estimates put the final-price decline at "
            f"{_bps(counterfactual.get('full_run_bps'))}, with approximate deltas of "
            f"{_bps(counterfactual.get('exogenous_shock_bps'))} from scheduled shocks, "
            f"{_bps(counterfactual.get('peer_cascade_bps'))} from peer cascade, "
            f"{_bps(counterfactual.get('own_exit_bps'))} from the exit order itself, "
            f"and {_bps(counterfactual.get('residual_market_behavior_bps'))} residual market "
            "behaviour. Treat those as estimates, not exact causes."
        )
    elif impact:
        impact_line = (
            "The run also reports heuristic impact estimates: scheduled shocks "
            f"{_bps(impact.get('exogenous_shock_bps'))}, endogenous trading "
            f"{_bps(impact.get('endogenous_trading_bps'))}, and liquidity withdrawal "
            f"{_bps(impact.get('liquidity_withdrawal_bps'))}."
        )
    else:
        impact_line = ""
    mechanism = (
        "The simulated mechanism is consistent with forced and panic sellers overwhelming thin "
        "bargain-hunter and market-maker support as the shocks landed: each break in "
        "the price armed the next tranche of sellers, draining the book faster than "
        "buyers replenished it."
    )
    return "  ".join(
        part for part in [verdict, price_line, halt_line, exit_line, impact_line, mechanism] if part
    )


class BaselineAnalystAgent(BaseAgent):
    """Writes the ``analysis`` key from a deterministic template (no LLM)."""

    def __init__(self, name: str = "BaselineAnalyst") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        scenario = state.get(SCENARIO_CONFIG) or {}
        metrics = state.get(RUN_METRICS) or {}
        summary = render_summary(scenario, metrics)
        state[ANALYSIS] = summary
        yield Event(author=self.name, actions=EventActions(state_delta={ANALYSIS: summary}))
