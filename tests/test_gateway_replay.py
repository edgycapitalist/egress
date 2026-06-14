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
    assert "ensemble" not in kinds  # cached replay mode remains a single recorded path
    # Every tick is delivered exactly once across the batches.
    _, ticks, _ = read_records(FLAGSHIP)
    streamed = [t for f in frames if f["type"] == "ticks" for t in f["ticks"]]
    assert len(streamed) == len(ticks)
    assert frames[0]["total_ticks"] == len(ticks)


def test_frames_can_include_ensemble_without_changing_replay_order() -> None:
    ensemble = {"type": "ensemble", "run_id": "e-1", "cases": [], "bands": {}}
    frames = list(
        frames_from_replay(FLAGSHIP, source="live-baseline", batch_size=100, ensemble=ensemble)
    )
    kinds = [f["type"] for f in frames]
    assert kinds.index("ensemble") > kinds.index("metrics")
    assert kinds[-1] == "done"
    assert next(f for f in frames if f["type"] == "ensemble")["ensemble"] == ensemble


def test_cached_websocket_run_offline() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/run") as ws:
        ws.send_json({"mode": "cached", "pace_ms": 0})
        meta = ws.receive_json()
        assert meta["type"] == "meta" and meta["source"] == "cached"
        symbol = meta["config"]["instrument"]["symbol"]

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
    assert analysis and symbol in analysis  # the analyst names the replay's instrument


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
    assert cfg.scenario_mode == "assumption_led"
    assert json.loads(cfg.model_dump_json())  # serialisable for the wire


def test_build_run_config_labels_instrument_evidence() -> None:
    cfg = build_run_config({"symbol": "CVNA"})
    assert cfg.evidence_summary is not None
    assert cfg.evidence_summary.items[0].field == "instrument"
    assert cfg.evidence_summary.items[0].source == "curated_fixture"


def test_build_run_config_applies_population_size() -> None:
    cfg = build_run_config({"population_size": 12_000})
    assert cfg.population_size == 12_000


def test_build_run_config_accepts_phase2_peer_and_time_scale_levers() -> None:
    cfg = build_run_config(
        {
            "peer_crowding": {
                "case": "base",
                "peer_fund_count": 4,
                "overlap_pct": 0.5,
                "avg_peer_position_pct_adv": 0.03,
                "shared_trigger_drawdown_pct": 0.04,
                "correlated_exit_probability": 0.8,
            },
            "time_scale": {"session_ticks": 80},
            "exit_horizon_days": 1.5,
        }
    )
    assert cfg.peer_crowding is not None
    assert cfg.peer_crowding.peer_fund_count == 4
    assert cfg.time_scale.session_ticks == 80
    assert cfg.time_scale.effective_exit_horizon_ticks() == 120


def test_scenario_defaults_includes_population_size() -> None:
    body = TestClient(app).get("/api/scenario/defaults").json()
    assert body["population_size"] > 0


def test_instrument_endpoint_reports_source() -> None:
    client = TestClient(app)
    # A curated ticker returns its preset values, so the panel matches the run.
    cvna = client.get("/api/instrument", params={"symbol": "CVNA"}).json()
    assert cvna["symbol"] == "CVNA"
    assert cvna["source"] == "curated"
    assert cvna["reference_price"] > 0 and cvna["adv"] > 0
    # A non-preset symbol falls through to the MCP, honestly labelled offline.
    other = client.get("/api/instrument", params={"symbol": "ACME"}).json()
    assert other["reference_price"] > 0 and other["adv"] > 0
    assert other["realized_vol_daily"] >= 0
    assert other["source"] in {"alphavantage", "synthetic"}
