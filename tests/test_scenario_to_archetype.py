"""CHANGE 1: the described scenario must reach the archetype mood-setters.

These confirm the scenario author writes a structured brief of the crisis into
session state, and that the archetype context block surfaces it — so the stance
is driven by the *situation*, not just the ticker. Fully offline (no LLM).
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.archetypes.agent import _context_block
from agents.common.state import (
    SCENARIO_BRIEF,
    SCENARIO_CONFIG,
    SCENARIO_RAW,
)
from agents.scenario_author.agent import _finalize_scenario, compose_brief

DRAFT = {
    "symbol": "ACME",
    "position_quantity": 250_000,
    "exit_mode": "participation",
    "participation_rate": 0.12,
    "crowding": {"forced_seller": 0.3, "panic_seller": 0.3, "trend_follower": 0.2,
                 "bargain_hunter": 0.1, "market_maker": 0.05, "holder": 0.05},
    "shocks": [
        {"tick": 0, "kind": "news", "severity": 0.8, "note": "rating downgrade to junk"},
        {"tick": 30, "kind": "price", "severity": 0.5, "note": "gap down on heavy volume"},
    ],
    "rationale": "A crowded mid-cap unwinds as forced sellers hit margin calls.",
}


def test_scenario_author_writes_brief() -> None:
    state = {"scenario_draft": DRAFT, SCENARIO_RAW: "I hold 250k ACME and fear a downgrade crash."}
    ctx = SimpleNamespace(state=state)
    _finalize_scenario(42)(ctx)

    brief = state.get(SCENARIO_BRIEF)
    assert brief, "scenario author must write a brief"
    assert "forced sellers" in brief  # the model's rationale
    assert "rating downgrade to junk" in brief  # a stress-event note
    assert "I hold 250k ACME" in brief  # a faithful echo of the user's words
    assert state.get(SCENARIO_CONFIG)  # config still produced


def test_compose_brief_is_robust_to_missing_pieces() -> None:
    # No rationale, no raw text: still surfaces the stress events.
    cfg = SimpleNamespace(shock_schedule=[SimpleNamespace(note="liquidity dries up")])
    brief = compose_brief({}, "", cfg)
    assert "liquidity dries up" in brief


def test_archetype_context_includes_the_scenario() -> None:
    state = {
        SCENARIO_CONFIG: {"instrument": {"symbol": "ACME"}},
        SCENARIO_BRIEF: "A crowded mid-cap unwinds.\nStress events: rating downgrade to junk.",
    }
    block = _context_block(SimpleNamespace(state=state))
    assert "ACME" in block
    assert "rating downgrade to junk" in block  # the crisis reaches the archetype
    assert "Scenario & stress" in block


def test_archetype_context_falls_back_to_raw_text() -> None:
    state = {
        SCENARIO_CONFIG: {"instrument": {"symbol": "ZZZ"}},
        SCENARIO_RAW: "Dump everything before the halt.",
    }
    block = _context_block(SimpleNamespace(state=state))
    assert "Dump everything before the halt." in block
