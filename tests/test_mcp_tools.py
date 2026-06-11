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


# --------------------------------------------------------------------------- #
# Real Alpha Vantage path vs. synthetic fallback — exercised offline by
# monkeypatching the HTTP call, so no key, no network, no DB are ever needed.
# --------------------------------------------------------------------------- #
import mcp.market_data.data as md  # noqa: E402
import mcp.news.data as nd  # noqa: E402


def test_no_key_path_is_synthetic_and_marked() -> None:
    # No ALPHAVANTAGE_API_KEY in the test env -> deterministic synthetic, marked.
    assert get_instrument_reference("ACME")["source"] == "synthetic"
    assert get_historical_window("ACME", "2025-01-01", "2025-01-10")["source"] == "synthetic"
    assert get_event_news("ACME", "2025-01")["source"] == "synthetic"


def _patch_av(monkeypatch, module, response) -> None:
    """Pretend a key is set and the HTTP call returns ``response``; bypass the DB."""
    monkeypatch.setattr(module, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(module, "_av_get", lambda params: response)
    monkeypatch.setattr(module, "_cache_get", lambda provider, key: None)
    monkeypatch.setattr(module, "_cache_put", lambda provider, key, payload: None)
    module._MEMO.clear()


_FAKE_DAILY = {
    "Time Series (Daily)": {
        "2022-12-01": {"1. open": "10.0", "2. high": "10.5", "3. low": "9.4",
                       "4. close": "9.8", "5. volume": "15000000"},
        "2022-12-02": {"1. open": "9.8", "2. high": "9.9", "3. low": "8.6",
                       "4. close": "8.7", "5. volume": "22000000"},
    }
}

_FAKE_NEWS = {
    "feed": [
        {"title": "Carvana plunges on bankruptcy fears", "source": "Bloomberg",
         "overall_sentiment_score": "-0.40",
         "ticker_sentiment": [{"ticker": "CVNA", "ticker_sentiment_score": "-0.55"}]},
        {"title": "Creditors organize as Carvana liquidity dries up", "source": "Reuters",
         "overall_sentiment_score": "-0.30",
         "ticker_sentiment": [{"ticker": "CVNA", "ticker_sentiment_score": "-0.45"}]},
    ]
}


def test_real_market_data_mapping(monkeypatch) -> None:
    _patch_av(monkeypatch, md, _FAKE_DAILY)
    ref = get_instrument_reference("CVNA")
    assert ref["source"] == "alphavantage"
    assert ref["reference_price"] == 8.7  # latest real close
    assert ref["adv"] > 0

    win = get_historical_window("CVNA", "2022-12-01", "2022-12-31")
    assert win["source"] == "alphavantage"
    assert [b["date"] for b in win["bars"]] == ["2022-12-01", "2022-12-02"]
    assert win["bars"][0]["close"] == 9.8


def test_real_news_mapping(monkeypatch) -> None:
    _patch_av(monkeypatch, nd, _FAKE_NEWS)
    news = get_event_news("CVNA", "2022-12")
    assert news["source"] == "alphavantage"
    assert news["headline_count"] == 2
    assert news["headlines"][0]["headline"].startswith("Carvana plunges")
    # ticker-specific sentiment used, aggregate is negative
    assert news["headlines"][0]["sentiment"] == -0.55
    assert news["overall_sentiment"] < 0 and news["sentiment_label"] == "negative"


def test_rate_limit_falls_back_to_synthetic(monkeypatch) -> None:
    # Key present but the API returns a miss (rate-limited) -> synthetic, no crash.
    _patch_av(monkeypatch, nd, None)
    news = get_event_news("CVNA", "2022-12")
    assert news["source"] == "synthetic"
    _patch_av(monkeypatch, md, None)
    assert get_instrument_reference("CVNA")["source"] == "synthetic"
