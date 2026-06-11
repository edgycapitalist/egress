"""Tests for the stress regime and threshold staggering."""

import numpy as np

from engine.stats.process import StressRegime, staggered_thresholds


def test_stress_rises_with_drop_and_shock() -> None:
    rng = np.random.default_rng(0)
    calm = StressRegime()
    for _ in range(20):
        level = calm.step(drop=0.0, shock_severity=0.0, rng=rng)
    assert level < 0.2

    rng = np.random.default_rng(0)
    stressed = StressRegime()
    for _ in range(20):
        level = stressed.step(drop=0.25, shock_severity=0.8, rng=rng)
    assert level > 0.5


def test_stress_bounded() -> None:
    rng = np.random.default_rng(1)
    regime = StressRegime()
    for _ in range(100):
        level = regime.step(drop=0.9, shock_severity=1.0, rng=rng)
        assert 0.0 <= level <= 1.0


def test_staggered_thresholds_spread_and_deterministic() -> None:
    a = staggered_thresholds(0.05, 1000, np.random.default_rng(7))
    b = staggered_thresholds(0.05, 1000, np.random.default_rng(7))
    assert np.array_equal(a, b)  # seeded -> reproducible
    assert a.min() > 0
    assert a.std() > 0  # genuinely spread, not constant
    assert 0.02 < a.mean() < 0.12
