"""Egress gateway / BFF — FastAPI WebSocket hub that streams a run to the frontend.

Two run sources, one frame protocol:

* **cached** — replays the flagship NDJSON recording. Pure standard-library file read;
  no engine, no agents, no cloud. This is the reliable demo path and it works fully
  offline. (``docs/contracts.md`` §3.4.)
* **live** — drives the ADK orchestrator now, records a fresh NDJSON, then streams it.
  By default the deterministic baseline lifecycle (a real ADK ``SequentialAgent`` run,
  zero LLM cost, offline-safe); with ``gemini`` requested and Vertex configured, the
  real Gemini pipeline.

Either way the server emits the same ordered frames (``meta`` → batched ``ticks`` →
``metrics`` → ``analysis`` → ``done``) and **batches the ticks** so a long run is a
handful of socket writes rather than hundreds — the gateway's job per AGENTS.md §3.

The A2A note in AGENTS.md §2 is optional for Track 1; the orchestrator is invoked
in-process through the run driver here, which is the same boundary an A2A client
would call.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

from engine.scenarios import flagship_scenario
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from gateway.replay import DEFAULT_BATCH, frames_from_replay
from gateway.run_config import EXIT_SPEED_PRESETS, build_run_config, scenario_prompt

# The committed cached replay lives under docs/ (version-controlled; runs/ is
# throwaway generated output). This is what the offline demo streams.
FLAGSHIP_REPLAY = Path(os.getenv("EGRESS_FLAGSHIP_REPLAY", "docs/replays/flagship-42.ndjson"))

# Demo pacing: ms of dwell between successive tick batches so the cascade animates.
DEFAULT_PACE_MS = int(os.getenv("EGRESS_PACE_MS", "110"))

app = FastAPI(title="Egress Gateway", version="0.3.0")

# The frontend (Next.js) runs on a different origin in dev; allow it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("EGRESS_CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "flagship_replay": str(FLAGSHIP_REPLAY),
        "flagship_available": FLAGSHIP_REPLAY.exists(),
        "gemini_enabled": _gemini_enabled(),
    }


@app.get("/api/scenario/defaults")
async def scenario_defaults() -> dict[str, Any]:
    """Seed values for the scenario builder — the flagship as the starting point."""
    cfg = flagship_scenario()
    sym = cfg.instrument.symbol
    return {
        "instrument": cfg.instrument.model_dump(),
        "position_size": cfg.position.quantity,
        "population_size": cfg.population_size,
        "exit_speed": "measured",
        "exit_speed_presets": EXIT_SPEED_PRESETS,
        "crowding_mix": cfg.crowding_mix.as_dict(),
        "scenario_text": (
            f"A heavily crowded name ({sym}) is hit by a surprise liquidity and "
            "bankruptcy scare. Forced sellers hit margin calls, panic and trend "
            "sellers pile on, and bargain-hunter and market-maker support is thin."
        ),
        "gemini_enabled": _gemini_enabled(),
    }


@app.get("/api/instrument")
def instrument_reference(symbol: str, period: str = "recent") -> dict[str, Any]:
    """Real sourced inputs for an instrument, via the Market Data MCP.

    Read-only BFF passthrough: returns the instrument's reference price, ADV, free
    float, and recent realized volatility, plus a ``source`` field that says whether
    the numbers came from the live Alpha Vantage feed or the synthetic fallback —
    so the UI can label them honestly. A sync def so the (possibly blocking) MCP
    call runs in a worker thread, not the event loop.
    """
    import datetime as _dt

    from mcp.market_data.data import get_historical_window, get_instrument_reference

    ref = get_instrument_reference(symbol)
    end = _dt.date.today()
    start = end - _dt.timedelta(days=120)
    hist = get_historical_window(symbol, start.isoformat(), end.isoformat())
    return {
        "symbol": ref["symbol"],
        "name": ref.get("name"),
        "reference_price": ref["reference_price"],
        "adv": ref["adv"],
        "free_float": ref["free_float"],
        "realized_vol_daily": hist.get("realized_vol_daily"),
        "bars": len(hist.get("bars", [])),
        "source": ref.get("source", "synthetic"),
    }


def _gemini_enabled() -> bool:
    """True only when a live Gemini run is both requested-capable and configured."""
    if os.getenv("EGRESS_LIVE_GEMINI", "").lower() not in {"1", "true", "yes"}:
        return False
    try:
        from agents.common.env import assert_vertex_config

        assert_vertex_config()
        return True
    except Exception:
        return False


async def _run_live(levers: dict[str, Any], use_gemini: bool) -> tuple[str, str, str | None]:
    """Drive the orchestrator for a fresh run. Returns (replay_path, source, analysis)."""
    from agents.orchestrator.driver import (  # lazy: keeps cached path import-light
        run_baseline_simulation,
        run_live_simulation,
    )

    if use_gemini and _gemini_enabled():
        result = await run_live_simulation(scenario_prompt(levers))
        source = "live-gemini"
    else:
        config = build_run_config(levers)
        result = await run_baseline_simulation(config)
        source = "live-baseline"

    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    replay_ref = result.get("replay_ref")
    if not replay_ref or not Path(replay_ref).exists():
        raise RuntimeError("live run produced no replay")
    return replay_ref, source, result.get("analysis")


async def _stream(ws: WebSocket, request: dict[str, Any]) -> None:
    mode = str(request.get("mode", "cached")).lower()
    pace = max(0, int(request.get("pace_ms", DEFAULT_PACE_MS))) / 1000.0
    batch_size = int(request.get("batch_size", DEFAULT_BATCH))
    levers = request.get("scenario") or {}

    if mode == "live":
        await ws.send_json({"type": "status", "message": "Running the simulation…"})
        try:
            replay_path, source, analysis = await _run_live(levers, bool(request.get("gemini")))
        except Exception as exc:  # surface a clean error frame, never a stack trace
            await ws.send_json({"type": "error", "message": f"Live run failed: {exc}"})
            return
    else:
        if not FLAGSHIP_REPLAY.exists():
            await ws.send_json({"type": "error", "message": "Flagship replay not found."})
            return
        replay_path, source, analysis = str(FLAGSHIP_REPLAY), "cached", None

    for frame in frames_from_replay(
        replay_path, source=source, batch_size=batch_size, analysis=analysis
    ):
        await ws.send_json(frame)
        if frame["type"] == "ticks" and pace:
            await asyncio.sleep(pace)


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket) -> None:
    await ws.accept()
    try:
        request = await ws.receive_json()
        await _stream(ws, request)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        # Last-resort guard so the socket closes cleanly with a message.
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "message": str(exc)})
