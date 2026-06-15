"""Record the committed cached replays under docs/replays/.

Cached mode streams a pre-recorded NDJSON so the demo runs instantly and offline.
This regenerates every cached replay deterministically:

* ``flagship-42`` — the home demo: the CVNA flagship at its manual 250k position.
* one file per curated ticker (CVNA/SIVB/AAPL/SPY) at a fixed 20% of ADV — the same
  config the live ticker picker uses — so cached and live agree and the picker works
  in cached mode too.

Each replay gets a ``*.analysis.txt`` sidecar (the deterministic analyst narrative)
so the explanation panel shows with no LLM call.

    python scripts/record_replays.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for a file run

from agents.analyst.baseline import render_summary
from engine.attribution import estimate_counterfactual_attribution
from engine.core import Engine
from engine.presets import DEFAULT_POSITION_FRAC, PRESETS
from engine.replay.recorder import Recorder, replace_metrics
from engine.scenarios import flagship_scenario
from gateway.run_config import build_run_config

REPLAY_DIR = Path("docs/replays")


def _record(cfg, stem: str) -> str:
    path = REPLAY_DIR / f"{stem}.ndjson"
    with Recorder(str(path)) as rec:
        metrics = Engine(cfg, recorder=rec).run_baseline()
    counterfactual = estimate_counterfactual_attribution(cfg, metrics)
    metrics = metrics.model_copy(update={"counterfactual_attribution": counterfactual})
    replace_metrics(path, metrics)
    sidecar = path.with_suffix(".analysis.txt")
    summary = render_summary(cfg.model_dump(), metrics.model_dump())
    sidecar.write_text(summary + "\n", encoding="utf-8")
    return (
        f"{stem:<14} {cfg.instrument.symbol:<6} ref {cfg.instrument.reference_price:>8.2f}  "
        f"fill {metrics.fill_rate:>4.0%}  stuck {metrics.pct_stuck:>4.0%}  "
        f"halts {metrics.halt_count}"
    )


def main() -> int:
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)

    # The home demo: CVNA flagship at its manual position.
    print(_record(flagship_scenario(), "flagship-42"))

    # One per curated ticker, at a fixed 20% of ADV (the live picker's config).
    for symbol in PRESETS:
        preset = PRESETS[symbol]
        cfg = build_run_config(
            {"symbol": symbol, "position_size": round(DEFAULT_POSITION_FRAC * preset.adv)}
        )
        cfg = cfg.model_copy(
            update={"run_id": f"{symbol.lower()}-cached", "scenario_mode": "historical_saved"}
        )
        print(_record(cfg, symbol.lower()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
