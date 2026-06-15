"""Run driver — executes the orchestrator with the ADK ``Runner`` and sessions.

This is the entry point both the gateway and the tests call. It builds the
orchestrator for the requested mode, seeds the ADK session state, runs the
``SequentialAgent`` through an ``InMemoryRunner``, and returns the contract outputs
(metrics, analysis, replay reference, final market state) read back from
``session.state``.

* ``run_baseline_simulation(config)`` — deterministic, no LLM, no cloud. Used by the
  offline test suite and for cost-free development. Defaults to the flagship scenario.
* ``run_baseline_ensemble(config)`` — deterministic low/base/high peer-crowding
  ensemble with a representative replay.
* ``run_fast_live_ensemble(scenario_raw, fallback_config=...)`` — Gemini scenario
  assumptions once, then deterministic low/base/high ensemble.
* ``run_detailed_live_ensemble(scenario_raw, fallback_config=...)`` — the gateway's
  detailed Gemini path, still returning the deterministic ensemble as authoritative.
* ``run_live_simulation(scenario_raw)`` — legacy/CLI full ADK single-run path with
  Gemini archetypes. Requires valid ADC + project (validated up front).

The driver guarantees the per-run engine handle is closed even if the run errors.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from engine.ensemble import run_ensemble
from engine.schema import EvidenceSummary, RunConfig
from google.adk.runners import InMemoryRunner
from google.genai import types
from memory import memory_context_for, write_run_outcome

from agents.analyst.baseline import render_ensemble_summary
from agents.common.env import assert_vertex_config, gemini_timeout_seconds, seed
from agents.common.state import (
    ANALYSIS,
    CALIBRATION_ADJUSTMENTS,
    CALIBRATION_REPORT,
    MARKET_STATE,
    REPLAY_REF,
    RUN_METRICS,
    SCENARIO_CONFIG,
    SCENARIO_RAW,
    TIMING_REPORT,
)
from agents.common.timing import record_timing
from agents.orchestrator.agent import build_orchestrator
from agents.orchestrator.engine_bridge import close_handle
from agents.scenario_author.agent import build_scenario_author

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
        "timing_report": state.get(TIMING_REPORT),
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


async def run_baseline_ensemble(
    config: RunConfig | None = None,
    *,
    seeds: list[int] | None = None,
    replay_dir: str = "runs",
    timing_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the deterministic low/base/high ensemble and return gateway-ready refs."""
    if config is None:
        from engine.scenarios import flagship_scenario

        config = flagship_scenario(seed=seed())
    config = config.model_copy(update={"baseline_mode": True})
    timing_state = timing_state if timing_state is not None else {}

    def on_window_timing(event: dict[str, Any]) -> None:
        record_timing(
            timing_state,
            kind="engine_window",
            name="EnsembleEngine",
            duration_ms=float(event.get("duration_ms", 0.0) or 0.0),
            run_id=event.get("run_id"),
            window_index=event.get("window_index"),
            ticks_requested=event.get("ticks_requested"),
            ticks_emitted=event.get("ticks_emitted"),
            ensemble_case=event.get("case"),
            ensemble_seed=event.get("seed"),
        )

    started = time.perf_counter()
    try:
        try:
            memory_context = memory_context_for(config.model_dump())
        except Exception as exc:
            memory_context = {"backend": "unavailable", "error": exc.__class__.__name__}
        ensemble = run_ensemble(
            config,
            replay_dir=replay_dir,
            seeds=seeds,
            on_window_timing=on_window_timing,
        )
        record_timing(
            timing_state,
            kind="ensemble",
            name="run_baseline_ensemble",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            run_id=ensemble.run_id,
            case_count=len(ensemble.cases),
        )
        analysis = render_ensemble_summary(
            config.model_dump(), ensemble.model_dump(), memory_context
        )
        representative = next(
            (
                case
                for case in ensemble.cases
                if case.case == ensemble.representative_case
            ),
            ensemble.cases[0],
        )
        with contextlib.suppress(Exception):
            write_run_outcome(
                config.model_dump(),
                {
                    **representative.metrics.model_dump(),
                    "ensemble": ensemble.model_dump(),
                },
                analysis=analysis,
            )
        return {
            "run_id": ensemble.run_id,
            "ensemble_result": ensemble.model_dump(),
            "representative_replay_ref": ensemble.representative_replay_ref,
            "analysis": analysis,
            "memory_context": memory_context,
            "timing_report": timing_state.get(TIMING_REPORT),
            "error": None,
        }
    except Exception as exc:
        record_timing(
            timing_state,
            kind="ensemble",
            name="run_baseline_ensemble",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            ok=False,
            run_id=config.run_id,
            error=exc.__class__.__name__,
        )
        return {
            "run_id": config.run_id,
            "ensemble_result": None,
            "representative_replay_ref": None,
            "timing_report": timing_state.get(TIMING_REPORT),
            "error": str(exc),
        }


