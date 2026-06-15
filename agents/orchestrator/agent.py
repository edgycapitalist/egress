"""The Orchestrator — the ADK run lifecycle as a ``SequentialAgent``.

Lifecycle (AGENTS.md §4):

    scenario_author → setup → simulate-loop → finalize → analyst

The simulate step is a ``LoopAgent`` whose body is ``[stance-producer, advance-engine]``:
each iteration refreshes the six archetype stances and then advances the deterministic
engine one window of ``k`` ticks. The loop stops as soon as the engine reports the run
is done (the advance agent escalates), with a hard window cap as a backstop.

Two assemblies share every deterministic piece and differ only at the swappable
seams:

* **live** — the product path: the Gemini scenario author, the ``ParallelAgent`` of
  Gemini mood-setters, and the Gemini analyst.
* **baseline** — the offline/test path: no scenario author (the driver supplies a
  prebuilt ``scenario_config``), the deterministic stance agent, and the deterministic
  template analyst. Zero LLM calls, end to end.

ADK workflow agents (``SequentialAgent``, ``LoopAgent``, ``ParallelAgent``) are used
deliberately here per the competition rubric; ADK 2.2 emits a deprecation notice for
them, which we accept.
"""

from __future__ import annotations

from google.adk.agents import LoopAgent, SequentialAgent

from agents.analyst.agent import build_analyst
from agents.analyst.baseline import BaselineAnalystAgent
from agents.archetypes.agent import build_archetypes_parallel
from agents.archetypes.baseline import BaselineStancesAgent
from agents.critic import build_critic
from agents.critic.baseline import BaselineCriticAgent
from agents.orchestrator.engine_bridge import (
    AdvanceEngineAgent,
    FinalizeEngineAgent,
    SetupEngineAgent,
)
from agents.orchestrator.memory_bridge import LoadMemoryContextAgent, PersistMemoryAgent
from agents.scenario_author.agent import build_scenario_author

# Backstop on loop iterations; real termination comes from the engine escalating
# (exit complete, stall, or max_ticks). One iteration == one window of k ticks.
MAX_TICK_WINDOWS = 500


def build_simulate_loop(*, baseline: bool) -> LoopAgent:
    """The tick engine: refresh stances, then advance one window, until done."""
    stance_producer = BaselineStancesAgent() if baseline else build_archetypes_parallel()
    return LoopAgent(
        name="SimulateLoop",
        max_iterations=MAX_TICK_WINDOWS,
        sub_agents=[stance_producer, AdvanceEngineAgent()],
    )


def build_orchestrator(*, baseline: bool, with_critic: bool = False) -> SequentialAgent:
    """Assemble the run-lifecycle ``SequentialAgent`` for the chosen mode.

    ``with_critic`` appends the calibration critic as the final step (the diagram's
    ORCH → CRITIC edge). It is off by default so the gateway's interactive runs stay
    lean; the backtest, ``make eval``, and the orchestrator CLI's ``--critic`` flag
    turn it on. One forward pass produces a ``calibration_report``; the iterate-and-
    re-run generator-critic loop lives in ``eval/backtest.py``.
    """
    sub_agents: list = []
    if not baseline:
        # Live: the LLM scenario author produces scenario_config first.
        sub_agents.append(build_scenario_author())
    sub_agents.append(SetupEngineAgent())
    sub_agents.append(build_simulate_loop(baseline=baseline))
    sub_agents.append(FinalizeEngineAgent())
    sub_agents.append(LoadMemoryContextAgent())
    sub_agents.append(BaselineAnalystAgent() if baseline else build_analyst())
    if with_critic:
        sub_agents.append(BaselineCriticAgent() if baseline else build_critic())
    sub_agents.append(PersistMemoryAgent())
    return SequentialAgent(name="EgressRun", sub_agents=sub_agents)
