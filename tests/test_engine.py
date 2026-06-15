"""End-to-end engine integration tests."""

from engine.baseline import baseline_stances
from engine.core import Engine
from engine.replay.recorder import Recorder, load_replay
from engine.scenarios import flagship_scenario
from engine.schema import RunConfig


def test_flagship_runs_and_is_deterministic() -> None:
    m1 = Engine(flagship_scenario()).run_baseline()
    m2 = Engine(flagship_scenario()).run_baseline()
    assert m1.model_dump() == m2.model_dump()  # seeded => byte-identical


def test_flagship_produces_a_cascade() -> None:
    eng = Engine(flagship_scenario())
    m = eng.run_baseline()
    # Price fell materially from the reference.
    assert m.final_price < eng.ref_price * 0.9
    assert m.max_drawdown_pct > 0.1
    # The exit did not fully clear — part of the position is stuck.
    assert 0.0 < m.fill_rate < 1.0
    assert m.stuck_qty > 0
    # Accounting holds.
    assert m.filled_qty + m.stuck_qty == flagship_scenario().position.quantity
    # Selling below arrival shows up as positive implementation shortfall.
    assert m.implementation_shortfall_bps > 0


def test_deeper_liquidity_fills_better() -> None:
    """Sensitivity: fewer forced sellers + more makers + no shock => better exit."""
    base = flagship_scenario().model_dump()
    base["run_id"] = "calm"
    base["shock_schedule"] = []
    base["crowding_mix"] = dict(
        forced_seller=0.05, panic_seller=0.05, trend_follower=0.05,
        bargain_hunter=0.30, market_maker=0.35, holder=0.20,
    )
    calm = Engine(RunConfig(**base)).run_baseline()
    crowded = Engine(flagship_scenario()).run_baseline()
    assert calm.fill_rate > crowded.fill_rate


def test_run_records_valid_replay(tmp_path) -> None:
    path = tmp_path / "flagship.ndjson"
    with Recorder(path) as rec:
        metrics = Engine(flagship_scenario(), recorder=rec).run_baseline()

    meta, ticks, loaded = load_replay(path)
    assert meta.config.run_id == "flagship-42"
    assert len(ticks) == metrics.ticks_run
    assert loaded.model_dump() == metrics.model_dump()
    # Tick stream is contiguous from 0.
    assert [t.tick for t in ticks] == list(range(len(ticks)))
    # Cumulative fills are monotonic non-decreasing.
    cum = [t.cumulative_filled for t in ticks]
    assert all(b >= a for a, b in zip(cum, cum[1:], strict=False))


def test_time_scale_sets_natural_volume_and_exit_horizon() -> None:
    data = flagship_scenario().model_dump()
    data["run_id"] = "time-scale"
    data["exit_speed"] = {"mode": "twap", "horizon_ticks": 999}
    data["time_scale"] = {
        "tick_duration_seconds": 234.0,
        "session_ticks": 50,
        "exit_horizon_days": 2.0,
    }
    cfg = RunConfig(**data)

    eng = Engine(cfg)
    assert eng.trader.natural_volume == cfg.instrument.adv // 50
    assert eng.effective_max_ticks == 100
    assert eng.trader.exit_speed.horizon_ticks == 100
    assert eng.trader.child_size(0) == 2500


def _maker_only_config(**book_persistence) -> RunConfig:
    data = flagship_scenario().model_dump()
    data["run_id"] = "persistent-book"
    data["position"]["quantity"] = 10
    data["exit_speed"] = {"mode": "twap", "horizon_ticks": 100}
    data["crowding_mix"] = dict(
        forced_seller=0.0,
        panic_seller=0.0,
        trend_follower=0.0,
        bargain_hunter=0.0,
        market_maker=1.0,
        holder=0.0,
    )
    data["population_size"] = 20
    data["shock_schedule"] = []
    data["max_ticks"] = 5
    data["ticks_per_window"] = 1
    data["book_persistence"] = {
        "enabled": True,
        "resting_ttl": 20,
        "base_cancel_rate": 0.0,
        "stress_cancel_multiplier": 0.0,
        "maker_replenish_rate": 1.0,
        "max_order_age": 80,
        **book_persistence,
    }
    return RunConfig(**data)


