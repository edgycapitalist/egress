"""Run the deterministic engine from the command line:

    python -m engine                     # flagship scenario, default seed
    python -m engine --seed 7 --out runs/my.ndjson

Runs a scenario with fixed-heuristic stances (no LLM, no cloud), writes the
NDJSON replay, and prints the metrics. This is the Phase-1 end-to-end proof.
"""

from __future__ import annotations

import argparse

from engine.core import Engine
from engine.replay.recorder import Recorder
from engine.scenarios import flagship_scenario
from engine.schema import INVESTOR_TYPES, Metrics, RunConfig

_BLOCKS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int = 48) -> str:
    if not values:
        return ""
    step = max(1, len(values) // width)
    sampled = values[::step]
    lo, hi = min(sampled), max(sampled)
    if hi - lo < 1e-9:
        return _BLOCKS[0] * len(sampled)
    return "".join(_BLOCKS[int((v - lo) / (hi - lo) * (len(_BLOCKS) - 1))] for v in sampled)


def _print_report(config: RunConfig, metrics: Metrics, price_path: list[float], out: str) -> None:
    inst = config.instrument
    pos = config.position
    bar = "─" * 58
    print(f"\n{bar}")
    print(f"  EGRESS · deterministic engine · run {config.run_id}")
    print(bar)
    print(f"  Instrument     {inst.symbol} @ {inst.reference_price:.2f}  (ADV {inst.adv:,})")
    print(f"  Position       sell {pos.quantity:,} shares  ·  {config.exit_speed.mode}")
    mix = config.crowding_mix.as_dict()
    mix_str = "  ".join(f"{t.split('_')[0]} {mix[t]:.0%}" for t in INVESTOR_TYPES)
    print(f"  Crowding mix   {mix_str}")
    print(bar)
    print(f"  Price path     {_sparkline(price_path)}")
    print(f"                 {price_path[0]:.2f}  →  {metrics.final_price:.2f}"
          f"   (drawdown {metrics.max_drawdown_pct:.0%})")
    print(bar)
    verdict = "EXIT CLOSED" if metrics.fill_rate < 0.999 else "fully exited"
    print(f"  Fill rate      {metrics.fill_rate:.1%}   ({metrics.filled_qty:,} sold)   [{verdict}]")
    print(f"  Stuck          {metrics.pct_stuck:.1%}   ({metrics.stuck_qty:,} shares)")
    vwap = f"{metrics.vwap_sold:.2f}" if metrics.vwap_sold is not None else "—"
    print(f"  VWAP sold      {vwap}   vs arrival {metrics.arrival_price:.2f}")
    print(f"  Impl shortfall {metrics.implementation_shortfall_bps:.0f} bps"
          f"   ·   slippage {metrics.slippage_bps:.0f} bps")
    tte = metrics.time_to_exit_ticks if metrics.time_to_exit_ticks is not None else "never"
    print(f"  Time to exit   {tte}   ·   halts {metrics.halt_count}"
          f"   ·   ticks {metrics.ticks_run}")
    print(bar)
    print(f"  Replay written {out}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m engine",
        description="Run the deterministic Egress simulation engine (no LLM, no cloud).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=None, help="NDJSON replay path")
    args = parser.parse_args(argv)

    config = flagship_scenario(seed=args.seed)
    out = args.out or f"runs/{config.run_id}.ndjson"
    with Recorder(out) as recorder:
        engine = Engine(config, recorder=recorder)
        metrics = engine.run_baseline()

    _print_report(config, metrics, engine.price_path, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
