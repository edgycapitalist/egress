"""Calibration backtest — the generator-critic loop against a real episode.

This is the heart of Phase 4. It demonstrates the behavioural-fidelity quality gate
end to end:

1. Start a crowd. By default an *over-rational* one (the known LLM failure mode:
   sellers too calm, support too sticky) so the loop has something to fix.
2. Run the deterministic pipeline with the calibration critic. The critic compares
   the run to the real CVNA 2022 unwind and, if the crowd was too calm, emits bounded
   per-type stance nudges.
3. Compose the nudges and re-run. Repeat until the crowd reproduces the episode's
   behavioural signature (``plausible``) or a hard iteration cap is hit.

It runs entirely on the deterministic baseline — no LLM, no cloud — so it is
reproducible and costs nothing, which is exactly the cost discipline the build asks
for. The live Gemini judge produces the same numeric verdict with a model-written
narrative; see ``python -m agents.orchestrator --live --critic``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.critic.adjust import calm_adjustments, compose_adjustments
from agents.critic.episode import episode_for_symbol, signature
from agents.critic.schema import (
    CalibrationAdjustments,
    CalibrationReport,
    Episode,
    EpisodeSignature,
    identity_adjustments,
)
from agents.orchestrator.driver import run_baseline_simulation
from engine.schema import RunConfig

DEFAULT_MAX_ITERATIONS = 4


@dataclass
class Iteration:
    index: int
    adjustments: CalibrationAdjustments
    metrics: dict
    report: CalibrationReport


@dataclass
class BacktestResult:
    episode: Episode
    signature: EpisodeSignature
    iterations: list[Iteration] = field(default_factory=list)
    converged: bool = False

    @property
    def final(self) -> Iteration:
        return self.iterations[-1]


async def run_calibration_backtest(
    *,
    config: RunConfig | None = None,
    start: str = "calm",
    intensity: float = 1.0,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> BacktestResult:
    """Run the generator-critic calibration loop and return every iteration.

    ``start='calm'`` seeds the over-rational failure mode; ``start='default'`` checks
    the shipped heuristic crowd as-is. The loop stops as soon as the critic returns a
    plausible verdict, with ``max_iterations`` as the backstop.
    """
    if config is None:
        from engine.scenarios import flagship_scenario

        config = flagship_scenario()

    symbol = config.instrument.symbol
    episode = episode_for_symbol(symbol)
    if episode is None:
        raise ValueError(f"no curated episode for {symbol}; cannot backtest")

    running: CalibrationAdjustments = (
        calm_adjustments(intensity) if start == "calm" else identity_adjustments()
    )

    result = BacktestResult(episode=episode, signature=signature(episode.closes))
    for i in range(max(1, max_iterations)):
        res = await run_baseline_simulation(
            config, with_critic=True, adjustments=running.model_dump()
        )
        report = CalibrationReport.model_validate(res["calibration_report"])
        result.iterations.append(
            Iteration(index=i, adjustments=running, metrics=res["run_metrics"], report=report)
        )
        if report.plausible:
            result.converged = True
            break
        # Sharpen the crowd by the critic's correction and try again.
        running = compose_adjustments(running, report.adjustments)

    return result


def _adj_summary(adj: CalibrationAdjustments) -> str:
    """One-line view of how far each block has been pushed from the heuristic."""
    sellers = adj.for_type("forced_seller").aggressiveness_mult
    support = adj.for_type("market_maker").aggressiveness_mult
    return f"sellers ×{sellers:.2f}, support ×{support:.2f}"


def render_report(result: BacktestResult, *, start: str) -> str:
    """A plain-text findings report for ``make eval``."""
    sig = result.signature
    ep = result.episode
    bar = "─" * 72
    lines = [
        bar,
        "  EGRESS · calibration backtest (Phase 4)",
        bar,
        f"  Reference episode : {ep.title}",
        f"  Window            : {ep.window}",
        f"  Signature         : {sig.max_drawdown:.0%} peak-to-trough drawdown, "
        f"worst day {sig.worst_day_return:.0%}, disorderliness {sig.disorderliness:.2f}",
        f"  Starting crowd    : "
        f"{'over-rational (the failure mode)' if start == 'calm' else 'shipped heuristic'}",
        bar,
        "  Iter  crowd nudge            drawdown   stuck   halt   verdict (fidelity)",
    ]
    for it in result.iterations:
        m = it.metrics
        lines.append(
            f"  {it.index:>4}  {_adj_summary(it.adjustments):<24} "
            f"{m['max_drawdown_pct']:>7.0%}  {m['pct_stuck']:>5.0%}  "
            f"{'yes' if m['halt_triggered'] else ' no':>4}   "
            f"{it.report.verdict} ({it.report.plausibility_score:.0%})"
        )
    lines.append(bar)
    final = result.final
    if result.converged:
        lines.append(
            f"  Converged in {len(result.iterations)} iteration(s): the calibrated crowd "
            "reproduces the episode's behavioural signature."
        )
    else:
        lines.append(
            f"  Did not converge within {len(result.iterations)} iteration(s); "
            f"final verdict: {final.report.verdict}."
        )
    lines.append("")
    lines.append("  Critic's verdict on the final run:")
    for g in final.report.gaps:
        mark = "ok " if g.plausible else "SHORT"
        lines.append(
            f"    · {g.axis:<10} simulated {g.simulated:>5.2f}  "
            f"vs expected {g.expected:>5.2f}  [{mark}]"
        )
    lines.append(bar)
    return "\n".join(lines)
