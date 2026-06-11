# Working notes for coding agents

Read [`AGENTS.md`](./AGENTS.md) in full first — it is the build specification.
This file is the short operational companion: the rules that are easy to get
wrong, the repo map, and the commands. Where this file and `AGENTS.md` disagree,
`AGENTS.md` wins.

## The one principle that must not be broken

The language model is **one part** of the system, not the engine. The market
mechanics — the order book, price formation, the tick loop, the metrics — are
deterministic code. Most of the thousands of agents are cheap deterministic
agents. A few Gemini agents set the behavioural mood per investor type and
explain the run.

- **Never put a Gemini call in the inner per-agent or per-tick loop.** Archetype
  stances refresh every *k* ticks, not every tick.
- If every LLM call were removed, the deterministic engine must still run a full
  simulation (this is the `DETERMINISTIC_BASELINE` mode).

## Authentication rule (applies to all code, every phase)

Gemini is called **only through Vertex AI**, never through Google AI Studio.

- Use **Application Default Credentials**: `gcloud auth application-default login`.
- Set `GOOGLE_GENAI_USE_VERTEXAI=true`, plus `GOOGLE_CLOUD_PROJECT` and
  `GOOGLE_CLOUD_LOCATION`, in `.env`. The agents read the project from `.env`,
  not from `gcloud config`.
- **Never use a `GOOGLE_API_KEY`** from AI Studio. Do not add one to `.env`, to
  code, or to any deployment. The full configuration lives in `.env.example`.

## The boundary contract

The deterministic engine and the ADK agents are built in parallel against one
shared boundary, defined in [`docs/contracts.md`](./docs/contracts.md): the
engine's input/output schema and the `session.state` keys. Change that file
deliberately and keep both sides in sync with it.

## Repo map

| Path | What lives here |
| --- | --- |
| `agents/` | ADK agents (Gemini via Vertex AI): scenario author, archetypes, analyst, critic, shared helpers in `common/`. |
| `engine/` | Deterministic simulation core — **no LLM**: order book, population, stats, metrics, replay. |
| `mcp/` | MCP servers: `market_data/`, `news/`. |
| `memory/` | ADK MemoryService wiring: calibration memory + scenario history. |
| `gateway/` | FastAPI gateway / BFF: WebSocket hub + A2A routing. |
| `web/` | Next.js + shadcn frontend. |
| `infra/` | Terraform (Agent Engine, Cloud Run, Cloud SQL, Redis). |
| `eval/` | Agent evals and the backtest against a real episode. |
| `scripts/` | `deploy.sh`, `seed_data.py`, DB init. |
| `tests/` | Offline-runnable suite (`conftest.py` mocks `google.auth`). |
| `docs/` | Architecture diagrams, the boundary contract, design notes. |

## Commands

```
make check-prereqs   # verify docker, python, gcloud
make init            # install (all extras + dev), create .env from the example
make start / stop    # local data layer (Postgres + Redis) via docker-compose
make test            # offline test suite — no network, no credentials
make lint / fmt      # ruff
make build           # build all service container images
make eval            # Phase 4
make deploy          # Phase 5
```

## Build phases

Follow the order in `AGENTS.md` §11 strictly. **Phase 0 (this scaffold) is
done.** Phase 1 is the deterministic engine; do not start agent or cloud work
until a cascade runs end to end with a recorded NDJSON stream and printed
metrics.

## Conventions

- Python 3.13 for engine, agents, gateway, MCP. TypeScript for `web/`.
- Keep the engine free of any cloud or LLM dependency — it installs from the
  core deps alone.
- Develop against the deterministic baseline and cached replay so most work
  costs nothing against the credit.
- Commit in small, logical steps with plain messages.
