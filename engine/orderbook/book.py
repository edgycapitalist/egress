"""Price-time priority limit order book.

A small, dependency-free matching engine: submit limit or marketable orders,
cancel resting orders, and match by best price then arrival order (FIFO within a
price level). It produces fills, the last traded price, best bid/offer, and depth
at each level — the raw material the population and metrics build on.

The book carries no notion of agents or strategy; callers tag each order with an
``investor_type`` only so downstream code can attribute volume. Prices are snapped
to the instrument tick grid on entry.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

Side = str  # "buy" | "sell"


@dataclass
class RestingOrder:
    order_id: int
    side: Side
    price: float
    size: int  # remaining shares
    investor_type: str
    seq: int  # global arrival sequence, for time priority


@dataclass
class Trade:
    price: float
    size: int
    aggressor: Side
    maker_type: str
    taker_type: str


def snap_to_tick(price: float, tick_size: float) -> float:
    """Round a price onto the tick grid, avoiding float dust."""
    ticks = round(price / tick_size)
    return round(ticks * tick_size, 10)


class OrderBook:
    def __init__(self, tick_size: float, last_price: float) -> None:
        self.tick_size = tick_size
        self.last_price = last_price
        # price -> FIFO queue of resting orders
        self._bids: dict[float, deque[RestingOrder]] = {}
        self._asks: dict[float, deque[RestingOrder]] = {}
        self._by_id: dict[int, RestingOrder] = {}
        self._next_id = 0
        self._next_seq = 0

    # -- introspection ----------------------------------------------------- #
    def best_bid(self) -> float | None:
        return max(self._bids) if self._bids else None

    def best_ask(self) -> float | None:
        return min(self._asks) if self._asks else None

    def spread(self) -> float | None:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return round(ask - bid, 10)

    def depth(self, levels: int = 5) -> dict[str, list[tuple[float, int]]]:
        bids = sorted(self._bids, reverse=True)[:levels]
        asks = sorted(self._asks)[:levels]
        return {
            "bids": [(p, self._level_size(self._bids, p)) for p in bids],
            "asks": [(p, self._level_size(self._asks, p)) for p in asks],
        }

    def total_depth(self) -> tuple[int, int]:
        bid = sum(o.size for q in self._bids.values() for o in q)
        ask = sum(o.size for q in self._asks.values() for o in q)
        return bid, ask

    @staticmethod
    def _level_size(book_side: dict[float, deque[RestingOrder]], price: float) -> int:
        return sum(o.size for o in book_side[price])

    # -- mutation ---------------------------------------------------------- #
    def cancel_all(self) -> None:
        """Clear every resting order. Used by the per-tick liquidity refresh."""
        self._bids.clear()
        self._asks.clear()
        self._by_id.clear()

    def add_limit(self, side: Side, price: float, size: int, investor_type: str) -> list[Trade]:
        """Add a (possibly marketable) limit order; match then rest the remainder."""
        if size <= 0:
            return []
        price = snap_to_tick(price, self.tick_size)
        trades, remaining = self._match(side, price, size, investor_type)
        if remaining > 0:
            self._rest(side, price, remaining, investor_type)
        return trades

    def add_market(self, side: Side, size: int, investor_type: str) -> list[Trade]:
        """Match against all available liquidity at any price; rest nothing."""
        if size <= 0:
            return []
        limit = float("inf") if side == "buy" else float("-inf")
        trades, _ = self._match(side, limit, size, investor_type)
        return trades

    # -- internals --------------------------------------------------------- #
    def _match(
        self, side: Side, limit_price: float, size: int, taker_type: str
    ) -> tuple[list[Trade], int]:
        trades: list[Trade] = []
        remaining = size
        opp = self._asks if side == "buy" else self._bids

        def crosses(level: float) -> bool:
            return level <= limit_price if side == "buy" else level >= limit_price

        while remaining > 0 and opp:
            best = min(opp) if side == "buy" else max(opp)
            if not crosses(best):
                break
            queue = opp[best]
            while remaining > 0 and queue:
                maker = queue[0]
                traded = min(remaining, maker.size)
                trades.append(
                    Trade(
                        price=best,
                        size=traded,
                        aggressor=side,
                        maker_type=maker.investor_type,
                        taker_type=taker_type,
                    )
                )
                maker.size -= traded
                remaining -= traded
                self.last_price = best
                if maker.size == 0:
                    queue.popleft()
                    self._by_id.pop(maker.order_id, None)
            if not queue:
                del opp[best]
        return trades, remaining

    def _rest(self, side: Side, price: float, size: int, investor_type: str) -> None:
        book_side = self._bids if side == "buy" else self._asks
        order = RestingOrder(
            order_id=self._next_id,
            side=side,
            price=price,
            size=size,
            investor_type=investor_type,
            seq=self._next_seq,
        )
        self._next_id += 1
        self._next_seq += 1
        book_side.setdefault(price, deque()).append(order)
        self._by_id[order.order_id] = order
