# Egress AI

**Simulate how an investment position would behave in a crisis — before the
money is committed.**

Egress models a crisis sell-off as a market of thousands of independent trading
agents that act on their own and react to each other. Their orders meet in an
order book that sets the prices, so the price moves emerge from the crowd's
collective behaviour rather than being assumed. You see whether a position can
actually be sold, how far the price moves while selling, and how much stays
stuck — and you can vary how much is held and how fast it is sold to find the
point where the exit closes.

> Entry to the Google for Startups AI Agents Challenge (Track 1, Build).

## The problem

Firms routinely measure how much they could lose on a position, but not whether
they could actually *sell* it in a crisis. Many firms unknowingly hold the same
crowded trades; when a shock hits and they all sell at once, there are not enough
buyers, the price collapses, and they cannot get out without heavy losses. Egress
lets you test that moment before committing.

## How it works

A plain-language scenario goes in; a simulated exit comes out. Gemini agents set
the behavioural *mood* per investor type; a deterministic engine runs the market.

- **System architecture:** [`docs/egress-architecture.svg`](./docs/egress-architecture.svg)
- **How a single run works:** [`docs/egress-run-flow.svg`](./docs/egress-run-flow.svg)

The defining design choice: the language model is one part of the system, not the
engine. The order book, price formation, the tick loop, and the metrics are
deterministic code; most agents are cheap deterministic agents; a few Gemini
agents supply judgement. Remove every LLM call and a full simulation still runs.

## Architecture at a glance

| Layer | Tech | Deploys to |
| --- | --- | --- |
| Frontend | Next.js + shadcn/ui | Cloud Run |
| Gateway / BFF | FastAPI · WebSocket hub · A2A | Cloud Run |
| ADK agents | Google ADK · Gemini via Vertex AI | Vertex AI Agent Engine |
| Simulation engine | Deterministic, NumPy, **no LLM** | Cloud Run |
| MCP servers | Market data · news | Cloud Run |
| Grounding / memory | Vertex AI Search · Vertex AI Memory Bank | Vertex AI |
| Data | Postgres + pgvector · Redis | Cloud SQL · Memorystore |

See [`AGENTS.md`](./AGENTS.md) for the full build specification and
[`docs/contracts.md`](./docs/contracts.md) for the engine ⇄ agents boundary.

## Quickstart

```bash
make check-prereqs        # verify docker, python, gcloud
make init                 # install deps + create .env from the example
# edit .env: set GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION (see auth note)
make start                # bring up Postgres + Redis locally
make test                 # offline test suite
make demo                 # run the deterministic engine on the flagship scenario
make demo-agents          # run the full ADK orchestration in baseline mode (no LLM)
make auth-check           # confirm Gemini works via Vertex AI (needs ADC + project)
```

`make demo` runs a crowded mid-cap through a downgrade shock with no LLM and no
cloud: it prints the metrics (fill rate, slippage, stuck %, halts) and writes an
NDJSON replay under `runs/`. This is the Phase-1 backbone of the live demo.

`make demo-agents` drives the **full ADK lifecycle** — scenario setup, the
simulate `LoopAgent`, finalize, and the analyst — through the real ADK `Runner`,
with the archetype and analyst LLMs swapped for deterministic stand-ins. It runs
end to end with zero LLM calls, proving the orchestration produces a cascade,
metrics, a replay, and a plain-language narrative without touching Gemini.

The deterministic baseline (`DETERMINISTIC_BASELINE=true`) runs the whole
simulation with zero LLM calls, so development costs nothing. The **live Vertex
path is the product**; baseline is the swappable offline/test mode. Once you have
Application Default Credentials and a project set, `make auth-check` makes one
real Gemini call to confirm auth and quota; after that the agents call Gemini with
no further wiring.

## Authentication

Gemini is reached **only through Vertex AI**, using Application Default
Credentials with `GOOGLE_GENAI_USE_VERTEXAI=true` and the project and location
set in `.env`. An AI Studio `GOOGLE_API_KEY` is never used. Details in
[`.env.example`](./.env.example) and [`CLAUDE.md`](./CLAUDE.md).

## Status

Built in phases (see `AGENTS.md` §11):

- [x] **Phase 0** — Scaffold: repo structure, tooling, and the boundary contract.
- [x] **Phase 1** — Deterministic engine MVP (order book, population, metrics, NDJSON record). Run `make demo`.
- [x] **Phase 2** — ADK orchestration (scenario author, six archetype mood-setters, simulate loop, analyst) + the two MCP servers. Run `make demo-agents`.
- [ ] **Phase 3** — Frontend + FastAPI gateway with WebSocket streaming.
- [ ] **Phase 4** — Calibration critic + backtest against a real episode.
- [ ] **Phase 4A** — Memory (scenario history, then calibration memory).
- [ ] **Phase 5** — Deploy to Google Cloud (Agent Engine + Cloud Run + Terraform).
- [ ] **Phase 6** — Docs, eval, demo, and submission write-up.

## License

[Apache 2.0](./LICENSE).
