"""Analyst agent — explains the run in plain language, grounded in the sim log and RAG.

The live path is a Gemini ``LlmAgent`` (``agent.py``) that interprets the engine's
metrics. The baseline path (``baseline.py``) renders the same explanation from a
deterministic template so an offline run still produces the narrative.
"""

from agents.analyst.agent import build_analyst
from agents.analyst.baseline import BaselineAnalystAgent, render_summary

__all__ = ["build_analyst", "BaselineAnalystAgent", "render_summary"]
