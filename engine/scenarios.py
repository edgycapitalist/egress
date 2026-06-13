"""Built-in scenarios for demos and tests.

The flagship scenario is modelled on a **real crowded-unwind episode**: Carvana
(CVNA) in late 2022, when bankruptcy fears and a creditor cooperation pact triggered
a violent sell-off in a name that was simultaneously a crowded long and a crowded
short. It captures exactly what Egress is about — a position that cannot get out
because everyone holding the same trade rushes the exit at once.

The ticker, reference price, average volume, and free float match Carvana over that
window, so when a live run resolves CVNA against the Market Data MCP (real Alpha
Vantage prices) and the News MCP (real NEWS_SENTIMENT headlines), the real data
applies. The crowding mix is heavy on forced/panic/trend sellers with thin
market-maker support, and the shocks track the episode's events; it is tuned to
produce a visible cascade — the price drains, a halt can trip, and part of the
position is left stuck. The deterministic engine still simulates the unwind itself;
the real feeds inform the agents' judgement, not the mechanics.
"""

from __future__ import annotations

from engine.schema import RunConfig


def flagship_scenario(seed: int = 42) -> RunConfig:
    return RunConfig(
        run_id=f"flagship-{seed}",
        seed=seed,
        instrument=dict(
            symbol="CVNA",  # Carvana Co. — the late-2022 crowded unwind
            reference_price=15.0,  # representative pre-collapse level, Nov–Dec 2022
            tick_size=0.01,
            adv=12_000_000,
            free_float=90_000_000,
            halt_tier=1,
            volatility=0.09,  # high daily realized vol of the late-2022 unwind (= reference)
        ),
        position=dict(side="sell", quantity=250_000, arrival_price=15.0),
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
            dict(
                tick=0,
                kind="news",
                severity=0.85,
                note="creditor cooperation pact stokes bankruptcy fears",
            ),
            dict(
                tick=30,
                kind="price",
                severity=0.5,
                note="shares gap down as solvency and liquidity fears mount",
            ),
            dict(
                tick=60,
                kind="news",
                severity=0.6,
                note="rating cut deepens; forced sellers and shorts pile on",
            ),
        ],
        halt_rule=dict(band_pct=0.10, window_ticks=5, pause_ticks=10),
        max_ticks=300,
        ticks_per_window=10,
        baseline_mode=True,
    )
