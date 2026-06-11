"""Run the Egress ADK orchestration from the command line.

    python -m agents.orchestrator               # baseline: no LLM, no cloud
    python -m agents.orchestrator --seed 7
    python -m agents.orchestrator --live        # live: real Gemini via Vertex AI

Baseline mode drives the real ADK ``SequentialAgent`` lifecycle (setup → simulate
loop → finalize → analyst) with the archetype and analyst LLMs swapped for their
deterministic stand-ins, so it runs end to end with zero LLM calls and zero cost.

Live mode runs the product path: the Gemini scenario author parses a plain-language
scenario, the six Gemini archetype mood-setters set stances each window, the
deterministic engine runs the market, and the Gemini analyst explains the result.
It requires ADC + a project (confirm with ``make auth-check``) and spends credits.
"""

from __future__ import annotations

import argparse
import asyncio

from engine.scenarios import flagship_scenario

from agents.orchestrator.driver import run_baseline_simulation, run_live_simulation

# A plain-language version of the flagship scenario for the live scenario author to
# parse. It describes the same crowded mid-cap downgrade the baseline runs.
FLAGSHIP_PROMPT = (
    "I hold 250,000 shares of ACME, a crowded mid-cap industrial, and I'm worried "
    "about a crisis exit. Simulate selling the whole position into a sell-off "
    "triggered by a surprise rating downgrade to junk: a sharp gap down on heavy "
    "volume, with forced sellers hitting risk limits, panic sellers and trend "
    "followers piling on, and thin bargain-hunter and market-maker support. Exit at "
    "about a 12% participation rate. How far does the price fall, and how much of the "
    "position gets stuck?"
)


def _print_report(title: str, analyst_label: str, result: dict) -> int:
    metrics = result["run_metrics"] or {}
    bar = "─" * 58
    print(f"\n{bar}")
    print(f"  {title} · run {result['run_id']}")
    print(bar)
    if result["error"]:
        print(f"  ERROR: {result['error']}")
        return 1
    if not metrics:
        print("  ERROR: no metrics produced")
        return 1
    drawdown = metrics["max_drawdown_pct"]
    print(f"  Fill rate      {metrics['fill_rate']:.1%}   ({metrics['filled_qty']:,} sold)")
    print(f"  Stuck          {metrics['pct_stuck']:.1%}   ({metrics['stuck_qty']:,} shares)")
    print(f"  Final price    {metrics['final_price']:.2f}   (drawdown {drawdown:.0%})")
    shortfall = metrics["implementation_shortfall_bps"]
    print(f"  Impl shortfall {shortfall:.0f} bps   ·   slippage {metrics['slippage_bps']:.0f} bps")
    print(f"  Halts          {metrics['halt_count']}   ·   ticks {metrics['ticks_run']}")
    print(f"  Replay         {result['replay_ref']}")
    print(bar)
    print(f"  Analyst ({analyst_label}):")
    print(f"  {result['analysis']}")
    print(f"{bar}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.orchestrator",
        description="Run the Egress ADK orchestration (baseline by default, --live for Gemini).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--live",
        action="store_true",
        help="run the live Gemini pipeline via Vertex AI (spends credits)",
    )
    parser.add_argument(
        "--scenario",
        default=FLAGSHIP_PROMPT,
        help="plain-language scenario for the live scenario author",
    )
    args = parser.parse_args(argv)

    if args.live:
        print("Running the LIVE pipeline (real Gemini via Vertex AI) — this spends credits.")
        print(f"Scenario: {args.scenario}")
        result = asyncio.run(run_live_simulation(args.scenario))
        return _print_report("EGRESS · ADK orchestration (LIVE Gemini)", "Gemini", result)

    config = flagship_scenario(seed=args.seed)
    result = asyncio.run(run_baseline_simulation(config))
    return _print_report("EGRESS · ADK orchestration (baseline)", "deterministic baseline", result)


if __name__ == "__main__":
    raise SystemExit(main())
