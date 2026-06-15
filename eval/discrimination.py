"""Offline discrimination and Gemini-fixture comparison evals.

The quick path preserves the original four-case smoke test:

    python -m eval.discrimination

Phase 6 adds corpus-wide modes:

    python -m eval.discrimination --split all --compare-gemini
    python -m eval.discrimination --split holdout --compare-gemini

All paths run offline. ``baseline`` uses one fixed deterministic configuration per
episode. ``gemini_fixture`` replays recorded Gemini scenario-author assumptions
(crisis intensity and shock severity) for the same evidence; it never calls Vertex.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Literal

from agents.critic.compare import compare_to_episode
from agents.critic.schema import Episode as CriticEpisode
from engine.core import Engine
from engine.scenarios import flagship_scenario
from engine.schema import EvidenceSummary, Metrics, RunConfig

from eval.episode_corpus import (
    DEFAULT_POSITION_FRAC,
    EvalEpisode,
    episodes_for_split,
)

Mode = Literal["baseline", "gemini_fixture"]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "gemini_assumptions.json"


@dataclass(frozen=True)
class Outcome:
    episode: EvalEpisode
    mode: Mode
    position_qty: int
    metrics: Metrics
    duration_ms: float
    signature_score: float | None = None

    @property
    def pos_pct_adv(self) -> float:
        return self.position_qty / self.episode.instrument.adv

    @property
    def actual_exit(self) -> Literal["closed", "open"]:
        closed = (
            self.metrics.fill_rate < 0.5
            or self.metrics.halt_count > 0
            or self.metrics.pct_stuck > 0.5
        )
        return "closed" if closed else "open"

    @property
    def correct(self) -> bool:
        return self.actual_exit == self.episode.expected_exit


def _load_gemini_fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_for(ep: EvalEpisode) -> dict[str, float]:
    data = _load_gemini_fixtures()
    default = data.get("default") or {}
    specific = (data.get("assumptions") or {}).get(ep.id) or {}
    return {
        "crisis_intensity": float(
            specific.get("crisis_intensity", default.get("crisis_intensity", ep.crisis_intensity))
        ),
        "shock_multiplier": float(
            specific.get("shock_multiplier", default.get("shock_multiplier", 1.0))
        ),
    }


def config_for_episode(ep: EvalEpisode, *, mode: Mode = "baseline") -> RunConfig:
    """Build the one fixed eval config for ``ep``.

    Only instrument data, position size, and optional recorded Gemini assumptions
    vary by episode. The crowd mix, exit speed, halt rule, population size, and
    baseline stances stay fixed across the corpus.
    """
    base = flagship_scenario()
    instrument = base.instrument.model_copy(
        update={
            "symbol": ep.symbol,
            "reference_price": ep.instrument.reference_price,
            "adv": ep.instrument.adv,
            "free_float": ep.instrument.free_float,
            "volatility": ep.instrument.volatility,
            "halt_tier": ep.instrument.halt_tier,
        }
    )
    position_qty = max(1, round(ep.position_frac_adv * ep.instrument.adv))
    position = base.position.model_copy(
        update={
            "quantity": position_qty,
            "arrival_price": ep.instrument.reference_price,
        }
    )

    crisis_intensity = ep.crisis_intensity
    shock_multiplier = 1.0
    if mode == "gemini_fixture":
        fixture = _fixture_for(ep)
        crisis_intensity = fixture["crisis_intensity"]
        shock_multiplier = fixture["shock_multiplier"]

    shock_schedule = [
        shock.model_copy(
            update={
                "severity": min(1.0, max(0.0, shock.severity * shock_multiplier)),
                "note": (
                    f"{shock.note}; recorded Gemini fixture"
                    if mode == "gemini_fixture"
                    else shock.note
                ),
            }
        )
        for shock in base.shock_schedule
    ]
    evidence_summary = EvidenceSummary.model_validate({
        "summary": (
            f"{ep.title}: representative public {ep.split} fixture. "
            f"Expected exit {ep.expected_exit}."
        ),
        "items": [
            {
                "field": "instrument",
                "source": "curated_fixture",
                "confidence": "high",
                "label": ep.symbol,
                "as_of": ep.window,
                "notes": ep.source,
            }
        ],
    })

    return RunConfig.model_validate(
        base.model_copy(
            update={
                "run_id": f"eval-{mode}-{ep.id}",
                "instrument": instrument,
                "position": position,
                "shock_schedule": shock_schedule,
                "crisis_intensity": crisis_intensity,
                "baseline_mode": True,
                "evidence_summary": evidence_summary,
            }
        ).model_dump()
    )


def _critic_episode(ep: EvalEpisode) -> CriticEpisode:
    return CriticEpisode(
        id=ep.id,
        symbol=ep.symbol,
        title=ep.title,
        window=ep.window,
        source=ep.source,
        note=ep.note,
        closes=list(ep.closes),
    )


def _signature_score(ep: EvalEpisode, metrics: Metrics) -> float | None:
    if ep.expected_exit != "closed" or len(ep.closes) < 2:
        return None
    report = compare_to_episode(metrics.model_dump(), _critic_episode(ep))
    return report.plausibility_score


def run_episode(ep: EvalEpisode, *, mode: Mode = "baseline") -> Outcome:
    config = config_for_episode(ep, mode=mode)
    started = time.perf_counter()
    metrics = Engine(config).run_baseline()
    return Outcome(
        episode=ep,
        mode=mode,
        position_qty=config.position.quantity,
        metrics=metrics,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        signature_score=_signature_score(ep, metrics),
    )


def run_discrimination(
    *,
    split: str = "quick",
    compare_gemini: bool = False,
) -> list[Outcome]:
    episodes = episodes_for_split(split)
    modes: tuple[Mode, ...] = ("baseline", "gemini_fixture") if compare_gemini else ("baseline",)
    return [run_episode(ep, mode=mode) for ep in episodes for mode in modes]


def _groups(outcomes: list[Outcome]) -> dict[tuple[str, Mode], list[Outcome]]:
    grouped: dict[tuple[str, Mode], list[Outcome]] = {}
    for outcome in outcomes:
        grouped.setdefault((outcome.episode.split, outcome.mode), []).append(outcome)
    return grouped


def _accuracy(rows: list[Outcome]) -> float:
    return sum(1 for row in rows if row.correct) / len(rows) if rows else 0.0


def _mean_signature(rows: list[Outcome]) -> float | None:
    scores = [row.signature_score for row in rows if row.signature_score is not None]
    return mean(scores) if scores else None


def _format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_report(outcomes: list[Outcome], *, split: str, compare_gemini: bool) -> str:
    bar = "-" * 118
    lines = [
        bar,
        f"  EGRESS · Phase 6 discrimination eval   "
        f"(split={split}, position={DEFAULT_POSITION_FRAC:.0%} ADV, offline)",
        bar,
        f"  {'Episode':<42}{'split':<13}{'mode':<16}{'expected':<10}{'actual':<9}"
        f"{'fill':>8}{'stuck':>8}{'halts':>7}{'sig':>7}{'ms':>8}",
        bar,
    ]
    for row in outcomes:
        lines.append(
            f"  {row.episode.display_name[:41]:<42}{row.episode.split:<13}{row.mode:<16}"
            f"{row.episode.expected_exit:<10}{row.actual_exit:<9}"
            f"{row.metrics.fill_rate:>7.0%}{row.metrics.pct_stuck:>8.0%}"
            f"{row.metrics.halt_count:>7}{_format_pct(row.signature_score):>7}"
            f"{row.duration_ms:>8.0f}"
        )
    lines.append(bar)

    grouped = _groups(outcomes)
    lines.append("  Summary by split and mode:")
    for split_name in ("calibration", "holdout"):
        for mode in ("baseline", "gemini_fixture"):
            rows = grouped.get((split_name, mode), [])
            if not rows:
                continue
            correct = sum(1 for row in rows if row.correct)
            sig = _mean_signature(rows)
            lines.append(
                f"    {split_name:<11} {mode:<15} "
                f"accuracy {correct}/{len(rows)} ({_accuracy(rows):.0%}); "
                f"closed-case signature score {_format_pct(sig)}"
            )

    if compare_gemini:
        lines.append("")
        lines.append("  Recorded Gemini fixture delta:")
        for split_name in ("calibration", "holdout"):
            base = grouped.get((split_name, "baseline"), [])
            gem = grouped.get((split_name, "gemini_fixture"), [])
            if not base or not gem:
                continue
            acc_delta = _accuracy(gem) - _accuracy(base)
            base_sig = _mean_signature(base)
            gem_sig = _mean_signature(gem)
            if base_sig is None or gem_sig is None:
                sig_delta = "n/a"
            else:
                sig_delta = f"{gem_sig - base_sig:+.0%}"
            lines.append(
                f"    {split_name:<11} accuracy {acc_delta:+.0%}; "
                f"closed-case signature {sig_delta}"
            )
        lines.append(
            "    Interpretation: positive means the recorded Gemini assumptions helped; "
            "zero or negative means no measurable lift over the deterministic baseline."
        )
    lines.append(bar)
    return "\n".join(lines)


def baseline_accuracy_by_split(outcomes: list[Outcome]) -> dict[str, float]:
    grouped = _groups(outcomes)
    return {
        split_name: _accuracy(rows)
        for (split_name, mode), rows in grouped.items()
        if mode == "baseline"
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.discrimination",
        description="Offline stress-model discrimination over the eval episode corpus.",
    )
    parser.add_argument(
        "--split",
        choices=["quick", "all", "calibration", "holdout"],
        default="quick",
        help="'quick' is the original four-case smoke; 'all' runs the full corpus",
    )
    parser.add_argument(
        "--compare-gemini",
        action="store_true",
        help="also run recorded Gemini assumption fixtures for the same episodes",
    )
    parser.add_argument(
        "--min-baseline-accuracy",
        type=float,
        default=1.0,
        help="minimum baseline classification accuracy required per reported split",
    )
    args = parser.parse_args(argv)

    outcomes = run_discrimination(split=args.split, compare_gemini=args.compare_gemini)
    print(render_report(outcomes, split=args.split, compare_gemini=args.compare_gemini))
    accuracies = baseline_accuracy_by_split(outcomes)
    ok = all(acc >= args.min_baseline_accuracy for acc in accuracies.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
