"""Deterministic baseline critic — the offline/test calibration stand-in.

Like the analyst and the archetypes, the critic has a zero-LLM stand-in so the whole
pipeline (and ``make eval``) runs offline. It resolves the run's reference episode,
runs the deterministic comparison, and writes both contract keys it owns:
``calibration_report`` (the verdict) and ``calibration_adjustments`` (the per-type
nudges the archetypes read on a re-run). The live Gemini judge (``agent.py``) is the
product path; this is its swappable fallback and produces identical numbers.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.common.state import (
    CALIBRATION_ADJUSTMENTS,
    CALIBRATION_REPORT,
    RUN_METRICS,
    SCENARIO_CONFIG,
)
from agents.common.timing import after_agent, before_agent
from agents.critic.compare import render_verdict
from agents.critic.core import report_for_run


class BaselineCriticAgent(BaseAgent):
    """Writes ``calibration_report`` + ``calibration_adjustments`` deterministically (no LLM)."""

    def __init__(self, name: str = "BaselineCritic") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        scenario = state.get(SCENARIO_CONFIG) or {}
        metrics = state.get(RUN_METRICS) or {}

        report = report_for_run(scenario, metrics)
        report.narrative = render_verdict(report)

        delta = {
            CALIBRATION_REPORT: report.model_dump(),
            CALIBRATION_ADJUSTMENTS: report.adjustments.model_dump(),
        }
        for key, value in delta.items():
            state[key] = value
        yield Event(author=self.name, actions=EventActions(state_delta=delta))
