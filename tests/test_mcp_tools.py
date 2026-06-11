"""Offline tests for the two MCP servers' tool backends.

The tool *logic* is deterministic and dependency-free, so these run with no MCP
server, no ADK, and no cloud.
"""

from mcp.market_data.data import (
    get_historical_window,
    get_instrument_reference,
    get_liquidity_profile,
)
from mcp.market_data.tools import MARKET_DATA_TOOLS
from mcp.news.data import get_event_news, get_sentiment
from mcp.news.tools import NEWS_TOOLS


def test_instrument_reference_fixture_matches_flagship() -> None:
    ref = get_instrument_reference("ACME")
    assert ref["symbol"] == "ACME"
    assert ref["reference_price"] == 100.0
    assert ref["adv"] == 5_000_000
    assert ref["halt_tier"] == 1


def test_instrument_reference_is_deterministic_for_unknown_symbols() -> None:
    a = get_instrument_reference("ZZZ9")
    b = get_instrument_reference("zzz9")  # case-insensitive
    assert a == b
    assert a["reference_price"] > 0 and a["adv"] > 0


def test_historical_window_is_deterministic_and_well_formed() -> None:
    w1 = get_historical_window("ACME", "2025-01-01", "2025-01-31")
    w2 = get_historical_window("ACME", "2025-01-01", "2025-01-31")
    assert w1 == w2
    assert len(w1["bars"]) == 31
    assert len(w1["returns"]) == len(w1["bars"]) - 1
    for bar in w1["bars"]:
        assert bar["low"] <= bar["high"]
        assert bar["volume"] > 0


def test_liquidity_profile_shape() -> None:
    liq = get_liquidity_profile("ACME")
    assert liq["adv"] == 5_000_000
    assert liq["spread_bps"] > 0
    assert liq["depth_at_touch"] > 0
    assert 0 < liq["turnover_ratio"] < 1


def test_event_news_is_negative_and_deterministic() -> None:
    n1 = get_event_news("ACME", "2025-Q1")
    n2 = get_event_news("ACME", "2025-Q1")
    assert n1 == n2
    assert n1["headline_count"] >= 4
    # A crisis tape skews negative.
    assert n1["overall_sentiment"] < 0
    assert n1["sentiment_label"] == "negative"


def test_sentiment_scorer_signs() -> None:
    neg = get_sentiment("Funds rush to trim ACME amid forced selling and margin calls")
    pos = get_sentiment("Strong rebound and record profit drive a rally and gains")
    neutral = get_sentiment("The company is headquartered downtown")
    assert neg["score"] < 0 and neg["label"] == "negative"
    assert pos["score"] > 0 and pos["label"] == "positive"
    assert neutral["label"] == "neutral"


def test_function_tools_expose_spec_signatures() -> None:
    market_names = {t.name for t in MARKET_DATA_TOOLS}
    news_names = {t.name for t in NEWS_TOOLS}
    assert market_names == {
        "get_instrument_reference",
        "get_historical_window",
        "get_liquidity_profile",
    }
    assert news_names == {"get_event_news", "get_sentiment"}
