"""Fast-live Gemini mode tests.

These never call Vertex AI. They monkeypatch the Scenario Author execution boundary
and verify the latency-control behavior around it.
"""

from __future__ import annotations

import asyncio

import pytest
from agents.orchestrator.driver import run_fast_live_ensemble
from engine.scenarios import flagship_scenario
from engine.schema import RunConfig


def _fast_config(run_id: str = "fast-live") -> RunConfig:
    data = flagship_scenario(seed=101).model_dump()
    data["run_id"] = run_id
    data["max_ticks"] = 80
    data["ticks_per_window"] = 10
    data["position"]["quantity"] = 120_000
    return RunConfig.model_validate(data)


@pytest.mark.asyncio
async def test_fast_live_uses_gemini_assumptions_once(monkeypatch, tmp_path) -> None:
    fallback = _fast_config("fallback-run")
    gemini_cfg = fallback.model_copy(
        deep=True,
        update={
            "run_id": "gemini-run",
            "position": fallback.position.model_copy(update={"quantity": 999_999}),
            "baseline_mode": False,
        },
    )

    async def fake_execute(*_args, **_kwargs):
        return {
            "run_id": "gemini-run",
            "scenario_config": gemini_cfg.model_dump(),
            "timing_report": {
                "version": 1,
                "events": [{"kind": "gemini_call", "name": "ScenarioAuthor"}],
                "summary": {
                    "agent_calls": 1,
                    "gemini_calls": 1,
                    "tool_calls": 1,
                    "engine_windows": 0,
                    "total_duration_ms": 5.0,
                },
            },
            "error": None,
        }

    monkeypatch.setattr("agents.orchestrator.driver.assert_vertex_config", lambda: {})
    monkeypatch.setattr("agents.orchestrator.driver._execute", fake_execute)

    result = await run_fast_live_ensemble(
        "stress this crowded exit",
        fallback_config=fallback,
        timeout_seconds=1,
        seeds=[101],
        replay_dir=str(tmp_path),
    )

    assert result["error"] is None
    assert result["fallback_reason"] is None
    assert result["ensemble_result"]["type"] == "ensemble"
    # Direct UI/product-accuracy levers remain deterministic; Gemini does not
    # get to silently change the position size.
    assert result["scenario_config"]["position"]["quantity"] == fallback.position.quantity
    assert result["timing_report"]["summary"]["gemini_calls"] == 1
    assert result["timing_report"]["summary"]["engine_windows"] > 0


@pytest.mark.asyncio
async def test_fast_live_timeout_falls_back_to_deterministic_assumptions(
    monkeypatch, tmp_path
) -> None:
    async def slow_execute(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return {"error": None}

    fallback = _fast_config("timeout-run")
    monkeypatch.setattr("agents.orchestrator.driver.assert_vertex_config", lambda: {})
    monkeypatch.setattr("agents.orchestrator.driver._execute", slow_execute)

    result = await run_fast_live_ensemble(
        "stress this crowded exit",
        fallback_config=fallback,
        timeout_seconds=0.001,
        seeds=[101],
        replay_dir=str(tmp_path),
    )

    assert result["error"] is None
    assert result["fallback_reason"] == "gemini_timeout"
    assert result["ensemble_result"]["type"] == "ensemble"
    assert result["scenario_config"]["run_id"] == fallback.run_id
    assert result["timing_report"]["summary"]["fallback_count"] == 1
    assert result["timing_report"]["summary"]["engine_windows"] > 0
