"""NDJSON record/replay round-trip tests."""

from pathlib import Path

from engine.replay.recorder import Recorder, load_replay
from engine.schema import SCHEMA_VERSION, Metrics, RunConfig, TickEvent


def _config() -> RunConfig:
    return RunConfig(
        run_id="r-1",
        instrument=dict(
            symbol="ACME", reference_price=100.0, tick_size=0.01,
            adv=5_000_000, free_float=120_000_000, halt_tier=1,
        ),
        position=dict(side="sell", quantity=1000, arrival_price=100.0),
        exit_speed=dict(mode="twap", horizon_ticks=10),
        crowding_mix=dict(
            forced_seller=0.2, panic_seller=0.2, trend_follower=0.2,
            bargain_hunter=0.15, market_maker=0.15, holder=0.1,
        ),
        population_size=100,
        halt_rule=dict(band_pct=0.1, window_ticks=5, pause_ticks=10),
        max_ticks=100,
        ticks_per_window=10,
    )


def _tick(i: int) -> TickEvent:
    return TickEvent(
        tick=i, last_price=100.0 - i, best_bid=99.0 - i, best_ask=100.5 - i,
        depth_bid=1000, depth_ask=900, fills=[], filled_this_tick=10,
        cumulative_filled=10 * i, vwap_sold=99.0,
        actions_by_type={"forced_seller": i, "panic_seller": 0, "trend_follower": 0,
                         "bargain_hunter": 0, "market_maker": 5, "holder": 0},
        halted=False, halt_started=False,
    )


def test_round_trip(tmp_path) -> None:
    path = tmp_path / "run.ndjson"
    cfg = _config()
    metrics = Metrics(
        run_id="r-1", fill_rate=0.5, filled_qty=500, stuck_qty=500, pct_stuck=0.5,
        implementation_shortfall_bps=120.0, slippage_bps=110.0, vwap_sold=98.8,
        arrival_price=100.0, final_price=97.0, max_drawdown_pct=0.12,
        time_to_exit_ticks=None, halt_triggered=False, halt_count=0, ticks_run=3,
    )
    with Recorder(path) as rec:
        rec.write_meta(cfg)
        for i in range(3):
            rec.write_tick(_tick(i))
        rec.write_metrics(metrics)

    meta, ticks, loaded_metrics = load_replay(path)
    assert meta.schema_version == SCHEMA_VERSION
    assert meta.config.run_id == "r-1"
    assert len(ticks) == 3
    assert ticks[1].last_price == 99.0
    assert loaded_metrics.filled_qty == 500


def test_committed_replay_loads_with_phase4_persistent_book() -> None:
    meta, ticks, metrics = load_replay(Path("docs/replays/cvna.ndjson"))
    assert meta.schema_version == SCHEMA_VERSION
    assert meta.config.scenario_mode == "historical_saved"
    assert meta.config.time_scale.session_ticks == 100
    assert meta.config.book_persistence.enabled is True
    assert meta.config.peer_crowding is not None
    assert ticks[0].peer_actions.triggered_funds >= 0
    assert ticks[0].impact_attribution.total_bps >= 0.0
    assert metrics is not None
    assert metrics.impact_attribution.total_bps >= 0.0


def test_line_count_and_order(tmp_path) -> None:
    path = tmp_path / "run.ndjson"
    with Recorder(path) as rec:
        rec.write_meta(_config())
        rec.write_tick(_tick(0))
        rec.write_metrics(
            Metrics(
                run_id="r-1", fill_rate=0.0, filled_qty=0, stuck_qty=1000, pct_stuck=1.0,
                implementation_shortfall_bps=0.0, slippage_bps=0.0, vwap_sold=None,
                arrival_price=100.0, final_price=100.0, max_drawdown_pct=0.0,
                time_to_exit_ticks=None, halt_triggered=False, halt_count=0, ticks_run=1,
            )
        )
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith('{"type":"meta"')
    assert lines[-1].startswith('{"type":"metrics"')
