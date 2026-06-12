# Egress AI

Egress simulates how an investment position would behave in a crisis sell-off, before the money is committed.

> Entry to the Google for Startups AI Agents Challenge (Track 1, Build).

## Live demo

**<https://egress-frontend-978090004115.us-central1.run.app>**

Cached mode plays the real CVNA cascade instantly; the "Use real Gemini (Vertex AI)" option runs the live multi-agent pipeline (~60–90s).

## Demo video

[![Watch the Egress demo](https://img.youtube.com/vi/8xlknY_OmvI/maxresdefault.jpg)](https://youtu.be/8xlknY_OmvI)

Watch the walkthrough: <https://youtu.be/8xlknY_OmvI>

## Problem and solution

Firms routinely measure how much they could lose on a position, but not whether they could actually *sell* it in a crisis. Many firms unknowingly hold the same crowded trades, and when a shock hits and they all sell at once there are not enough buyers, the price collapses, and they cannot get out without heavy losses. There is no easy way today to test how a position behaves in that moment before committing.

Egress lets you test it. You describe a position and a stress event in plain language. The system simulates the sell-off as a market of thousands of independent trading agents that each act on their own and react to each other. Their orders meet in an order book that sets the prices, so the price moves come out of the agents' collective behaviour rather than being assumed. You see whether the position can actually be sold, how far the price moves while selling, and how much stays stuck, and you can vary how much is held and how fast it is sold to find the point where the exit closes.

## Three-tier architecture

The defining design choice is that the language model is one part of the system, not the engine. Egress runs in three tiers:

1. **Gemini archetype agents set the stances.** Six Gemini `LlmAgent`s (via Vertex AI), one per investor type, run as an ADK `ParallelAgent`. Each reads the scenario and writes only its own `*_stance` key into session state, setting the behavioural mood for its investor type. These refresh once per window, never inside the per-tick loop.
2. **A deterministic NumPy population acts on those stances.** Thousands of lightweight agents live as rows in NumPy arrays, parameterised by their type's current stance. Staggered per-agent thresholds decide who sells and when, which is what produces a cascade rather than a single synchronised dump.
3. **An order-book engine matches the orders.** A deterministic matching engine clears the buy and sell orders tick by tick. Prices, fill rate, slippage, stuck percentage, and halts all emerge from the matching, and the run is recorded to an NDJSON replay stream.

Because the mechanics are deterministic code, removing every Gemini call still produces a full simulation. That is the baseline mode the test suite and the offline demo run on. The live Vertex path is the product; baseline is the swappable offline and test mode.

### System architecture

![Egress system architecture](./docs/egress-architecture.svg)

### How a single run works

![Egress run flow](./docs/egress-run-flow.svg)

The diagrams show the full target architecture. Boxes marked *planned* (the calibration critic, cross-run memory, RAG grounding, A2A transport, and the cloud deployment) are specified but not yet built. What ships today is the deterministic engine, the ADK orchestration through the analyst, the two MCP servers, and the gateway plus frontend. The gateway currently calls the orchestrator in-process rather than over A2A.

## Running it yourself

Everything in steps 1 and 2 runs fully offline with no cloud account and no API keys. Step 3 is optional and adds real data and real Gemini.

### Prerequisites

- **Python 3.13** and **pip**
- **Node.js 18+** and **npm** (only for the frontend in step 2)
- **Docker** with `docker compose` (only if you want to cache API responses or run the local data layer)
- **gcloud** (only for the live Gemini path in step 3)

### Setup

```bash
git clone https://github.com/edgycapitalist/egress.git
cd egress
make init     # installs the package (all extras + dev) and creates .env from .env.example
make test     # offline test suite: no network, no credentials. Confirms the install works.
```

### 1. Offline simulation (no cloud, no keys)

```bash
make demo          # deterministic engine on the flagship CVNA scenario
make demo-agents   # the full ADK orchestration in baseline mode, end to end with zero LLM calls
```

`make demo` runs the crowded CVNA position through a downgrade shock and prints the metrics (fill rate, slippage, stuck percentage, halts), then writes an NDJSON replay under `runs/`. `make demo-agents` drives the whole ADK lifecycle (setup, the simulate `LoopAgent`, finalize, and the analyst) through the real ADK `Runner` with the archetype and analyst models swapped for deterministic stand-ins, so it produces a cascade, metrics, a replay, and a plain-language narrative without a single Gemini call.

### 2. The app locally (gateway + frontend)

Run the two services in separate terminals:

```bash
# terminal 1: the gateway (FastAPI + WebSocket hub) on http://127.0.0.1:8000
make gateway

# terminal 2: the frontend (Next.js) on http://localhost:3000
make web-install   # first time only
make web
```

Open <http://localhost:3000>. The frontend defaults to the gateway at `ws://127.0.0.1:8000/ws/run` (override with `NEXT_PUBLIC_GATEWAY_WS`; see [`web/.env.example`](./web/.env.example)). The UI boots in **cached** mode and replays the committed flagship cascade, so it works with no cloud and no keys. The scenario levers (position size, exit speed, crowding mix) re-run against that replay.

### 3. Real data and live Gemini (optional)

**Real market data.** Get a free Alpha Vantage key at <https://www.alphavantage.co/support/#api-key> and set `ALPHAVANTAGE_API_KEY` in `.env`. The MCP servers then pull real CVNA prices and news, caching responses in Postgres (`make start` brings up Postgres and Redis locally). Without a key, the servers use the deterministic synthetic fallback automatically.

**Live Gemini through Vertex AI.**

```bash
gcloud auth application-default login
# in .env: set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION (GOOGLE_GENAI_USE_VERTEXAI=true is the default)
make auth-check    # makes one real Gemini call to confirm auth and quota
make demo-live     # the full ADK pipeline against real Gemini via Vertex AI
```

To run the app's **live** toggle against Gemini, start the gateway with `EGRESS_LIVE_GEMINI=true make gateway`; otherwise a live run in the UI uses the deterministic stances. Gemini is reached only through Vertex AI with Application Default Credentials; an AI Studio `GOOGLE_API_KEY` is never used. The full configuration is documented in [`.env.example`](./.env.example).

## Tech stack

| Layer | Tech |
| --- | --- |
| Simulation engine | Python 3.13, NumPy, Pydantic. No LLM, no cloud. |
| Agent orchestration | Google ADK (`SequentialAgent`, `ParallelAgent`, `LoopAgent`), Gemini via Vertex AI. |
| External data | Two MCP servers (market data, news) over the Model Context Protocol. |
| Gateway / BFF | FastAPI, WebSocket streaming of tick telemetry. |
| Frontend | Next.js 15, React 19, shadcn/ui, Tailwind CSS. |
| Local data layer | Postgres and Redis via docker-compose. Postgres backs an optional cache for fetched market data; Redis is provisioned for a planned tick-state layer and is not yet used in code. |

See [`AGENTS.md`](./AGENTS.md) for the full build specification and [`docs/contracts.md`](./docs/contracts.md) for the engine and agents boundary.

## Data sources

Real market data comes from **Alpha Vantage**. The market-data MCP server pulls daily OHLCV and reference data for the flagship ticker, **CVNA (Carvana)**, when `ALPHAVANTAGE_API_KEY` is set in the environment, with built-in per-run and per-day call budgets so a run never exhausts the free tier.

When no API key is present, or when a call is rate-limited or errors, the server falls back automatically to a **deterministic synthetic feed**: a NumPy random walk seeded by the symbol, so the whole system runs offline and reproducibly with no credentials. The news MCP server works the same way, synthesising a deterministic crisis tape when no live source is configured. This is why `make test`, `make demo`, and `make demo-agents` all run with no network access.

## License

[Apache 2.0](./LICENSE).
