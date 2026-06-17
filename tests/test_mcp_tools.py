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
    # Offline (no key), the flagship ticker must resolve to exactly the instrument
    # the engine simulates, so the scenario author and engine agree.
    from engine.scenarios import flagship_scenario

    inst = flagship_scenario().instrument
    ref = get_instrument_reference(inst.symbol)
    assert ref["symbol"] == inst.symbol
    assert ref["reference_price"] == inst.reference_price
    assert ref["adv"] == inst.adv
    assert ref["halt_tier"] == inst.halt_tier


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
import logging  # noqa: E402

import mcp.market_data.data as md  # noqa: E402
import mcp.news.data as nd  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_av_guard():
    """Reset the per-process budget-guard state so tests never leak into each other."""
    for m in (md, nd):
        m._MEMO.clear()
        m._PROC_REAL_CALLS = 0
        m._RATE_LIMITED = False
        m._PROVIDER_RESTRICTED_UNTIL = 0.0
        m._LAST_REAL_TS = 0.0
    yield


def test_no_key_path_is_synthetic_and_marked() -> None:
    # No ALPHAVANTAGE_API_KEY in the test env -> deterministic synthetic, marked.
    assert get_instrument_reference("ACME")["source"] == "synthetic"
    assert get_historical_window("ACME", "2025-01-01", "2025-01-10")["source"] == "synthetic"
    assert get_event_news("ACME", "2025-01")["source"] == "synthetic"


def _patch_av(monkeypatch, module, response) -> None:
    """Pretend a key is set and the HTTP call returns ``response``; bypass the DB and
    the usage counter so the real mapping path runs with no network."""
    monkeypatch.setattr(module, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(module, "_av_get", lambda params: response)
    monkeypatch.setattr(module, "_cache_get", lambda provider, key: None)
    monkeypatch.setattr(module, "_cache_put", lambda provider, key, payload: None)
    monkeypatch.setattr(module, "_usage_today", lambda: 0)
    monkeypatch.setattr(module, "_record_usage", lambda: 1)


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


# --------------------------------------------------------------------------- #
# The hard budget guard: per-run cap, daily limit, rate-limit envelope, logging,
# and zero real calls on a cache hit. All offline (no network, no DB).
# --------------------------------------------------------------------------- #
def _forbid_http(module, monkeypatch) -> None:
    def boom(_params):
        raise AssertionError("a real Alpha Vantage HTTP call was attempted")

    monkeypatch.setattr(module, "_av_get", boom)


def test_real_call_logs_count(monkeypatch, caplog) -> None:
    _patch_av(monkeypatch, nd, _FAKE_NEWS)
    monkeypatch.setattr(nd, "_record_usage", lambda: 3)  # pretend this is the 3rd today
    with caplog.at_level(logging.INFO, logger="mcp.news.data"):
        get_event_news("CVNA", "2022-12")
    assert "Alpha Vantage real call 3/25 today: NEWS_SENTIMENT CVNA" in caplog.text


def test_daily_limit_blocks_call_and_logs(monkeypatch, caplog) -> None:
    monkeypatch.setattr(nd, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(nd, "_cache_get", lambda provider, key: None)
    monkeypatch.setattr(nd, "_usage_today", lambda: 25)  # quota exhausted
    _forbid_http(nd, monkeypatch)  # must not even try the network
    with caplog.at_level(logging.WARNING, logger="mcp.news.data"):
        news = get_event_news("CVNA", "2022-12")
    assert news["source"] == "synthetic"  # automatic fallback
    assert "daily limit reached" in caplog.text


def test_per_run_cap_blocks_excess_calls(monkeypatch, caplog) -> None:
    monkeypatch.setattr(md, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(md, "_cache_get", lambda provider, key: None)
    monkeypatch.setattr(md, "_usage_today", lambda: 0)
    monkeypatch.setattr(md, "AV_MAX_CALLS_PER_RUN", 0)  # cap already spent
    _forbid_http(md, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="mcp.market_data.data"):
        ref = get_instrument_reference("CVNA")
    assert ref["source"] == "synthetic"
    assert "per-run cap" in caplog.text


def test_av_provider_envelope_uses_short_cooldown_not_daily_latch(monkeypatch, caplog) -> None:
    limit_msg = {"Information": "Our standard API rate limit is 25 requests per day."}
    monkeypatch.setattr(nd, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(nd, "_cache_get", lambda provider, key: None)
    monkeypatch.setattr(nd, "_cache_put", lambda provider, key, payload: None)
    monkeypatch.setattr(nd, "_usage_today", lambda: 0)
    monkeypatch.setattr(nd, "_av_get", lambda params: limit_msg)
    with caplog.at_level(logging.WARNING, logger="mcp.news.data"):
        news = get_event_news("CVNA", "2022-12")
    assert news["source"] == "synthetic"
    assert "cooling down" in caplog.text
    assert nd._RATE_LIMITED is False
    assert nd._PROVIDER_RESTRICTED_UNTIL > 0

    nd._PROVIDER_RESTRICTED_UNTIL = 0.0
    monkeypatch.setattr(nd, "_av_get", lambda params: _FAKE_NEWS)
    assert get_event_news("CVNA", "2022-12")["source"] == "alphavantage"


def test_av_transient_restriction_does_not_latch(monkeypatch, caplog) -> None:
    # A per-second / premium-only message must NOT latch — later calls may still work.
    burst = {"Information": "Please consider spreading out your requests (1 request per second)."}
    monkeypatch.setattr(nd, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(nd, "_cache_get", lambda provider, key: None)
    monkeypatch.setattr(nd, "_cache_put", lambda provider, key, payload: None)
    monkeypatch.setattr(nd, "_usage_today", lambda: 0)
    monkeypatch.setattr(nd, "_av_get", lambda params: burst)
    with caplog.at_level(logging.WARNING, logger="mcp.news.data"):
        assert get_event_news("CVNA", "2022-12")["source"] == "synthetic"
    assert "restricted this request" in caplog.text
    assert nd._RATE_LIMITED is False  # not latched
    assert nd._PROVIDER_RESTRICTED_UNTIL > 0


def test_cache_hit_makes_zero_real_calls(monkeypatch) -> None:
    cached = {"symbol": "CVNA", "period": "2022-12", "headlines": [], "overall_sentiment": -0.4,
              "sentiment_label": "negative", "headline_count": 0, "source": "alphavantage"}
    monkeypatch.setattr(nd, "_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(nd, "_cache_get", lambda provider, key: cached)
    _forbid_http(nd, monkeypatch)  # a cache hit must never touch the network
    out = get_event_news("CVNA", "2022-12")
    assert out == cached
