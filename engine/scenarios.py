"""Built-in scenarios for demos and tests.

The flagship scenario is a crowded mid-cap hit by a rating downgrade: a heavy mix
of forced, panic, and trend sellers, thin maker support, and two follow-on shocks.
It is tuned to produce a visible cascade — the price drains, a halt can trip, and
part of the position is left stuck.
"""

from __future__ import annotations

from engine.schema import RunConfig


def flagship_scenario(seed: int = 42) -> RunConfig:
    return RunConfig(
        run_id=f"flagship-{seed}",
        seed=seed,
        instrument=dict(
            symbol="ACME",
            reference_price=100.0,
            tick_size=0.01,
            adv=5_000_000,
            free_float=120_000_000,
            halt_tier=1,
        ),
        position=dict(side="sell", quantity=250_000, arrival_price=100.0),
        exit_speed=dict(mode="participation", participation_rate=0.12),
        crowding_mix=dict(
            forced_seller=0.18,
            panic_seller=0.22,
            trend_follower=0.20,
            bargain_hunter=0.15,
            market_maker=0.10,
            holder=0.15,
        ),
        population_size=5000,
        shock_schedule=[
            dict(tick=0, kind="news", severity=0.8, note="rating downgrade"),
            dict(tick=30, kind="price", severity=0.5, note="gap down on volume"),
            dict(tick=60, kind="news", severity=0.6, note="forced-seller headlines"),
        ],
        halt_rule=dict(band_pct=0.10, window_ticks=5, pause_ticks=10),
        max_ticks=300,
        ticks_per_window=10,
        baseline_mode=True,
    )
