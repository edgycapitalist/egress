from __future__ import annotations

from pathlib import Path

import engine.service as engine_service
from engine.scenarios import flagship_scenario
from fastapi.testclient import TestClient


def test_engine_service_start_advance_metrics_replay(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(engine_service, "REPLAY_DIR", tmp_path)
    monkeypatch.setattr(engine_service, "_STORE", None)
    engine_service.RUNS.clear()

    config = flagship_scenario().model_copy(
        update={
            "run_id": "svc-smoke",
            "max_ticks": 3,
            "ticks_per_window": 1,
            "population_size": 100,
            "shock_schedule": [],
        },
        deep=True,
    )

    client = TestClient(engine_service.app)
    start = client.post("/runs", json={"config": config.model_dump()})
    assert start.status_code == 200
    assert start.json()["run_id"] == "svc-smoke"
    assert start.json()["state_backend"] == "memory"

    advance = client.post("/runs/svc-smoke/advance", json={"ticks": 2})
    assert advance.status_code == 200
    assert len(advance.json()["ticks"]) == 2
    assert advance.json()["market_state"]["tick"] == 2

    metrics = client.get("/runs/svc-smoke/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["metrics"]["run_id"] == "svc-smoke"

    replay = client.get("/runs/svc-smoke/replay")
    assert replay.status_code == 200
    assert '"type":"meta"' in replay.text
    assert '"type":"metrics"' in replay.text


def test_engine_service_requires_redis_when_deployed(monkeypatch) -> None:
    monkeypatch.setenv("EGRESS_DEPLOYED_MODE", "true")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(engine_service, "_STORE", None)
    engine_service.RUNS.clear()

    client = TestClient(engine_service.app)
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert "REDIS_URL" in body["error"]
