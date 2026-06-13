"""Statistical / Markov models for the population.

Two pieces the deterministic crowd needs:

* ``StressRegime`` — a two-state (calm / stressed) Markov chain that yields a
  continuous stress level in [0, 1]. Stress rises with the price drop and with
  news shocks; it makes panic sellers fire and market makers withdraw. This is
  the regime model referenced in AGENTS.md §5.
* ``staggered_thresholds`` — spread a type's trigger level across its population
  so agents do not all fire at once. The stagger is the cascade mechanism: one
  seller breaks, the price moves into the next agent's threshold, and so on.

Phase 1 keeps these self-contained and seeded. In Phase 2 the transition
probabilities and return statistics are calibrated to a real instrument's
history pulled over the Market Data MCP; the interface here does not change.
"""

from __future__ import annotations

import numpy as np


class StressRegime:
    """A seeded two-state Markov regime producing a continuous stress level."""

    def __init__(self, calm_to_stressed: float = 0.05, stressed_to_calm: float = 0.20) -> None:
        self.p_cs = calm_to_stressed
        self.p_sc = stressed_to_calm
        self.stressed = False
        self.level = 0.0

    def step(
        self,
        drop: float,
        shock_severity: float,
        rng: np.random.Generator,
        vol_gain: float = 1.0,
    ) -> float:
        """Advance one tick. ``drop`` is the fractional fall from reference price.

        ``vol_gain`` (the name's daily volatility relative to the reference level)
        calibrates how prone this instrument is to cascade: a calm, deep name
        (``vol_gain`` well below 1) resists flipping into stress and stays mild even
        when it does, so its market makers keep quoting and the exit stays open; a
        fragile name (``vol_gain`` above 1) ignites readily. At ``vol_gain == 1``
        every term reduces to the original fixed-probability regime.
        """
        raw_pressure = max(0.0, drop) * 2.0 + shock_severity
        pressure = raw_pressure * vol_gain
        if self.stressed:
            if rng.random() < self.p_sc * (1.0 - min(pressure, 0.9)):
                self.stressed = False
        else:
            if rng.random() < self.p_cs * vol_gain + pressure:
                self.stressed = True

        target = 1.0 if self.stressed else 0.0
        target = float(np.clip(target * 0.6 * vol_gain + raw_pressure * vol_gain, 0.0, 1.0))
        # Smooth so stress eases rather than snapping back.
        self.level += 0.5 * (target - self.level)
        return self.level


def staggered_thresholds(
    base: float, n: int, rng: np.random.Generator, spread: float = 1.5
) -> np.ndarray:
    """Spread ``n`` trigger levels around ``base``.

    Returns thresholds in roughly ``[base * (1 - spread/2), base * (1 + spread)]``,
    clipped to stay positive. A wider spread means a more gradual cascade.
    """
    u = rng.random(n)
    factors = (1.0 - spread / 2.0) + (spread * 1.5) * u
    return np.maximum(base * factors, 1e-4)
