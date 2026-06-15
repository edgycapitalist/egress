"""Tier-A archetype mood-setter agents (Gemini via Vertex AI).

One ``LlmAgent`` per investor type, each writing **only** its own ``*_stance`` key
via ``output_key`` so the ``ParallelAgent`` fan-out never races on shared state
(the documented ADK parallel-write pattern, contract §4). Each agent calls the
News and Market Data MCP tools, reads the current market state from session state,
and emits a stance for its whole type.

The LLM writes to a **permissive** ``StanceOut`` schema (no value bounds), and an
after-agent callback clamps it into the contract's ``Stance`` ranges before the
engine reads it. This is deliberate: Gemini occasionally emits a slightly
out-of-range number (e.g. a negative threshold), and ADK validates ``output_schema``
strictly — a strict ``Stance`` there would crash the whole run on any drift. Keeping
the model schema permissive and clamping deterministically is the robust pattern.

This is the live product path: real Gemini calls through Vertex AI. The
deterministic baseline (``baseline.py``) is the swappable offline fallback — it
fills the same six stance keys without any LLM call.
"""

from __future__ import annotations

import json

from engine.schema import INVESTOR_TYPES, InvestorType, Stance
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from pydantic import BaseModel, Field

from agents.archetypes.prompts import AGENT_NAMES, instruction_for
from agents.common.env import fast_model
from agents.common.state import (
    LATEST_NEWS,
    MARKET_STATE,
    SCENARIO_BRIEF,
    SCENARIO_CONFIG,
    SCENARIO_RAW,
    TICK_WINDOW_INDEX,
    stance_key,
)
from agents.common.timing import (
    after_agent,
    after_model,
    after_tool,
    before_agent,
    before_model,
    before_tool,
    on_model_error,
    on_tool_error,
)


class StanceOut(BaseModel):
    """Permissive output schema for the archetype LLMs (clamped to ``Stance`` after).

    Mirrors the contract ``Stance`` fields but without value bounds, so a small
    out-of-range model output never trips ADK's strict ``output_schema`` validation
    and crashes the run. The values are clamped into the contract ranges by
    :func:`_clamp_stance_callback` before the engine ever reads them.
    """

    aggressiveness: float = Field(default=0.5, description="0..1, how hard this type acts.")
    sell_threshold_pct: float = Field(
        default=0.05, description="Fractional price move that arms the action (>= 0)."
    )
    participation: float = Field(default=0.5, description="0..1, share that may act this window.")
    updated_at_tick: int = Field(default=0, description="Tick at which this stance was set.")
    rationale: str = Field(default="", description="One short sentence explaining the stance.")


def _clip01(x: object) -> float:
    try:
        return max(0.0, min(1.0, float(x)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5


def _clamp_stance_callback(investor_type: InvestorType):
    """After-agent callback: clamp the model's StanceOut into a valid Stance."""
    key = stance_key(investor_type)

    def callback(callback_context: CallbackContext):
        state = callback_context.state
        raw = state.get(key)
        if not isinstance(raw, dict):
            return None  # nothing usable; the engine bridge falls back to baseline
        try:
            threshold = max(0.0, float(raw.get("sell_threshold_pct", 0.05)))
            stance = Stance(
                aggressiveness=_clip01(raw.get("aggressiveness", 0.5)),
                sell_threshold_pct=threshold,
                participation=_clip01(raw.get("participation", 0.5)),
                updated_at_tick=int(raw.get("updated_at_tick", 0) or 0),
                rationale=str(raw.get("rationale", "")),
            )
        except (TypeError, ValueError):
            return None
        state[key] = stance.model_dump()
        return None

    return callback


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

    # The scenario author's structured read of the crisis (rationale + stress
    # events + the user's words), falling back to the raw user text. This is what
    # makes the *described* situation drive the stance, not just the ticker.
    brief = state.get(SCENARIO_BRIEF) or state.get(SCENARIO_RAW) or "(no scenario brief)"

    market = _summary(
        MARKET_STATE,
        ("tick", "last_price", "best_bid", "best_ask", "remaining_qty", "halted"),
    )
    news = _summary(LATEST_NEWS, ("overall_sentiment", "sentiment_label", "headline_count"))
    window = state.get(TICK_WINDOW_INDEX, 0)

    return (
        f"\n\n--- Live run context (window {window}) ---\n"
        f"Instrument: {instrument}\n"
        f"Scenario & stress (the situation to react to):\n{brief}\n\n"
        f"Market state: {market}\n"
        f"Latest news (session): {news}\n"
        "Set your levers to fit THIS scenario and the current tape. Call the news "
        "tools for the latest headlines. If the described crisis is severe, the news "
        "sentiment is strongly negative, or the price is already well below the "
        "reference price, your stance should reflect that — do not stay artificially calm."
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
    from mcp.market_data.tools import market_data_tools
    from mcp.news.tools import news_tools

    return LlmAgent(
        name=AGENT_NAMES[investor_type],
        model=fast_model(),
        instruction=_instruction_provider(investor_type),
        description=f"Sets the behavioural stance for {investor_type} investors.",
        tools=[*news_tools(), *market_data_tools()],
        output_schema=StanceOut,
        output_key=stance_key(investor_type),
        before_agent_callback=before_agent(AGENT_NAMES[investor_type]),
        after_agent_callback=[
            _clamp_stance_callback(investor_type),
            after_agent(AGENT_NAMES[investor_type]),
        ],
        before_model_callback=before_model,
        after_model_callback=after_model,
        on_model_error_callback=on_model_error,
        before_tool_callback=before_tool,
        after_tool_callback=after_tool,
        on_tool_error_callback=on_tool_error,
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
