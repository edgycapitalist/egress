"""Counterfactual attribution diagnostics for representative runs.

The main engine emits heuristic impact estimates while it runs. This module is
slower and optional: it reruns a selected representative scenario with one driver
removed at a time, then reports approximate final-price deltas. It is deliberately
kept out of the per-seed ensemble hot path.
"""

from __future__ import annotations

from engine.core import Engine
from engine.schema import CounterfactualAttribution, Metrics, RunConfig


def _final_price_move_bps(config: RunConfig, metrics: Metrics) -> float:
    reference = config.instrument.reference_price
    if reference <= 0:
        return 0.0
    return (reference - metrics.final_price) / reference * 1e4


def _run_ablation(
    config: RunConfig,
    *,
    without_peer_cohorts: bool = False,
    without_exogenous_shocks: bool = False,
    without_exit_trader: bool = False,
) -> Metrics:
    updates: dict[str, object] = {
        "run_id": f"{config.run_id}-counterfactual",
        "baseline_mode": True,
    }
    if without_peer_cohorts:
        updates["peer_crowding"] = None
    if without_exogenous_shocks:
        updates["shock_schedule"] = []
    ablated = config.model_copy(deep=True, update=updates)
    return Engine(ablated, enable_exit_trader=not without_exit_trader).run_baseline()


def estimate_counterfactual_attribution(
    config: RunConfig, full_metrics: Metrics
) -> CounterfactualAttribution:
    """Run paired ablations and return approximate final-price deltas.

    Positive deltas mean the removed driver made the full run's final-price decline
    worse. Negative deltas are possible because the book path is interactive; the
    analyst and UI should describe these as estimates, not exact causes.
    """
    no_peer = _run_ablation(config, without_peer_cohorts=True)
    no_exogenous = _run_ablation(config, without_exogenous_shocks=True)
    no_exit = _run_ablation(config, without_exit_trader=True)

    full_move = _final_price_move_bps(config, full_metrics)
    peer_delta = full_move - _final_price_move_bps(config, no_peer)
    shock_delta = full_move - _final_price_move_bps(config, no_exogenous)
    own_delta = full_move - _final_price_move_bps(config, no_exit)
    residual = full_move - shock_delta - peer_delta - own_delta

    return CounterfactualAttribution(
        full_run_bps=round(full_move, 4),
        exogenous_shock_bps=round(shock_delta, 4),
        peer_cascade_bps=round(peer_delta, 4),
        own_exit_bps=round(own_delta, 4),
        residual_market_behavior_bps=round(residual, 4),
        full_run_final_price=full_metrics.final_price,
        no_peer_final_price=no_peer.final_price,
        no_exogenous_final_price=no_exogenous.final_price,
        no_exit_final_price=no_exit.final_price,
    )
