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
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from engine.scenarios import flagship_scenario
from engine.schema import (
    EnsembleCaseSummary,
    EnsembleResult,
    EvidenceSummary,
    MetricBand,
    Metrics,
    RunConfig,
)
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from gateway.replay import DEFAULT_BATCH, frames_from_replay, read_records
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


def _merge_evidence(
    current: EvidenceSummary | None, incoming: EvidenceSummary | None
) -> EvidenceSummary | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    return EvidenceSummary(
        summary=" ".join(
            part
            for part in (current.summary.strip(), incoming.summary.strip())
            if part
        ),
        items=[*current.items, *incoming.items],
    )


def _flat_band(value: float | int | None) -> MetricBand:
    val = round(float(value or 0.0), 6)
    return MetricBand(low=val, median=val, high=val)


def _cached_overlay_config_and_ensemble(
    replay_file: Path, levers: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Add the v0.4 evidence envelope to a committed replay without rewriting it."""
    meta, _ticks, metrics_raw = read_records(replay_file)
    raw_config = meta.get("config")
    if not isinstance(raw_config, dict) or not isinstance(metrics_raw, dict):
        return None, None

    replay_config = RunConfig.model_validate(raw_config)
    peer_levers: dict[str, Any] = {
        "symbol": replay_config.instrument.symbol,
        "peer_source_mode": levers.get("peer_source_mode") or "auto",
        "user_holdings_csv": levers.get("user_holdings_csv") or "",
        "time_scale": levers.get("time_scale") or replay_config.time_scale.model_dump(),
    }
    if levers.get("peer_crowding") is not None:
        peer_levers["peer_crowding"] = levers["peer_crowding"]
    if levers.get("exit_horizon_ticks") is not None:
        peer_levers["exit_horizon_ticks"] = levers["exit_horizon_ticks"]
    if levers.get("exit_horizon_hours") is not None:
        peer_levers["exit_horizon_hours"] = levers["exit_horizon_hours"]
    if levers.get("exit_horizon_days") is not None:
        peer_levers["exit_horizon_days"] = levers["exit_horizon_days"]

    evidence_config = build_run_config(peer_levers, live_data=False)
    config = replay_config.model_copy(
        deep=True,
        update={
            "peer_crowding": evidence_config.peer_crowding,
            "time_scale": evidence_config.time_scale,
            "scenario_mode": "historical_saved",
            "evidence_summary": _merge_evidence(
                replay_config.evidence_summary,
                evidence_config.evidence_summary,
            ),
        },
    )
    metrics = Metrics.model_validate(metrics_raw)
    ensemble_metrics = metrics.model_copy(
        update={"ensemble_case": "base", "ensemble_seed": replay_config.seed}
    )
    representative_ref = str(replay_file)
    ensemble = EnsembleResult(
        run_id=f"{replay_config.run_id}-cached-ensemble",
        cases=[
            EnsembleCaseSummary(
                case="base",
                seeds=[replay_config.seed],
                peer_crowding=config.peer_crowding,
                metrics=ensemble_metrics,
                representative_replay_ref=representative_ref,
            )
        ],
        bands={
            "fill_rate": _flat_band(metrics.fill_rate),
            "pct_stuck": _flat_band(metrics.pct_stuck),
            "slippage_bps": _flat_band(metrics.slippage_bps),
            "implementation_shortfall_bps": _flat_band(
                metrics.implementation_shortfall_bps
            ),
            "max_drawdown_pct": _flat_band(metrics.max_drawdown_pct),
            "time_to_exit_ticks": _flat_band(
                metrics.time_to_exit_ticks
                if metrics.time_to_exit_ticks is not None
                else metrics.ticks_run
            ),
            "halt_probability": _flat_band(1.0 if metrics.halt_triggered else 0.0),
        },
        representative_case="base",
        representative_replay_ref=representative_ref,
        evidence_summary=config.evidence_summary,
    )
    return config.model_dump(), ensemble.model_dump()

# Demo pacing: ms of dwell between successive tick batches so the cascade animates.
DEFAULT_PACE_MS = int(os.getenv("EGRESS_PACE_MS", "110"))
ProgressSender = Callable[[str], Awaitable[None]]

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


@app.post("/api/positioning")
def positioning_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Sourced peer-crowding inputs for an instrument.

    This is the preview surface for Phase 4 positioning evidence. It uses the same
    backend as ``build_run_config``: user CSV first, then opt-in SEC EDGAR, curated
    fixtures, and synthetic assumptions. A sync def keeps any optional blocking
    SEC call in FastAPI's worker thread rather than the event loop.
    """
    from mcp.positioning.data import get_peer_crowding_evidence

    symbol = str(payload.get("symbol") or "CVNA")
    return get_peer_crowding_evidence(
        symbol,
        period=str(payload.get("period") or "recent"),
        source_mode=str(payload.get("peer_source_mode") or payload.get("source_mode") or "auto"),
        user_holdings_csv=str(payload.get("user_holdings_csv") or ""),
    )


def _safe_replay_path(ref: str) -> Path:
    """Resolve a frontend replay ref without exposing arbitrary filesystem reads."""
    requested = Path(str(ref or ""))
    if not str(requested):
        raise HTTPException(status_code=400, detail="Missing replay ref.")
    candidate = requested if requested.is_absolute() else Path.cwd() / requested
    resolved = candidate.resolve()
    roots = [(Path.cwd() / "runs").resolve(), REPLAY_DIR.resolve()]
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise HTTPException(status_code=400, detail="Replay ref is outside allowed roots.")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Replay not found.")
    return resolved


@app.get("/api/replay")
def replay_payload(ref: str) -> dict[str, Any]:
    """Return one recorded replay for case selection in the frontend."""
    meta, ticks, metrics = read_records(_safe_replay_path(ref))
    return {
        "schema_version": meta.get("schema_version"),
        "config": meta.get("config"),
        "total_ticks": len(ticks),
        "ticks": ticks,
        "metrics": metrics,
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
    levers: dict[str, Any],
    use_gemini: bool,
    gemini_mode: str | None = None,
    progress: ProgressSender | None = None,
) -> tuple[str, str, str | None, dict[str, Any] | None]:
    """Drive a fresh run. Returns (replay_path, source, analysis, ensemble)."""
    from agents.orchestrator.driver import (  # lazy: keeps cached path import-light
        run_baseline_ensemble,
        run_detailed_live_ensemble,
        run_fast_live_ensemble,
    )

    from gateway.crisis import derive_crisis_intensity

    async def send(message: str) -> None:
        if progress is not None:
            await progress(message)

    await send("Gathering market data and peer-crowding evidence…")
    text = str(levers.get("scenario_text") or "")
    intensity, _detail = derive_crisis_intensity(
        text, levers.get("symbol"), fetch_news=True
    )
    config = build_run_config(levers, live_data=True, crisis_intensity=intensity)

    if use_gemini and _gemini_enabled():
        from agents.common.env import gemini_live_mode, gemini_timeout_seconds

        requested_mode = (
            gemini_mode
            or levers.get("gemini_mode")
            or os.getenv("EGRESS_GEMINI_LIVE_MODE")
            or gemini_live_mode()
        )
        requested_mode = str(requested_mode).strip().lower().replace("-", "_")
        if requested_mode in {"detailed", "ai_detailed", "full"}:
            await send("Generating detailed Gemini assumptions, then running the ensemble…")
            result = await run_detailed_live_ensemble(
                scenario_prompt(levers),
                fallback_config=config,
                timeout_seconds=gemini_timeout_seconds(),
            )
            source = "live-baseline" if result.get("fallback_reason") else "live-gemini"
            ensemble = result.get("ensemble_result")
        else:
            await send("Generating Gemini assumptions, then running the ensemble…")
            result = await run_fast_live_ensemble(
                scenario_prompt(levers),
                fallback_config=config,
                timeout_seconds=gemini_timeout_seconds(),
            )
            source = "live-baseline" if result.get("fallback_reason") else "live-gemini"
            ensemble = result.get("ensemble_result")
    else:
        # A live run pulls the instrument's real Alpha Vantage data and derives the
        # crisis magnitude from the typed stress text + the instrument's real news
        # (synthetic fallback when no key), then runs low/base/high peer-crowding
        # cases over fixed deterministic seeds. The representative replay animates
        # exactly like the old single run; the ensemble frame carries the ranges.
        await send("Running low/base/high deterministic ensemble…")
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
        try:
            replay_path, source, analysis, ensemble = await _run_live(
                levers,
                bool(request.get("gemini")),
                request.get("gemini_mode"),
                progress=lambda message: ws.send_json(
                    {"type": "status", "message": message}
                ),
            )
            await ws.send_json({"type": "status", "message": "Streaming representative path…"})
        except Exception as exc:  # surface a clean error frame, never a stack trace
            await ws.send_json({"type": "error", "message": f"Live run failed: {exc}"})
            return
    else:
        await ws.send_json({"type": "status", "message": "Loading saved historical replay…"})
        replay_file = _cached_replay_for(levers.get("symbol"))
        if not replay_file.exists():
            await ws.send_json({"type": "error", "message": "Flagship replay not found."})
            return
        cached_config, ensemble = _cached_overlay_config_and_ensemble(replay_file, levers)
        replay_path, source, analysis = str(replay_file), "cached", None

    for frame in frames_from_replay(
        replay_path,
        source=source,
        batch_size=batch_size,
        analysis=analysis,
        ensemble=ensemble,
        config=cached_config if source == "cached" else None,
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
