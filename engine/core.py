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
from engine.orderbook.book import OrderBook, snap_to_tick
from engine.population.population import MarketView, OrderIntent, Population
from engine.population.trader import ExitTrader
from engine.replay.recorder import Recorder
from engine.schema import (
    INVESTOR_TYPES,
    Depth,
    Fill,
    InvestorType,
    MarketState,
    Metrics,
    RunConfig,
    Stance,
    TickEvent,
)
from engine.stats.process import StressRegime

LOOKBACK = 5  # ticks for the recent-return signal
STALL_TICKS = 40  # end the run if no trades print for this long and the exit is stuck


class Engine:
    def __init__(self, config: RunConfig, recorder: Recorder | None = None) -> None:
        self.config = config
        self.recorder = recorder
        self.rng = np.random.default_rng(config.seed)

        ref = config.instrument.reference_price
        self.ref_price = ref
        self.tick_size = config.instrument.tick_size
        self.last_price = ref

        self.book = OrderBook(self.tick_size, ref)
        self.population = Population(config, self.rng)
        self.trader = ExitTrader(config.position, config.exit_speed)
        self.halt = HaltController(config.halt_rule)
        self.stress_regime = StressRegime()

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
            if self.tick >= self.config.max_ticks or self.trader.remaining <= 0 or stalled:
                self.done = True
        self.window_index += 1
        return self._market_state(), events

    def finalize(self) -> Metrics:
        metrics = compute_metrics(
            self.config, self.trader, self.price_path, self.halt.halt_count, self.tick
        )
        if self.recorder:
            self.recorder.write_metrics(metrics)
        return metrics

    # -- one tick ---------------------------------------------------------- #
    def _step(self, stances: dict[InvestorType, Stance]) -> TickEvent:
        tick = self.tick
        shock = self._shocks.get(tick)
        shock_sev = shock.severity if shock else 0.0

        # A price shock gaps the last price down before any trading.
        if shock and shock.kind == "price":
            self.last_price = snap_to_tick(
                self.last_price * (1.0 - 0.08 * shock.severity), self.tick_size
            )

        drop = max(0.0, (self.ref_price - self.last_price) / self.ref_price)
        self.stress = self.stress_regime.step(drop, shock_sev, self.rng)
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

        if trading_allowed:
            fills, actions, volume = self._trade(view, stances)

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
    ) -> tuple[list[Fill], dict[str, int], int]:
        # Fresh liquidity each tick: providers repost, aggressors sweep it.
        self.book.cancel_all()
        liquidity, aggressors, actions = self.population.step(view, stances, self.rng)
        for intent in liquidity:
            self.book.add_limit(intent.side, intent.price, intent.size, intent.investor_type)

        # The exiting trader competes with the crowd in randomised order.
        trader_size = self.trader.child_size(self.recent_volume)
        trader_intent = (
            OrderIntent("sell", trader_size, None, "exit_trader") if trader_size > 0 else None
        )
        sequence: list[OrderIntent] = list(aggressors)
        if trader_intent is not None:
            sequence.append(trader_intent)

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
        return fills, actions, volume

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
