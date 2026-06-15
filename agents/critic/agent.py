"""Calibration Critic (Gemini via Vertex AI) — LLM-as-judge against a real episode.

After a run finishes, the critic compares the simulated unwind to a curated real
crisis episode and judges whether the simulated crowd behaved plausibly or too
calmly — the known tendency of model-driven market agents to behave too rationally
(AGENTS.md §4). It is grounded in the deterministic comparison (the episode's
behavioural signature and the per-axis gaps) so the model interprets real numbers
rather than inventing a verdict, exactly as the analyst is grounded in the run's
metrics. The model writes the narrative judgement; an ``after_agent_callback``
attaches the structured verdict and the bounded per-type ``calibration_adjustments``
the generator-critic loop re-runs with — the same permissive-model / deterministic-
guardrail split used by the scenario author and the archetypes.

Vertex AI Search grounding over the wider episode corpus is a later phase; for now
the reference is the packaged episode.
"""

from __future__ import annotations

import json

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext

from agents.common.env import strong_model
from agents.common.state import (
    CALIBRATION_ADJUSTMENTS,
    CALIBRATION_REPORT,
    MEMORY_CONTEXT,
    RUN_METRICS,
    SCENARIO_CONFIG,
)
from agents.common.timing import (
    after_agent,
    after_model,
    before_agent,
    before_model,
    on_model_error,
)
from agents.critic.core import report_for_run

# The model writes its narrative judgement here; the callback finalises the
# structured report + adjustments, mirroring the scenario author's draft→config split.
CALIBRATION_NARRATIVE = "calibration_narrative"

INSTRUCTION = """\
You are the Calibration Critic for Egress, a crisis-exit market simulator. A
simulation has just run. Your job is the quality gate on behavioural fidelity: judge
whether the simulated crowd behaved like a real crisis crowd, or implausibly calmly.

Model-driven market agents tend to behave too rationally — selling too orderly,
support that does not evaporate, a price that does not move far enough. You check the
run against a real historical episode to catch exactly that.

A deterministic comparison has already measured the run against the reference episode
on three axes — how far the price was forced (drawdown), how much of the position was
left stranded (liquidity), and whether the move was disorderly enough to halt. Use
those numbers (below) as your evidence. In a few tight sentences:
- State the verdict: is the simulated crowd plausible, or too calm?
- Cite the specific axes that fell short, with the numbers.
- If too calm, say which investor types should act harder and which support should
  thin out to match how the real episode behaved.

Be concrete and honest. Do not invent numbers beyond those given. Do not recommend
trades; judge the simulation's realism."""


def _evidence_block(ctx: ReadonlyContext) -> str:
    state = getattr(ctx, "state", {}) or {}
    scenario = state.get(SCENARIO_CONFIG) or {}
    metrics = state.get(RUN_METRICS) or {}
    report = report_for_run(scenario, metrics)
    gaps = [
        {
            "axis": g.axis,
            "simulated": g.simulated,
            "expected": g.expected,
            "plausible": g.plausible,
            "note": g.note,
        }
        for g in report.gaps
    ]
    try:
        from rag import format_snippets, retrieve_context

        query = " ".join(
            str(part)
            for part in (
                report.symbol,
                report.episode_id,
                report.flags,
                "calibration crisis episode microstructure crowded exit",
            )
        )
        rag_context = format_snippets(retrieve_context(query))
    except Exception as exc:
        rag_context = f"Retrieval unavailable: {exc.__class__.__name__}"
    return (
        "\n\n--- Calibration evidence (the source of truth) ---\n"
        f"Reference episode: {report.symbol or 'none'} ({report.episode_id or 'no reference'})\n"
        f"Deterministic verdict: {report.verdict} "
        f"(fidelity {report.plausibility_score:.0%}, flags {report.flags})\n"
        f"Per-axis gaps: {json.dumps(gaps)}\n"
        f"Run metrics: {json.dumps(metrics)}\n"
        f"Memory context: {json.dumps(state.get(MEMORY_CONTEXT) or {})}\n"
        "\n--- Retrieved reference snippets (source-labelled grounding) ---\n"
        f"{rag_context}"
    )


def _instruction_provider(ctx: ReadonlyContext) -> str:
    return INSTRUCTION + _evidence_block(ctx)


def _finalize_report(callback_context: CallbackContext):
    """Attach the structured verdict + bounded adjustments to the model's narrative."""
    state = callback_context.state
    scenario = state.get(SCENARIO_CONFIG) or {}
    metrics = state.get(RUN_METRICS) or {}
    report = report_for_run(scenario, metrics)

    narrative = state.get(CALIBRATION_NARRATIVE)
    if isinstance(narrative, str) and narrative.strip():
        report.narrative = narrative.strip()
    else:  # model produced nothing usable — fall back to the deterministic verdict
        from agents.critic.compare import render_verdict

        report.narrative = render_verdict(report)

    state[CALIBRATION_REPORT] = report.model_dump()
    state[CALIBRATION_ADJUSTMENTS] = report.adjustments.model_dump()
    return None


def build_critic() -> LlmAgent:
    """The Calibration Critic ``LlmAgent`` (live Vertex path)."""
    return LlmAgent(
        name="CalibrationCritic",
        model=strong_model(),
        instruction=_instruction_provider,
        description="Judges whether the simulated crowd behaved plausibly vs a real episode.",
        output_key=CALIBRATION_NARRATIVE,
        before_agent_callback=before_agent("CalibrationCritic"),
        after_agent_callback=[_finalize_report, after_agent("CalibrationCritic")],
        before_model_callback=before_model,
        after_model_callback=after_model,
        on_model_error_callback=on_model_error,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
