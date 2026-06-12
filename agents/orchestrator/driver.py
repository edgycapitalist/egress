"""Run driver — executes the orchestrator with the ADK ``Runner`` and sessions.

This is the entry point both the gateway and the tests call. It builds the
orchestrator for the requested mode, seeds the ADK session state, runs the
``SequentialAgent`` through an ``InMemoryRunner``, and returns the contract outputs
(metrics, analysis, replay reference, final market state) read back from
``session.state``.

* ``run_baseline_simulation(config)`` — deterministic, no LLM, no cloud. Used by the
  offline test suite and for cost-free development. Defaults to the flagship scenario.
* ``run_live_simulation(scenario_raw)`` — the product path: real Gemini calls through
  Vertex AI. Requires valid ADC + project (validated up front).

The driver guarantees the per-run engine handle is closed even if the run errors.
"""

from __future__ import annotations

from typing import Any

from engine.schema import RunConfig
from google.adk.runners import InMemoryRunner
from google.genai import types

from agents.common.env import assert_vertex_config, seed
from agents.common.state import (
    ANALYSIS,
    CALIBRATION_ADJUSTMENTS,
    CALIBRATION_REPORT,
    MARKET_STATE,
    REPLAY_REF,
    RUN_METRICS,
    SCENARIO_CONFIG,
    SCENARIO_RAW,
)
from agents.orchestrator.agent import build_orchestrator
from agents.orchestrator.engine_bridge import close_handle

APP_NAME = "egress"


def _collect(state: dict) -> dict[str, Any]:
    return {
        "run_id": (state.get(SCENARIO_CONFIG) or {}).get("run_id"),
        "scenario_config": state.get(SCENARIO_CONFIG),
        "market_state": state.get(MARKET_STATE),
        "run_metrics": state.get(RUN_METRICS),
        "analysis": state.get(ANALYSIS),
        "calibration_report": state.get(CALIBRATION_REPORT),
        "replay_ref": state.get(REPLAY_REF),
        "error": state.get("engine_error") or state.get("scenario_error"),
    }


async def _execute(orchestrator, initial_state: dict, message: str) -> dict[str, Any]:
    runner = InMemoryRunner(agent=orchestrator, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id="local", state=initial_state
    )
    run_id_for_cleanup = (initial_state.get(SCENARIO_CONFIG) or {}).get("run_id")
    try:
        async for _event in runner.run_async(
            user_id="local",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            pass
        final = await runner.session_service.get_session(
            app_name=APP_NAME, user_id="local", session_id=session.id
        )
        result = _collect(final.state)
        run_id_for_cleanup = result.get("run_id") or run_id_for_cleanup
        return result
    finally:
        if run_id_for_cleanup:
            close_handle(run_id_for_cleanup)


async def run_baseline_simulation(
    config: RunConfig | None = None,
    *,
    with_critic: bool = False,
    adjustments: dict | None = None,
) -> dict[str, Any]:
    """Run the full pipeline deterministically (no LLM). Defaults to the flagship.

    ``with_critic`` appends the calibration critic; ``adjustments`` seeds the
    ``calibration_adjustments`` the archetypes read at run start, which is how the
    backtest's generator-critic loop re-runs a crowd it has nudged.
    """
    if config is None:
        from engine.scenarios import flagship_scenario

        config = flagship_scenario(seed=seed())
    config = config.model_copy(update={"baseline_mode": True})

    orchestrator = build_orchestrator(baseline=True, with_critic=with_critic)
    initial_state: dict[str, Any] = {SCENARIO_CONFIG: config.model_dump()}
    if adjustments:
        initial_state[CALIBRATION_ADJUSTMENTS] = adjustments
    return await _execute(orchestrator, initial_state, message="Run the baseline simulation.")


async def run_live_simulation(scenario_raw: str, *, with_critic: bool = False) -> dict[str, Any]:
    """Run the product pipeline with real Gemini calls through Vertex AI.

    Validates the Vertex configuration first so the failure is a clear auth error,
    not a deep SDK stack trace. Build/test offline with ``run_baseline_simulation``.
    ``with_critic`` appends the live Gemini calibration judge.
    """
    assert_vertex_config()
    orchestrator = build_orchestrator(baseline=False, with_critic=with_critic)
    initial_state = {SCENARIO_RAW: scenario_raw}
    return await _execute(orchestrator, initial_state, message=scenario_raw)
