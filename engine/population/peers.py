"""Deterministic peer-fund cohorts for crowded-exit overlap risk.

The regular ``Population`` models anonymous market participants by behavioural
type. Peer cohorts are different: they represent similar institutional holders
that own the same crowded trade and may liquidate together when a shared drawdown
trigger fires. They are deterministic, seeded, and LLM-free.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.population.population import MarketView, OrderIntent
from engine.schema import PeerActionCounts, RunConfig


@dataclass(frozen=True)
class PeerCohortSnapshot:
    """Small test/debug view of the cohort state."""

    fund_count: int
    total_initial_shares: int
    total_remaining_shares: int
    triggered_funds: int


class PeerCohorts:
    """Materialise ``PeerCrowdingProfile`` into deterministic fund cohorts."""

    def __init__(self, config: RunConfig, rng: np.random.Generator) -> None:
        profile = config.peer_crowding
        self.profile = profile
        self._active = bool(
            profile
            and profile.peer_fund_count > 0
            and profile.overlap_pct > 0
            and profile.avg_peer_position_pct_adv > 0
            and profile.correlated_exit_probability > 0
        )
        if not self._active or profile is None:
            self._fund_count = 0
            self._initial = np.zeros(0, dtype=np.int64)
            self._remaining = np.zeros(0, dtype=np.int64)
            self._trigger = np.zeros(0, dtype=np.float64)
            self._triggered = np.zeros(0, dtype=bool)
            return

        self._fund_count = profile.peer_fund_count
        avg_overlap_shares = (
            config.instrument.adv * profile.avg_peer_position_pct_adv * profile.overlap_pct
        )
        # Keep cohort sizes staggered but bounded; the average remains anchored to
        # the profile's %ADV assumption, and total peer holdings cannot exceed float.
        size_factor = rng.lognormal(mean=0.0, sigma=0.35, size=self._fund_count)
        initial = np.maximum(1, np.rint(avg_overlap_shares * size_factor)).astype(np.int64)
        float_cap = max(1, int(config.instrument.free_float * profile.overlap_pct))
        total = int(initial.sum())
        if total > float_cap:
            initial = np.maximum(1, np.floor(initial * (float_cap / total))).astype(np.int64)

        stress_relief = (
            0.45 * profile.leverage_sensitivity
            + 0.25 * profile.redemption_pressure
            + 0.15 * profile.etf_flow_pressure
        )
        trigger_base = max(0.01, profile.shared_trigger_drawdown_pct)
        trigger_base *= max(0.25, 1.0 - stress_relief)
        trigger_jitter = 0.8 + 0.4 * rng.random(self._fund_count)

        self._initial = initial
        self._remaining = initial.copy()
        self._trigger = trigger_base * trigger_jitter
        self._triggered = np.zeros(self._fund_count, dtype=bool)

    @property
    def active(self) -> bool:
        return self._active

    def snapshot(self) -> PeerCohortSnapshot:
        return PeerCohortSnapshot(
            fund_count=self._fund_count,
            total_initial_shares=int(self._initial.sum()),
            total_remaining_shares=int(self._remaining.sum()),
            triggered_funds=int(self._triggered.sum()),
        )

    def step(
        self, view: MarketView, rng: np.random.Generator
    ) -> tuple[list[OrderIntent], PeerActionCounts]:
        """Return peer market-sell intents and action counts for one tick."""
        if not self._active or self.profile is None:
            return [], PeerActionCounts()

        crossed = (~self._triggered) & (self._remaining > 0) & (view.drop >= self._trigger)
        newly_triggered = np.zeros(self._fund_count, dtype=bool)
        if crossed.any():
            # One common draw creates the correlated exit behaviour. If the shared
            # event does not happen, a small idiosyncratic tail can still fire.
            pressure = (
                0.20 * view.stress
                + 0.15 * self.profile.redemption_pressure
                + 0.10 * self.profile.etf_flow_pressure
            )
            common_probability = float(
                np.clip(self.profile.correlated_exit_probability + pressure, 0.0, 1.0)
            )
            if rng.random() < common_probability:
                newly_triggered = crossed
            else:
                tail_probability = min(0.10, common_probability * 0.20)
                newly_triggered = crossed & (rng.random(self._fund_count) < tail_probability)
            self._triggered |= newly_triggered

        liquidating = self._triggered & (self._remaining > 0)
        if not liquidating.any():
            return [], PeerActionCounts(shares_remaining=int(self._remaining.sum()))

        urgency = np.clip(
            0.12
            + 0.28 * view.stress
            + 0.18 * self.profile.leverage_sensitivity
            + 0.16 * self.profile.redemption_pressure
            + 0.10 * self.profile.etf_flow_pressure,
            0.05,
            0.75,
        )
        idx = np.where(liquidating)[0]
        sizes = np.maximum(1, np.ceil(self._initial[idx] * urgency)).astype(np.int64)
        sizes = np.minimum(sizes, self._remaining[idx])

        intents: list[OrderIntent] = []
        shares_sold = 0
        liquidating_funds = 0
        for fund_idx, size in zip(idx, sizes, strict=True):
            size_int = int(size)
            if size_int <= 0:
                continue
            intents.append(OrderIntent("sell", size_int, None, "peer_cohort"))
            self._remaining[fund_idx] -= size_int
            shares_sold += size_int
            liquidating_funds += 1

        return intents, PeerActionCounts(
            triggered_funds=int(newly_triggered.sum()),
            liquidating_funds=liquidating_funds,
            shares_sold=shares_sold,
            shares_remaining=int(self._remaining.sum()),
        )
