"""Shared agent helpers — session/state keys, Vertex config, and Pydantic schemas.

This package is the seam between the deterministic engine and the ADK agents. It
re-exports the boundary schemas (owned by ``engine/schema.py``), names the
``session.state`` keys from the contract, and resolves the Vertex AI / model
configuration the live agents run against. Importing it must never require cloud
credentials, so the offline test suite can build the whole agent tree.
"""

from __future__ import annotations

from agents.common import schema, state
from agents.common.env import (
    VertexAuthError,
    assert_vertex_config,
    baseline_mode,
    fast_model,
    load_dotenv,
    seed,
    strong_model,
)
from agents.common.schema import (
    INVESTOR_TYPES,
    MarketState,
    Metrics,
    RunConfig,
    Stance,
)

__all__ = [
    "schema",
    "state",
    "INVESTOR_TYPES",
    "MarketState",
    "Metrics",
    "RunConfig",
    "Stance",
    "VertexAuthError",
    "assert_vertex_config",
    "baseline_mode",
    "fast_model",
    "strong_model",
    "load_dotenv",
    "seed",
]
