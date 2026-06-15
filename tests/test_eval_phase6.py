"""Phase 6 eval corpus and offline validation targets."""

from __future__ import annotations

from engine.presets import DEFAULT_POSITION_FRAC, PRESETS
from engine.schema import Metrics
from eval.discrimination import (
    Outcome,
    config_for_episode,
    render_report,
    run_episode,
)
from eval.episode_corpus import all_episodes, episodes_for_split, load_eval_episode
from eval.latency import LatencyReport, render_latency_report


def _metrics(*, fill_rate: float, pct_stuck: float, halts: int) -> Metrics:
    return Metrics(
        run_id="fixture",
        fill_rate=fill_rate,
        filled_qty=100,
        stuck_qty=0,
        pct_stuck=pct_stuck,
        implementation_shortfall_bps=0.0,
        slippage_bps=0.0,
        vwap_sold=100.0,
        arrival_price=100.0,
        final_price=90.0,
        max_drawdown_pct=0.5,
        time_to_exit_ticks=10,
        halt_triggered=halts > 0,
        halt_count=halts,
        ticks_run=10,
    )


def test_episode_corpus_has_phase6_shape() -> None:
    episodes = all_episodes()
    assert 10 <= len(episodes) <= 20
    assert {ep.split for ep in episodes} == {"calibration", "holdout"}
    assert {ep.expected_exit for ep in episodes} == {"closed", "open"}
    assert len(episodes_for_split("calibration")) >= 4
    assert len(episodes_for_split("holdout")) >= 4


def test_engine_presets_are_loaded_from_eval_corpus() -> None:
    episodes = all_episodes()
    assert DEFAULT_POSITION_FRAC == 0.20
    assert set(PRESETS) == {ep.symbol for ep in episodes}
    cvna = load_eval_episode("cvna_2022")
    assert PRESETS["CVNA"].adv == cvna.instrument.adv
    assert PRESETS["CVNA"].reference_price == cvna.instrument.reference_price


def test_gemini_fixture_preserves_episode_evidence_and_direct_levers() -> None:
    ep = load_eval_episode("cvna_2022")
    baseline = config_for_episode(ep, mode="baseline")
    gemini = config_for_episode(ep, mode="gemini_fixture")
    assert baseline.instrument == gemini.instrument
    assert baseline.position == gemini.position
    assert baseline.evidence_summary == gemini.evidence_summary
    assert gemini.crisis_intensity != baseline.crisis_intensity
    assert all("recorded Gemini fixture" in shock.note for shock in gemini.shock_schedule)


def test_report_separates_calibration_holdout_and_gemini_delta() -> None:
    calibration = load_eval_episode("cvna_2022")
    holdout = load_eval_episode("rivn_2022")
    outcomes = [
        Outcome(
            calibration,
            "baseline",
            1,
            _metrics(fill_rate=0.1, pct_stuck=0.9, halts=1),
            1.0,
            0.7,
        ),
        Outcome(
            calibration,
            "gemini_fixture",
            1,
            _metrics(fill_rate=0.1, pct_stuck=0.9, halts=1),
            1.0,
            0.9,
        ),
        Outcome(
            holdout,
            "baseline",
            1,
            _metrics(fill_rate=1.0, pct_stuck=0.0, halts=0),
            1.0,
            0.3,
        ),
        Outcome(
            holdout,
            "gemini_fixture",
            1,
            _metrics(fill_rate=0.1, pct_stuck=0.9, halts=1),
            1.0,
            1.0,
        ),
    ]
    text = render_report(outcomes, split="all", compare_gemini=True)
    assert "calibration baseline" in text
    assert "holdout     baseline" in text
    assert "Recorded Gemini fixture delta" in text
    assert "accuracy +100%" in text


def test_run_episode_smoke_for_fast_closed_case() -> None:
    outcome = run_episode(load_eval_episode("cvna_2022"), mode="baseline")
    assert outcome.correct
    assert outcome.actual_exit == "closed"
    assert outcome.signature_score is not None


def test_latency_report_renders_percentiles() -> None:
    ep = load_eval_episode("cvna_2022")
    outcomes = [
        Outcome(ep, "baseline", 1, _metrics(fill_rate=0.1, pct_stuck=0.9, halts=1), 10.0),
        Outcome(ep, "baseline", 1, _metrics(fill_rate=0.1, pct_stuck=0.9, halts=1), 20.0),
    ]
    text = render_latency_report(LatencyReport(outcomes))
    assert "p50 15 ms" in text
    assert "p95 20 ms" in text
