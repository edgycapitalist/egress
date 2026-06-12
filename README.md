# Egress AI

Egress simulates how an investment position would behave in a crisis sell-off, before the money is committed.

> Entry to the Google for Startups AI Agents Challenge (Track 1, Build).

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

## Running it

Prerequisites: Docker, Python 3.13, and (for the live path only) `gcloud`.

```bash
make check-prereqs        # verify docker, python, gcloud
make init                 # install deps and create .env from the example
make test                 # offline test suite, no network, no credentials
```

### Baseline (offline, zero LLM, zero cloud)

```bash
make demo                 # deterministic engine on the flagship scenario; prints metrics, writes an NDJSON replay
make demo-agents          # the full ADK orchestration in baseline mode, end to end with no LLM calls
```

`make demo` runs a crowded mid-cap through a downgrade shock and prints fill rate, slippage, stuck percentage, and halts. `make demo-agents` drives the whole ADK lifecycle (scenario setup, the simulate `LoopAgent`, finalize, and the analyst) through the real ADK `Runner`, with the archetype and analyst models swapped for deterministic stand-ins, proving the orchestration produces a cascade, metrics, a replay, and a plain-language narrative with zero Gemini calls.

### Live (Gemini via Vertex AI)

```bash
gcloud auth application-default login
# set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION in .env (GOOGLE_GENAI_USE_VERTEXAI=true)
make auth-check           # one real Gemini call to confirm auth and quota
make demo-live            # the full ADK pipeline against real Gemini via Vertex AI
```

Gemini is reached only through Vertex AI, using Application Default Credentials. An AI Studio `GOOGLE_API_KEY` is never used. The full configuration lives in [`.env.example`](./.env.example).

### The app (gateway and frontend)

```bash
make gateway              # FastAPI WebSocket hub on :8000
make web-install          # one-time: install the frontend's npm deps
make web                  # Next.js dev server on :3000, open http://localhost:3000
```

The UI replays the flagship cascade with no live call, so the demo always runs offline. To run a fresh simulation through the ADK orchestrator against real Gemini, set `EGRESS_LIVE_GEMINI=true` for the gateway (otherwise the live toggle uses the deterministic stances).

## Tech stack

| Layer | Tech |
| --- | --- |
| Simulation engine | Python 3.13, NumPy, Pydantic. No LLM, no cloud. |
| Agent orchestration | Google ADK (`SequentialAgent`, `ParallelAgent`, `LoopAgent`), Gemini via Vertex AI. |
| External data | Two MCP servers (market data, news) over the Model Context Protocol. |
| Gateway / BFF | FastAPI, WebSocket streaming of tick telemetry. |
| Frontend | Next.js 15, React 19, shadcn/ui, Tailwind CSS. |
| Local data layer | Postgres and Redis via docker-compose; Postgres also backs an optional cache for fetched market data. |

See [`AGENTS.md`](./AGENTS.md) for the full build specification and [`docs/contracts.md`](./docs/contracts.md) for the engine and agents boundary.

## Data sources

Real market data comes from **Alpha Vantage**. The market-data MCP server pulls daily OHLCV and reference data for the flagship ticker, **CVNA (Carvana)**, when `ALPHAVANTAGE_API_KEY` is set in the environment, with built-in per-run and per-day call budgets so a run never exhausts the free tier.

When no API key is present, or when a call is rate-limited or errors, the server falls back automatically to a **deterministic synthetic feed**: a NumPy random walk seeded by the symbol, so the whole system runs offline and reproducibly with no credentials. The news MCP server works the same way, synthesising a deterministic crisis tape when no live source is configured. This is why `make test`, `make demo`, and `make demo-agents` all run with no network access.

## License

[Apache 2.0](./LICENSE).
