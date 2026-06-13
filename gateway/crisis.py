"""Derive an engine ``crisis_intensity`` from the user's stress text and real news.

This is the deterministic counterpart to what the live-Gemini path does qualitatively:
read the plain-language stress description *and* the instrument's news sentiment, and
turn them into a single crisis magnitude the engine acts on (``RunConfig.crisis_intensity``).
It is what makes the typed description **load-bearing** — two different descriptions of
the same name produce two different outcomes — without needing an LLM, so it runs and is
testable offline and serves as the no-LLM fallback.

Signals, both deterministic:

* **Stress text** — scored by the News MCP's lexicon (``get_sentiment``): how negative
  the description is, weighted by how sentiment-laden it is.
* **News** — the News MCP's ``get_event_news`` aggregate sentiment for the ticker: real
  Alpha Vantage ``NEWS_SENTIMENT`` when a key is set (cached + budget-guarded), else the
  deterministic synthetic crisis tape. Only fetched when ``fetch_news`` is set, so an
  offline/baseline build never makes a network call.

The output is clamped to ``[CI_MIN, CI_MAX]`` and centred so a benign description maps to
a mild stress and a catastrophic one to a crisis that can close even a deep name's exit.
``1.0`` is the engine's neutral baseline; the calibration here is tuned against the
orchestrator path (see ``eval/discrimination.py`` and the severity sweep).
"""

from __future__ import annotations

from typing import Any

# Crisis-intensity mapping. A benign description on a name with calm news lands near
# CI_MIN (a mild stress a deep name shrugs off); a dense, strongly negative description
# plus strongly negative news approaches CI_MAX (a severe crisis that closes even a
# liquid name). The weights are tuned so the *text alone* can move a liquid name from
# "exit open" to "exit closes", i.e. the description genuinely drives the outcome.
CI_MIN = 0.3
CI_MAX = 1.6
CI_BASE = 0.3  # benign floor
CI_TEXT = 0.7  # full weight of a maximally severe description
CI_NEWS = 0.4  # full weight of maximally negative news


def _text_severity(stress_text: str) -> tuple[float, dict[str, Any]]:
    """How severe the *described* crisis is, in [0, 1], from the lexicon scorer."""
    from mcp.news.data import get_sentiment

    s = get_sentiment(stress_text or "")
    neg_strength = max(0.0, -float(s["score"]))  # 0 (benign/positive) .. 1 (all-negative)
    density = min(1.0, float(s["magnitude"]) * 4.0)  # how sentiment-laden the text is
    # A lone negative word in a long benign text counts for less than a dense crisis tape.
    severity = neg_strength * (0.4 + 0.6 * density)
    return severity, {"score": s["score"], "magnitude": s["magnitude"], "label": s["label"]}


def _news_severity(symbol: str, *, fetch_news: bool) -> tuple[float, dict[str, Any]]:
    """How negative the ticker's news is, in [0, 1]. Network only when ``fetch_news``."""
    if not symbol or not fetch_news:
        return 0.0, {"overall_sentiment": None, "source": "skipped", "headline_count": 0}
    from mcp.news.data import get_event_news

    news = get_event_news(symbol, "recent")  # real (cached/budgeted) or synthetic fallback
    overall = float(news.get("overall_sentiment") or 0.0)
    severity = max(0.0, -overall)
    return severity, {
        "overall_sentiment": overall,
        "sentiment_label": news.get("sentiment_label"),
        "headline_count": news.get("headline_count", 0),
        "source": news.get("source", "synthetic"),
    }


def derive_crisis_intensity(
    stress_text: str | None,
    symbol: str | None,
    *,
    fetch_news: bool,
) -> tuple[float, dict[str, Any]]:
    """Map the stress description + news to an engine ``crisis_intensity`` in [CI_MIN, CI_MAX].

    Returns ``(intensity, detail)`` where ``detail`` records the component signals so the
    gateway can surface *why* a run was severe (and so it stays honest about real-vs-synthetic
    news). ``fetch_news=False`` skips the news call entirely for offline/baseline builds.
    """
    text_sev, text_detail = _text_severity(stress_text or "")
    news_sev, news_detail = _news_severity((symbol or "").strip().upper(), fetch_news=fetch_news)

    # Real Alpha Vantage news is genuine signal about this name and carries full weight;
    # the synthetic fallback is a generic crisis tape (always negative), so it only nudges
    # — the user's own description, not a stand-in feed, must drive a synthetic run.
    news_weight = 1.0 if news_detail.get("source") == "alphavantage" else 0.25
    raw = CI_BASE + CI_TEXT * text_sev + CI_NEWS * news_weight * news_sev
    intensity = round(max(CI_MIN, min(CI_MAX, raw)), 3)
    detail = {
        "intensity": intensity,
        "text_severity": round(text_sev, 3),
        "news_severity": round(news_sev, 3),
        "news_weight": news_weight,
        "text": text_detail,
        "news": news_detail,
    }
    return intensity, detail
