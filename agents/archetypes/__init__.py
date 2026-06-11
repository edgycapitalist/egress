"""Archetype mood-setter agents — one LlmAgent per investor type, run as a ParallelAgent.

The live path is six Gemini ``LlmAgent``s (``agent.py``) fanned out by a
``ParallelAgent``, each writing its own ``*_stance`` key. The baseline path
(``baseline.py``) is a single deterministic agent that fills the same keys with
zero LLM calls for offline runs and tests.
"""

from agents.archetypes.agent import (
    build_archetype_agent,
    build_archetype_agents,
    build_archetypes_parallel,
)
from agents.archetypes.baseline import BaselineStancesAgent

__all__ = [
    "build_archetype_agent",
    "build_archetype_agents",
    "build_archetypes_parallel",
    "BaselineStancesAgent",
]
