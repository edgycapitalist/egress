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
# Per-ticker cached recordings live beside the flagship (e.g. aapl.ndjson). Selecting a
# curated ticker in cached mode streams its recording instead of the CVNA flagship.
REPLAY_DIR = FLAGSHIP_REPLAY.parent


def _cached_replay_for(symbol: str | None) -> Path:
    """The cached recording for a chosen ticker, defaulting to the CVNA example.

    An empty/unknown symbol (or a name with no committed recording) plays the curated
    CVNA recording, so cached mode is always safe and offline. (The older flagship
    recording remains only as a last-resort fallback if the CVNA file is missing.)
    """
    if symbol:
        candidate = REPLAY_DIR / f"{str(symbol).strip().lower()}.ndjson"
        if candidate.exists():
            return candidate
    default = REPLAY_DIR / "cvna.ndjson"
    return default if default.exists() else FLAGSHIP_REPLAY

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
        # Whether a live run can fetch real Alpha Vantage data, so the UI can say so
        # honestly instead of implying real data when only the synthetic fallback runs.
        "av_enabled": bool(os.environ.get("ALPHAVANTAGE_API_KEY")),
    }


@app.get("/api/instrument")
def instrument_reference(symbol: str, live: bool = False, period: str = "recent") -> dict[str, Any]:
    """Sourced inputs for an instrument — the same values its run uses.

    Resolves the instrument exactly as a run does (``gateway.instruments``): the real
    Alpha Vantage feed when ``live`` is set and a key is configured, otherwise the
    curated/synthetic fallback. Returns the reference price, ADV, free float, recent
    realized volatility, the real date ``window`` the numbers cover (when live), and a
    ``source`` label so the UI is honest. Pass ``live`` matching the run's mode so the
    panel always agrees with the simulation. A sync def so the (possibly blocking) MCP
    call runs in a worker thread, not the event loop.
    """
    from gateway.instruments import _synthetic_reference, resolve_instrument

    # resolve_instrument returns None only for a non-live query of an unknown symbol;
    # fall back to a synthetic reference there so a non-live lookup never calls the feed.
    inst = resolve_instrument(symbol, live=live) or _synthetic_reference(symbol)
    return {
        "symbol": inst["symbol"],
        "name": inst.get("name"),
        "reference_price": inst["reference_price"],
        "adv": inst["adv"],
        "free_float": inst["free_float"],
        "realized_vol_daily": inst["volatility"],
        "window": inst.get("window"),
        "bars": inst.get("bars", 0),
        "source": inst["source"],
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


async def _run_live(
    levers: dict[str, Any], use_gemini: bool
) -> tuple[str, str, str | None, dict[str, Any] | None]:
    """Drive a fresh run. Returns (replay_path, source, analysis, ensemble)."""
    from agents.orchestrator.driver import (  # lazy: keeps cached path import-light
        run_baseline_ensemble,
        run_live_simulation,
    )

    if use_gemini and _gemini_enabled():
        result = await run_live_simulation(scenario_prompt(levers))
        source = "live-gemini"
        ensemble = None
    else:
        # A live run pulls the instrument's real Alpha Vantage data and derives the
        # crisis magnitude from the typed stress text + the instrument's real news
        # (synthetic fallback when no key), then runs low/base/high peer-crowding
        # cases over fixed deterministic seeds. The representative replay animates
        # exactly like the old single run; the ensemble frame carries the ranges.
        from gateway.crisis import derive_crisis_intensity

        text = str(levers.get("scenario_text") or "")
        intensity, _detail = derive_crisis_intensity(
            text, levers.get("symbol"), fetch_news=True
        )
        config = build_run_config(levers, live_data=True, crisis_intensity=intensity)
        result = await run_baseline_ensemble(config)
        source = "live-baseline"
        ensemble = result.get("ensemble_result")

    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    replay_ref = result.get("representative_replay_ref") or result.get("replay_ref")
    if not replay_ref or not Path(replay_ref).exists():
        raise RuntimeError("live run produced no replay")
    return replay_ref, source, result.get("analysis"), ensemble


async def _stream(ws: WebSocket, request: dict[str, Any]) -> None:
    mode = str(request.get("mode", "cached")).lower()
    pace = max(0, int(request.get("pace_ms", DEFAULT_PACE_MS))) / 1000.0
    batch_size = int(request.get("batch_size", DEFAULT_BATCH))
    levers = request.get("scenario") or {}

    if mode == "live":
        await ws.send_json({"type": "status", "message": "Running the simulation ensemble…"})
        try:
            replay_path, source, analysis, ensemble = await _run_live(
                levers, bool(request.get("gemini"))
            )
        except Exception as exc:  # surface a clean error frame, never a stack trace
            await ws.send_json({"type": "error", "message": f"Live run failed: {exc}"})
            return
    else:
        replay_file = _cached_replay_for(levers.get("symbol"))
        if not replay_file.exists():
            await ws.send_json({"type": "error", "message": "Flagship replay not found."})
            return
        replay_path, source, analysis, ensemble = str(replay_file), "cached", None, None

    for frame in frames_from_replay(
        replay_path,
        source=source,
        batch_size=batch_size,
        analysis=analysis,
        ensemble=ensemble,
    ):
        await ws.send_json(frame)
        # Pace the ticks for the animation; give the trailing frames (metrics /
        # analysis / done) a small gap too, so they are not a single burst right
        # before the close. Cloud Run's WebSocket proxy can drop an un-flushed tail
        # when the server sends the last frames and immediately closes.
        if frame["type"] == "ticks":
            if pace:
                await asyncio.sleep(pace)
        else:
            await asyncio.sleep(0.06)
    # Hold the socket open briefly so the final frames flush across the proxy
    # before the handler returns and the connection is torn down.
    await asyncio.sleep(0.5)


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket) -> None:
    await ws.accept()
    try:
        request = await ws.receive_json()
        await _stream(ws, request)
        # Wait for the client to close first (it does once it has the `done` frame),
        # so we never tear the connection down before the tail is delivered.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.receive_text(), timeout=5)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        # Last-resort guard so the socket closes cleanly with a message.
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "message": str(exc)})
