"""Calibration critic — offline tests (no LLM, no cloud).

Covers the deterministic core (episode signature, the three-axis comparison, the
bounded adjustments) and the baseline critic agent end to end through the real ADK
runner. The live Gemini judge shares this exact comparison, so these guard both.
"""

from __future__ import annotations

import pytest
from agents.critic.adjust import (
    apply_adjustments,
    calm_adjustments,
    compose_adjustments,
)
from agents.critic.compare import (
    DRAWDOWN_FIDELITY,
    STUCK_FLOOR,
    compare_to_episode,
)
from agents.critic.core import report_for_run
from agents.critic.episode import episode_for_symbol, load_episode, signature
from agents.critic.schema import ADJ_MAX, ADJ_MIN, CalibrationAdjustments
from agents.orchestrator.driver import run_baseline_simulation
from engine.baseline import baseline_stances
from engine.scenarios import flagship_scenario
from engine.schema import INVESTOR_TYPES
from eval.backtest import over_rational_book_config

# A genuinely violent unwind (matches the shipped baseline run) and an over-rational
# one, used to exercise both verdicts.
VIOLENT = {"max_drawdown_pct": 0.50, "pct_stuck": 0.78, "halt_triggered": True}
CALM = {"max_drawdown_pct": 0.06, "pct_stuck": 0.0, "halt_triggered": False}


def test_episode_signature_matches_cvna_collapse() -> None:
    ep = load_episode("cvna_2022")
    sig = signature(ep.closes)
    # The real episode fell ~75% peak-to-trough, with a violent single-day cliff.
    assert 0.70 < sig.max_drawdown < 0.80
    assert sig.worst_day_return < -0.25
    assert sig.disorderliness > 0.20  # disorderly cascade, not a smooth glide
    assert sig.n_days == len(ep.closes)


def test_episode_lookup_by_symbol() -> None:
    assert episode_for_symbol("CVNA").id == "cvna_2022"
    assert episode_for_symbol("cvna").id == "cvna_2022"  # case-insensitive
    assert episode_for_symbol("NOPE") is None
    assert episode_for_symbol(None) is None


def test_violent_run_is_plausible() -> None:
    ep = load_episode("cvna_2022")
    report = compare_to_episode(VIOLENT, ep)
    assert report.verdict == "plausible"
    assert report.plausible
    assert report.flags == []
    # A plausible run is left untouched (identity multipliers).
    for t in INVESTOR_TYPES:
        adj = report.adjustments.for_type(t)
        assert adj.aggressiveness_mult == pytest.approx(1.0)
        assert adj.participation_mult == pytest.approx(1.0)


def test_calm_run_is_flagged_and_sharpened() -> None:
    ep = load_episode("cvna_2022")
    report = compare_to_episode(CALM, ep)
    assert report.verdict == "too_calm"
    assert not report.plausible
    assert set(report.flags) == {"too_calm", "too_liquid", "too_orderly"}
    # The correction sharpens sellers and thins support.
    assert report.adjustments.for_type("forced_seller").aggressiveness_mult > 1.0
    assert report.adjustments.for_type("panic_seller").participation_mult > 1.0
    assert report.adjustments.for_type("market_maker").aggressiveness_mult < 1.0
    assert report.adjustments.for_type("bargain_hunter").participation_mult < 1.0


def test_thresholds_are_episode_derived() -> None:
    ep = load_episode("cvna_2022")
    sig = signature(ep.closes)
    report = compare_to_episode({"max_drawdown_pct": 0.0, "pct_stuck": 0.0}, ep)
    drawdown_gap = next(g for g in report.gaps if g.axis == "drawdown")
    assert drawdown_gap.expected == pytest.approx(DRAWDOWN_FIDELITY * sig.max_drawdown, abs=1e-3)
    liquidity_gap = next(g for g in report.gaps if g.axis == "liquidity")
    assert liquidity_gap.expected == STUCK_FLOOR


def test_apply_adjustments_stays_in_stance_range() -> None:
    stances = baseline_stances(drop=0.4, stress=0.9, tick=30)
    # An extreme upward nudge must still leave every stance field within [0, 1].
    extreme = CalibrationAdjustments(
        multipliers={
            t: {"aggressiveness_mult": ADJ_MAX, "participation_mult": ADJ_MAX}
            for t in INVESTOR_TYPES
        }
    )
    out = apply_adjustments(stances, extreme)
    for t in INVESTOR_TYPES:
        assert 0.0 <= out[t].aggressiveness <= 1.0
        assert 0.0 <= out[t].participation <= 1.0


def test_apply_adjustments_none_is_identity() -> None:
    stances = baseline_stances(drop=0.2, stress=0.5, tick=10)
    assert apply_adjustments(stances, None) is stances


def test_compose_clamps_to_multiplier_band() -> None:
    a = calm_adjustments(1.0)
    # Composing two upward sharpening sets must not exceed the multiplier ceiling.
    up = CalibrationAdjustments(
        multipliers={
            t: {"aggressiveness_mult": 1.6, "participation_mult": 1.6} for t in INVESTOR_TYPES
        }
    )
    composed = compose_adjustments(up, up)
    for t in INVESTOR_TYPES:
        adj = composed.for_type(t)
        assert ADJ_MIN <= adj.aggressiveness_mult <= ADJ_MAX
        assert ADJ_MIN <= adj.participation_mult <= ADJ_MAX
    assert a is not None  # calm_adjustments builds without error


def test_report_for_run_no_reference_for_unknown_symbol() -> None:
    scenario = {"instrument": {"symbol": "ZZZZ"}}
    report = report_for_run(scenario, VIOLENT)
    assert report.verdict == "no_reference"
    assert report.plausible  # honest: not checked, not failed


@pytest.mark.asyncio
async def test_baseline_critic_writes_report_end_to_end() -> None:
    res = await run_baseline_simulation(with_critic=True)
    report = res["calibration_report"]
    assert report is not None
    assert report["symbol"] == "CVNA"
    assert report["verdict"] == "plausible"  # the shipped crowd is faithful to CVNA
    assert report["narrative"]


@pytest.mark.asyncio
async def test_calm_seeded_run_is_flagged_too_calm() -> None:
    calm = calm_adjustments(1.0).model_dump()
    res = await run_baseline_simulation(
        over_rational_book_config(flagship_scenario()),
        with_critic=True,
        adjustments=calm,
    )
    report = res["calibration_report"]
    assert report["verdict"] == "too_calm"
    # The over-rational crowd cleared the position with little price impact.
    assert res["run_metrics"]["max_drawdown_pct"] < 0.45
    # And the critic wrote corrective nudges (sharper sellers) for the next run.
    sellers = report["adjustments"]["multipliers"]["forced_seller"]["aggressiveness_mult"]
    assert sellers > 1.0
