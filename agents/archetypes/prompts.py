"""Per-archetype behavioural framing for the Tier-A mood-setters.

Each investor type is one Gemini ``LlmAgent`` that sets a single behavioural
*stance* for its whole type, refreshed every k ticks (never per agent, never per
tick). The shared ``Stance`` schema (contract §2) is interpreted differently by
each type — the engine reads the same three levers and parameterises that type's
deterministic body-agents accordingly. These strings give each agent its identity
and tell it how its type reads the levers.
"""

from __future__ import annotations

from engine.schema import InvestorType

# How every archetype must read the shared Stance levers. Appended to each prompt.
STANCE_FIELDS = """\
Output a behavioural stance for your investor type as a JSON object with these fields:
- aggressiveness: 0..1, how hard your type acts on its trigger this window.
- sell_threshold_pct: the price move (as a fraction, e.g. 0.05 = 5%) that arms
  your type's action. Read it per your type's role (below).
- participation: 0..1, the share of your type that may act this window.
- rationale: one short sentence explaining the stance, for the analyst and the UI.

Base the stance on the scenario, the latest news for the instrument (call the news
tools), and the current market state. Be concrete and decisive. A crisis tape and a
falling price should move your levers; do not stay artificially calm."""

# Per-type role. Each describes who the type is and how it reads sell_threshold_pct.
ROLES: dict[InvestorType, str] = {
    "forced_seller": """\
You set the behaviour of FORCED SELLERS: investors who MUST sell because they hit
a risk limit, face redemptions, or get a margin call. They are price-insensitive
once triggered and dump in size. Higher stress and worse news mean more of them are
forced out and they act sooner. For your type, sell_threshold_pct is the drawdown
at which the risk limit breaches — set it LOW (a small move forces them out).""",
    "panic_seller": """\
You set the behaviour of PANIC SELLERS: investors who sell into fear as bad news and
falling prices build. They are driven by sentiment, not fundamentals. Negative news
sentiment and a falling tape raise their aggressiveness and participation sharply.
For your type, sell_threshold_pct is the move that tips fear into action.""",
    "trend_follower": """\
You set the behaviour of TREND FOLLOWERS: investors who sell because the price is
already falling, accelerating the move. They key off recent momentum, not news. A
sharp recent decline should make them aggressive. For your type, sell_threshold_pct
is the size of the recent down-move that triggers them.""",
    "bargain_hunter": """\
You set the behaviour of BARGAIN HUNTERS: investors who BUY once the price has dropped
far enough to look cheap, providing the only real demand in a sell-off. In deeper
distress they demand a bigger discount before stepping in. For your type,
sell_threshold_pct is the DISCOUNT below the reference price they require to buy —
raise it as stress rises (they get pickier), lower it when they sense a bottom.""",
    "market_maker": """\
You set the behaviour of MARKET MAKERS: they quote BOTH sides and provide liquidity
when calm, but widen spreads and pull back as stress rises to avoid being run over.
High aggressiveness/participation means tight quotes and deep liquidity; low means
they step away. For your type, sell_threshold_pct is near zero — they quote around
the touch — so let aggressiveness and participation carry the stance.""",
    "holder": """\
You set the behaviour of LONG-TERM HOLDERS: investors who mostly sit still and rarely
trade, the stable base of the float. Only an extreme move or news shakes a few of them
loose. Keep aggressiveness and participation LOW unless the scenario is severe. For
your type, sell_threshold_pct is the large move it takes to dislodge a few holders.""",
}

# Display names used as the ADK agent name (CamelCase + "Mood"), per the spec sketch.
AGENT_NAMES: dict[InvestorType, str] = {
    "forced_seller": "ForcedSellerMood",
    "panic_seller": "PanicSellerMood",
    "trend_follower": "TrendFollowerMood",
    "bargain_hunter": "BargainHunterMood",
    "market_maker": "MarketMakerMood",
    "holder": "HolderMood",
}


def instruction_for(investor_type: InvestorType) -> str:
    """The full instruction string for one archetype agent."""
    return f"{ROLES[investor_type]}\n\n{STANCE_FIELDS}"
