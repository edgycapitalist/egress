"""Calibration Critic agent — LLM-as-judge against a real historical episode.

The critic is the behavioural-fidelity quality gate (AGENTS.md §4, §11 Phase 4): it
compares a finished run to a curated real crisis episode and judges whether the
simulated crowd was plausible or too calm, proposing bounded per-type stance nudges
the generator-critic loop re-runs with.

* :func:`build_critic` — the live Gemini judge (``agent.py``).
* :class:`BaselineCriticAgent` — the deterministic, zero-LLM stand-in (``baseline.py``).

Both resolve the reference episode and run the same deterministic comparison
(``compare.py`` / ``core.py``); only the narrative differs.
"""

from __future__ import annotations

from agents.critic.baseline import BaselineCriticAgent
from agents.critic.core import report_for_run

__all__ = ["BaselineCriticAgent", "build_critic", "report_for_run"]


def build_critic():  # lazy import keeps the live ADK agent off the offline import path
    from agents.critic.agent import build_critic as _build

    return _build()
