"""Run metrics — the decision aid (AGENTS.md §5, contract §3.3).

Computed from deterministic state only: the exit trader's fills, the price path,
and the halt count. No estimation, no model — these are exact summaries of what
the simulated unwind did.
"""

from __future__ import annotations

from engine.population.trader import ExitTrader
from engine.schema import Metrics, RunConfig


def _max_drawdown_pct(price_path: list[float]) -> float:
    peak = price_path[0] if price_path else 0.0
    worst = 0.0
    for p in price_path:
        peak = max(peak, p)
        if peak > 0:
            worst = max(worst, (peak - p) / peak)
    return worst


def compute_metrics(
    config: RunConfig,
    trader: ExitTrader,
    price_path: list[float],
    halt_count: int,
    ticks_run: int,
) -> Metrics:
    quantity = config.position.quantity
    arrival = config.position.arrival_price
    reference = config.instrument.reference_price
    filled = trader.filled
    stuck = quantity - filled
    vwap = trader.vwap

    if vwap is not None:
        is_bps = (arrival - vwap) / arrival * 1e4
        slip_bps = (reference - vwap) / reference * 1e4
    else:
        is_bps = 0.0
        slip_bps = 0.0

    final_price = price_path[-1] if price_path else reference

    return Metrics(
        run_id=config.run_id,
        fill_rate=filled / quantity if quantity else 0.0,
        filled_qty=filled,
        stuck_qty=stuck,
        pct_stuck=stuck / quantity if quantity else 0.0,
        implementation_shortfall_bps=round(is_bps, 2),
        slippage_bps=round(slip_bps, 2),
        vwap_sold=round(vwap, 4) if vwap is not None else None,
        arrival_price=arrival,
        final_price=round(final_price, 4),
        max_drawdown_pct=round(_max_drawdown_pct(price_path), 4),
        time_to_exit_ticks=trader.completed_tick,
        halt_triggered=halt_count > 0,
        halt_count=halt_count,
        ticks_run=ticks_run,
    )
