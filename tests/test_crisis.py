"""Offline tests for the crisis-intensity derivation (gateway/crisis.py).

These run with no Alpha Vantage key, so the news side always takes the deterministic
synthetic fallback — which is exactly the offline/no-key path the gateway uses.
"""

from __future__ import annotations

from gateway.crisis import CI_MAX, CI_MIN, derive_crisis_intensity


def test_severe_description_outranks_mild() -> None:
    mild, _ = derive_crisis_intensity(
        "A modest, orderly pullback; some profit-taking. Calm, ample buyers.",
        "AAPL",
        fetch_news=False,
    )
    severe, _ = derive_crisis_intensity(
        "Sudden bankruptcy scare: panic, mass forced liquidation, a crash, "
        "collapsing liquidity, margin calls, contagion.",
        "AAPL",
        fetch_news=False,
    )
    assert severe > mild
    assert CI_MIN <= mild <= CI_MAX
    assert CI_MIN <= severe <= CI_MAX


def test_blank_description_is_mild_floor() -> None:
    ci, detail = derive_crisis_intensity("", "AAPL", fetch_news=False)
    assert ci == CI_MIN
    assert detail["text_severity"] == 0.0


def test_news_off_makes_no_call_and_zero_news_severity() -> None:
    ci, detail = derive_crisis_intensity("a downgrade and selling", "AAPL", fetch_news=False)
    assert detail["news"]["source"] == "skipped"
    assert detail["news_severity"] == 0.0
    assert CI_MIN <= ci <= CI_MAX


def test_synthetic_news_is_downweighted() -> None:
    # With fetch_news the synthetic crisis tape is negative, but down-weighted so it
    # only nudges — the same description with news on stays close to news-off.
    off, _ = derive_crisis_intensity("a downgrade and selling", "AAPL", fetch_news=False)
    on, detail = derive_crisis_intensity("a downgrade and selling", "AAPL", fetch_news=True)
    assert detail["news"]["source"] == "synthetic"
    assert detail["news_weight"] == 0.25
    assert on >= off
    assert on - off < 0.2  # a nudge, not a takeover
