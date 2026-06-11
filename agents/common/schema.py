"""Shared boundary schemas, re-exported for the agents (contract §36).

The Pydantic models are owned by the engine (``engine/schema.py``) because the
engine must depend on nothing but the core deps and stay LLM- and cloud-free.
The agents import them from here so both halves of the build share one definition
of ``RunConfig``, ``Stance``, ``MarketState``, ``TickEvent``, and ``Metrics``.
"""

from __future__ import annotations

from engine.schema import (
    INVESTOR_TYPES,
    SCHEMA_VERSION,
    STANCE_KEYS,
    CrowdingMix,
    Depth,
    ExitSpeed,
    Fill,
    Instrument,
    InvestorType,
    MarketState,
    MetaRecord,
    Metrics,
    Position,
    RunConfig,
    Shock,
    Stance,
    TickEvent,
)

__all__ = [
    "INVESTOR_TYPES",
    "SCHEMA_VERSION",
    "STANCE_KEYS",
    "CrowdingMix",
    "Depth",
    "ExitSpeed",
    "Fill",
    "Instrument",
    "InvestorType",
    "MarketState",
    "MetaRecord",
    "Metrics",
    "Position",
    "RunConfig",
    "Shock",
    "Stance",
    "TickEvent",
]