def test_engine_persists_resting_orders_across_ticks() -> None:
    eng = Engine(_maker_only_config())
    eng.start()
    eng.advance(baseline_stances(0.0, 0.0, 0), 1)
    first_ids = {order.order_id for order in eng.book.resting_orders()}

    eng.advance(baseline_stances(0.0, 0.0, 1), 1)
    orders = {order.order_id: order for order in eng.book.resting_orders()}

    persisted = first_ids & set(orders)
    assert persisted
    assert any(orders[order_id].age >= 1 for order_id in persisted)


def test_engine_cancels_stale_resting_orders_before_replenishing() -> None:
    eng = Engine(_maker_only_config(resting_ttl=1, base_cancel_rate=1.0))
    eng.start()
    eng.advance(baseline_stances(0.0, 0.0, 0), 1)
    first_ids = {order.order_id for order in eng.book.resting_orders()}

    eng.advance(baseline_stances(0.0, 0.0, 1), 1)
    current_ids = {order.order_id for order in eng.book.resting_orders()}

    assert first_ids
    assert first_ids.isdisjoint(current_ids)


def test_halted_ticks_still_age_persistent_book() -> None:
    eng = Engine(_maker_only_config())
    eng.start()
    eng.advance(baseline_stances(0.0, 0.0, 0), 1)
    first_ids = {order.order_id for order in eng.book.resting_orders()}

    eng.halt._pause_remaining = 2
    eng.advance(baseline_stances(0.0, 0.0, 1), 1)
    orders = {order.order_id: order for order in eng.book.resting_orders()}

    assert first_ids
    assert any(orders[order_id].age >= 1 for order_id in first_ids & set(orders))


def test_legacy_fresh_auction_mode_remains_explicit() -> None:
    eng = Engine(_maker_only_config(enabled=False))
    eng.start()
    eng.advance(baseline_stances(0.0, 0.0, 0), 1)
    first_ids = {order.order_id for order in eng.book.resting_orders()}

    eng.advance(baseline_stances(0.0, 0.0, 1), 1)
    current_ids = {order.order_id for order in eng.book.resting_orders()}

    assert first_ids
    assert first_ids.isdisjoint(current_ids)


def test_peer_crowding_emits_peer_actions_from_engine_tick() -> None:
    data = flagship_scenario().model_dump()
    data["run_id"] = "peer-actions"
    data["shock_schedule"] = [
        {"tick": 0, "kind": "price", "severity": 0.8, "note": "gap through peer trigger"}
    ]
    data["peer_crowding"] = {
        "case": "high",
        "peer_fund_count": 8,
        "overlap_pct": 0.75,
        "avg_peer_position_pct_adv": 0.01,
        "shared_trigger_drawdown_pct": 0.02,
        "correlated_exit_probability": 1.0,
        "leverage_sensitivity": 0.5,
        "redemption_pressure": 0.5,
        "etf_flow_pressure": 0.2,
    }
    eng = Engine(RunConfig(**data))
    eng.start()
    _, events = eng.advance(baseline_stances(0.0, 0.0, 0), 1)

    assert events[0].peer_actions.triggered_funds == 8
    assert events[0].peer_actions.liquidating_funds == 8
    assert events[0].peer_actions.shares_sold > 0
    assert events[0].peer_actions.shares_remaining > 0


def test_tick_and_metrics_impact_attribution_are_populated() -> None:
    data = flagship_scenario().model_dump()
    data["run_id"] = "impact-attribution"
    data["shock_schedule"] = [
        {"tick": 0, "kind": "price", "severity": 0.8, "note": "opening gap"}
    ]
    data["time_scale"]["exit_horizon_ticks"] = 1
    eng = Engine(RunConfig(**data))
    eng.start()
    _, events = eng.advance(baseline_stances(0.0, 0.0, 0), 1)
    metrics = eng.finalize()

    impact = events[0].impact_attribution
    assert impact.exogenous_shock_bps > 0
    assert round(impact.total_bps, 4) == round(
        impact.exogenous_shock_bps
        + impact.endogenous_trading_bps
        + impact.liquidity_withdrawal_bps,
        4,
    )
    assert metrics.impact_attribution.exogenous_shock_bps == impact.exogenous_shock_bps
    assert metrics.impact_attribution.total_bps == impact.total_bps
