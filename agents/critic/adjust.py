"""Applying and composing calibration adjustments to archetype stances.

The calibration critic expresses its correction as bounded per-type *multipliers* on
``aggressiveness`` and ``participation`` (``CalibrationAdjustments``). This module is
where those multipliers actually bite: ``apply_adjustments`` scales a window's
stances before the engine reads them, and ``compose_adjustments`` accumulates the
nudges across generator-critic loop iterations. Everything stays inside the
contract's stance ranges — a nudge can sharpen or soften a crowd, never invert it.
"""

from __future__ import annotations

from engine.schema import INVESTOR_TYPES, InvestorType, Stance

from agents.critic.schema import (
    ADJ_MAX,
    ADJ_MIN,
    CalibrationAdjustments,
    TypeAdjustment,
)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _clip_mult(x: float) -> float:
    return max(ADJ_MIN, min(ADJ_MAX, x))


def apply_adjustments(
    stances: dict[InvestorType, Stance],
    adjustments: CalibrationAdjustments | dict | None,
) -> dict[InvestorType, Stance]:
    """Return new stances with each type's multipliers applied and re-clamped to [0,1].

    Robust to a missing or partial adjustment set: any type without an entry is left
    exactly as it was, so a bad/empty calibration can never distort the crowd.
    """
    if adjustments is None:
        return stances
    if not isinstance(adjustments, CalibrationAdjustments):
        adjustments = CalibrationAdjustments.model_validate(adjustments)

    out: dict[InvestorType, Stance] = {}
    for t in INVESTOR_TYPES:
        adj = adjustments.for_type(t)
        s = stances[t]
        out[t] = s.model_copy(
            update={
                "aggressiveness": _clip01(s.aggressiveness * _clip_mult(adj.aggressiveness_mult)),
                "participation": _clip01(s.participation * _clip_mult(adj.participation_mult)),
            }
        )
    return out


def compose_adjustments(
    base: CalibrationAdjustments, delta: CalibrationAdjustments
) -> CalibrationAdjustments:
    """Accumulate a new nudge onto the running adjustments (multiply, then clamp)."""
    out: dict[str, TypeAdjustment] = {}
    for t in INVESTOR_TYPES:
        b = base.for_type(t)
        d = delta.for_type(t)
        out[t] = TypeAdjustment(
            aggressiveness_mult=_clip_mult(b.aggressiveness_mult * d.aggressiveness_mult),
            participation_mult=_clip_mult(b.participation_mult * d.participation_mult),
        )
    return CalibrationAdjustments(multipliers=out)


def calm_adjustments(intensity: float = 1.0) -> CalibrationAdjustments:
    """A deliberately over-rational crowd: softer sellers, stronger support.

    This is the *failure mode* the critic exists to catch — LLM-driven market agents
    tending to behave too calmly and rationally (AGENTS.md §4). The backtest starts a
    crowd here to demonstrate the generator-critic loop detecting and correcting it.
    """
    intensity = max(0.0, min(1.0, intensity))
    soften = _clip_mult(1.0 - 0.5 * intensity)  # damp seller pressure (down to the floor)
    prop = _clip_mult(1.0 + 0.6 * intensity)  # prop up the bid side (up to the ceiling)
    mults = {
        "forced_seller": TypeAdjustment(aggressiveness_mult=soften, participation_mult=soften),
        "panic_seller": TypeAdjustment(aggressiveness_mult=soften, participation_mult=soften),
        "trend_follower": TypeAdjustment(aggressiveness_mult=soften, participation_mult=soften),
        "bargain_hunter": TypeAdjustment(aggressiveness_mult=prop, participation_mult=prop),
        "market_maker": TypeAdjustment(aggressiveness_mult=prop, participation_mult=prop),
        "holder": TypeAdjustment(),
    }
    return CalibrationAdjustments(multipliers=mults)
