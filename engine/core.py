"""The deterministic simulation engine — the contract surface.

Ties the order book, population, exit trader, halt rule, stress regime, metrics,
and NDJSON recorder into one run. Exposes the in-process surface from
``docs/contracts.md``:

    engine = Engine(config, recorder)
    market_state = engine.start()
    market_state, ticks = engine.advance(stances, k)   # repeat per window
    metrics = engine.finalize()

``advance`` takes the six stances as input and never calls an LLM — in baseline
mode those stances come from ``engine.baseline``; in a live run they come from the
Tier-A archetype agents over the same dict. This is the firewall that keeps the
engine LLM-free and independently testable.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from engine.halt import HaltController
from engine.metrics.metrics import compute_metrics
from engine.orderbook.book import OrderBook, RestingOrder, snap_to_tick
from engine.population.peers import PeerCohorts
from engine.population.population import MarketView, OrderIntent, Population
from engine.population.trader import ExitTrader
from engine.replay.recorder import Recorder
from engine.schema import (
    INVESTOR_TYPES,
    REFERENCE_VOLATILITY,
    Depth,
    Fill,
    ImpactAttribution,
    InvestorType,
    MarketState,
    Metrics,
    PeerActionCounts,
    RunConfig,
    Stance,
    TickEvent,
)
from engine.stats.process import StressRegime

LOOKBACK = 5  # ticks for the recent-return signal
STALL_TICKS = 40  # end the run if no trades print for this long and the exit is stuck
# How far the real volatility can scale the cascade propensity, either way.
VOL_GAIN_BOUNDS = (0.05, 2.5)
# Volatility is a fragility *amplifier*, not a gate. A name's shock response is
# floored at FRAG_FLOOR of the full effect even when its volatility is near zero, so
# a severe enough shock can break a calm, deep name — it is just harder than for a
# fragile one. At vol_gain == 1 the fragility factor is 1, so the flagship (and any
# reference-vol name) is unchanged. frag = FRAG_FLOOR + (1 - FRAG_FLOOR) * vol_gain.
FRAG_FLOOR = 0.45

class Engine:
    def __init__(
        self,
        config: RunConfig,
        recorder: Recorder | None = None,
        *,
        enable_exit_trader: bool = True,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.enable_exit_trader = enable_exit_trader
        self.rng = np.random.default_rng(config.seed)

        ref = config.instrument.reference_price
        self.ref_price = ref
        self.tick_size = config.instrument.tick_size
        self.last_price = ref

        # Real volatility, relative to the reference level, scales how readily this
        # name cascades (stress transitions) and how hard a price shock gaps it.
        # A name at the reference vol has vol_gain == 1 and behaves as before.
        lo, hi = VOL_GAIN_BOUNDS
        self.vol_gain = float(
            np.clip(config.instrument.volatility / REFERENCE_VOLATILITY, lo, hi)
        )
        # Fragility amplifier: floored so a big shock still bites a calm name; 1.0 at
        # the reference vol (flagship unchanged).
        self.frag = FRAG_FLOOR + (1.0 - FRAG_FLOOR) * self.vol_gain
        # Overall crisis magnitude (from the described stress + news, set on the
        # config by the live path). 1.0 = neutral baseline; >1 escalates the cascade
        # independently of volatility, so a severe crisis can close even a deep name.
        self.crisis = float(config.crisis_intensity)

        self.book = OrderBook(self.tick_size, ref)
        self.population = Population(config, self.rng)
        self.peer_cohorts = PeerCohorts(config, self.rng)
        horizon_ticks = config.time_scale.effective_exit_horizon_ticks()
        self.effective_max_ticks = (
            min(config.max_ticks, horizon_ticks) if horizon_ticks is not None else config.max_ticks
        )
        exit_speed = config.exit_speed
        if exit_speed.mode == "twap" and horizon_ticks is not None:
            exit_speed = exit_speed.model_copy(update={"horizon_ticks": horizon_ticks})
        self.trader = ExitTrader(
            config.position,
            exit_speed,
            natural_volume=config.time_scale.natural_volume_per_tick(config.instrument.adv),
        )
        self.halt = HaltController(config.halt_rule)
        self.stress_regime = StressRegime()
        self.impact_attribution = ImpactAttribution()

        self.tick = 0
        self.window_index = 0
        self.stress = 0.0
        self.recent_volume = 0
        self._idle = 0  # consecutive ticks with no fills
        self.price_path: list[float] = [ref]
        self._price_hist: deque[float] = deque([ref], maxlen=LOOKBACK + 1)
        self._shocks = {s.tick: s for s in config.shock_schedule}
        self.done = False

    # -- contract surface -------------------------------------------------- #
    def start(self) -> MarketState:
        if self.recorder:
            self.recorder.write_meta(self.config)
        return self._market_state()

    def advance(
        self, stances: dict[InvestorType, Stance], ticks: int
    ) -> tuple[MarketState, list[TickEvent]]:
        events: list[TickEvent] = []
        for _ in range(ticks):
            if self.done:
                break
            event = self._step(stances)
            events.append(event)
            if self.recorder:
                self.recorder.write_tick(event)
            stalled = self._idle >= STALL_TICKS and self.trader.remaining > 0
            if self.tick >= self.effective_max_ticks or self.trader.remaining <= 0 or stalled:
                self.done = True
        self.window_index += 1
        return self._market_state(), events

    def finalize(self) -> Metrics:
        metrics = compute_metrics(
            self.config,
            self.trader,
            self.price_path,
            self.halt.halt_count,
            self.tick,
            impact_attribution=ImpactAttribution(
                exogenous_shock_bps=round(self.impact_attribution.exogenous_shock_bps, 4),
                endogenous_trading_bps=round(
                    self.impact_attribution.endogenous_trading_bps, 4
                ),
                liquidity_withdrawal_bps=round(
                    self.impact_attribution.liquidity_withdrawal_bps, 4
                ),
            ),
        )
        if self.recorder:
            self.recorder.write_metrics(metrics)
        return metrics

    # -- one tick ---------------------------------------------------------- #
    def _step(self, stances: dict[InvestorType, Stance]) -> TickEvent:
        tick = self.tick
        shock = self._shocks.get(tick)
        shock_sev = shock.severity if shock else 0.0
        exogenous_shock_bps = 0.0

        # A price shock gaps the last price down before any trading; a deep, calm
        # name barely gaps, a fragile one gaps hard.
        if shock and shock.kind == "price":
            before_shock = self.last_price
            self.last_price = snap_to_tick(
                self.last_price * (1.0 - 0.08 * shock.severity * self.crisis * self.frag),
                self.tick_size,
            )
            exogenous_shock_bps = (before_shock - self.last_price) / self.ref_price * 1e4

        drop = max(0.0, (self.ref_price - self.last_price) / self.ref_price)
        self.stress = self.stress_regime.step(
            drop, shock_sev, self.rng, self.vol_gain, frag=self.frag, crisis=self.crisis
        )
        recent_return = (self.last_price - self._price_hist[0]) / self._price_hist[0]

        view = MarketView(
            ref_price=self.ref_price,
            last_price=self.last_price,
            recent_return=recent_return,
            stress=self.stress,
            tick=tick,
        )

        trading_allowed = not self.halt.halted
        fills: list[Fill] = []
        actions = dict.fromkeys(INVESTOR_TYPES, 0)
        volume = 0
        peer_actions = PeerActionCounts()
        trading_impact = ImpactAttribution()

        if trading_allowed:
            fills, actions, volume, peer_actions, trading_impact = self._trade(view, stances)
        elif self.config.book_persistence.enabled:
            self.book.age_orders()
            self._cancel_stale_liquidity(view.stress)

        impact = ImpactAttribution(
            exogenous_shock_bps=round(exogenous_shock_bps, 4),
            endogenous_trading_bps=round(trading_impact.endogenous_trading_bps, 4),
            liquidity_withdrawal_bps=round(trading_impact.liquidity_withdrawal_bps, 4),
        )
        self.impact_attribution.exogenous_shock_bps += impact.exogenous_shock_bps
        self.impact_attribution.endogenous_trading_bps += impact.endogenous_trading_bps
        self.impact_attribution.liquidity_withdrawal_bps += impact.liquidity_withdrawal_bps

        halted_now, halt_started = self.halt.update(self.last_price, self.ref_price)

        bid, ask = self.book.best_bid(), self.book.best_ask()
        depth_bid, depth_ask = self.book.total_depth()

        event = TickEvent(
            tick=tick,
            last_price=round(self.last_price, 6),
            best_bid=bid,
            best_ask=ask,
            depth_bid=depth_bid,
            depth_ask=depth_ask,
            fills=fills,
            filled_this_tick=sum(f.size for f in fills if f.aggressor == "sell"),
            cumulative_filled=self.trader.filled,
            vwap_sold=round(self.trader.vwap, 4) if self.trader.vwap is not None else None,
            actions_by_type=dict(actions),
            peer_actions=peer_actions,
            impact_attribution=impact,
            halted=halted_now,
            halt_started=halt_started,
            shock_applied=shock,
        )

        self.price_path.append(self.last_price)
        self._price_hist.append(self.last_price)
        self.recent_volume = volume
        self._idle = 0 if fills else self._idle + 1
        self.tick += 1
        return event

    def _trade(
        self, view: MarketView, stances: dict[InvestorType, Stance]
    ) -> tuple[list[Fill], dict[str, int], int, PeerActionCounts, ImpactAttribution]:
        persistence = self.config.book_persistence
        if persistence.enabled:
            self.book.age_orders()
            self._cancel_stale_liquidity(view.stress)
        else:
            self.book.cancel_all()

        liquidity, aggressors, actions = self.population.step(view, stances, self.rng)
        if persistence.enabled:
            liquidity = self._persistent_liquidity(liquidity, view)
            actions["market_maker"] = (
                sum(1 for intent in liquidity if intent.investor_type == "market_maker") + 1
            ) // 2
            actions["bargain_hunter"] = sum(
                1 for intent in liquidity if intent.investor_type == "bargain_hunter"
            )
        peer_aggressors, peer_actions = self.peer_cohorts.step(view, self.rng)
        for intent in liquidity:
            price = self._non_marketable_liquidity_price(intent)
            self.book.add_limit(intent.side, price, intent.size, intent.investor_type)

        # The exiting trader competes with the crowd in randomised order. It can
        # be disabled only for representative paired counterfactual attribution.
        trader_size = (
            self.trader.child_size(self.recent_volume) if self.enable_exit_trader else 0
        )
        trader_intent = (
            OrderIntent("sell", trader_size, None, "exit_trader") if trader_size > 0 else None
        )
        sequence: list[OrderIntent] = [*aggressors, *peer_aggressors]
        if trader_intent is not None:
            sequence.append(trader_intent)

        bid_depth_before, _ask_depth_before = self.book.total_depth()
        sell_intent = sum(intent.size for intent in sequence if intent.side == "sell")
        start_price = self.last_price
        fills: list[Fill] = []
        volume = 0
        for i in self.rng.permutation(len(sequence)):
            intent = sequence[i]
            trades = self.book.add_market(intent.side, intent.size, intent.investor_type)
            for t in trades:
                fills.append(Fill(price=round(t.price, 6), size=t.size, aggressor=intent.side))
                volume += t.size
                if intent is trader_intent:
                    self.trader.record(t.price, t.size, view.tick)

        self.last_price = self.book.last_price
        raw_trading_bps = (start_price - self.last_price) / self.ref_price * 1e4
        withdrawal_bps = 0.0
        endogenous_bps = raw_trading_bps
        if raw_trading_bps > 0 and sell_intent > 0:
            # Same-run impact estimates, not causal proof: the book path is
            # interactive, so true attribution needs paired counterfactual runs.
            if bid_depth_before <= 0:
                withdrawal_share = 1.0
            else:
                withdrawal_share = 1.0 - min(1.0, bid_depth_before / sell_intent)
            withdrawal_share = float(np.clip(withdrawal_share, 0.0, 0.85))
            withdrawal_bps = raw_trading_bps * withdrawal_share
            endogenous_bps = raw_trading_bps - withdrawal_bps
        impact = ImpactAttribution(
            endogenous_trading_bps=endogenous_bps,
            liquidity_withdrawal_bps=withdrawal_bps,
        )
        return fills, actions, volume, peer_actions, impact

    def _cancel_stale_liquidity(self, stress: float) -> None:
        cfg = self.config.book_persistence
        provider_types = {"market_maker", "bargain_hunter"}

        def should_cancel(order: RestingOrder) -> bool:
            if order.investor_type not in provider_types:
                return False
            if order.age >= cfg.max_order_age:
                return True

            stale = order.age >= cfg.resting_ttl
            maker_stress_withdrawal = (
                order.investor_type == "market_maker" and stress > 0.0
            )
            if not stale and not maker_stress_withdrawal:
                return False

            probability = cfg.base_cancel_rate + stress * cfg.stress_cancel_multiplier
            if order.investor_type == "market_maker":
                probability *= 1.35
            if not stale:
                probability *= stress
            probability = float(np.clip(probability, 0.0, 1.0))
            return bool(self.rng.random() < probability)

        self.book.cancel_where(should_cancel)

    def _persistent_liquidity(
        self, liquidity: list[OrderIntent], view: MarketView
    ) -> list[OrderIntent]:
        cfg = self.config.book_persistence
        accepted: list[OrderIntent] = []
        refill_pressure = max(0.05, 1.0 - 0.85 * view.stress)
        base_replenish = cfg.maker_replenish_rate * refill_pressure

        for intent in liquidity:
            if intent.price is None:
                accepted.append(intent)
                continue

            replenish = base_replenish
            size_scale = 1.0
            price = intent.price
            if intent.investor_type == "market_maker":
                size_scale = max(0.05, 1.0 - 0.85 * view.stress)
                gap = intent.price - view.last_price
                price = view.last_price + gap * (1.0 + 1.5 * view.stress)
            elif intent.investor_type == "bargain_hunter":
                replenish = min(1.0, base_replenish + 0.25 * view.drop)
                size_scale = max(0.25, 1.0 - 0.50 * view.stress)

            if self.rng.random() > replenish:
                continue
            size = int(intent.size * size_scale)
            if size <= 0:
                continue
            accepted.append(
                OrderIntent(
                    side=intent.side,
                    size=size,
                    price=snap_to_tick(price, self.tick_size),
                    investor_type=intent.investor_type,
                )
            )

        return accepted

    def _non_marketable_liquidity_price(self, intent: OrderIntent) -> float:
        if intent.price is None:
            raise ValueError("liquidity intents must be limit orders")
        price = intent.price
        if intent.side == "buy":
            best_ask = self.book.best_ask()
            if best_ask is not None and price >= best_ask:
                price = best_ask - self.tick_size
        else:
            best_bid = self.book.best_bid()
            if best_bid is not None and price <= best_bid:
                price = best_bid + self.tick_size
        return max(self.tick_size, snap_to_tick(price, self.tick_size))

    # -- driver ------------------------------------------------------------ #
    def run_baseline(self) -> Metrics:
        """Run the whole simulation using fixed-heuristic stances (no LLM)."""
        from engine.baseline import baseline_stances

        self.start()
        while not self.done:
            drop = max(0.0, (self.ref_price - self.last_price) / self.ref_price)
            stances = baseline_stances(drop, self.stress, self.tick)
            self.advance(stances, self.config.ticks_per_window)
        return self.finalize()

    # -- helpers ----------------------------------------------------------- #
    def _market_state(self) -> MarketState:
        bid, ask = self.book.best_bid(), self.book.best_ask()
        depth = self.book.depth(levels=5)
        return MarketState(
            run_id=self.config.run_id,
            tick=self.tick,
            window_index=self.window_index,
            last_price=round(self.last_price, 6),
            best_bid=bid,
            best_ask=ask,
            spread=self.book.spread(),
            depth=Depth(bids=depth["bids"], asks=depth["asks"]),
            cumulative_filled=self.trader.filled,
            remaining_qty=self.trader.remaining,
            halted=self.halt.halted,
        )
