"""Deterministic baseline stand-in for the Tier-A archetype fan-out.

In baseline mode the six archetype stances come from the engine's existing
``baseline_stances`` heuristic instead of Gemini, so the whole system runs with
zero LLM calls — the proof that the model is one part of the system, not the
engine (AGENTS.md §5, contract §2). This agent is a drop-in replacement for the
``ParallelAgent`` of mood-setters: it writes the same six ``*_stance`` keys, read
the same way by the engine bridge.

It derives its inputs only from observable session state (the latest
``market_state`` plus the scenario's reference price), exactly as the LLM
archetypes do — it never reaches into engine internals. Stress is taken as a
proxy of the price drawdown, since stress is not part of the public market state.
"""

from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.common.state import MARKET_STATE, SCENARIO_CONFIG, stance_key
from engine.baseline import baseline_stances
from engine.schema import INVESTOR_TYPES


def _stress_proxy(drop: float, halted: bool) -> float:
    """A bounded stress signal from observable state (drawdown + halt)."""
    stress = min(1.0, 2.5 * max(0.0, drop))
    if halted:
        stress = min(1.0, stress + 0.2)
    return stress


class BaselineStancesAgent(BaseAgent):
    """Writes the six ``*_stance`` keys from the deterministic heuristic (no LLM)."""

    def __init__(self, name: str = "BaselineStances") -> None:
        super().__init__(name=name)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        market = state.get(MARKET_STATE) or {}
        scenario = state.get(SCENARIO_CONFIG) or {}

        ref_price = scenario.get("instrument", {}).get("reference_price", 0.0) or 0.0
        last_price = market.get("last_price", ref_price) or ref_price
        tick = int(market.get("tick", 0) or 0)
        halted = bool(market.get("halted", False))

        drop = max(0.0, (ref_price - last_price) / ref_price) if ref_price else 0.0
        stress = _stress_proxy(drop, halted)

        stances = baseline_stances(drop, stress, tick)
        delta = {stance_key(t): stances[t].model_dump() for t in INVESTOR_TYPES}
        for key, value in delta.items():
            state[key] = value

        yield Event(author=self.name, actions=EventActions(state_delta=delta))
