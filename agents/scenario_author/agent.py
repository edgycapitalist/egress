"""Scenario Author agent (Gemini via Vertex AI) — plain language to ``RunConfig``.

A coordinator ``LlmAgent`` that turns the user's plain-language position and stress
event into a structured :class:`ScenarioDraft`, grounding the instrument on the
Market Data MCP. An ``after_agent_callback`` then deterministically assembles and
**validates** a full ``RunConfig`` and writes it to ``scenario_config`` before any
run starts (contract §1, §4). The model chooses *what* to simulate; the callback
guarantees the result is schema-valid and that ADV / free float / halt tier come
from the data source, not the model's imagination.
"""

from __future__ import annotations

import uuid

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from pydantic import ValidationError

from agents.common.env import seed
from agents.common.state import INSTRUMENT_REFERENCE, SCENARIO_CONFIG
from agents.scenario_author.validation import ScenarioDraft, build_run_config

# The LLM writes its draft here; the callback finalises it into SCENARIO_CONFIG.
SCENARIO_DRAFT = "scenario_draft"
SCENARIO_ERROR = "scenario_error"

INSTRUCTION = """\
You are the Scenario Author for Egress, a crisis-exit market simulator. The user
describes, in plain language, a position they hold and a stress event they fear.
Turn it into a structured scenario for the simulation engine.

Steps:
1. Identify the instrument (ticker) the user means. Call get_instrument_reference to
   confirm it resolves and to see its reference price, average daily volume, and
   halt tier. You may call get_liquidity_profile and get_historical_window to judge
   how hard the position is to exit.
2. Decide the position size (shares to sell), the exit speed, and the crowding mix —
   the fractions of the trading crowd that are forced sellers, panic sellers, trend
   followers, bargain hunters, market makers, and long-term holders. A crowded,
   fragile trade has heavy forced/panic/trend weight and thin market-maker support.
   The fractions are normalised for you, so approximate weights are fine.
3. Translate the stress event into a shock schedule: news shocks (a downgrade, a
   scare) and price shocks (a gap down) at specific ticks within the horizon, each
   with a severity in 0..1.

Output a ScenarioDraft. Choose values that realistically express what the user
described; do not echo placeholders. Keep ticks within max_ticks."""


def _finalize_scenario(seed_value: int):
    """Build the ``after_agent_callback`` that assembles + validates the RunConfig."""

    def callback(callback_context: CallbackContext):
        state = callback_context.state
        draft = state.get(SCENARIO_DRAFT)
        if draft is None:
            state[SCENARIO_ERROR] = "scenario author produced no draft"
            return None
        # A run_id may be pre-assigned by the gateway/orchestrator; otherwise mint one.
        run_id = state.get("run_id") or f"run-{uuid.uuid4().hex[:12]}"
        try:
            config, reference = build_run_config(
                draft, run_id=run_id, seed=seed_value, baseline_mode=False
            )
        except ValidationError as exc:
            state[SCENARIO_ERROR] = f"invalid scenario: {exc.errors()}"
            return None
        state[SCENARIO_CONFIG] = config.model_dump()
        state[INSTRUMENT_REFERENCE] = reference
        return None

    return callback


def build_scenario_author(*, seed_value: int | None = None) -> LlmAgent:
    """The Scenario Author ``LlmAgent`` (live Vertex path)."""
    from mcp.market_data.tools import MARKET_DATA_TOOLS

    return LlmAgent(
        name="ScenarioAuthor",
        model="gemini-2.5-flash",
        instruction=INSTRUCTION,
        description="Parses the user's plain-language scenario into a validated RunConfig.",
        tools=[*MARKET_DATA_TOOLS],
        output_schema=ScenarioDraft,
        output_key=SCENARIO_DRAFT,
        after_agent_callback=_finalize_scenario(seed_value if seed_value is not None else seed()),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
