"""Analyst agent (Gemini via Vertex AI) — the plain-language explanation of a run.

After the simulation finishes, the analyst reads the engine's metrics and final
market state from session.state and writes a plain-language narrative of how the
exit unfolded: whether the position could actually be sold, how far the price moved
while selling, how much was left stuck, and whether a halt closed the door. The
simulation is the source of truth — the model interprets it, it does not invent the
dynamics (AGENTS.md §4). Output is written to the ``analysis`` key.

Vertex AI Search grounding (the historical-episode corpus) is wired in a later
phase; for now the analyst is grounded in the run's own metrics and replay.
"""

from __future__ import annotations

import json

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from agents.common.env import strong_model
from agents.common.state import ANALYSIS, MARKET_STATE, REPLAY_REF, RUN_METRICS, SCENARIO_CONFIG

INSTRUCTION = """\
You are the Analyst for Egress, a crisis-exit market simulator. A simulation has
just run. Using ONLY the run's metrics and final state (below), explain to an
investment professional, in plain language, how the exit unfolded.

Cover, in a few tight paragraphs:
- Whether the position could actually be sold — the fill rate and how much was left
  stuck, and what that means.
- How far and how fast the price moved while selling — implementation shortfall and
  slippage in plain terms, the drawdown, and the VWAP achieved versus the arrival
  price.
- Whether a volatility halt triggered, and if so how it shaped the outcome.
- The mechanism: why the exit closed (or did not). Tie it to the crowding mix —
  forced sellers and panic sellers overwhelming thin bargain-hunter and
  market-maker support — and to the shocks in the scenario.

Be concrete and honest. Do not invent numbers beyond those given. If the position
exited cleanly, say so. Do not recommend trades; explain what happened."""


def _context_block(ctx: ReadonlyContext) -> str:
    state = getattr(ctx, "state", {}) or {}
    scenario = state.get(SCENARIO_CONFIG) or {}
    metrics = state.get(RUN_METRICS) or {}
    market = state.get(MARKET_STATE) or {}
    instrument = scenario.get("instrument", {})
    position = scenario.get("position", {})
    mix = scenario.get("crowding_mix", {})
    return (
        "\n\n--- Run facts (the source of truth) ---\n"
        f"Instrument: {json.dumps(instrument)}\n"
        f"Position: {json.dumps(position)}\n"
        f"Crowding mix: {json.dumps(mix)}\n"
        f"Shocks: {json.dumps(scenario.get('shock_schedule', []))}\n"
        f"Final market state: {json.dumps(market)}\n"
        f"Metrics: {json.dumps(metrics)}\n"
        f"Replay file: {state.get(REPLAY_REF)}"
    )


def _instruction_provider(ctx: ReadonlyContext) -> str:
    return INSTRUCTION + _context_block(ctx)


def build_analyst() -> LlmAgent:
    """The Analyst ``LlmAgent`` (live Vertex path)."""
    return LlmAgent(
        name="Analyst",
        model=strong_model(),
        instruction=_instruction_provider,
        description="Explains, in plain language, how the simulated exit unfolded.",
        output_key=ANALYSIS,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
