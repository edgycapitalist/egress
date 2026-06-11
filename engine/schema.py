"""Canonical data shapes for the engine ⇄ agents boundary.

These Pydantic models are the importable source of truth for the schemas written
in prose in `docs/contracts.md` (v0.1.0). They live in the engine because the
engine depends on nothing but the core deps (pydantic + numpy) and must stay
LLM- and cloud-free; `agents/common/` re-exports them in Phase 2 so both halves
build to the same boundary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "0.1.0"

InvestorType = Literal[
    "forced_seller",
    "panic_seller",
    "trend_follower",
    "bargain_hunter",
    "market_maker",
    "holder",
]

#: Closed enum of investor types, in canonical order.
INVESTOR_TYPES: tuple[InvestorType, ...] = (
    "forced_seller",
    "panic_seller",
    "trend_follower",
    "bargain_hunter",
    "market_maker",
    "holder",
)

#: session.state key each archetype writes its stance to (contract §4).
STANCE_KEYS: dict[InvestorType, str] = {t: f"{t}_stance" for t in INVESTOR_TYPES}


# --------------------------------------------------------------------------- #
# 1. Engine input — RunConfig
# --------------------------------------------------------------------------- #
class Instrument(BaseModel):
    symbol: str
    reference_price: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    adv: int = Field(gt=0, description="average daily volume, shares")
    free_float: int = Field(gt=0)
    halt_tier: int = 1


class Position(BaseModel):
    side: Literal["sell"] = "sell"
    quantity: int = Field(gt=0)
    arrival_price: float = Field(gt=0)


class ExitSpeed(BaseModel):
    mode: Literal["participation", "twap", "immediate"]
    participation_rate: float | None = Field(default=None, ge=0, le=1)
    horizon_ticks: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _require_mode_field(self) -> ExitSpeed:
        if self.mode == "participation" and self.participation_rate is None:
            raise ValueError("participation mode requires participation_rate")
        if self.mode == "twap" and self.horizon_ticks is None:
            raise ValueError("twap mode requires horizon_ticks")
        return self


class CrowdingMix(BaseModel):
    forced_seller: float = Field(ge=0)
    panic_seller: float = Field(ge=0)
    trend_follower: float = Field(ge=0)
    bargain_hunter: float = Field(ge=0)
    market_maker: float = Field(ge=0)
    holder: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> CrowdingMix:
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"crowding_mix must sum to 1.0, got {total}")
        return self

    def as_dict(self) -> dict[InvestorType, float]:
        return {t: getattr(self, t) for t in INVESTOR_TYPES}

    def counts(self, population_size: int) -> dict[InvestorType, int]:
        """Split a population into integer per-type counts that sum exactly to N."""
        raw = {t: getattr(self, t) * population_size for t in INVESTOR_TYPES}
        floored = {t: int(v) for t, v in raw.items()}
        remainder = population_size - sum(floored.values())
        # Hand the leftover to the types with the largest fractional parts.
        order = sorted(INVESTOR_TYPES, key=lambda t: raw[t] - floored[t], reverse=True)
        for t in order[:remainder]:
            floored[t] += 1
        return floored


class Shock(BaseModel):
    tick: int = Field(ge=0)
    kind: Literal["news", "price"]
    severity: float = Field(ge=0, le=1)
    note: str = ""


class HaltRule(BaseModel):
    band_pct: float = Field(gt=0)
    window_ticks: int = Field(gt=0)
    pause_ticks: int = Field(gt=0)


class RunConfig(BaseModel):
    run_id: str
    seed: int = 42
    instrument: Instrument
    position: Position
    exit_speed: ExitSpeed
    crowding_mix: CrowdingMix
    population_size: int = Field(gt=0)
    shock_schedule: list[Shock] = Field(default_factory=list)
    halt_rule: HaltRule
    max_ticks: int = Field(gt=0)
    ticks_per_window: int = Field(gt=0)
    baseline_mode: bool = True

    @model_validator(mode="after")
    def _coherent(self) -> RunConfig:
        if self.ticks_per_window > self.max_ticks:
            raise ValueError("ticks_per_window must be <= max_ticks")
        for s in self.shock_schedule:
            if s.tick >= self.max_ticks:
                raise ValueError(f"shock tick {s.tick} is outside [0, max_ticks)")
        return self


# --------------------------------------------------------------------------- #
# 2. Per-window input — Stance
# --------------------------------------------------------------------------- #
class Stance(BaseModel):
    aggressiveness: float = Field(ge=0, le=1)
    sell_threshold_pct: float = Field(ge=0)
    participation: float = Field(ge=0, le=1)
    updated_at_tick: int = 0
    rationale: str = ""


# --------------------------------------------------------------------------- #
# 3. Engine output
# --------------------------------------------------------------------------- #
class Depth(BaseModel):
    bids: list[tuple[float, int]] = Field(default_factory=list)
    asks: list[tuple[float, int]] = Field(default_factory=list)


class MarketState(BaseModel):
    run_id: str
    tick: int
    window_index: int
    last_price: float
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    depth: Depth
    cumulative_filled: int
    remaining_qty: int
    halted: bool


class Fill(BaseModel):
    price: float
    size: int
    aggressor: Literal["buy", "sell"]


class TickEvent(BaseModel):
    type: Literal["tick"] = "tick"
    tick: int
    last_price: float
    best_bid: float | None
    best_ask: float | None
    depth_bid: int
    depth_ask: int
    fills: list[Fill] = Field(default_factory=list)
    filled_this_tick: int
    cumulative_filled: int
    vwap_sold: float | None
    actions_by_type: dict[str, int]
    halted: bool
    halt_started: bool
    shock_applied: Shock | None = None


class Metrics(BaseModel):
    type: Literal["metrics"] = "metrics"
    run_id: str
    fill_rate: float
    filled_qty: int
    stuck_qty: int
    pct_stuck: float
    implementation_shortfall_bps: float
    slippage_bps: float
    vwap_sold: float | None
    arrival_price: float
    final_price: float
    max_drawdown_pct: float
    time_to_exit_ticks: int | None
    halt_triggered: bool
    halt_count: int
    ticks_run: int


class MetaRecord(BaseModel):
    """First line of an NDJSON replay (contract §3.4)."""

    type: Literal["meta"] = "meta"
    schema_version: str = SCHEMA_VERSION
    config: RunConfig
