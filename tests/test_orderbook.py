"""Order book matching tests."""

from engine.orderbook.book import OrderBook, snap_to_tick


def test_snap_to_tick() -> None:
    assert snap_to_tick(100.017, 0.01) == 100.02
    assert snap_to_tick(99.994, 0.01) == 99.99


def test_resting_and_bbo() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 1000, "market_maker")
    book.add_limit("sell", 100.10, 800, "market_maker")
    assert book.best_bid() == 99.90
    assert book.best_ask() == 100.10
    assert round(book.spread(), 2) == 0.20
    assert book.total_depth() == (1000, 800)


def test_marketable_sell_sweeps_bids_and_moves_price() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 500, "bargain_hunter")
    book.add_limit("buy", 99.80, 500, "bargain_hunter")
    trades = book.add_market("sell", 700, "forced_seller")
    # Best bid filled first (price-time priority), then the next level down.
    assert [t.price for t in trades] == [99.90, 99.80]
    assert sum(t.size for t in trades) == 700
    assert book.last_price == 99.80  # swept into the lower level
    bid_depth, _ = book.total_depth()
    assert bid_depth == 300  # 1000 resting - 700 taken


def test_time_priority_within_level() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 100, "maker_a")
    book.add_limit("buy", 99.90, 100, "maker_b")
    trades = book.add_market("sell", 100, "forced_seller")
    # First-arriving maker at the level is hit first.
    assert trades[0].maker_type == "maker_a"


def test_resting_orders_age_and_later_fill_in_queue_order() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 100, "maker_a")
    book.age_orders()
    book.add_limit("buy", 99.90, 100, "maker_b")

    orders = book.resting_orders("buy")
    assert [o.investor_type for o in orders] == ["maker_a", "maker_b"]
    assert [o.age for o in orders] == [1, 0]

    trades = book.add_market("sell", 150, "forced_seller")
    assert [(t.maker_type, t.size) for t in trades] == [("maker_a", 100), ("maker_b", 50)]

    remaining = book.resting_orders("buy")
    assert len(remaining) == 1
    assert remaining[0].investor_type == "maker_b"
    assert remaining[0].size == 50


def test_cancel_where_removes_stale_liquidity() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 100, "market_maker")
    book.add_limit("buy", 99.80, 200, "bargain_hunter")
    book.age_orders()
    book.age_orders()

    count, shares = book.cancel_where(
        lambda order: order.investor_type == "market_maker" and order.age >= 2
    )

    assert (count, shares) == (1, 100)
    assert [(p, s) for p, s in book.depth()["bids"]] == [(99.80, 200)]


def test_unmatched_marketable_returns_partial() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 200, "bargain_hunter")
    trades = book.add_market("sell", 1000, "forced_seller")
    assert sum(t.size for t in trades) == 200  # only 200 of liquidity existed
    assert book.total_depth()[0] == 0  # bids fully drained


def test_cancel_all_clears_book() -> None:
    book = OrderBook(tick_size=0.01, last_price=100.0)
    book.add_limit("buy", 99.90, 200, "market_maker")
    book.add_limit("sell", 100.10, 200, "market_maker")
    book.cancel_all()
    assert book.best_bid() is None and book.best_ask() is None
    assert book.total_depth() == (0, 0)
