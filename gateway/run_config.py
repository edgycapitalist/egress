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

from gateway.crisis import derive_crisis_intensity
from gateway.instruments import resolve_instrument

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


def build_run_config(
    levers: dict[str, Any] | None,
    *,
    live_data: bool = False,
    crisis_intensity: float | None = None,
) -> RunConfig:
    """Build a validated ``RunConfig`` from the UI levers, based on the flagship.

    Recognised levers (all optional; anything missing keeps the flagship default):

    * ``symbol``          str   — a ticker; its real instrument data (price, ADV, free
                                  float, volatility) is resolved (real Alpha Vantage when
                                  ``live_data`` is set, else the curated/synthetic fallback)
    * ``position_size``   int   — shares to exit; the user's own free, editable position,
                                  independent of the instrument's ADV
    * ``population_size`` int   — number of trading agents (market participants / depth)
    * ``exit_speed``      str   — one of EXIT_SPEED_PRESETS, or…
    * ``participation_rate`` float — an explicit rate, overriding the preset
    * ``crowding_mix``    dict  — {investor_type: fraction}, renormalised to sum 1
    * ``seed``            int   — reproducibility seed

    ``live_data`` enables the real Alpha Vantage feed for the instrument. It defaults
    off so offline runs (tests, cached recordings, the discrimination harness) stay
    deterministic on the curated/synthetic reference. ``crisis_intensity``, when given,
    sets the engine's crisis magnitude (derived from the stress text + news on the live
    path); ``None`` keeps the engine's neutral default.
    """
    levers = levers or {}
    base = flagship_scenario(seed=int(levers.get("seed", 42)))
    data = base.model_dump()

    # Resolve the instrument: real data drives the run when available, otherwise the
    # curated/synthetic fallback. Only the instrument changes — the crowd mix, shocks,
    # and halt rule stay the flagship's, so the comparison is honest.
    inst = resolve_instrument(levers.get("symbol"), live=live_data)
    if inst is not None:
        data["instrument"].update(
            {
                "symbol": inst["symbol"],
                "reference_price": inst["reference_price"],
                "adv": inst["adv"],
                "free_float": inst["free_float"],
                "volatility": inst["volatility"],
            }
        )
        data["position"]["arrival_price"] = inst["reference_price"]

    # Position size is the user's own free, editable share count — the real position
    # being stress-tested — never auto-sized to the name's ADV.
    if levers.get("position_size"):
        data["position"]["quantity"] = int(levers["position_size"])

    if crisis_intensity is not None:
        data["crisis_intensity"] = float(crisis_intensity)

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

    Starts from the user's own text, grounds it with the structured levers, and adds a
    news-derived crisis read so the model schedules shocks that match the real headlines
    and the described severity. ``live_data=True`` so a typed, non-preset ticker resolves
    to its real symbol/data and the prompt names the right instrument (not the flagship).
    """
    levers = levers or {}
    text = str(levers.get("scenario_text") or "").strip()
    cfg = build_run_config(levers, live_data=True)
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
    # A deterministic crisis read from the description + real news, to anchor the model's
    # shock severities (it may still exercise judgement above/below this).
    intensity, detail = derive_crisis_intensity(text, sym, fetch_news=True)
    news = detail["news"]
    crisis = (
        f"Assessed crisis intensity {intensity:.2f} on a 0.3 (mild) to 1.6 (catastrophic) "
        f"scale, from the description and {sym} news (overall sentiment "
        f"{news.get('overall_sentiment')}, {news.get('headline_count')} headlines, "
        f"source {news.get('source')}). Schedule shocks whose severity and number match "
        f"this intensity; a high intensity means severe, repeated shocks and thin support."
    )
    body = "\n\n".join(p for p in (text, spec, crisis) if p)
    return re.sub(r"[ \t]+", " ", body).strip()
