"""Vectorized deterministic agent population (Tier B).

Thousands of lightweight agents, one row per agent in NumPy arrays, parameterised
by their type's current stance (Tier A) and acting with no LLM call. Each tick the
population splits into:

* **liquidity providers** — market makers and bargain hunters post resting bids
  (and makers also post asks). Makers withdraw as stress rises; bargain hunters
  sit deeper, forming a discount floor.
* **aggressors** — forced, panic, trend, and holder agents send marketable sells
  that sweep the resting bids and push the price down.

Staggered per-agent thresholds are what turn one early seller into a cascade: one
breaks, the price moves into the next agent's trigger, and so on. The population
owns only mechanics; the *level* of each trigger comes from the stance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.schema import INVESTOR_TYPES, InvestorType, Stance

# Stable integer id per type, in canonical order.
TYPE_ID: dict[InvestorType, int] = {t: i for i, t in enumerate(INVESTOR_TYPES)}


@dataclass
class OrderIntent:
    side: str  # "buy" | "sell"
    size: int
    price: float | None  # None => marketable (sweep)
    investor_type: str


@dataclass
class MarketView:
    """What the population sees each tick."""

    ref_price: float
    last_price: float
    recent_return: float  # fractional change over the recent lookback (<0 = falling)
    stress: float  # [0, 1] from the StressRegime
    tick: int

    @property
    def drop(self) -> float:
        return max(0.0, (self.ref_price - self.last_price) / self.ref_price)


class Population:
    def __init__(self, config, rng: np.random.Generator) -> None:
        n = config.population_size
        counts = config.crowding_mix.counts(config.population_size)

        type_id = np.empty(n, dtype=np.int8)
        cursor = 0
        for t in INVESTOR_TYPES:
            c = counts[t]
            type_id[cursor : cursor + c] = TYPE_ID[t]
            cursor += c
        self.type_id = type_id

        # Per-agent stagger factor (fixed) applied to the stance threshold, and a
        # base order size. Sellers hold sellable shares; buyers hold buy capacity.
        self.factor = (0.4 + 1.6 * rng.random(n)).astype(np.float64)
        self.size_base = rng.lognormal(mean=np.log(1200.0), sigma=0.6, size=n)
        self.holdings = rng.lognormal(mean=np.log(1500.0), sigma=0.6, size=n)
        self.has_acted = np.zeros(n, dtype=bool)

        # Precompute index arrays per type for cheap masking each tick.
        self.idx: dict[InvestorType, np.ndarray] = {
            t: np.where(type_id == TYPE_ID[t])[0] for t in INVESTOR_TYPES
        }

    # -- per-tick decision ------------------------------------------------- #
    def step(
        self, view: MarketView, stances: dict[InvestorType, Stance], rng: np.random.Generator
    ) -> tuple[list[OrderIntent], list[OrderIntent], dict[str, int]]:
        """Return (liquidity_intents, aggressor_intents, actions_by_type)."""
        liquidity: list[OrderIntent] = []
        aggressors: list[OrderIntent] = []
        actions = dict.fromkeys(INVESTOR_TYPES, 0)

        self._market_makers(view, stances["market_maker"], liquidity, actions)
        self._bargain_hunters(view, stances["bargain_hunter"], rng, liquidity, actions)
        self._forced_sellers(view, stances["forced_seller"], aggressors, actions)
        self._panic_sellers(view, stances["panic_seller"], rng, aggressors, actions)
        self._trend_followers(view, stances["trend_follower"], aggressors, actions)
        self._holders(view, stances["holder"], rng, aggressors, actions)
        return liquidity, aggressors, actions

    # -- liquidity providers ---------------------------------------------- #
    def _market_makers(self, view, stance, out, actions) -> None:
        idx = self.idx["market_maker"]
        if idx.size == 0:
            return
        # Makers pull back as stress rises; spread widens with stress.
        liquidity_factor = (
            max(0.0, 1.0 - view.stress) * stance.aggressiveness * stance.participation
        )
        if liquidity_factor <= 1e-3:
            return
        half_spread = view.last_price * (0.0005 + 0.01 * view.stress)
        bid_px = view.last_price - half_spread
        ask_px = view.last_price + half_spread
        sizes = (self.size_base[idx] * liquidity_factor).astype(int)
        acted = 0
        for s in sizes:
            if s <= 0:
                continue
            out.append(OrderIntent("buy", int(s), bid_px, "market_maker"))
            out.append(OrderIntent("sell", int(s), ask_px, "market_maker"))
            acted += 1
        actions["market_maker"] = acted

    def _bargain_hunters(self, view, stance, rng, out, actions) -> None:
        idx = self.idx["bargain_hunter"]
        if idx.size == 0:
            return
        # Each posts a resting bid at its own discount below the reference price.
        # sell_threshold_pct is read as the discount this type demands.
        discount = stance.sell_threshold_pct * self.factor[idx]
        bid_px = view.ref_price * (1.0 - discount)
        # They only bid below the current price (otherwise they'd be lifting offers).
        active = bid_px < view.last_price
        capacity = self.holdings[idx]
        sizes = (capacity * 0.25 * stance.aggressiveness).astype(int)
        acted = 0
        for i in range(idx.size):
            if not active[i] or sizes[i] <= 0:
                continue
            out.append(OrderIntent("buy", int(sizes[i]), float(bid_px[i]), "bargain_hunter"))
            acted += 1
        actions["bargain_hunter"] = acted

    # -- aggressors -------------------------------------------------------- #
    def _forced_sellers(self, view, stance, out, actions) -> None:
        idx = self.idx["forced_seller"]
        if idx.size == 0:
            return
        threshold = stance.sell_threshold_pct * self.factor[idx]
        fire = (~self.has_acted[idx]) & (view.drop >= threshold) & (self.holdings[idx] > 0)
        firing = idx[fire]
        if firing.size == 0:
            return
        # A margin call dumps a large chunk at market, once.
        sizes = (self.holdings[firing] * (0.6 + 0.4 * stance.aggressiveness)).astype(int)
        for j, s in zip(firing, sizes, strict=True):
            if s <= 0:
                continue
            out.append(OrderIntent("sell", int(s), None, "forced_seller"))
            self.holdings[j] -= s
        self.has_acted[firing] = True
        actions["forced_seller"] = int(firing.size)

    def _panic_sellers(self, view, stance, rng, out, actions) -> None:
        idx = self.idx["panic_seller"]
        if idx.size == 0:
            return
        threshold = np.maximum(stance.sell_threshold_pct * self.factor[idx], 1e-4)
        prob = np.clip(
            stance.aggressiveness * (view.drop / threshold) * (0.3 + view.stress), 0.0, 1.0
        )
        fire = (rng.random(idx.size) < prob) & (self.holdings[idx] > 0)
        firing = idx[fire]
        if firing.size == 0:
            return
        sizes = (self.holdings[firing] * 0.3 * stance.aggressiveness).astype(int)
        for j, s in zip(firing, sizes, strict=True):
            if s <= 0:
                continue
            out.append(OrderIntent("sell", int(s), None, "panic_seller"))
            self.holdings[j] -= s
        actions["panic_seller"] = int(firing.size)

    def _trend_followers(self, view, stance, out, actions) -> None:
        idx = self.idx["trend_follower"]
        if idx.size == 0:
            return
        # Sell when the recent move is sharply down, beyond a staggered threshold.
        threshold = stance.sell_threshold_pct * self.factor[idx]
        fire = (-view.recent_return >= threshold) & (self.holdings[idx] > 0)
        firing = idx[fire]
        if firing.size == 0:
            return
        sizes = (self.holdings[firing] * 0.25 * stance.aggressiveness).astype(int)
        for j, s in zip(firing, sizes, strict=True):
            if s <= 0:
                continue
            out.append(OrderIntent("sell", int(s), None, "trend_follower"))
            self.holdings[j] -= s
        actions["trend_follower"] = int(firing.size)

    def _holders(self, view, stance, rng, out, actions) -> None:
        idx = self.idx["holder"]
        if idx.size == 0:
            return
        prob = 0.01 * stance.aggressiveness
        fire = (rng.random(idx.size) < prob) & (self.holdings[idx] > 0)
        firing = idx[fire]
        if firing.size == 0:
            return
        sizes = (self.holdings[firing] * 0.1).astype(int)
        for j, s in zip(firing, sizes, strict=True):
            if s <= 0:
                continue
            out.append(OrderIntent("sell", int(s), None, "holder"))
            self.holdings[j] -= s
        actions["holder"] = int(firing.size)
