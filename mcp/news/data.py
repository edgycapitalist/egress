"""Deterministic news backend for the News MCP.

Plain functions, no MCP/ADK/cloud dependency, shared by the FastMCP server and the
in-process ADK ``FunctionTool`` wrappers. The archetype agents read these to set
their behavioural mood: how negative the tape is for an instrument over a period,
and the sentiment of a piece of text.

Data source: a deterministic synthesiser seeded by symbol + period for the build.
A live, authorised news feed is a later upgrade (AGENTS.md §6) and will be declared
in the submission.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

# Lexicons for the lightweight deterministic sentiment scorer.
_NEGATIVE = {
    "downgrade", "cut", "plunge", "plummet", "selloff", "sell-off", "crash",
    "default", "bankruptcy", "fraud", "probe", "lawsuit", "withdrawal",
    "margin", "liquidation", "fear", "panic", "collapse", "halt", "loss",
    "losses", "miss", "warning", "slump", "fall", "falling", "weak", "risk",
    "downturn", "recession", "contagion", "distress", "redemptions",
}
_POSITIVE = {
    "upgrade", "beat", "surge", "rally", "rebound", "growth", "profit",
    "gain", "gains", "strong", "record", "support", "stabilise", "stabilize",
    "recovery", "inflow", "inflows", "optimism", "calm",
}

# Curated negative headline templates for a credible crisis tape.
_TEMPLATES = [
    ("{sym} cut to junk by major rating agency", "Ratings Wire", -0.8),
    ("Funds rush to trim {sym} as risk limits breach", "Market Desk", -0.7),
    ("{sym} gaps lower on heavy volume amid forced selling", "Tape Report", -0.75),
    ("Analysts warn of crowded positioning in {sym}", "Street Research", -0.5),
    ("Liquidity thins in {sym} as market makers step back", "Microstructure Daily", -0.6),
    ("{sym} slides as redemptions hit holders", "Flows Monitor", -0.65),
    ("Bargain hunters eye {sym} after steep drop", "Value Watch", 0.2),
]


def _symbol_seed(symbol: str, period: str) -> int:
    digest = hashlib.sha256(f"{symbol.upper()}|{period}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def get_sentiment(text: str) -> dict:
    """Score the sentiment of ``text`` in ``[-1, 1]`` with a magnitude.

    A deterministic lexicon scorer — no model call — so the archetypes get a
    stable, explainable mood signal offline. ``label`` is negative/neutral/positive.
    """
    tokens = re.findall(r"[a-zA-Z'-]+", text.lower())
    if not tokens:
        return {"score": 0.0, "label": "neutral", "magnitude": 0.0, "tokens": 0}
    neg = sum(1 for t in tokens if t in _NEGATIVE)
    pos = sum(1 for t in tokens if t in _POSITIVE)
    raw = (pos - neg) / max(1, neg + pos)
    score = round(max(-1.0, min(1.0, raw)), 3)
    magnitude = round((neg + pos) / len(tokens), 3)
    label = "negative" if score < -0.15 else "positive" if score > 0.15 else "neutral"
    return {"score": score, "label": label, "magnitude": magnitude, "tokens": len(tokens)}


def get_event_news(instrument: str, period: str) -> dict:
    """Headlines and an aggregate sentiment for ``instrument`` over ``period``.

    Returns a list of dated headlines (each with a source and sentiment) and an
    overall sentiment score the archetype agents use to set their stance. The
    crisis tape skews negative, as a real sell-off would.
    """
    sym = instrument.upper()
    rng = np.random.default_rng(_symbol_seed(sym, period))
    n = int(rng.integers(4, len(_TEMPLATES) + 1))
    chosen = rng.choice(len(_TEMPLATES), size=n, replace=False)
    headlines: list[dict] = []
    scores: list[float] = []
    for offset, i in enumerate(sorted(chosen)):
        template, source, base = _TEMPLATES[i]
        jitter = float(rng.normal(0.0, 0.08))
        s = round(max(-1.0, min(1.0, base + jitter)), 3)
        scores.append(s)
        headlines.append(
            {
                "day": offset,
                "headline": template.format(sym=sym),
                "source": source,
                "sentiment": s,
            }
        )
    overall = round(sum(scores) / len(scores), 3) if scores else 0.0
    label = "negative" if overall < -0.15 else "positive" if overall > 0.15 else "neutral"
    return {
        "symbol": sym,
        "period": period,
        "headlines": headlines,
        "overall_sentiment": overall,
        "sentiment_label": label,
        "headline_count": len(headlines),
    }
