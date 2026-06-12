"""Run the Egress calibration backtest from the command line.

    python -m eval                      # over-rational crowd, calibrate to CVNA 2022
    python -m eval --start default      # check the shipped heuristic crowd as-is
    python -m eval --max-iterations 6

Runs fully offline on the deterministic baseline — no LLM, no cloud, no credits —
and prints a findings report: the real episode's signature, each loop iteration's
metrics and verdict, and whether the calibrated crowd converged to the episode's
behaviour. This is the credibility layer (AGENTS.md §11, Phase 4).
"""

from __future__ import annotations

import argparse
import asyncio

from eval.backtest import render_report, run_calibration_backtest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval",
        description="Calibration backtest: the generator-critic loop vs a real episode.",
    )
    parser.add_argument(
        "--start",
        choices=["calm", "default"],
        default="calm",
        help="'calm' seeds the over-rational failure mode (default); 'default' checks "
        "the shipped heuristic crowd as-is",
    )
    parser.add_argument("--intensity", type=float, default=1.0, help="how calm the start is (0..1)")
    parser.add_argument("--max-iterations", type=int, default=4)
    args = parser.parse_args(argv)

    result = asyncio.run(
        run_calibration_backtest(
            start=args.start,
            intensity=args.intensity,
            max_iterations=args.max_iterations,
        )
    )
    print(render_report(result, start=args.start))
    return 0 if result.converged else 1


if __name__ == "__main__":
    raise SystemExit(main())
