"""Shared glue: turn a finished run's session state into a calibration report.

Both critic implementations — the deterministic baseline stand-in and the live
Gemini judge — resolve the reference episode and run the same deterministic
comparison through here, so they always agree on the numbers and differ only in who
writes the narrative.
"""

from __future__ import annotations

from agents.critic.compare import compare_to_episode, no_reference_report
from agents.critic.episode import episode_for_symbol
from agents.critic.schema import CalibrationReport


def report_for_run(scenario: dict, metrics: dict) -> CalibrationReport:
    """Compare a run to its instrument's curated episode (``no_reference`` if none)."""
    symbol = ((scenario or {}).get("instrument") or {}).get("symbol")
    episode = episode_for_symbol(symbol)
    if episode is None or not metrics:
        return no_reference_report(symbol)
    return compare_to_episode(metrics, episode)
