"""Tier-A archetype mood-setter agents (Gemini via Vertex AI).

One ``LlmAgent`` per investor type, each writing **only** its own ``*_stance`` key
via ``output_key`` so the ``ParallelAgent`` fan-out never races on shared state
(the documented ADK parallel-write pattern, contract §4). Each agent calls the
News and Market Data MCP tools, reads the current market state from session state,
and emits a validated ``Stance`` (``output_schema=Stance``) for its whole type.

This is the live product path: real Gemini calls through Vertex AI. The
deterministic baseline (``baseline.py``) is the swappable offline fallback — it
fills the same six stance keys without any LLM call.
"""

from __future__ import annotations

import json

from engine.schema import INVESTOR_TYPES, InvestorType, Stance
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.agents.readonly_context import ReadonlyContext

from agents.archetypes.prompts import AGENT_NAMES, instruction_for
from agents.common.env import fast_model
from agents.common.state import (
    LATEST_NEWS,
    MARKET_STATE,
    SCENARIO_CONFIG,
    TICK_WINDOW_INDEX,
    stance_key,
)


def _context_block(ctx: ReadonlyContext) -> str:
    """Render the live run context the archetype reasons over, from session state."""
    state = getattr(ctx, "state", {}) or {}

    def _summary(key: str, fields: tuple[str, ...]) -> str:
        val = state.get(key)
        if not isinstance(val, dict):
            return "(not yet available)"
        return ", ".join(f"{f}={val.get(f)}" for f in fields if f in val) or json.dumps(val)

    scenario = state.get(SCENARIO_CONFIG)
    instrument = "the instrument"
    if isinstance(scenario, dict):
        instrument = scenario.get("instrument", {}).get("symbol", instrument)

    market = _summary(
        MARKET_STATE,
        ("tick", "last_price", "best_bid", "best_ask", "remaining_qty", "halted"),
    )
    news = _summary(LATEST_NEWS, ("overall_sentiment", "sentiment_label", "headline_count"))
    window = state.get(TICK_WINDOW_INDEX, 0)

    return (
        f"\n\n--- Live run context (window {window}) ---\n"
        f"Instrument: {instrument}\n"
        f"Market state: {market}\n"
        f"Latest news (session): {news}\n"
        "If market state shows the price already well below the reference price, or "
        "the news sentiment is strongly negative, your stance should reflect that."
    )


def _instruction_provider(investor_type: InvestorType):
    base = instruction_for(investor_type)

    def provider(ctx: ReadonlyContext) -> str:
        return base + _context_block(ctx)

    return provider


def build_archetype_agent(investor_type: InvestorType) -> LlmAgent:
    """One archetype mood-setter ``LlmAgent`` for ``investor_type``."""
    # Imported lazily so the offline test suite can import this module without the
    # MCP tool wrappers pulling in anything heavy at module load.
    from mcp.market_data.tools import MARKET_DATA_TOOLS
    from mcp.news.tools import NEWS_TOOLS

    return LlmAgent(
        name=AGENT_NAMES[investor_type],
        model=fast_model(),
        instruction=_instruction_provider(investor_type),
        description=f"Sets the behavioural stance for {investor_type} investors.",
        tools=[*NEWS_TOOLS, *MARKET_DATA_TOOLS],
        output_schema=Stance,
        output_key=stance_key(investor_type),
        # Each mood-setter is a leaf; it must not hand control to a peer.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def build_archetype_agents() -> list[LlmAgent]:
    """The six archetype agents, in canonical investor-type order."""
    return [build_archetype_agent(t) for t in INVESTOR_TYPES]


def build_archetypes_parallel() -> ParallelAgent:
    """The Tier-A fan-out: six mood-setters run concurrently, distinct output keys."""
    return ParallelAgent(name="Archetypes", sub_agents=build_archetype_agents())
