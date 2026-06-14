"""Deterministic low/base/high peer-crowding ensemble runner.

Phase 3 keeps the core promise LLM-free: vary the peer-crowding assumptions and
seeds, run the deterministic engine, and return an ``EnsembleResult`` summary plus
representative replay paths for animation.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.baseline import baseline_stances
from engine.core import Engine
from engine.replay.recorder import Recorder
from engine.schema import (
    EnsembleCaseSummary,
    EnsembleResult,
    MetricBand,
    Metrics,
    PeerCrowdingCase,
    PeerCrowdingProfile,
    RunConfig,
)

ENSEMBLE_CASES: tuple[PeerCrowdingCase, ...] = ("low", "base", "high")
ENSEMBLE_SEED_COUNT = 3
WindowTimingHook = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class EnsembleRun:
    case: PeerCrowdingCase
    seed: int
    config: RunConfig
    metrics: Metrics
    replay_ref: str


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _default_peer_profile(case: PeerCrowdingCase) -> PeerCrowdingProfile:
    defaults = {
        "low": dict(
            peer_fund_count=4,
            overlap_pct=0.20,
            avg_peer_position_pct_adv=0.020,
            shared_trigger_drawdown_pct=0.080,
            correlated_exit_probability=0.35,
            leverage_sensitivity=0.20,
            redemption_pressure=0.20,
            etf_flow_pressure=0.10,
        ),
        "base": dict(
            peer_fund_count=10,
            overlap_pct=0.45,
            avg_peer_position_pct_adv=0.050,
            shared_trigger_drawdown_pct=0.060,
            correlated_exit_probability=0.65,
            leverage_sensitivity=0.40,
            redemption_pressure=0.35,
            etf_flow_pressure=0.20,
        ),
        "high": dict(
            peer_fund_count=20,
            overlap_pct=0.75,
            avg_peer_position_pct_adv=0.080,
            shared_trigger_drawdown_pct=0.040,
            correlated_exit_probability=0.90,
            leverage_sensitivity=0.65,
            redemption_pressure=0.60,
            etf_flow_pressure=0.35,
        ),
        "custom": dict(),
    }[case]
    return PeerCrowdingProfile(
        case=case,
        evidence_source="synthetic_assumption",
        confidence="low",
        notes=f"{case} assumption-led peer-crowding ensemble case.",
        **defaults,
    )


def _scale_peer_profile(
    profile: PeerCrowdingProfile, case: PeerCrowdingCase
) -> PeerCrowdingProfile:
    if case == "base":
        return profile.model_copy(
            update={
                "case": "base",
                "notes": profile.notes or "Base peer-crowding ensemble case.",
            }
        )

    if case == "low":
        count_scale, risk_scale, trigger_scale = 0.55, 0.65, 1.30
    else:
        count_scale, risk_scale, trigger_scale = 1.75, 1.30, 0.72

    return profile.model_copy(
        update={
            "case": case,
            "peer_fund_count": max(1, round(profile.peer_fund_count * count_scale)),
            "overlap_pct": _clamp(profile.overlap_pct * risk_scale),
            "avg_peer_position_pct_adv": max(
                0.001, profile.avg_peer_position_pct_adv * risk_scale
            ),
            "shared_trigger_drawdown_pct": _clamp(
                profile.shared_trigger_drawdown_pct * trigger_scale, 0.001, 1.0
            ),
            "correlated_exit_probability": _clamp(
                profile.correlated_exit_probability * risk_scale
            ),
            "leverage_sensitivity": _clamp(profile.leverage_sensitivity * risk_scale),
            "redemption_pressure": _clamp(profile.redemption_pressure * risk_scale),
            "etf_flow_pressure": _clamp(profile.etf_flow_pressure * risk_scale),
            "notes": profile.notes or f"{case} scaled peer-crowding ensemble case.",
        }
    )


def peer_crowding_cases(
    profile: PeerCrowdingProfile | None,
) -> dict[PeerCrowdingCase, PeerCrowdingProfile]:
    """Return low/base/high profiles from a supplied profile or defaults."""
    if profile is None or profile.peer_fund_count <= 0:
        return {case: _default_peer_profile(case) for case in ENSEMBLE_CASES}
    base = profile.model_copy(update={"case": "base"})
    return {case: _scale_peer_profile(base, case) for case in ENSEMBLE_CASES}


def ensemble_seeds(seed: int, count: int = ENSEMBLE_SEED_COUNT) -> list[int]:
    return [seed + offset for offset in range(count)]


def _with_case(
    config: RunConfig, case: PeerCrowdingCase, profile: PeerCrowdingProfile, seed: int
) -> RunConfig:
    run_id = f"{config.run_id}-{case}-{seed}"
    return config.model_copy(
        deep=True,
        update={
            "run_id": run_id,
            "seed": seed,
            "peer_crowding": profile,
            "baseline_mode": True,
        },
    )


def _record_baseline_run(
    config: RunConfig,
    path: Path,
    case: PeerCrowdingCase,
    seed: int,
    on_window_timing: WindowTimingHook | None = None,
) -> Metrics:
    engine = Engine(config)
    with Recorder(path) as recorder:
        engine.start()
        recorder.write_meta(config)
        while not engine.done:
            drop = max(0.0, (engine.ref_price - engine.last_price) / engine.ref_price)
            stances = baseline_stances(drop, engine.stress, engine.tick)
            started = time.perf_counter()
            _state, events = engine.advance(stances, config.ticks_per_window)
            if on_window_timing is not None:
                on_window_timing(
                    {
                        "case": case,
                        "seed": seed,
                        "run_id": config.run_id,
                        "window_index": engine.window_index - 1,
                        "ticks_requested": config.ticks_per_window,
                        "ticks_emitted": len(events),
                        "duration_ms": (time.perf_counter() - started) * 1000.0,
                    }
                )
            for event in events:
                recorder.write_tick(event)
        metrics = engine.finalize().model_copy(
            update={"ensemble_case": case, "ensemble_seed": seed}
        )
        recorder.write_metrics(metrics)
        return metrics


def _median_run(runs: list[EnsembleRun]) -> EnsembleRun:
    ordered = sorted(runs, key=lambda run: (run.metrics.pct_stuck, run.metrics.slippage_bps))
    return ordered[len(ordered) // 2]


def _band(values: Iterable[float]) -> MetricBand:
    series = sorted(float(v) for v in values)
    if not series:
        return MetricBand(low=0.0, median=0.0, high=0.0)
    return MetricBand(
        low=round(series[0], 6),
        median=round(float(statistics.median(series)), 6),
        high=round(series[-1], 6),
    )


def _bands(runs: list[EnsembleRun]) -> dict[str, MetricBand]:
    halt_probability = (
        sum(1.0 for run in runs if run.metrics.halt_triggered) / len(runs) if runs else 0.0
    )
    time_values = [
        float(run.metrics.time_to_exit_ticks)
        if run.metrics.time_to_exit_ticks is not None
        else float(run.metrics.ticks_run)
        for run in runs
    ]
    return {
        "fill_rate": _band(run.metrics.fill_rate for run in runs),
        "pct_stuck": _band(run.metrics.pct_stuck for run in runs),
        "slippage_bps": _band(run.metrics.slippage_bps for run in runs),
        "implementation_shortfall_bps": _band(
            run.metrics.implementation_shortfall_bps for run in runs
        ),
        "max_drawdown_pct": _band(run.metrics.max_drawdown_pct for run in runs),
        "time_to_exit_ticks": _band(time_values),
        "halt_probability": MetricBand(
            low=round(halt_probability, 6),
            median=round(halt_probability, 6),
            high=round(halt_probability, 6),
        ),
    }


def run_ensemble(
    config: RunConfig,
    *,
    replay_dir: str | Path = "runs",
    seeds: Iterable[int] | None = None,
    on_window_timing: WindowTimingHook | None = None,
) -> EnsembleResult:
    """Run low/base/high peer-crowding cases over deterministic seeds."""
    replay_root = Path(replay_dir)
    replay_root.mkdir(parents=True, exist_ok=True)
    seed_list = list(seeds) if seeds is not None else ensemble_seeds(config.seed)
    profiles = peer_crowding_cases(config.peer_crowding)
    all_runs: list[EnsembleRun] = []
    summaries: list[EnsembleCaseSummary] = []

    for case in ENSEMBLE_CASES:
        case_runs: list[EnsembleRun] = []
        profile = profiles[case]
        for seed in seed_list:
            run_config = _with_case(config, case, profile, seed)
            replay_ref = replay_root / f"{run_config.run_id}.ndjson"
            metrics = _record_baseline_run(
                run_config,
                replay_ref,
                case,
                seed,
                on_window_timing=on_window_timing,
            )
            run = EnsembleRun(case, seed, run_config, metrics, str(replay_ref))
            case_runs.append(run)
            all_runs.append(run)
        representative = _median_run(case_runs)
        summaries.append(
            EnsembleCaseSummary(
                case=case,
                seeds=seed_list,
                peer_crowding=profile,
                metrics=representative.metrics,
                representative_replay_ref=representative.replay_ref,
            )
        )

    representative_case: PeerCrowdingCase = "base"
    representative = next(summary for summary in summaries if summary.case == representative_case)
    return EnsembleResult(
        run_id=f"{config.run_id}-ensemble",
        cases=summaries,
        bands=_bands(all_runs),
        representative_case=representative_case,
        representative_replay_ref=representative.representative_replay_ref,
        evidence_summary=config.evidence_summary,
    )
