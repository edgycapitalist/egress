"""Deterministic comparison of a simulated unwind to a real episode.

This is the quantitative core of the calibration critic. It compares a run's
``Metrics`` to a historical episode's behavioural signature on three timescale-fair
axes and decides whether the simulated crowd behaved *plausibly* — or whether it was
too calm, the known failure mode of LLM-driven market agents (AGENTS.md §4). When it
finds the crowd too calm it proposes bounded per-type stance nudges that the
generator-critic loop applies before re-running.

The same comparison feeds two consumers: the deterministic baseline critic uses its
verdict directly, and the live Gemini judge is *grounded* in it so the model
interprets real numbers rather than inventing a verdict.

Why these axes (not a price match): a single intraday exit simulation cannot and
should not reproduce a multi-week episode tick for tick. What *is* comparable is the
crowd's behaviour — how far it forced the price, how much of the position it left
stranded, and whether the move was disorderly enough to trip a halt.
"""

from __future__ import annotations

from agents.critic.episode import signature
from agents.critic.schema import (
    AxisGap,
    CalibrationAdjustments,
    CalibrationReport,
    Episode,
    TypeAdjustment,
    identity_adjustments,
)

# How much of the episode's eventual drawdown a faithful crisis-exit simulation is
# expected to reproduce. The real episode fell ~75%; at 0.6 we ask the sim to show at
# least ~45% drawdown to be considered "as violent as the episode", not a soft glide.
DRAWDOWN_FIDELITY = 0.6

# A crisis exit in a name that collapsed this hard should strand a substantial part
# of the position — support evaporates. Below this, the exit was implausibly easy.
STUCK_FLOOR = 0.30

# An episode counts as disorderly (a cascade, not a slide) above this concentration;
# a disorderly episode should produce a halt in the sim.
DISORDER_THRESHOLD = 0.20

# Sellers pushed up / support pulled down when the crowd is too calm. Step scales with
# the worst relative shortfall across the failing axes, within a sane band.
BASE_STEP = 0.12
STEP_GAIN = 0.45
MAX_STEP = 0.45

_SELLER_TYPES = ("forced_seller", "panic_seller", "trend_follower")
_SUPPORT_TYPES = ("bargain_hunter", "market_maker")


def _rel_shortfall(simulated: float, expected: float) -> float:
    """How far short of ``expected`` the sim fell, as a fraction in [0, 1]."""
    if expected <= 0:
        return 0.0
    return max(0.0, min(1.0, (expected - simulated) / expected))


def _too_calm_adjustments(gap: float) -> CalibrationAdjustments:
    """Sharpen sellers and thin out support, proportional to the shortfall ``gap``."""
    step = max(0.0, min(MAX_STEP, BASE_STEP + STEP_GAIN * gap))
    seller = TypeAdjustment(
        aggressiveness_mult=1.0 + step,
        participation_mult=1.0 + step,
    )
    support = TypeAdjustment(
        aggressiveness_mult=1.0 - 0.7 * step,
        participation_mult=1.0 - 0.7 * step,
    )
    mults = {t: seller for t in _SELLER_TYPES}
    mults.update({t: support for t in _SUPPORT_TYPES})
    mults["holder"] = TypeAdjustment()
    return CalibrationAdjustments(multipliers=mults)


