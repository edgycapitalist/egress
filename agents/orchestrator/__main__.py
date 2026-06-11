"""Run the full ADK orchestration in baseline mode from the command line:

    python -m agents.orchestrator            # flagship scenario, no LLM, no cloud
    python -m agents.orchestrator --seed 7

This drives the real ADK ``SequentialAgent`` lifecycle (setup → simulate loop →
finalize → analyst) with the archetype and analyst LLMs swapped for their
deterministic stand-ins, so it runs end to end with zero LLM calls and zero cost.
It is the Phase-2 proof that the orchestration produces a cascade, metrics, a
replay, and a narrative without touching Gemini. For the live Vertex path, set up
ADC + a project, confirm with ``make auth-check``, then call
``agents.orchestrator.run_live_simulation``.
"""

from __future__ import annotations

import argparse
import asyncio

from engine.scenarios import flagship_scenario

from agents.orchestrator.driver import run_baseline_simulation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.orchestrator",
        description="Run the Egress ADK orchestration in baseline mode (no LLM, no cloud).",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    config = flagship_scenario(seed=args.seed)
    result = asyncio.run(run_baseline_simulation(config))

    metrics = result["run_metrics"] or {}
    bar = "─" * 58
    print(f"\n{bar}")
    print(f"  EGRESS · ADK orchestration (baseline) · run {result['run_id']}")
    print(bar)
    if result["error"]:
        print(f"  ERROR: {result['error']}")
        return 1
    drawdown = metrics["max_drawdown_pct"]
    print(f"  Fill rate      {metrics['fill_rate']:.1%}   ({metrics['filled_qty']:,} sold)")
    print(f"  Stuck          {metrics['pct_stuck']:.1%}   ({metrics['stuck_qty']:,} shares)")
    print(f"  Final price    {metrics['final_price']:.2f}   (drawdown {drawdown:.0%})")
    print(f"  Halts          {metrics['halt_count']}   ·   ticks {metrics['ticks_run']}")
    print(f"  Replay         {result['replay_ref']}")
    print(bar)
    print("  Analyst (deterministic baseline):")
    print(f"  {result['analysis']}")
    print(f"{bar}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
