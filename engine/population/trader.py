"""The exiting trader — the user's position being unwound.

This is the agent the run is *about*: it sells ``position.quantity`` over the run
according to ``exit_speed``, and its fills are what the metrics measure (fill
rate, slippage, how much stays stuck). It is deterministic and separate from the
surrounding crowd, though its sell flow adds to the pressure like any other.
"""

from __future__ import annotations

import math

from engine.schema import ExitSpeed, Position


class ExitTrader:
    def __init__(self, position: Position, exit_speed: ExitSpeed) -> None:
        self.quantity = position.quantity
        self.exit_speed = exit_speed
        self.remaining = position.quantity
        self.filled = 0
        self._notional = 0.0
        self.completed_tick: int | None = None

    @property
    def vwap(self) -> float | None:
        return self._notional / self.filled if self.filled else None

    def child_size(self, recent_volume: int) -> int:
        """Shares to offer this tick, before matching, per the exit schedule."""
        if self.remaining <= 0:
            return 0
        mode = self.exit_speed.mode
        if mode == "immediate":
            target = self.remaining
        elif mode == "twap":
            per_tick = math.ceil(self.quantity / self.exit_speed.horizon_ticks)
            target = per_tick
        else:  # participation
            rate = self.exit_speed.participation_rate or 0.0
            # Before any volume prints, seed with a small slice so the unwind starts.
            base = recent_volume if recent_volume > 0 else self.quantity // 50
            target = int(rate * base)
            target = max(target, 1)
        return int(min(target, self.remaining))

    def record(self, price: float, size: int, tick: int) -> None:
        if size <= 0:
            return
        self.filled += size
        self.remaining -= size
        self._notional += price * size
        if self.remaining <= 0 and self.completed_tick is None:
            self.completed_tick = tick
