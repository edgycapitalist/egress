"""Halt controller and metrics tests."""

from engine.halt import HaltController
from engine.metrics.metrics import _max_drawdown_pct, compute_metrics
from engine.population.trader import ExitTrader
from engine.schema import ExitSpeed, HaltRule, RunConfig


def test_halt_triggers_on_band_breach_and_pauses() -> None:
    ctl = HaltController(HaltRule(band_pct=0.10, window_ticks=3, pause_ticks=4))
    ref = 100.0
    # Gentle moves: no halt.
    for p in [100.0, 99.0, 98.5, 98.0]:
        halted, started = ctl.update(p, ref)
        assert not halted
    # A 10%+ move within the 3-tick window trips the halt.
    halted, started = ctl.update(88.0, ref)  # 98.0 -> 88.0 over window
    assert halted and started
    assert ctl.halt_count == 1
    # Pause holds for the remaining ticks.
    paused = [ctl.update(88.0, ref)[0] for _ in range(3)]
    assert all(paused)
    # Then trading resumes.
    assert ctl.update(88.0, ref) == (False, False)


def test_max_drawdown() -> None:
    assert _max_drawdown_pct([100, 110, 88, 95]) == round((110 - 88) / 110, 10)
    assert _max_drawdown_pct([100, 100, 100]) == 0.0


def _config() -> RunConfig:
    return RunConfig(
        run_id="m",
        instrument=dict(
            symbol="ACME", reference_price=100.0, tick_size=0.01,
            adv=5_000_000, free_float=120_000_000, halt_tier=1,
        ),
        position=dict(side="sell", quantity=1000, arrival_price=100.0),
        exit_speed=dict(mode="immediate"),
        crowding_mix=dict(
            forced_seller=0.2, panic_seller=0.2, trend_follower=0.2,
            bargain_hunter=0.15, market_maker=0.15, holder=0.1,
        ),
        population_size=100,
        halt_rule=dict(band_pct=0.1, window_ticks=5, pause_ticks=10),
        max_ticks=100,
        ticks_per_window=10,
    )


def test_metrics_partial_fill_leaves_stuck() -> None:
    cfg = _config()
    trader = ExitTrader(cfg.position, ExitSpeed(mode="immediate"))
    trader.record(price=95.0, size=600, tick=3)  # only 600 of 1000 sold
    metrics = compute_metrics(
        cfg, trader, price_path=[100.0, 95.0, 90.0], halt_count=1, ticks_run=50
    )
    assert metrics.filled_qty == 600
    assert metrics.stuck_qty == 400
    assert metrics.pct_stuck == 0.4
    assert metrics.fill_rate == 0.6
    assert metrics.vwap_sold == 95.0
    # Sold below arrival -> positive implementation shortfall.
    assert metrics.implementation_shortfall_bps > 0
    assert metrics.halt_triggered is True
    assert metrics.time_to_exit_ticks is None  # never fully exited
