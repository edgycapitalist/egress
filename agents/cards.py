"""Machine-readable agent metadata for A2A-style discovery.

This is the smallest honest platform surface: it advertises the agents, their
roles, models, state keys, and whether the current process is using local
in-process orchestration or a remote Agent Engine route. Full A2A transport is a
separate deployment concern; these cards are intentionally explicit about that.
"""

from __future__ import annotations

from typing import Any

from engine.schema import INVESTOR_TYPES, STANCE_KEYS

from agents.archetypes.prompts import AGENT_NAMES
from agents.orchestrator.remote import orchestrator_mode, remote_configured


def agent_cards() -> list[dict[str, Any]]:
    transport = (
        "agent_engine_remote" if orchestrator_mode() == "agent_engine" else "in_process_adk"
    )
    cards: list[dict[str, Any]] = [
        {
            "name": "EgressRun",
            "role": "orchestrator",
            "framework": "google_adk",
            "transport": transport,
            "remote_configured": remote_configured(),
            "description": (
                "SequentialAgent lifecycle for scenario setup, simulation, "
                "analysis, and optional critic."
            ),
            "inputs": ["scenario_raw", "scenario_config"],
            "outputs": ["run_metrics", "analysis", "replay_ref", "ensemble_result"],
        },
        {
            "name": "ScenarioAuthor",
            "role": "scenario_author",
            "framework": "google_adk",
            "model_class": "gemini_fast",
            "tools": ["market_data_mcp"],
            "description": "Turns plain-language stress text into a validated RunConfig.",
            "outputs": ["scenario_config", "scenario_brief"],
        },
        {
            "name": "Analyst",
            "role": "analyst",
            "framework": "google_adk",
            "model_class": "gemini_strong",
            "tools": ["vertex_search_rag", "memory_service"],
            "description": (
                "Explains the deterministic run using metrics, replay, memory "
                "context, and retrieved references."
            ),
            "outputs": ["analysis"],
        },
        {
            "name": "CalibrationCritic",
            "role": "critic",
            "framework": "google_adk",
            "model_class": "gemini_strong",
            "tools": ["vertex_search_rag", "memory_service"],
            "description": (
                "Judges simulated behaviour against historical episodes and "
                "writes bounded calibration adjustments."
            ),
            "outputs": ["calibration_report", "calibration_adjustments"],
        },
    ]
    for investor_type in INVESTOR_TYPES:
        cards.append(
            {
                "name": AGENT_NAMES[investor_type],
                "role": "archetype_stance",
                "investor_type": investor_type,
                "framework": "google_adk",
                "model_class": "gemini_fast",
                "tools": ["news_mcp", "market_data_mcp"],
                "description": (
                    "Sets one group-level behavioural stance; deterministic "
                    "agents fan it out to the population."
                ),
                "outputs": [STANCE_KEYS[investor_type]],
            }
        )
    return cards


def discovery_payload() -> dict[str, Any]:
    return {
        "protocol": "a2a-discovery-compatible",
        "transport_status": (
            "remote_agent_engine_configured"
            if orchestrator_mode() == "agent_engine" and remote_configured()
            else "metadata_only_local_or_unconfigured"
        ),
        "cards": agent_cards(),
    }
