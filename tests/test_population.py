"""Population and exit-trader behaviour tests."""

import numpy as np
from engine.population.peers import PeerCohorts
from engine.population.population import MarketView, Population
from engine.population.trader import ExitTrader
from engine.schema import ExitSpeed, Position, RunConfig, Stance


def _stances(**overrides) -> dict:
    base = {
        t: Stance(aggressiveness=0.7, sell_threshold_pct=0.05, participation=0.8)
        for t in (
            "forced_seller", "panic_seller", "trend_follower",
            "bargain_hunter", "market_maker", "holder",
        )
    }
    base.update(overrides)
    return base


def _config(seed=1, n=2000):
    from engine.schema import CrowdingMix, RunConfig

    return RunConfig(
        run_id="t",
        seed=seed,
        instrument=dict(
            symbol="ACME", reference_price=100.0, tick_size=0.01,
            adv=5_000_000, free_float=120_000_000, halt_tier=1,
        ),
        position=dict(side="sell", quantity=100_000, arrival_price=100.0),
        exit_speed=dict(mode="participation", participation_rate=0.1),
        crowding_mix=CrowdingMix(
            forced_seller=0.2, panic_seller=0.2, trend_follower=0.2,
            bargain_hunter=0.15, market_maker=0.15, holder=0.1,
        ),
        population_size=n,
        halt_rule=dict(band_pct=0.1, window_ticks=5, pause_ticks=10),
        max_ticks=200,
        ticks_per_window=10,
    )


def test_population_counts_match_mix() -> None:
    cfg = _config()
    pop = Population(cfg, np.random.default_rng(cfg.seed))
    assert sum(arr.size for arr in pop.idx.values()) == cfg.population_size


def test_calm_market_no_forced_selling_stressed_triggers_it() -> None:
    cfg = _config()
    rng = np.random.default_rng(0)
    pop = Population(cfg, rng)
    calm = MarketView(ref_price=100.0, last_price=100.0, recent_return=0.0, stress=0.0, tick=0)
    _, aggr, actions = pop.step(calm, _stances(), rng)
    assert actions["forced_seller"] == 0

    crash = MarketView(ref_price=100.0, last_price=85.0, recent_return=-0.1, stress=0.8, tick=1)
    _, aggr2, actions2 = pop.step(crash, _stances(), rng)
    assert actions2["forced_seller"] > 0
    assert any(o.investor_type == "forced_seller" and o.side == "sell" for o in aggr2)


def test_market_makers_withdraw_under_stress() -> None:
    cfg = _config()
    rng = np.random.default_rng(0)
    pop = Population(cfg, rng)
    calm = MarketView(ref_price=100.0, last_price=100.0, recent_return=0.0, stress=0.0, tick=0)
    liq_calm, _, _ = pop.step(calm, _stances(), rng)
    maker_bids_calm = [o for o in liq_calm if o.investor_type == "market_maker" and o.side == "buy"]

    pop2 = Population(cfg, np.random.default_rng(0))
    stressed = MarketView(ref_price=100.0, last_price=100.0, recent_return=0.0, stress=0.95, tick=0)
    liq_stress, _, _ = pop2.step(stressed, _stances(), np.random.default_rng(0))
    maker_bids_stress = [
        o for o in liq_stress if o.investor_type == "market_maker" and o.side == "buy"
    ]
    calm_size = sum(o.size for o in maker_bids_calm)
    stress_size = sum(o.size for o in maker_bids_stress)
    assert stress_size < calm_size


def test_forced_sellers_fire_once() -> None:
    cfg = _config()
    rng = np.random.default_rng(0)
    pop = Population(cfg, rng)
    crash = MarketView(ref_price=100.0, last_price=80.0, recent_return=-0.2, stress=0.5, tick=1)
    _, _, a1 = pop.step(crash, _stances(), rng)
    _, _, a2 = pop.step(crash, _stances(), rng)
    assert a1["forced_seller"] > 0
    assert a2["forced_seller"] == 0  # already acted, one-shot


def test_peer_cohorts_wait_for_shared_trigger_then_liquidate() -> None:
    data = _config().model_dump()
    data["peer_crowding"] = {
        "case": "high",
        "peer_fund_count": 6,
        "overlap_pct": 0.8,
        "avg_peer_position_pct_adv": 0.02,
        "shared_trigger_drawdown_pct": 0.03,
        "correlated_exit_probability": 1.0,
        "leverage_sensitivity": 0.4,
        "redemption_pressure": 0.5,
        "etf_flow_pressure": 0.1,
    }
    cfg = RunConfig(**data)
    rng = np.random.default_rng(cfg.seed)
    peers = PeerCohorts(cfg, rng)

    calm = MarketView(ref_price=100.0, last_price=100.0, recent_return=0.0, stress=0.0, tick=0)
    intents, actions = peers.step(calm, rng)
    assert intents == []
    assert actions.triggered_funds == 0

    crash = MarketView(ref_price=100.0, last_price=85.0, recent_return=-0.15, stress=0.8, tick=1)
    intents, actions = peers.step(crash, rng)
    assert actions.triggered_funds == cfg.peer_crowding.peer_fund_count
    assert actions.liquidating_funds == cfg.peer_crowding.peer_fund_count
    assert actions.shares_sold == sum(intent.size for intent in intents)
    assert actions.shares_remaining < peers.snapshot().total_initial_shares
    assert all(
        intent.side == "sell" and intent.investor_type == "peer_cohort" for intent in intents
    )


def test_exit_trader_twap_paces_and_records() -> None:
    trader = ExitTrader(
        Position(side="sell", quantity=1000, arrival_price=100.0),
        ExitSpeed(mode="twap", horizon_ticks=10),
    )
    assert trader.child_size(recent_volume=0) == 100
    trader.record(price=99.0, size=100, tick=0)
    assert trader.filled == 100 and trader.remaining == 900
    assert trader.vwap == 99.0


def test_exit_trader_completes() -> None:
    trader = ExitTrader(
        Position(side="sell", quantity=200, arrival_price=100.0),
        ExitSpeed(mode="immediate"),
    )
    assert trader.child_size(0) == 200
    trader.record(98.0, 200, tick=5)
    assert trader.remaining == 0
    assert trader.completed_tick == 5
    assert trader.child_size(0) == 0
