"""End-to-end engine integration tests."""

from engine.core import Engine
from engine.replay.recorder import Recorder, load_replay
from engine.schema import RunConfig
from engine.scenarios import flagship_scenario


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
