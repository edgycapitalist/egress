"""Single-stock volatility halt — a fixed, known constraint (AGENTS.md §5).

If the price moves past the band within a short window, trading pauses for a set
number of ticks. A position caught on the wrong side of a halt is one concrete
way the exit closes. This is enforced by the engine, not tuned by the user.
"""

from __future__ import annotations

from collections import deque

from engine.schema import HaltRule


class HaltController:
    def __init__(self, rule: HaltRule) -> None:
        self.band_pct = rule.band_pct
        self.window_ticks = rule.window_ticks
        self.pause_ticks = rule.pause_ticks
        self.halt_count = 0
        self._pause_remaining = 0
        self._prices: deque[float] = deque(maxlen=rule.window_ticks + 1)

    @property
    def halted(self) -> bool:
        return self._pause_remaining > 0

    def update(self, price: float, ref_price: float) -> tuple[bool, bool]:
        """Advance one tick. Returns (halted_now, halt_started_this_tick)."""
        self._prices.append(price)

        if self._pause_remaining > 0:
            self._pause_remaining -= 1
            return True, False

        if len(self._prices) == self._prices.maxlen:
            past = self._prices[0]
            move = abs(price - past) / ref_price
            if move >= self.band_pct:
                self.halt_count += 1
                self._pause_remaining = self.pause_ticks - 1
                return True, True
        return False, False
