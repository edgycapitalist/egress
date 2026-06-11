"""Offline tests for the gateway's cached-replay path.

These exercise the WebSocket hub end to end with no engine, agents, or cloud — the
guarantee that the cached demo always runs. They use FastAPI's TestClient, which
speaks the WebSocket protocol in-process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gateway.app import app
from gateway.replay import frames_from_replay, read_records
from gateway.run_config import build_run_config

FLAGSHIP = Path("docs/replays/flagship-42.ndjson")


def test_read_records_shapes_match_contract() -> None:
    meta, ticks, metrics = read_records(FLAGSHIP)
    assert meta["type"] == "meta"
    assert meta["config"]["run_id"] == "flagship-42"
    assert ticks and all(t["type"] == "tick" for t in ticks)
    assert metrics is not None and metrics["type"] == "metrics"
    # Monotonic tick numbering, the replay invariant the frontend relies on.
    assert [t["tick"] for t in ticks] == sorted(t["tick"] for t in ticks)


def test_frames_order_and_batching() -> None:
    frames = list(frames_from_replay(FLAGSHIP, source="cached", batch_size=4))
    kinds = [f["type"] for f in frames]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert "metrics" in kinds and "analysis" in kinds  # sidecar narrative present
    # Every tick is delivered exactly once across the batches.
    _, ticks, _ = read_records(FLAGSHIP)
    streamed = [t for f in frames if f["type"] == "ticks" for t in f["ticks"]]
    assert len(streamed) == len(ticks)
    assert frames[0]["total_ticks"] == len(ticks)


def test_cached_websocket_run_offline() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/run") as ws:
        ws.send_json({"mode": "cached", "pace_ms": 0})
        meta = ws.receive_json()
        assert meta["type"] == "meta" and meta["source"] == "cached"

        ticks: list[dict] = []
        analysis = None
        metrics = None
        while True:
            frame = ws.receive_json()
            if frame["type"] == "ticks":
                ticks.extend(frame["ticks"])
            elif frame["type"] == "metrics":
                metrics = frame["metrics"]
            elif frame["type"] == "analysis":
                analysis = frame["analysis"]
            elif frame["type"] == "done":
                break
            elif frame["type"] == "error":
                pytest.fail(frame["message"])

    assert ticks, "expected tick frames"
    assert metrics and 0.0 <= metrics["fill_rate"] <= 1.0
    assert analysis and "ACME" in analysis


def test_health_endpoint() -> None:
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["flagship_available"] is True


def test_scenario_defaults_endpoint() -> None:
    client = TestClient(app)
    body = client.get("/api/scenario/defaults").json()
    assert body["position_size"] > 0
    assert set(body["crowding_mix"]) == {
        "forced_seller",
        "panic_seller",
        "trend_follower",
        "bargain_hunter",
        "market_maker",
        "holder",
    }


def test_build_run_config_applies_levers() -> None:
    cfg = build_run_config(
        {
            "position_size": 400_000,
            "exit_speed": "urgent",
            "crowding_mix": {"forced_seller": 2, "panic_seller": 2},  # renormalised
            "seed": 7,
        }
    )
    assert cfg.position.quantity == 400_000
    assert cfg.exit_speed.participation_rate == 0.20
    mix = cfg.crowding_mix.as_dict()
    assert abs(sum(mix.values()) - 1.0) < 1e-6
    assert mix["forced_seller"] == 0.5 and mix["holder"] == 0.0
    assert cfg.run_id.startswith("run-")
    assert json.loads(cfg.model_dump_json())  # serialisable for the wire
