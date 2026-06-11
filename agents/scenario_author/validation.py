"""Deterministic assembly + validation of a ``RunConfig`` from a scenario draft.

The Scenario Author LLM decides the *user-facing* choices — which instrument, how
big the position, how fast to exit, the crowding mix, and the shock schedule — and
emits a :class:`ScenarioDraft`. This module turns that draft into a fully-formed,
schema-valid :class:`RunConfig` deterministically: it resolves the instrument's
reference data from the Market Data backend (never trusting the model to copy ADV
or free float), fills the fixed halt rule from the exchange halt tier, assigns the
``run_id`` and ``seed``, and normalises the crowding mix. Validation happens against
the engine's own schema, so an invalid scenario can never reach a run (contract §1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from engine.schema import INVESTOR_TYPES, RunConfig
from mcp.market_data.data import get_instrument_reference

# Default run mechanics the model should not have to think about.
DEFAULT_POPULATION = 5000
DEFAULT_MAX_TICKS = 300
DEFAULT_TICKS_PER_WINDOW = 10

# Halt band by exchange tier — a fixed constraint the engine enforces (AGENTS.md §5).
_HALT_BAND_BY_TIER: dict[int, float] = {1: 0.10, 2: 0.20, 3: 0.30}


class DraftCrowding(BaseModel):
    """The crowding mix as the model proposes it; normalised before validation."""

    forced_seller: float = Field(default=0.0, ge=0)
    panic_seller: float = Field(default=0.0, ge=0)
    trend_follower: float = Field(default=0.0, ge=0)
    bargain_hunter: float = Field(default=0.0, ge=0)
    market_maker: float = Field(default=0.0, ge=0)
    holder: float = Field(default=0.0, ge=0)

    def normalised(self) -> dict[str, float]:
        raw = {t: max(0.0, getattr(self, t)) for t in INVESTOR_TYPES}
        total = sum(raw.values())
        if total <= 0:
            # Fall back to an even mix rather than fail — defensive against a blank draft.
            return {t: 1.0 / len(INVESTOR_TYPES) for t in INVESTOR_TYPES}
        return {t: v / total for t, v in raw.items()}


class DraftShock(BaseModel):
    tick: int = Field(ge=0)
    kind: Literal["news", "price"]
    severity: float = Field(ge=0, le=1)
    note: str = ""


class ScenarioDraft(BaseModel):
    """Structured output of the Scenario Author LLM (its ``output_schema``)."""

    symbol: str = Field(description="Ticker the user described, e.g. ACME.")
    position_quantity: int = Field(gt=0, description="Shares to exit (long position).")
    arrival_price: float | None = Field(
        default=None, description="Benchmark price; defaults to the reference price."
    )
    exit_mode: Literal["participation", "twap", "immediate"] = "participation"
    participation_rate: float | None = Field(default=0.10, ge=0, le=1)
    horizon_ticks: int | None = Field(default=None, gt=0)
    crowding: DraftCrowding = Field(default_factory=DraftCrowding)
    shocks: list[DraftShock] = Field(default_factory=list)
    population_size: int = Field(default=DEFAULT_POPULATION, gt=0)
    max_ticks: int = Field(default=DEFAULT_MAX_TICKS, gt=0)
    ticks_per_window: int = Field(default=DEFAULT_TICKS_PER_WINDOW, gt=0)
    rationale: str = Field(default="", description="One sentence on the scenario.")


def _halt_rule(halt_tier: int) -> dict:
    band = _HALT_BAND_BY_TIER.get(halt_tier, 0.10)
    return {"band_pct": band, "window_ticks": 5, "pause_ticks": 10}


def build_run_config(
    draft: ScenarioDraft | dict, *, run_id: str, seed: int, baseline_mode: bool = True
) -> tuple[RunConfig, dict]:
    """Assemble and validate a :class:`RunConfig` from a draft.

    Returns ``(run_config, instrument_reference)``. Raises ``pydantic.ValidationError``
    if the assembled scenario is invalid — the gate that stops a bad run starting.
    """
    if isinstance(draft, dict):
        draft = ScenarioDraft.model_validate(draft)

    reference = get_instrument_reference(draft.symbol)
    ref_price = reference["reference_price"]

    exit_speed: dict = {"mode": draft.exit_mode}
    if draft.exit_mode == "participation":
        exit_speed["participation_rate"] = draft.participation_rate or 0.10
    elif draft.exit_mode == "twap":
        exit_speed["horizon_ticks"] = draft.horizon_ticks or draft.max_ticks

    # Keep shocks inside the run; drop any the model placed past the horizon.
    shocks = [s.model_dump() for s in draft.shocks if s.tick < draft.max_ticks]

    config = RunConfig(
        run_id=run_id,
        seed=seed,
        instrument={
            "symbol": reference["symbol"],
            "reference_price": ref_price,
            "tick_size": reference["tick_size"],
            "adv": reference["adv"],
            "free_float": reference["free_float"],
            "halt_tier": reference["halt_tier"],
        },
        position={
            "side": "sell",
            "quantity": draft.position_quantity,
            "arrival_price": draft.arrival_price or ref_price,
        },
        exit_speed=exit_speed,
        crowding_mix=draft.crowding.normalised(),
        population_size=draft.population_size,
        shock_schedule=shocks,
        halt_rule=_halt_rule(reference["halt_tier"]),
        max_ticks=draft.max_ticks,
        ticks_per_window=draft.ticks_per_window,
        baseline_mode=baseline_mode,
    )
    return config, reference
