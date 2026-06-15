"""FastAPI service wrapper for the deterministic simulation engine.

This is the Cloud Run surface for the LLM-free core. The service owns active
``Engine`` handles in-process and mirrors serialized state/replay records into
the configured ``RunStateStore``. In deployed mode that store is Redis; local
tests and no-cloud development use the in-memory fallback.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from engine.baseline import baseline_stances
from engine.core import Engine
from engine.replay.recorder import Recorder
from engine.schema import (
    INVESTOR_TYPES,
    SCHEMA_VERSION,
    InvestorType,
    MarketState,
    Metrics,
    RunConfig,
    Stance,
)
from engine.service_state import RunStateStore, build_run_state_store


class StartRunRequest(BaseModel):
    config: RunConfig


class AdvanceRunRequest(BaseModel):
    stances: dict[InvestorType, Stance] | None = None
    ticks: int | None = Field(default=None, gt=0)


class RunStartResponse(BaseModel):
    run_id: str
    market_state: MarketState
    replay_ref: str
    state_backend: str


class AdvanceRunResponse(BaseModel):
    run_id: str
    market_state: MarketState
    ticks: list[dict[str, Any]]
    done: bool
    state_backend: str


class MetricsResponse(BaseModel):
    run_id: str
    metrics: Metrics
    replay_ref: str | None = None
    state_backend: str


@dataclass
class ActiveRun:
    engine: Engine
    recorder: Recorder
    replay_path: Path
    finalized: bool = False
    metrics: Metrics | None = None


RUNS: dict[str, ActiveRun] = {}
REPLAY_DIR = Path(os.getenv("EGRESS_ENGINE_REPLAY_DIR", "runs"))
app = FastAPI(title="Egress Engine Service", version="0.1.0")
_STORE: RunStateStore | None = None


def state_store() -> RunStateStore:
    global _STORE
    if _STORE is None:
        _STORE = build_run_state_store()
    return _STORE


def _record(run_id: str, record: dict[str, Any]) -> None:
    state_store().append_replay_record(run_id, record)


def _set_state(run_id: str, **state: Any) -> None:
    state_store().set_run_state(run_id, {"run_id": run_id, **state})


def _close(active: ActiveRun) -> None:
    with contextlib.suppress(Exception):
        active.recorder.__exit__(None, None, None)


def _finalize(run_id: str, active: ActiveRun) -> Metrics:
    if active.metrics is None:
        active.metrics = active.engine.finalize()
        active.finalized = True
        _record(run_id, active.metrics.model_dump())
        _set_state(
            run_id,
            status="complete",
            metrics=active.metrics.model_dump(),
            replay_ref=str(active.replay_path),
        )
        _close(active)
    return active.metrics


def _active(run_id: str) -> ActiveRun:
    active = RUNS.get(run_id)
    if active is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return active


def _coerce_stances(active: ActiveRun, raw: dict[InvestorType, Stance] | None) -> dict:
    engine = active.engine
    drop = max(0.0, (engine.ref_price - engine.last_price) / engine.ref_price)
    fallback = baseline_stances(drop, engine.stress, engine.tick)
    if not raw:
        return fallback
    stances: dict[InvestorType, Stance] = {}
    for investor_type in INVESTOR_TYPES:
        stances[investor_type] = raw.get(investor_type) or fallback[investor_type]
    return stances


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        store = state_store()
        backend_health = store.health()
        ok = True
        error = None
    except Exception as exc:
        backend_health = {"backend": "unavailable", "ok": False}
        ok = False
        error = str(exc)
    return {
        "status": "ok" if ok else "degraded",
        "engine": "deterministic",
        "state": backend_health,
        "active_runs": len(RUNS),
        "error": error,
    }


@app.post("/runs", response_model=RunStartResponse)
def start_run(request: StartRunRequest) -> RunStartResponse:
    config = request.config
    if config.run_id in RUNS:
        _close(RUNS.pop(config.run_id))

    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    replay_path = REPLAY_DIR / f"{config.run_id}.ndjson"
    recorder = Recorder(replay_path)
    recorder.__enter__()
    engine = Engine(config, recorder=recorder)
    market_state = engine.start()
    active = ActiveRun(engine=engine, recorder=recorder, replay_path=replay_path)
    RUNS[config.run_id] = active

    _record(
        config.run_id,
        {"type": "meta", "schema_version": SCHEMA_VERSION, "config": config.model_dump()},
    )
    _set_state(
        config.run_id,
        status="active",
        market_state=market_state.model_dump(),
        replay_ref=str(replay_path),
    )
    return RunStartResponse(
        run_id=config.run_id,
        market_state=market_state,
        replay_ref=str(replay_path),
        state_backend=state_store().name,
    )


@app.post("/runs/{run_id}/advance", response_model=AdvanceRunResponse)
def advance_run(run_id: str, payload: AdvanceRunRequest | None = None) -> AdvanceRunResponse:
    active = _active(run_id)
    if active.finalized:
        raise HTTPException(status_code=409, detail="run has already been finalized")

    request = payload or AdvanceRunRequest()
    ticks_requested = request.ticks or active.engine.config.ticks_per_window
    stances = _coerce_stances(active, request.stances)
    market_state, events = active.engine.advance(stances, ticks_requested)
    dumped_events = [event.model_dump() for event in events]
    for record in dumped_events:
        _record(run_id, record)
    state_store().publish(
        f"ticks:{run_id}",
        {
            "run_id": run_id,
            "tick_count": len(dumped_events),
            "market_state": market_state.model_dump(),
            "done": active.engine.done,
        },
    )
    _set_state(
        run_id,
        status="done" if active.engine.done else "active",
        market_state=market_state.model_dump(),
        replay_ref=str(active.replay_path),
    )
    return AdvanceRunResponse(
        run_id=run_id,
        market_state=market_state,
        ticks=dumped_events,
        done=active.engine.done,
        state_backend=state_store().name,
    )


@app.get("/runs/{run_id}/metrics", response_model=MetricsResponse)
def metrics(run_id: str) -> MetricsResponse:
    active = RUNS.get(run_id)
    if active is not None:
        metrics_obj = _finalize(run_id, active)
        return MetricsResponse(
            run_id=run_id,
            metrics=metrics_obj,
            replay_ref=str(active.replay_path),
            state_backend=state_store().name,
        )

    state = state_store().get_run_state(run_id)
    raw_metrics = (state or {}).get("metrics")
    if raw_metrics is None:
        raise HTTPException(status_code=404, detail=f"metrics for {run_id!r} not found")
    return MetricsResponse(
        run_id=run_id,
        metrics=Metrics.model_validate(raw_metrics),
        replay_ref=(state or {}).get("replay_ref"),
        state_backend=state_store().name,
    )


@app.get("/runs/{run_id}/replay")
def replay(run_id: str) -> PlainTextResponse:
    active = RUNS.get(run_id)
    if active is not None and active.replay_path.exists():
        text = active.replay_path.read_text(encoding="utf-8")
        return PlainTextResponse(text, media_type="application/x-ndjson")

    records = state_store().get_replay_records(run_id)
    if records:
        return PlainTextResponse(
            "\n".join(json.dumps(record) for record in records) + "\n",
            media_type="application/x-ndjson",
        )
    raise HTTPException(status_code=404, detail=f"replay for {run_id!r} not found")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
