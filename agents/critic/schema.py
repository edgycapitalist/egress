"""Calibration schemas — the critic's reference, report, and adjustment shapes.

These are *agents-side* models: the calibration critic and the backtest depend on
them, but the engine never does (it stays LLM- and cloud-free, contract §4). They
back the two contract keys the critic owns — ``calibration_report`` and
``calibration_adjustments`` (contract §4) — and the historical-episode reference the
backtest checks a run against (AGENTS.md §11, Phase 4).
"""

from __future__ import annotations

from engine.schema import INVESTOR_TYPES, InvestorType
from pydantic import BaseModel, Field

# Per-type stance multipliers are bounded so a calibration nudge can sharpen or
# soften a crowd but never invert it or blow past the contract's [0,1] stance range
# once applied. These are the knobs the generator-critic loop turns.
ADJ_MIN = 0.5
ADJ_MAX = 1.6


class EpisodeSignature(BaseModel):
    """The behavioural fingerprint of a real episode, derived from its price path.

    Magnitudes (drawdown) and shape (disorderliness) are what the critic checks a
    simulated unwind against — not a tick-for-tick price match, which would conflate
    an intraday simulation with a multi-week episode.
    """

    peak: float
    trough: float
    max_drawdown: float  # (peak - trough) / peak, in [0, 1]
    worst_day_return: float  # most negative single-step return (<= 0)
    disorderliness: float  # [0,1] share of the total decline in the worst single step
    n_days: int


class Episode(BaseModel):
    """A curated historical crisis episode — the calibration reference (AGENTS.md §7)."""

    id: str
    symbol: str
    title: str
    window: str = ""
    source: str = ""
    note: str = ""
    closes: list[float] = Field(default_factory=list)


class TypeAdjustment(BaseModel):
    """Bounded multipliers applied to one investor type's stance before a re-run."""

    aggressiveness_mult: float = 1.0
    participation_mult: float = 1.0


class CalibrationAdjustments(BaseModel):
    """Per-type stance nudges the critic writes for the archetypes to read (contract §4).

    Keyed by investor type. An empty/identity set means "no change" — the crowd is
    already calibrated. The backtest composes these across loop iterations.
    """

    multipliers: dict[str, TypeAdjustment] = Field(default_factory=dict)

    def for_type(self, t: InvestorType) -> TypeAdjustment:
        return self.multipliers.get(t, TypeAdjustment())


class AxisGap(BaseModel):
    """One axis of the comparison: what the sim did vs what the episode implies."""

    axis: str
    simulated: float
    expected: float
    plausible: bool
    note: str = ""


class CalibrationReport(BaseModel):
    """The critic's verdict on one run vs a real episode (the ``calibration_report`` key)."""

    episode_id: str
    symbol: str
    verdict: str  # "plausible" | "too_calm" | "no_reference"
    plausible: bool
    plausibility_score: float  # [0,1], 1.0 = matches the episode signature
    flags: list[str] = Field(default_factory=list)
    gaps: list[AxisGap] = Field(default_factory=list)
    adjustments: CalibrationAdjustments = Field(default_factory=CalibrationAdjustments)
    narrative: str = ""  # plain-language judgement (template in baseline, Gemini when live)


def identity_adjustments() -> CalibrationAdjustments:
    """A no-op adjustment set — every type left exactly as the heuristic produced it."""
    return CalibrationAdjustments(multipliers={t: TypeAdjustment() for t in INVESTOR_TYPES})
