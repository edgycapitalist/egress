"""Schema/contract tests — validation rules from docs/contracts.md §1."""

import pytest
from engine.schema import (
    INVESTOR_TYPES,
    STANCE_KEYS,
    CrowdingMix,
    ExitSpeed,
    RunConfig,
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