def compare_to_episode(metrics: dict, episode: Episode) -> CalibrationReport:
    """Judge one run's metrics against a real episode; propose nudges if too calm."""
    sig = signature(episode.closes)
    sim_dd = float(metrics.get("max_drawdown_pct", 0.0) or 0.0)
    sim_stuck = float(metrics.get("pct_stuck", 0.0) or 0.0)
    sim_halt = bool(metrics.get("halt_triggered", False))
    episode_disorderly = sig.disorderliness >= DISORDER_THRESHOLD

    expected_dd = DRAWDOWN_FIDELITY * sig.max_drawdown
    gaps: list[AxisGap] = []
    flags: list[str] = []

    # Axis 1 — drawdown: did the crowd force the price as hard as the episode did?
    dd_ok = sim_dd >= expected_dd
    gaps.append(
        AxisGap(
            axis="drawdown",
            simulated=round(sim_dd, 4),
            expected=round(expected_dd, 4),
            plausible=dd_ok,
            note=(
                f"episode drawdown {sig.max_drawdown:.0%}; expect the sim to reach "
                f"≥{expected_dd:.0%}"
            ),
        )
    )
    if not dd_ok:
        flags.append("too_calm")

    # Axis 2 — liquidity: a crisis exit this size should leave a lot stranded.
    stuck_ok = sim_stuck >= STUCK_FLOOR
    gaps.append(
        AxisGap(
            axis="liquidity",
            simulated=round(sim_stuck, 4),
            expected=STUCK_FLOOR,
            plausible=stuck_ok,
            note=f"expect ≥{STUCK_FLOOR:.0%} of the position stranded as support evaporates",
        )
    )
    if not stuck_ok:
        flags.append("too_liquid")

    # Axis 3 — disorder: a disorderly episode should trip a halt in the sim.
    disorder_ok = (not episode_disorderly) or sim_halt
    gaps.append(
        AxisGap(
            axis="disorder",
            simulated=1.0 if sim_halt else 0.0,
            expected=1.0 if episode_disorderly else 0.0,
            plausible=disorder_ok,
            note=(
                f"episode disorderliness {sig.disorderliness:.2f}"
                + ("; expect a halt" if episode_disorderly else "; orderly enough, no halt needed")
            ),
        )
    )
    if not disorder_ok:
        flags.append("too_orderly")

    plausible = not flags
    # Closeness per axis, averaged: drawdown & liquidity are ratios capped at 1.0;
    # disorder is binary.
    dd_score = min(1.0, sim_dd / expected_dd) if expected_dd > 0 else 1.0
    stuck_score = min(1.0, sim_stuck / STUCK_FLOOR) if STUCK_FLOOR > 0 else 1.0
    disorder_score = 1.0 if disorder_ok else 0.0
    plausibility = round((dd_score + stuck_score + disorder_score) / 3.0, 4)

    if plausible:
        verdict = "plausible"
        adjustments = identity_adjustments()
    else:
        verdict = "too_calm"
        worst_gap = max(
            _rel_shortfall(sim_dd, expected_dd),
            _rel_shortfall(sim_stuck, STUCK_FLOOR),
            0.4 if not disorder_ok else 0.0,
        )
        adjustments = _too_calm_adjustments(worst_gap)

    return CalibrationReport(
        episode_id=episode.id,
        symbol=episode.symbol,
        verdict=verdict,
        plausible=plausible,
        plausibility_score=plausibility,
        flags=flags,
        gaps=gaps,
        adjustments=adjustments,
    )


def no_reference_report(symbol: str | None) -> CalibrationReport:
    """The honest verdict when no curated episode matches the scenario's instrument."""
    return CalibrationReport(
        episode_id="",
        symbol=(symbol or "").upper(),
        verdict="no_reference",
        plausible=True,
        plausibility_score=1.0,
        flags=[],
        gaps=[],
        adjustments=identity_adjustments(),
        narrative=(
            f"No curated historical episode for {symbol or 'this instrument'}; "
            "the run was not calibration-checked."
        ),
    )


def render_verdict(report: CalibrationReport) -> str:
    """Plain-language calibration verdict from a report (deterministic template)."""
    if report.verdict == "no_reference":
        return report.narrative
    head = (
        f"Checked against the {report.symbol} episode, the simulated crowd looks "
        + ("plausible" if report.plausible else "too calm")
        + f" (fidelity {report.plausibility_score:.0%})."
    )
    lines = [head]
    for g in report.gaps:
        verdict = "ok" if g.plausible else "SHORT"
        lines.append(f"  · {g.axis}: {g.simulated:.2f} vs expected {g.expected:.2f} [{verdict}]")
    if not report.plausible:
        lines.append(
            "  The crowd cleared too easily relative to the real episode — the known "
            "tendency of model-driven agents to behave too rationally. Sharpening "
            "forced/panic/trend sellers and thinning bargain-hunter and market-maker "
            "support to re-run."
        )
    return "\n".join(lines)
