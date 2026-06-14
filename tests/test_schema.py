"""Schema/contract tests — validation rules from docs/contracts.md §1."""

import pytest
from engine.schema import (
    INVESTOR_TYPES,
    STANCE_KEYS,
    CrowdingMix,
    EnsembleCaseSummary,
    EnsembleResult,
    EvidenceSummary,
    ExitSpeed,
    ImpactAttribution,
    MetricBand,
    Metrics,
    PeerCrowdingProfile,
    RunConfig,
    TickEvent,
)


def _valid_config_kwargs() -> dict:
    return dict(
        run_id="t-1",
        seed=1,
        instrument=dict(
            symbol="ACME", reference_price=100.0, tick_size=0.01,
            adv=5_000_000, free_float=120_000_000, halt_tier=1,
        ),
        position=dict(side="sell", quantity=250_000, arrival_price=100.0),
        exit_speed=dict(mode="participation", participation_rate=0.1),
        crowding_mix=dict(
            forced_seller=0.15, panic_seller=0.20, trend_follower=0.20,
            bargain_hunter=0.15, market_maker=0.10, holder=0.20,
        ),
        population_size=2000,
        shock_schedule=[dict(tick=0, kind="news", severity=0.8, note="downgrade")],
        halt_rule=dict(band_pct=0.10, window_ticks=5, pause_ticks=10),
        max_ticks=300,
        ticks_per_window=10,
        baseline_mode=True,
    )


def test_stance_keys_match_types() -> None:
    assert set(STANCE_KEYS) == set(INVESTOR_TYPES)
    assert STANCE_KEYS["forced_seller"] == "forced_seller_stance"


def test_valid_config_parses() -> None:
    cfg = RunConfig(**_valid_config_kwargs())
    assert cfg.position.quantity == 250_000
    assert cfg.exit_speed.mode == "participation"
    assert cfg.scenario_mode == "historical_saved"
    assert cfg.time_scale.session_ticks == 100
    assert cfg.time_scale.session_hours() == 6.5
    assert cfg.peer_crowding is None
    assert cfg.evidence_summary is None


def test_run_config_accepts_peer_crowding_and_evidence() -> None:
    kwargs = _valid_config_kwargs()
    kwargs["scenario_mode"] = "sec_evidence"
    kwargs["peer_crowding"] = dict(
        case="base",
        peer_fund_count=12,
        overlap_pct=0.42,
        avg_peer_position_pct_adv=0.08,
        shared_trigger_drawdown_pct=0.12,
        correlated_exit_probability=0.7,
        leverage_sensitivity=0.5,
        redemption_pressure=0.4,
        etf_flow_pressure=0.2,
        evidence_source="sec_edgar",
        confidence="medium",
        notes="13F-derived overlap proxy",
    )
    kwargs["evidence_summary"] = dict(
        summary="SEC-derived peer-crowding assumptions.",
        items=[
            dict(
                field="peer_crowding",
                source="sec_edgar",
                confidence="medium",
                label="13F snapshot",
            )
        ],
    )
    cfg = RunConfig(**kwargs)
    assert cfg.peer_crowding is not None
    assert cfg.peer_crowding.peer_fund_count == 12
    assert cfg.evidence_summary is not None
    assert cfg.evidence_summary.items[0].source == "sec_edgar"


def test_peer_crowding_bounds_are_enforced() -> None:
    with pytest.raises(ValueError):
        PeerCrowdingProfile(overlap_pct=1.2)
    with pytest.raises(ValueError):
        PeerCrowdingProfile(correlated_exit_probability=-0.1)


def test_old_tick_and_metrics_records_default_new_fields() -> None:
    tick = TickEvent(
        tick=1,
        last_price=99.0,
        best_bid=98.9,
        best_ask=99.1,
        depth_bid=100,
        depth_ask=120,
        fills=[],
        filled_this_tick=0,
        cumulative_filled=0,
        vwap_sold=None,
        actions_by_type={t: 0 for t in INVESTOR_TYPES},
        halted=False,
        halt_started=False,
    )
    metrics = Metrics(
        run_id="t-1",
        fill_rate=0.0,
        filled_qty=0,
        stuck_qty=100,
        pct_stuck=1.0,
        implementation_shortfall_bps=0.0,
        slippage_bps=0.0,
        vwap_sold=None,
        arrival_price=100.0,
        final_price=99.0,
        max_drawdown_pct=0.01,
        time_to_exit_ticks=None,
        halt_triggered=False,
        halt_count=0,
        ticks_run=1,
    )
    assert tick.peer_actions.triggered_funds == 0
    assert tick.impact_attribution.total_bps == 0.0
    assert metrics.impact_attribution == ImpactAttribution()
    assert metrics.ensemble_case is None


def test_ensemble_result_serializes_summary_contract() -> None:
    metrics = Metrics(
        run_id="t-1",
        fill_rate=0.7,
        filled_qty=70,
        stuck_qty=30,
        pct_stuck=0.3,
        implementation_shortfall_bps=120.0,
        slippage_bps=110.0,
        vwap_sold=98.8,
        arrival_price=100.0,
        final_price=97.0,
        max_drawdown_pct=0.12,
        time_to_exit_ticks=12,
        halt_triggered=False,
        halt_count=0,
        ticks_run=20,
        ensemble_case="base",
        ensemble_seed=42,
    )
    result = EnsembleResult(
        run_id="ensemble-1",
        cases=[EnsembleCaseSummary(case="base", seeds=[42], metrics=metrics)],
        bands={"fill_rate": MetricBand(low=0.6, median=0.7, high=0.8)},
        evidence_summary=EvidenceSummary(summary="Assumption-led ensemble."),
    )
    payload = result.model_dump()
    assert payload["type"] == "ensemble"
    assert payload["cases"][0]["metrics"]["ensemble_case"] == "base"
    assert payload["bands"]["fill_rate"]["median"] == 0.7


def test_crowding_mix_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        CrowdingMix(
            forced_seller=0.5, panic_seller=0.5, trend_follower=0.2,
            bargain_hunter=0.0, market_maker=0.0, holder=0.0,
        )


def test_crowding_counts_sum_to_population() -> None:
    mix = CrowdingMix(
        forced_seller=0.15, panic_seller=0.20, trend_follower=0.20,
        bargain_hunter=0.15, market_maker=0.10, holder=0.20,
    )
    counts = mix.counts(2000)
    assert sum(counts.values()) == 2000
    assert set(counts) == set(INVESTOR_TYPES)


def test_exit_speed_requires_mode_field() -> None:
    with pytest.raises(ValueError, match="participation_rate"):
        ExitSpeed(mode="participation")
    with pytest.raises(ValueError, match="horizon_ticks"):
        ExitSpeed(mode="twap")


def test_shock_outside_horizon_rejected() -> None:
    kwargs = _valid_config_kwargs()
    kwargs["shock_schedule"] = [dict(tick=999, kind="price", severity=0.5)]
    with pytest.raises(ValueError, match="outside"):
        RunConfig(**kwargs)
