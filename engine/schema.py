"""Canonical data shapes for the engine ⇄ agents boundary.

These Pydantic models are the importable source of truth for the schemas written
in prose in `docs/contracts.md` (v0.1.0). They live in the engine because the
engine depends on nothing but the core deps (pydantic + numpy) and must stay
LLM- and cloud-free; `agents/common/` re-exports them in Phase 2 so both halves
build to the same boundary.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "0.5.0"

#: Reference daily realized volatility. A name at this level has ``vol_gain == 1``
#: (the crisis-fragile regime the engine was originally tuned to); a calmer name
#: scales below it. Instruments default to this so a config that does not specify
#: volatility behaves exactly as before this field existed.
REFERENCE_VOLATILITY = 0.09

InvestorType = Literal[
    "forced_seller",
    "panic_seller",
    "trend_follower",
    "bargain_hunter",
    "market_maker",
    "holder",
]

Confidence = Literal["low", "medium", "high"]
EvidenceSource = Literal[
    "alpha_vantage",
    "sec_edgar",
    "user_upload",
    "curated_fixture",
    "synthetic_assumption",
    "gemini_inference",
    "none",
]
ScenarioMode = Literal[
    "historical_saved",
    "live_current",
    "assumption_led",
    "sec_evidence",
    "user_upload",
]
PeerCrowdingCase = Literal["low", "base", "high", "custom"]

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
class EvidenceItem(BaseModel):
    """One source behind a major assumption used in a run."""

    field: str
    source: EvidenceSource = "none"
    confidence: Confidence = "low"
    label: str = ""
    as_of: str | None = None
    notes: str = ""


class EvidenceSummary(BaseModel):
    """Human-readable evidence ledger for the scenario and its assumptions."""

    items: list[EvidenceItem] = Field(default_factory=list)
    summary: str = ""


class PeerCrowdingProfile(BaseModel):
    """Assumptions/evidence for similar funds that may sell the same trade together.

    This is separate from ``CrowdingMix``. The mix controls the behavioural types
    inside the simulated market; the peer profile describes the user's institutional
    overlap risk: how many similar holders exist, how large they are, and how likely
    they are to liquidate on the same trigger.
    """

    case: PeerCrowdingCase = "base"
    peer_fund_count: int = Field(default=0, ge=0)
    overlap_pct: float = Field(default=0.0, ge=0, le=1)
    avg_peer_position_pct_adv: float = Field(
        default=0.0,
        ge=0,
        description="Average peer position as a fraction of one ADV session.",
    )
    shared_trigger_drawdown_pct: float = Field(default=0.0, ge=0, le=1)
    correlated_exit_probability: float = Field(default=0.0, ge=0, le=1)
    leverage_sensitivity: float = Field(default=0.0, ge=0, le=1)
    redemption_pressure: float = Field(default=0.0, ge=0, le=1)
    etf_flow_pressure: float = Field(default=0.0, ge=0, le=1)
    evidence_source: EvidenceSource = "synthetic_assumption"
    confidence: Confidence = "low"
    notes: str = ""


class TimeScale(BaseModel):
    """Translate UI horizons into engine ticks without changing tick mechanics.

    The current convention is 100 ticks per ADV session. A regular US equity
    session is 6.5 hours, so one default tick represents 234 seconds.
    """

    tick_duration_seconds: float = Field(default=234.0, gt=0)
    session_ticks: int = Field(default=100, gt=0)
    exit_horizon_ticks: int | None = Field(default=None, gt=0)
    exit_horizon_hours: float | None = Field(default=None, gt=0)
    exit_horizon_days: float | None = Field(default=None, gt=0)

    def session_hours(self) -> float:
        return self.tick_duration_seconds * self.session_ticks / 3600.0

    def natural_volume_per_tick(self, adv: int) -> int:
        """Shares in one natural market tick under this time scale."""
        return max(1, int(adv / self.session_ticks))

    def effective_exit_horizon_ticks(self) -> int | None:
        """The configured exit horizon converted to engine ticks.

        Explicit ticks win over clock hours, which win over trading days. The
        default ``None`` preserves the legacy ``RunConfig.max_ticks`` horizon.
        """
        if self.exit_horizon_ticks is not None:
            return self.exit_horizon_ticks
        if self.exit_horizon_hours is not None:
            hours_ticks = self.exit_horizon_hours * 3600.0 / self.tick_duration_seconds
            return max(1, int(math.ceil(hours_ticks)))
        if self.exit_horizon_days is not None:
            return max(1, int(math.ceil(self.exit_horizon_days * self.session_ticks)))
        return None


class Instrument(BaseModel):
    symbol: str
    reference_price: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    adv: int = Field(gt=0, description="average daily volume, shares")
    free_float: int = Field(gt=0)
    halt_tier: int = 1
    volatility: float = Field(
        default=REFERENCE_VOLATILITY,
        gt=0,
        description="real daily realized volatility; scales book depth and cascade propensity",
    )


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


class BookPersistence(BaseModel):
    """Controls persistent resting-order behavior in the simulated book.

    ``enabled=False`` is an explicit legacy/test fresh-auction mode. The product
    default is persistent: orders age, stale provider liquidity can cancel, and
    replenishment slows as stress rises.
    """

    enabled: bool = True
    resting_ttl: int = Field(
        default=20,
        gt=0,
        description="Age in ticks after which resting provider orders are stale.",
    )
    base_cancel_rate: float = Field(
        default=0.02,
        ge=0,
        le=1,
        description="Per-tick stale-order cancel probability in calm markets.",
    )
    stress_cancel_multiplier: float = Field(
        default=0.45,
        ge=0,
        description="Extra cancel pressure applied as stress approaches 1.",
    )
    maker_replenish_rate: float = Field(
        default=0.35,
        ge=0,
        le=1,
        description="Fraction of potential provider quotes refreshed in calm markets.",
    )
    max_order_age: int = Field(
        default=80,
        gt=0,
        description="Hard age cap after which resting orders are cancelled.",
    )

    @model_validator(mode="after")
    def _age_bounds(self) -> BookPersistence:
        if self.max_order_age < self.resting_ttl:
            raise ValueError("max_order_age must be >= resting_ttl")
        return self


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
    peer_crowding: PeerCrowdingProfile | None = None
    time_scale: TimeScale = Field(default_factory=TimeScale)
    scenario_mode: ScenarioMode = "historical_saved"
    evidence_summary: EvidenceSummary | None = None
    book_persistence: BookPersistence = Field(default_factory=BookPersistence)
    crisis_intensity: float = Field(
        default=1.0,
        ge=0,
        description=(
            "Overall magnitude of the described/news-driven crisis, decoupled from "
            "trailing volatility. 1.0 is the neutral baseline (the engine behaves "
            "exactly as if this field were absent); >1 is a severe crisis that can "
            "close the exit on even a calm, deep name; <1 is a mild stress. The live "
            "path derives it from the user's stress text and real news sentiment."
        ),
    )

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


class PeerActionCounts(BaseModel):
    """Peer-cohort activity emitted per tick once Phase 2 wires cohorts in."""

    triggered_funds: int = 0
    liquidating_funds: int = 0
    shares_sold: int = 0
    shares_remaining: int = 0


class ImpactAttribution(BaseModel):
    """Price-move attribution fields for exogenous and endogenous effects.

    Values are basis points. Phase 1 defaults them to zero; later engine phases
    populate them so UI/analyst copy can avoid attributing every move to trading.
    """

    exogenous_shock_bps: float = 0.0
    endogenous_trading_bps: float = 0.0
    liquidity_withdrawal_bps: float = 0.0

    @property
    def total_bps(self) -> float:
        return (
            self.exogenous_shock_bps
            + self.endogenous_trading_bps
            + self.liquidity_withdrawal_bps
        )


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
    peer_actions: PeerActionCounts = Field(default_factory=PeerActionCounts)
    impact_attribution: ImpactAttribution = Field(default_factory=ImpactAttribution)
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
    impact_attribution: ImpactAttribution = Field(default_factory=ImpactAttribution)
    ensemble_case: PeerCrowdingCase | None = None
    ensemble_seed: int | None = None


class MetricBand(BaseModel):
    low: float
    median: float
    high: float


class EnsembleCaseSummary(BaseModel):
    case: PeerCrowdingCase
    seeds: list[int] = Field(default_factory=list)
    peer_crowding: PeerCrowdingProfile | None = None
    metrics: Metrics
    representative_replay_ref: str | None = None


class EnsembleResult(BaseModel):
    """Multi-case result envelope for low/base/high crowded-exit bands."""

    type: Literal["ensemble"] = "ensemble"
    run_id: str
    cases: list[EnsembleCaseSummary] = Field(default_factory=list)
    bands: dict[str, MetricBand] = Field(default_factory=dict)
    representative_case: PeerCrowdingCase = "base"
    representative_replay_ref: str | None = None
    evidence_summary: EvidenceSummary | None = None


class MetaRecord(BaseModel):
    """First line of an NDJSON replay (contract §3.4)."""

    type: Literal["meta"] = "meta"
    schema_version: str = SCHEMA_VERSION
    config: RunConfig
