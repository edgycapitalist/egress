"""Offline construction tests for the Tier-A archetypes and the scenario author.

These build the live (Gemini) agents and assert their wiring without making any
LLM call — the conftest stub keeps imports offline.
"""

import pytest
from agents.archetypes.agent import build_archetype_agents, build_archetypes_parallel
from agents.common.env import fast_model
from agents.common.state import ALL_STANCE_KEYS
from agents.scenario_author.agent import build_scenario_author
from agents.scenario_author.validation import ScenarioDraft, build_run_config
from engine.schema import INVESTOR_TYPES, RunConfig
from pydantic import ValidationError


def test_six_archetypes_write_distinct_stance_keys() -> None:
    agents = build_archetype_agents()
    assert len(agents) == len(INVESTOR_TYPES)
    keys = [a.output_key for a in agents]
    assert keys == list(ALL_STANCE_KEYS)
    assert len(set(keys)) == 6  # distinct => no parallel-write race


def test_archetypes_use_fast_model_and_both_mcp_tools() -> None:
    agents = build_archetype_agents()
    for a in agents:
        assert a.model == fast_model()  # the configured Gemini model (3.1 by default)
        # News (2) + Market Data (3) tools attached.
        assert len(a.tools) == 5


def test_archetypes_parallel_fans_out_six() -> None:
    par = build_archetypes_parallel()
    assert par.name == "Archetypes"
    assert len(par.sub_agents) == 6


def test_scenario_author_is_wired() -> None:
    sa = build_scenario_author(seed_value=42)
    assert sa.name == "ScenarioAuthor"
    assert sa.output_key == "scenario_draft"
    assert len(sa.tools) == 3  # market-data tools for grounding
    assert sa.after_agent_callback is not None


def test_build_run_config_normalises_and_validates() -> None:
    draft = ScenarioDraft(
        symbol="ACME",
        position_quantity=250_000,
        exit_mode="participation",
        participation_rate=0.12,
        crowding={
            "forced_seller": 2,
            "panic_seller": 2,
            "trend_follower": 2,
            "bargain_hunter": 1.5,
            "market_maker": 1,
            "holder": 1.5,
        },
        shocks=[
            {"tick": 0, "kind": "news", "severity": 0.8, "note": "downgrade"},
            {"tick": 999, "kind": "news", "severity": 0.4, "note": "past horizon"},
        ],
        max_ticks=300,
        ticks_per_window=10,
    )
    cfg, ref = build_run_config(draft, run_id="t1", seed=42, baseline_mode=False)
    assert isinstance(cfg, RunConfig)
    # Crowding normalised to 1.0 and instrument resolved from the data source.
    assert abs(sum(cfg.crowding_mix.as_dict().values()) - 1.0) < 1e-9
    assert cfg.instrument.adv == ref["adv"] == 5_000_000
    assert cfg.position.arrival_price == cfg.instrument.reference_price
    # Shock past the horizon is dropped; halt rule comes from the tier.
    assert len(cfg.shock_schedule) == 1
    assert cfg.halt_rule.band_pct == 0.10


def test_build_run_config_carries_volatility_and_crisis_intensity(monkeypatch) -> None:
    def fake_reference(symbol: str) -> dict:
        return {
            "symbol": symbol.upper(),
            "reference_price": 42.0,
            "tick_size": 0.01,
            "adv": 1_250_000,
            "free_float": 25_000_000,
            "halt_tier": 1,
            "volatility": 0.045,
            "source": "synthetic",
        }

    monkeypatch.setattr(
        "agents.scenario_author.validation.get_instrument_reference",
        fake_reference,
    )
    draft = ScenarioDraft(
        symbol="ACME",
        position_quantity=50_000,
        crisis_intensity=1.35,
    )
    cfg, _ = build_run_config(draft, run_id="vol", seed=42, baseline_mode=False)
    assert cfg.instrument.volatility == 0.045
    assert cfg.crisis_intensity == 1.35


def test_blank_crowding_falls_back_to_even_mix() -> None:
    cfg, _ = build_run_config(
        ScenarioDraft(symbol="ZZZ9", position_quantity=1000), run_id="t2", seed=1
    )
    assert abs(sum(cfg.crowding_mix.as_dict().values()) - 1.0) < 1e-9


def test_degenerate_draft_is_clamped_not_crashed() -> None:
    """The draft schema is permissive; build_run_config clamps to a valid RunConfig.

    This is the robustness contract for live runs: a slightly-out-of-range model
    output (here a zero quantity, blank crowding, and an over-1 shock severity) must
    never crash — it is clamped into legal ranges and validated.
    """
    draft = ScenarioDraft(
        symbol="ACME",
        position_quantity=0,
        ticks_per_window=99_999,  # larger than max_ticks -> clamped down
        shocks=[{"tick": 5, "kind": "news", "severity": 1.8, "note": "over-range"}],
    )
    cfg, _ = build_run_config(draft, run_id="t3", seed=1, baseline_mode=False)
    assert isinstance(cfg, RunConfig)
    assert cfg.position.quantity >= 1
    assert cfg.ticks_per_window <= cfg.max_ticks
    assert all(0.0 <= s.severity <= 1.0 for s in cfg.shock_schedule)


def test_draft_missing_symbol_is_rejected() -> None:
    # `symbol` has no default, so a draft without it is still a hard rejection.
    with pytest.raises(ValidationError):
        ScenarioDraft(position_quantity=1000)
