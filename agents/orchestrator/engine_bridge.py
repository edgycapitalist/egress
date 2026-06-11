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

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

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
from engine.baseline import baseline_stances
from engine.core import Engine
from engine.replay.recorder import Recorder
from engine.schema import INVESTOR_TYPES, RunConfig, Stance

# Where NDJSON replays are written for live agent runs.
REPLAY_DIR = Path("runs")


@dataclass
class RunHandle:
    """Per-run engine state held out-of-band (never in session.state)."""

    engine: Engine
    recorder: Recorder
    replay_path: str


_RUNS: dict[str, RunHandle] = {}


def get_handle(run_id: str) -> RunHandle | None:
    return _RUNS.get(run_id)


def close_handle(run_id: str) -> None:
    """Close the recorder and drop the handle. Safe to call more than once."""
    handle = _RUNS.pop(run_id, None)
    if handle is not None:
        try:
            handle.recorder.__exit__(None, None, None)
        except Exception:
            pass


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
        super().__init__(name=name)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
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
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        replay_path = str(REPLAY_DIR / f"{config.run_id}.ndjson")
        recorder = Recorder(replay_path)
        recorder.__enter__()
        engine = Engine(config, recorder=recorder)
        market_state = engine.start()
        _RUNS[config.run_id] = RunHandle(engine, recorder, replay_path)

        delta = {
            MARKET_STATE: market_state.model_dump(),
            REPLAY_REF: replay_path,
            TICK_WINDOW_INDEX: 0,
        }
        for key, value in delta.items():
            state[key] = value
        yield Event(author=self.name, actions=EventActions(state_delta=delta))


class AdvanceEngineAgent(BaseAgent):
    """Advance the engine one window of ``k`` ticks from the current stances."""

    def __init__(self, name: str = "AdvanceEngine") -> None:
        super().__init__(name=name)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
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
        market_state, _events = handle.engine.advance(stances, config.ticks_per_window)

        window = int(state.get(TICK_WINDOW_INDEX, 0)) + 1
        delta = {MARKET_STATE: market_state.model_dump(), TICK_WINDOW_INDEX: window}
        for key, value in delta.items():
            state[key] = value
        # Stop the LoopAgent the moment the engine finishes (exit done, stall, or cap).
        yield Event(
            author=self.name,
            actions=EventActions(state_delta=delta, escalate=handle.engine.done),
        )


class FinalizeEngineAgent(BaseAgent):
    """Finalize metrics and close the recorder (deterministic)."""

    def __init__(self, name: str = "FinalizeEngine") -> None:
        super().__init__(name=name)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        config = RunConfig.model_validate(state[SCENARIO_CONFIG])
        handle = _RUNS.get(config.run_id)
        if handle is None:
            yield Event(author=self.name, actions=EventActions(state_delta={}))
            return

        metrics = handle.engine.finalize()
        close_handle(config.run_id)
        delta = {RUN_METRICS: metrics.model_dump(), REPLAY_REF: handle.replay_path}
        for key, value in delta.items():
            state[key] = value
        yield Event(author=self.name, actions=EventActions(state_delta=delta))
