"""Canonical ``session.state`` keys (contract §4).

The ADK session is the short-term memory of a single run. These constants are the
literal keys the agents and the engine read and write, named exactly as in
``docs/contracts.md`` so both halves of the build agree. Using the constants
instead of string literals keeps the firewall auditable: the engine bridge only
touches the six ``*_stance`` keys plus ``SCENARIO_CONFIG`` (read) and
``MARKET_STATE`` / ``RUN_METRICS`` / ``REPLAY_REF`` (write).
"""

from __future__ import annotations

from engine.schema import INVESTOR_TYPES, STANCE_KEYS, InvestorType

# Inputs / scenario
SCENARIO_RAW = "scenario_raw"  # str, user's plain-language request
SCENARIO_CONFIG = "scenario_config"  # RunConfig (scenario_author output_key)
INSTRUMENT_REFERENCE = "instrument_reference"  # object, from Market Data MCP

# Per-window archetype stances — one distinct key per type (no parallel races).
STANCE_KEYS = STANCE_KEYS  # re-export: {InvestorType: f"{type}_stance"}

# News + loop bookkeeping
LATEST_NEWS = "latest_news"  # object, from News MCP, refreshed each window
TICK_WINDOW_INDEX = "tick_window_index"  # int, simulate loop counter

# Engine outputs
MARKET_STATE = "market_state"  # MarketState, written by the engine bridge
RUN_METRICS = "run_metrics"  # Metrics, written at run end
REPLAY_REF = "replay_ref"  # str path/URI to the NDJSON replay

# Narrative / quality (analyst now; critic in a later phase)
ANALYSIS = "analysis"  # analyst output_key
CALIBRATION_REPORT = "calibration_report"  # critic output_key (later phase)
CALIBRATION_ADJUSTMENTS = "calibration_adjustments"  # memory <-> archetypes (later phase)


def stance_key(investor_type: InvestorType) -> str:
    """The session.state key an archetype of ``investor_type`` writes its stance to."""
    return STANCE_KEYS[investor_type]


#: All six stance keys, in canonical investor-type order.
ALL_STANCE_KEYS: tuple[str, ...] = tuple(STANCE_KEYS[t] for t in INVESTOR_TYPES)
