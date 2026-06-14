"""Deterministic ensemble runner tests."""

from pathlib import Path

import pytest
from agents.orchestrator.driver import run_baseline_ensemble
from engine.ensemble import peer_crowding_cases, run_ensemble
from engine.replay.recorder import load_replay
from engine.scenarios import flagship_scenario
from engine.schema import RunConfig


def _fast_config(run_id: str = "ensemble-test") -> RunConfig:
    data = flagship_scenario().model_dump()
    data["run_id"] = run_id
    data["population_size"] = 700
    data["max_ticks"] = 50
    data["ticks_per_window"] = 10
    data["position"]["quantity"] = 80_000
    data["shock_schedule"] = [
        {"tick": 0, "kind": "price", "severity": 0.55, "note": "opening stress gap"},
        {"tick": 10, "kind": "news", "severity": 0.65, "note": "redemption pressure"},
    ]
    data["time_scale"]["exit_horizon_ticks"] = 35
    return RunConfig(**data)


def test_peer_crowding_cases_have_monotone_risk() -> None:
    cases = peer_crowding_cases(None)
    assert set(cases) == {"low", "base", "high"}
    assert (
        cases["low"].peer_fund_count
        < cases["base"].peer_fund_count
        < cases["high"].peer_fund_count
    )
    assert cases["low"].overlap_pct < cases["base"].overlap_pct < cases["high"].overlap_pct
    assert (
        cases["low"].shared_trigger_drawdown_pct
        > cases["base"].shared_trigger_drawdown_pct
        > cases["high"].shared_trigger_drawdown_pct
    )


def test_run_ensemble_returns_case_summaries_bands_and_replays(tmp_path: Path) -> None:
    result = run_ensemble(_fast_config(), replay_dir=tmp_path, seeds=[11, 12])

    assert result.type == "ensemble"
    assert [case.case for case in result.cases] == ["low", "base", "high"]
    assert set(result.bands) >= {
        "fill_rate",
        "pct_stuck",
        "slippage_bps",
        "max_drawdown_pct",
        "halt_probability",
    }
    assert result.representative_case == "base"
    assert result.representative_replay_ref is not None
    assert Path(result.representative_replay_ref).exists()

    for summary in result.cases:
        assert summary.seeds == [11, 12]
        assert summary.peer_crowding is not None
        assert summary.peer_crowding.case == summary.case
        assert summary.representative_replay_ref is not None
        assert Path(summary.representative_replay_ref).exists()
        assert summary.metrics.ensemble_case == summary.case
        assert summary.metrics.ensemble_seed in summary.seeds

    _meta, _ticks, metrics = load_replay(result.representative_replay_ref)
    assert metrics is not None
    assert metrics.ensemble_case == "base"
    assert metrics.ensemble_seed in [11, 12]


@pytest.mark.asyncio
async def test_driver_returns_gateway_ready_ensemble(tmp_path: Path) -> None:
    result = await run_baseline_ensemble(
        _fast_config("driver-ensemble"), seeds=[21], replay_dir=str(tmp_path)
    )

    assert result["error"] is None
    assert result["ensemble_result"]["type"] == "ensemble"
    assert result["representative_replay_ref"]
    assert Path(result["representative_replay_ref"]).exists()
