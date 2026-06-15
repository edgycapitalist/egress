"""Offline latency eval for the deterministic stress model."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import median

from eval.discrimination import Outcome, run_episode
from eval.episode_corpus import all_episodes


@dataclass(frozen=True)
class LatencyReport:
    outcomes: list[Outcome]

    @property
    def durations_ms(self) -> list[float]:
        return [o.duration_ms for o in self.outcomes]

    @property
    def p50_ms(self) -> float:
        return median(self.durations_ms) if self.outcomes else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.outcomes:
            return 0.0
        values = sorted(self.durations_ms)
        index = max(0, min(len(values) - 1, round(0.95 * (len(values) - 1))))
        return values[index]

    @property
    def max_ms(self) -> float:
        return max(self.durations_ms) if self.outcomes else 0.0


def run_latency_eval(*, limit: int | None = None) -> LatencyReport:
    episodes = list(all_episodes())
    if limit is not None:
        episodes = episodes[: max(1, limit)]
    return LatencyReport(outcomes=[run_episode(ep, mode="baseline") for ep in episodes])


def render_latency_report(report: LatencyReport) -> str:
    bar = "-" * 88
    lines = [
        bar,
        "  EGRESS · Phase 6 latency eval   (deterministic engine, offline)",
        bar,
        f"  {'Episode':<42}{'split':<13}{'ticks':>8}{'fill':>8}{'ms':>9}",
        bar,
    ]
    for outcome in report.outcomes:
        lines.append(
            f"  {outcome.episode.display_name[:41]:<42}{outcome.episode.split:<13}"
            f"{outcome.metrics.ticks_run:>8}{outcome.metrics.fill_rate:>8.0%}"
            f"{outcome.duration_ms:>9.0f}"
        )
    lines.extend(
        [
            bar,
            f"  p50 {report.p50_ms:.0f} ms   p95 {report.p95_ms:.0f} ms   "
            f"max {report.max_ms:.0f} ms   cases {len(report.outcomes)}",
            bar,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.latency",
        description="Offline latency eval for the deterministic stress model.",
    )
    parser.add_argument("--limit", type=int, default=None, help="sample the first N episodes")
    parser.add_argument(
        "--max-p95-ms",
        type=float,
        default=30_000.0,
        help="fail if p95 exceeds this threshold",
    )
    args = parser.parse_args(argv)

    report = run_latency_eval(limit=args.limit)
    print(render_latency_report(report))
    return 0 if report.p95_ms <= args.max_p95_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())
