"""Turn the frontend's scenario levers into a validated engine ``RunConfig``.

The scenario builder sends a small, flat set of levers — position size, exit speed,
and the crowding mix. This module folds them onto the flagship scenario as a base
and validates the result against the boundary schema (``engine/schema.py``) before a
run starts, exactly as the contract requires. It imports only the engine (a core
dep: pydantic + numpy), never the agents or the cloud.

For a live **Gemini** run the plain-language text is what the Scenario Author parses;
for a live **deterministic** run (the offline default) these structured levers drive
the engine directly, so the builder's controls have a real, visible effect either way.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from engine.scenarios import flagship_scenario
from engine.schema import INVESTOR_TYPES, RunConfig

# Exit-speed presets the UI exposes as labelled choices map to a participation rate.
EXIT_SPEED_PRESETS: dict[str, float] = {
    "patient": 0.06,
    "measured": 0.12,
    "urgent": 0.20,
    "fire_sale": 0.35,
}
DEFAULT_EXIT_SPEED = "measured"


def _normalise_mix(mix: dict[str, float] | None) -> dict[str, float]:
    """Coerce a partial/odd crowding mix into six non-negative fractions summing to 1."""
    if not mix:
        return flagship_scenario().crowding_mix.as_dict()
    vals = {t: max(0.0, float(mix.get(t, 0.0))) for t in INVESTOR_TYPES}
    total = sum(vals.values())
    if total <= 0:
        return flagship_scenario().crowding_mix.as_dict()
    # Largest-remainder rounding to land exactly on 1.0 within the schema tolerance.
    return {t: v / total for t, v in vals.items()}


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def build_run_config(levers: dict[str, Any] | None) -> RunConfig:
    """Build a validated ``RunConfig`` from the UI levers, based on the flagship.

    Recognised levers (all optional; anything missing keeps the flagship default):

    * ``position_size``   int   — shares to exit
    * ``population_size`` int   — number of trading agents (market participants / depth)
    * ``exit_speed``      str   — one of EXIT_SPEED_PRESETS, or…
    * ``participation_rate`` float — an explicit rate, overriding the preset
    * ``crowding_mix``    dict  — {investor_type: fraction}, renormalised to sum 1
    * ``seed``            int   — reproducibility seed
    """
    levers = levers or {}
    base = flagship_scenario(seed=int(levers.get("seed", 42)))
    data = base.model_dump()

    if levers.get("position_size"):
        data["position"]["quantity"] = int(levers["position_size"])

    if levers.get("population_size"):
        data["population_size"] = max(1, int(levers["population_size"]))

    rate = levers.get("participation_rate")
    if rate is None:
        preset = str(levers.get("exit_speed", DEFAULT_EXIT_SPEED)).lower()
        rate = EXIT_SPEED_PRESETS.get(preset, EXIT_SPEED_PRESETS[DEFAULT_EXIT_SPEED])
    data["exit_speed"] = {
        "mode": "participation",
        "participation_rate": float(rate),
        "horizon_ticks": None,
    }

    data["crowding_mix"] = _normalise_mix(levers.get("crowding_mix"))

    # A fresh id per custom run so live NDJSON files never collide with the flagship.
    data["run_id"] = f"run-{_short_id()}"
    data["baseline_mode"] = True

    return RunConfig.model_validate(data)


def scenario_prompt(levers: dict[str, Any] | None) -> str:
    """Compose the plain-language prompt the live Gemini Scenario Author parses.

    Starts from the user's own text and appends the structured levers so the live
    parse and the deterministic fallback describe the same run.
    """
    levers = levers or {}
    text = str(levers.get("scenario_text") or "").strip()
    cfg = build_run_config(levers)
    pos = cfg.position.quantity
    rate = cfg.exit_speed.participation_rate or 0.0
    sym = cfg.instrument.symbol
    spec = (
        f"Exit {pos:,} shares of {sym} into a crisis sell-off at about a "
        f"{rate:.0%} participation rate. Crowding mix: "
        + ", ".join(
            f"{t.replace('_', ' ')} {cfg.crowding_mix.as_dict()[t]:.0%}" for t in INVESTOR_TYPES
        )
        + "."
    )
    if not text:
        return spec
    # Keep the user's narrative first; ground it with the explicit numbers.
    return re.sub(r"\s+", " ", f"{text}\n\n{spec}").strip()
