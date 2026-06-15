"""ADK bridge agents for long-term memory read/write."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from memory import (
    memory_context_for,
    write_calibration_adjustment,
    write_run_outcome,
)

from agents.common.state import (
    ANALYSIS,
    CALIBRATION_ADJUSTMENTS,
    CALIBRATION_REPORT,
    MEMORY_CONTEXT,
    RUN_METRICS,
    SCENARIO_CONFIG,
)
from agents.common.timing import after_agent, before_agent


class LoadMemoryContextAgent(BaseAgent):
    """Load comparable run history and calibration memories before analysis."""

    def __init__(self, name: str = "LoadMemoryContext") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        scenario = state.get(SCENARIO_CONFIG) or {}
        try:
            context = memory_context_for(scenario)
        except Exception as exc:
            context = {"backend": "unavailable", "error": exc.__class__.__name__}
        state[MEMORY_CONTEXT] = context
        yield Event(author=self.name, actions=EventActions(state_delta={MEMORY_CONTEXT: context}))


class PersistMemoryAgent(BaseAgent):
    """Write the completed run outcome and any critic calibration adjustment."""

    def __init__(self, name: str = "PersistMemory") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        scenario = state.get(SCENARIO_CONFIG) or {}
        metrics = state.get(RUN_METRICS) or {}
        delta: dict[str, object] = {}
        try:
            record = write_run_outcome(scenario, metrics, analysis=state.get(ANALYSIS))
            delta["memory_write"] = {"backend": "long_term", "run_id": record.run_id}
        except Exception as exc:
            delta["memory_write"] = {"backend": "unavailable", "error": exc.__class__.__name__}

        report = state.get(CALIBRATION_REPORT)
        adjustments = state.get(CALIBRATION_ADJUSTMENTS)
        if isinstance(report, dict) and isinstance(adjustments, dict):
            try:
                record = write_calibration_adjustment(scenario, report, adjustments)
                delta["calibration_memory_write"] = {"run_id": record.run_id}
            except Exception as exc:
                delta["calibration_memory_write"] = {"error": exc.__class__.__name__}
        state.update(delta)
        yield Event(author=self.name, actions=EventActions(state_delta=delta))
