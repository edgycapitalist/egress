"""Deterministic baseline stances — the fixed-heuristic stand-in for Gemini.

In ``baseline_mode`` the six archetype stances come from this heuristic instead of
the Tier-A LlmAgents, so the whole system runs with zero LLM calls (contract §2).
This is the proof that the model is one part of the system: swap these stances for
Gemini's and nothing else in the engine changes.

The heuristic is a plain function of the current price drop and stress level — the
same inputs the real archetype agents read from market state.
"""

from __future__ import annotations

from engine.schema import INVESTOR_TYPES, InvestorType, Stance


def baseline_stances(drop: float, stress: float, tick: int) -> dict[InvestorType, Stance]:
    drop = max(0.0, drop)
    clip = lambda x: max(0.0, min(1.0, x))  # noqa: E731

    stances: dict[InvestorType, Stance] = {
        "forced_seller": Stance(
            aggressiveness=0.85,
            sell_threshold_pct=0.03,
            participation=clip(0.4 + 2.0 * drop),
            updated_at_tick=tick,
            rationale="risk limits breach as the price falls",
        ),
        "panic_seller": Stance(
            aggressiveness=clip(0.4 + drop + 0.5 * stress),
            sell_threshold_pct=0.05,
            participation=clip(0.3 + stress),
            updated_at_tick=tick,
            rationale="fear builds with bad news and a falling tape",
        ),
        "trend_follower": Stance(
            aggressiveness=0.7,
            sell_threshold_pct=0.02,
            participation=0.7,
            updated_at_tick=tick,
            rationale="momentum: sell because the price is already falling",
        ),
        "bargain_hunter": Stance(
            aggressiveness=0.5,
            # Demand a deeper discount as stress rises before stepping in to buy.
            sell_threshold_pct=0.10 + 0.15 * stress,
            participation=0.6,
            updated_at_tick=tick,
            rationale="buy once the discount is large enough",
        ),
        "market_maker": Stance(
            aggressiveness=clip(1.0 - stress),
            sell_threshold_pct=0.0,
            participation=clip(1.0 - stress),
            updated_at_tick=tick,
            rationale="quote both sides when calm, pull back under stress",
        ),
        "holder": Stance(
            aggressiveness=0.05,
            sell_threshold_pct=0.20,
            participation=0.1,
            updated_at_tick=tick,
            rationale="mostly sit still",
        ),
    }
    assert set(stances) == set(INVESTOR_TYPES)
    return stances
