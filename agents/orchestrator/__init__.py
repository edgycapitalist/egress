"""Orchestrator — the ADK run lifecycle (SequentialAgent) and its run driver.

``agent.py`` assembles the lifecycle (scenario author → setup → simulate loop →
finalize → analyst); ``engine_bridge.py`` is the deterministic glue to the Phase-1
engine; ``driver.py`` executes a run with the ADK Runner and returns the contract
outputs. The baseline assembly runs the whole pipeline with zero LLM calls.
"""

from agents.orchestrator.agent import build_orchestrator, build_simulate_loop
from agents.orchestrator.driver import run_baseline_simulation, run_live_simulation

__all__ = [
    "build_orchestrator",
    "build_simulate_loop",
    "run_baseline_simulation",
    "run_live_simulation",
]
