"""Deterministic engine-bridge agents — ADK ↔ the Phase-1 simulation engine.

These three custom ``BaseAgent``s are the *only* place the agent layer touches the
engine, and they contain no LLM call. They honour the contract firewall (§4): they
read just the six ``*_stance`` keys plus ``scenario_config``, and write only
``market_state``, ``run_metrics``, ``replay_ref``, and the loop's window index.

* :class:`SetupEngineAgent`   — build the engine from ``scenario_config``, start it,
  open the NDJSON recorder, publish the opening ``market_state``.
* :class:`AdvanceEngineAgent` — read the six stances, call ``engine.advance(stances, k)``
  for one window of ``k`` ticks, publish the new ``market_state``; escalate (stop the
  loop) when the engine reports the run is done.
* :class:`FinalizeEngineAgent` — finalize metrics, close the recorder, publish
  ``run_metrics`` and ``replay_ref``.

The live ``Engine`` object is not JSON-serialisable and is *not* placed in
session.state. It lives in a per-run registry keyed by ``run_id`` and is shared by
the three agents within one run; the driver guarantees cleanup. ``session.state``
only ever carries the contract shapes.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.baseline import baseline_stances
from engine.core import Engine
from engine.replay.recorder import Recorder
from engine.schema import INVESTOR_TYPES, RunConfig, Stance
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.common.state import (
    MARKET_STATE,
    REPLAY_REF,
    RUN_METRICS,
    SCENARIO_CONFIG,
    TICK_WINDOW_INDEX,
    stance_key,
)
from agents.common.timing import after_agent, before_agent, record_timing, timing_block

# Where NDJSON replays are written for live agent runs.
REPLAY_DIR = Path("runs")


@dataclass
class RunHandle:
    """Per-run engine state held out-of-band (never in session.state)."""

    engine: Engine | None
    recorder: Recorder | None
    replay_path: str
    remote: bool = False


_RUNS: dict[str, RunHandle] = {}


def get_handle(run_id: str) -> RunHandle | None:
    return _RUNS.get(run_id)


def close_handle(run_id: str) -> None:
    """Close the recorder and drop the handle. Safe to call more than once."""
    handle = _RUNS.pop(run_id, None)
    if handle is not None and handle.recorder is not None:
        with contextlib.suppress(Exception):
            handle.recorder.__exit__(None, None, None)


def _engine_service_url() -> str | None:
    return (
        os.getenv("EGRESS_ENGINE_SERVICE_URL")
        or os.getenv("ENGINE_SERVICE_URL")
        or ""
    ).strip() or None


async def _engine_service_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - gateway extra provides this
        raise RuntimeError("httpx is required for EGRESS_ENGINE_SERVICE_URL") from exc
    base = _engine_service_url()
    if not base:
        raise RuntimeError("EGRESS_ENGINE_SERVICE_URL is not configured")
    timeout = float(os.getenv("EGRESS_ENGINE_TIMEOUT_SECONDS", "60"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, f"{base.rstrip('/')}{path}", json=json_body)
    response.raise_for_status()
    return response.json()


def _drop_stress_tick(state: dict, config: RunConfig) -> tuple[float, float, int]:
    """Derive (drop, stress-proxy, tick) from the published market state."""
    market = state.get(MARKET_STATE) or {}
    ref = config.instrument.reference_price
    last = market.get("last_price", ref) or ref
    tick = int(market.get("tick", 0) or 0)
    halted = bool(market.get("halted", False))
    drop = max(0.0, (ref - last) / ref) if ref else 0.0
    stress = min(1.0, 2.5 * drop + (0.2 if halted else 0.0))
    return drop, stress, tick


def _coerce_stances(state: dict, config: RunConfig) -> dict:
    """Read the six stance keys into validated ``Stance`` objects.

    Robust against a missing or malformed stance (e.g. an LLM hiccup): any stance
    that fails to validate falls back to the deterministic baseline for that type,
    derived from the current market state, so a bad model output can never crash the
    engine or stall the run.
    """
    drop, stress, tick = _drop_stress_tick(state, config)
    fallback = baseline_stances(drop, stress, tick)
    stances: dict = {}
    for t in INVESTOR_TYPES:
        raw = state.get(stance_key(t))
        try:
            stances[t] = Stance.model_validate(raw) if raw is not None else fallback[t]
        except Exception:
            stances[t] = fallback[t]
    return stances


class SetupEngineAgent(BaseAgent):
    """Build and start the engine from ``scenario_config`` (deterministic)."""

    def __init__(self, name: str = "SetupEngine") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        raw_config = state.get(SCENARIO_CONFIG)
        if raw_config is None:
            err = state.get("scenario_error", "scenario_config missing before setup")
            yield Event(
                author=self.name,
                actions=EventActions(escalate=True, state_delta={"engine_error": err}),
            )
            return

        config = RunConfig.model_validate(raw_config)
        with timing_block(state, kind="engine_setup", name=self.name, run_id=config.run_id):
            if _engine_service_url():
                result = await _engine_service_request(
                    "POST",
                    "/runs",
                    json_body={"config": config.model_dump()},
                )
                market_state = result["market_state"]
                replay_path = str(result.get("replay_ref") or "")
                _RUNS[config.run_id] = RunHandle(
                    engine=None,
                    recorder=None,
                    replay_path=replay_path,
                    remote=True,
                )
            else:
                REPLAY_DIR.mkdir(parents=True, exist_ok=True)
                replay_path = str(REPLAY_DIR / f"{config.run_id}.ndjson")
                recorder = Recorder(replay_path)
                recorder.__enter__()
                engine = Engine(config, recorder=recorder)
                market_state_obj = engine.start()
                market_state = market_state_obj.model_dump()
                _RUNS[config.run_id] = RunHandle(engine, recorder, replay_path)

        delta = {
            MARKET_STATE: market_state,
            REPLAY_REF: replay_path,
            TICK_WINDOW_INDEX: 0,
        }
        for key, value in delta.items():
            state[key] = value
        yield Event(author=self.name, actions=EventActions(state_delta=delta))


class AdvanceEngineAgent(BaseAgent):
    """Advance the engine one window of ``k`` ticks from the current stances."""

    def __init__(self, name: str = "AdvanceEngine") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        config = RunConfig.model_validate(state[SCENARIO_CONFIG])
        handle = _RUNS.get(config.run_id)
        if handle is None:
            yield Event(
                author=self.name,
                actions=EventActions(escalate=True, state_delta={"engine_error": "no engine"}),
            )
            return

        stances = _coerce_stances(state, config)
        started = time.perf_counter()
        if handle.remote:
            result = await _engine_service_request(
                "POST",
                f"/runs/{config.run_id}/advance",
                json_body={
                    "stances": {key: value.model_dump() for key, value in stances.items()},
                    "ticks": config.ticks_per_window,
                },
            )
            market_state = result["market_state"]
            events_emitted = len(result.get("ticks") or [])
            done = bool(result.get("done"))
        else:
            assert handle.engine is not None
            market_state_obj, events = handle.engine.advance(stances, config.ticks_per_window)
            market_state = market_state_obj.model_dump()
            events_emitted = len(events)
            done = handle.engine.done
        record_timing(
            state,
            kind="engine_window",
            name=self.name,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            run_id=config.run_id,
            window_index=int(state.get(TICK_WINDOW_INDEX, 0)),
            ticks_requested=config.ticks_per_window,
            ticks_emitted=events_emitted,
        )

        window = int(state.get(TICK_WINDOW_INDEX, 0)) + 1
        delta = {MARKET_STATE: market_state, TICK_WINDOW_INDEX: window}
        for key, value in delta.items():
            state[key] = value
        # Stop the LoopAgent the moment the engine finishes (exit done, stall, or cap).
        yield Event(
            author=self.name,
            actions=EventActions(state_delta=delta, escalate=done),
        )


class FinalizeEngineAgent(BaseAgent):
    """Finalize metrics and close the recorder (deterministic)."""

    def __init__(self, name: str = "FinalizeEngine") -> None:
        super().__init__(
            name=name,
            before_agent_callback=before_agent(name),
            after_agent_callback=after_agent(name),
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        state = ctx.session.state
        config = RunConfig.model_validate(state[SCENARIO_CONFIG])
        handle = _RUNS.get(config.run_id)
        if handle is None:
            yield Event(author=self.name, actions=EventActions(state_delta={}))
            return

        with timing_block(state, kind="engine_finalize", name=self.name, run_id=config.run_id):
            if handle.remote:
                result = await _engine_service_request("GET", f"/runs/{config.run_id}/metrics")
                metrics_payload = result["metrics"]
            else:
                assert handle.engine is not None
                metrics_obj = handle.engine.finalize()
                metrics_payload = metrics_obj.model_dump()
            close_handle(config.run_id)
        delta = {RUN_METRICS: metrics_payload, REPLAY_REF: handle.replay_path}
        for key, value in delta.items():
            state[key] = value
        yield Event(author=self.name, actions=EventActions(state_delta=delta))
