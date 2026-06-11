"""Scenario Author agent — parses and validates the user scenario into a RunConfig.

The ``LlmAgent`` (``agent.py``) drafts the scenario and grounds the instrument on
the Market Data MCP; ``validation.py`` deterministically assembles and validates
the full ``RunConfig``. The validation helpers are reused by the baseline driver,
which skips the LLM and supplies a prebuilt scenario.
"""

from agents.scenario_author.agent import build_scenario_author
from agents.scenario_author.validation import (
    ScenarioDraft,
    build_run_config,
)

__all__ = ["build_scenario_author", "ScenarioDraft", "build_run_config"]
