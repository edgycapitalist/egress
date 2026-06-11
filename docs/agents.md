# The ADK orchestration layer (Phase 2)

This document describes the `agents/` and `mcp/` layers built in Phase 2 and how
they meet the deterministic engine at the boundary in
[`contracts.md`](./contracts.md). For the full specification see
[`AGENTS.md`](../AGENTS.md) §4 and §6.

## The agent tree

The run lifecycle is a `SequentialAgent`:

```
EgressRun (SequentialAgent)
├── ScenarioAuthor      LlmAgent  → scenario_config        (live path only)
├── SetupEngine         BaseAgent → market_state, replay_ref
├── SimulateLoop        LoopAgent (one iteration == one window of k ticks)
│   ├── Archetypes      ParallelAgent of 6 LlmAgents → *_stance   (live)
│   │   └── (baseline)  BaselineStances BaseAgent → the same 6 keys, no LLM
│   └── AdvanceEngine   BaseAgent → engine.advance(stances, k), market_state
├── FinalizeEngine      BaseAgent → run_metrics, replay_ref
└── Analyst             LlmAgent  → analysis               (live)
    └── (baseline)      BaselineAnalyst BaseAgent → analysis from a template, no LLM
```

ADK patterns used, explicitly: **sequential** pipeline (lifecycle), **loop** (the
tick engine + stance refresh), **parallel fan-out** (the six archetypes, each with
a distinct `output_key`), **coordinator** (the scenario author), **tools** (MCP
tools as ADK `FunctionTool`s), and **sessions / state** as the short-term memory
of one run. The workflow agents are required by the rubric; ADK 2.2 marks them
deprecated and we accept that notice.

## Tier A — the six archetype mood-setters

One `LlmAgent` per investor type (`forced_seller`, `panic_seller`,
`trend_follower`, `bargain_hunter`, `market_maker`, `holder`), each using the fast
Gemini model, calling the News and Market Data MCP tools, reading the current
`market_state` from session state, and emitting a validated `Stance`
(`output_schema=Stance`) to its **own** `*_stance` key. Distinct keys mean the
`ParallelAgent` fan-out never races on shared state. Stances refresh **once per
window** (every `k` ticks) — never per agent, never per tick — which is what keeps
thousands of agents cheap.

## Live vs. baseline — one tree, two seams

The **live Vertex/Gemini path is the product.** Baseline is the swappable
offline/test mode, and it swaps exactly two seams:

| Seam | Live | Baseline |
| --- | --- | --- |
| Stance producer | `ParallelAgent` of 6 Gemini `LlmAgent`s | `BaselineStancesAgent` (engine's `baseline_stances` heuristic) |
| Analyst | Gemini `LlmAgent` | `BaselineAnalystAgent` (template from metrics) |
| Scenario author | Gemini `LlmAgent` | bypassed — the driver supplies a prebuilt `RunConfig` |

Every other agent (setup, advance, finalize) is identical and deterministic. In
baseline mode the whole pipeline runs with **zero LLM calls** — this is the
`DETERMINISTIC_BASELINE` requirement and what the offline test suite exercises
(`tests/test_orchestrator_baseline.py`, `make demo-agents`). There is **no mock
model in the live path**: once Vertex auth is in place (`make auth-check`), the
`LlmAgent`s make real Gemini calls with no further wiring.

## The firewall

The engine bridge (`agents/orchestrator/engine_bridge.py`) is the only place the
agent layer touches the engine, and it contains no LLM call. It honours the
contract firewall (§4): it **reads** only the six `*_stance` keys plus
`scenario_config`, and **writes** only `market_state`, `run_metrics`, and
`replay_ref`. The live `Engine` object is not JSON-serialisable and is **not**
placed in `session.state`; it lives in a per-run registry keyed by `run_id`, so
the contract keys in session state stay clean and the engine remains independently
testable. A malformed or missing stance (an LLM hiccup) falls back to the
deterministic baseline for that type, so a bad model output can never crash the
engine or stall the run.

## The two MCP servers

`mcp/market_data` and `mcp/news` each expose their spec tool signatures two ways:

- **In-process `FunctionTool`s** (`tools.py`) — the path the agents use today.
  No running server, no cloud, fully offline and unit-tested (`tools.py` wraps the
  deterministic backend in `data.py`).
- **FastMCP servers** (`server.py`) — the deployment surface, run as a path script.

### Name-collision note

The repo's package is named `mcp` (per the AGENTS.md repo map), which shadows the
PyPI `mcp` SDK. The agent path never imports the SDK (it uses `FunctionTool`s), so
the offline suite is unaffected. The FastMCP servers import the SDK lazily and are
run **as path scripts** (`python mcp/market_data/server.py`) so that `import mcp`
resolves to the installed SDK rather than this repo's package; in a container the
server code is the only `mcp` on the path, so the question does not arise.

## Authentication

Gemini is reached **only through Vertex AI** with Application Default Credentials
(`GOOGLE_GENAI_USE_VERTEXAI=true`, project + location set). An AI Studio
`GOOGLE_API_KEY` is forbidden and actively rejected in `agents/common/env.py`.
`scripts/check_vertex_auth.py` (`make auth-check`) makes one real Gemini call to
confirm auth and quota before a live run.