def _merge_evidence(
    gemini: EvidenceSummary | None, fallback: EvidenceSummary | None
) -> EvidenceSummary | None:
    if gemini is None:
        return fallback
    if fallback is None:
        return gemini
    return EvidenceSummary(
        summary=" ".join(
            part
            for part in (gemini.summary.strip(), fallback.summary.strip())
            if part
        ),
        items=[*gemini.items, *fallback.items],
    )


def _fast_live_config(gemini_config: RunConfig, fallback_config: RunConfig | None) -> RunConfig:
    """Use Gemini's scenario assumptions once while preserving deterministic levers.

    The fast path lets Gemini shape the crisis schedule and crowd mood assumptions,
    but direct UI/product-accuracy inputs remain deterministic: instrument data,
    position size, exit speed, peer-crowding evidence, time scale, and crisis scalar.
    """
    updates: dict[str, Any] = {"baseline_mode": True}
    if fallback_config is not None:
        updates.update(
            {
                "instrument": fallback_config.instrument,
                "position": fallback_config.position,
                "exit_speed": fallback_config.exit_speed,
                "population_size": fallback_config.population_size,
                "halt_rule": fallback_config.halt_rule,
                "max_ticks": fallback_config.max_ticks,
                "ticks_per_window": fallback_config.ticks_per_window,
                "peer_crowding": fallback_config.peer_crowding,
                "time_scale": fallback_config.time_scale,
                "scenario_mode": fallback_config.scenario_mode,
                "evidence_summary": _merge_evidence(
                    gemini_config.evidence_summary,
                    fallback_config.evidence_summary,
                ),
                "crisis_intensity": fallback_config.crisis_intensity,
            }
        )
    merged = gemini_config.model_copy(deep=True, update=updates)
    return RunConfig.model_validate(merged.model_dump())


async def run_fast_live_ensemble(
    scenario_raw: str,
    *,
    fallback_config: RunConfig | None = None,
    timeout_seconds: float | None = None,
    seeds: list[int] | None = None,
    replay_dir: str = "runs",
) -> dict[str, Any]:
    """Fast live mode: Gemini assumptions once, deterministic ensemble after.

    If the Scenario Author times out or errors, the supplied deterministic
    ``fallback_config`` still produces a usable ensemble. No per-window Gemini
    stance refresh happens in this path.
    """
    assert_vertex_config()
    timeout = timeout_seconds if timeout_seconds is not None else gemini_timeout_seconds()
    timing_state: dict[str, Any] = {}
    fallback_reason: str | None = None
    config: RunConfig | None = None

    try:
        assumption = await asyncio.wait_for(
            _execute(
                build_scenario_author(),
                {SCENARIO_RAW: scenario_raw},
                message=scenario_raw,
            ),
            timeout=timeout,
        )
        if assumption.get("timing_report"):
            timing_state = {TIMING_REPORT: assumption["timing_report"]}
        if assumption.get("error"):
            raise RuntimeError(str(assumption["error"]))
        raw_config = assumption.get("scenario_config")
        if raw_config is None:
            raise RuntimeError("scenario author produced no config")
        config = _fast_live_config(RunConfig.model_validate(raw_config), fallback_config)
    except TimeoutError:
        fallback_reason = "gemini_timeout"
    except Exception as exc:
        fallback_reason = f"gemini_error:{exc.__class__.__name__}"

    if config is None:
        if fallback_config is None:
            return {
                "run_id": None,
                "ensemble_result": None,
                "representative_replay_ref": None,
                "timing_report": timing_state.get(TIMING_REPORT),
                "fallback_reason": fallback_reason,
                "error": fallback_reason or "gemini_assumption_failed",
            }
        config = fallback_config.model_copy(update={"baseline_mode": True})
        record_timing(
            timing_state,
            kind="fallback",
            name="fast_live_assumptions",
            duration_ms=0.0,
            ok=False,
            reason=fallback_reason,
            run_id=config.run_id,
        )

    result = await run_baseline_ensemble(
        config,
        seeds=seeds,
        replay_dir=replay_dir,
        timing_state=timing_state,
    )
    result["fallback_reason"] = fallback_reason
    result["scenario_config"] = config.model_dump()
    return result


async def run_detailed_live_ensemble(
    scenario_raw: str,
    *,
    fallback_config: RunConfig | None = None,
    timeout_seconds: float | None = None,
    seeds: list[int] | None = None,
    replay_dir: str = "runs",
) -> dict[str, Any]:
    """Detailed gateway mode with Gemini judgment and ensemble-safe output.

    The older detailed path returned one full Gemini-driven ADK run. For Phase 1 the
    product contract is stricter: detailed mode may use Gemini for judgment, but the
    low/base/high deterministic ensemble remains the authoritative output. This keeps
    live modes comparable and avoids running six archetype agents for every ensemble
    case and seed.
    """
    result = await run_fast_live_ensemble(
        scenario_raw,
        fallback_config=fallback_config,
        timeout_seconds=timeout_seconds,
        seeds=seeds,
        replay_dir=replay_dir,
    )
    result["gemini_mode"] = "detailed_ensemble"
    return result


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
