# The gateway and frontend (Phase 3)

This document describes the `gateway/` and `web/` layers built in Phase 3 — the
parts a judge actually sees — and how they meet the rest of the system at the
boundary in [`contracts.md`](./contracts.md). For the full specification see
[`AGENTS.md`](../AGENTS.md) §8.

## The shape of a run, end to end

```
web/ (Next.js)  ──WebSocket──▶  gateway/ (FastAPI)  ──▶  source of frames
  scenario builder                 /ws/run                ├─ cached: docs/replays/flagship-42.ndjson
  price + cascade viz              batches ticks          └─ live:   agents/orchestrator driver
  metrics + analyst                                                  (deterministic, or Gemini)
```

Both the cached and the live path end the same way: a recorded **NDJSON** run
(the contract's §3.4 stream) is read back and pushed to the browser as an ordered
sequence of frames. The frontend never computes market dynamics — it renders what
the engine produced. This is the same replay guarantee the whole project is built
on, surfaced to the screen.

## The gateway

`gateway/app.py` is a FastAPI app with one WebSocket endpoint, `/ws/run`, and two
small REST helpers (`/api/health`, `/api/scenario/defaults`). A client opens the
socket and sends one request frame:

```jsonc
{ "mode": "cached" | "live", "gemini": false, "scenario": { …levers… }, "pace_ms": 110 }
```

The server replies with an ordered stream of frames:

| Frame | Payload | Notes |
| --- | --- | --- |
| `meta` | `source`, `schema_version`, `config`, `total_ticks` | first |
| `ticks` | `ticks: TickEvent[]` | **batched** (default 4/frame), repeated |
| `metrics` | `metrics: Metrics` | once |
| `analysis` | `analysis: str` | the plain-language narrative |
| `done` | — | terminal |
| `status` / `error` | `message` | progress / clean failure |

**Batching is the gateway's job** (AGENTS.md §3): a 300-tick run becomes a handful
of socket writes, not 300 — the thundering-herd lesson. A small `pace_ms` dwell
between tick batches animates the cascade at a watchable speed.

### Cached vs. live

* **Cached** streams `docs/replays/flagship-42.ndjson` and its committed
  `*.analysis.txt` sidecar. It reads only the standard library — no engine, no
  agents, no cloud — so the demo runs end to end **offline** and identically every
  time. This is the reliability mechanism for judging.
* **Live** drives the ADK orchestrator now via `agents/orchestrator/driver.py`,
  records a fresh NDJSON, then streams it. By default it runs the **deterministic
  baseline** lifecycle (a real ADK `SequentialAgent` run, zero LLM cost,
  offline-safe), so the scenario builder's levers drive a genuinely new
  simulation. With `gemini: true` and Vertex configured (`EGRESS_LIVE_GEMINI=true`
  + ADC), it runs the **real Gemini** pipeline. The `source` field on the `meta`
  frame (`cached` / `live-baseline` / `live-gemini`) tells the UI which ran, so the
  product is always honest about whether a model was in the loop.

`gateway/run_config.py` folds the flat UI levers (position size, exit speed,
crowding mix) onto the flagship scenario and validates the result against
`engine/schema.py` before the run starts — the same validation the contract
requires of the Scenario Author. A malformed mix is renormalised, not rejected.

The offline test suite (`tests/test_gateway_replay.py`) exercises the cached path
end to end through FastAPI's `TestClient` WebSocket — no network, no credentials.

## The frontend

`web/` is a Next.js (App Router) + Tailwind v4 app. The shadcn-style primitives
(`components/ui/`) are hand-authored to keep the look restrained and intentional
rather than a default template: a near-black, faintly cool base, one neutral
scale, and colour reserved for data semantics — warm for selling and stress, cool
for buying and fills, amber for a volatility halt. Every number is tabular mono.

Panels:

* **Scenario builder** (`scenario-builder.tsx`) — the plain-language position and
  stress event, plus the levers: position size, exit speed, and a per-type
  crowding mix. The cached/live toggle lives here.
* **Price path** (`price-chart.tsx`) — the cascade as an SVG line over the run,
  with the arrival-price reference, shaded **halt bands**, and shock markers. The
  demo centrepiece.
* **Who is selling** (`cascade-flow.tsx`) — `actions_by_type` as a stacked area on
  the *same tick axis* as the price path, so the seller surge that drives each
  price break is legible at a glance.
* **Order book** (`order-book.tsx`) — bid/ask depth draining, the spread, and a
  sparkline of buy-side support collapsing.
* **Fill progress**, **Outcome metrics** (fill rate, slippage, drawdown, % stuck,
  time to exit, halts), and the **analyst explanation**.

`lib/useRun.ts` owns the WebSocket lifecycle and reduces the streamed frames into
render-ready state; ticks are appended as batches arrive so the visualisation
animates live. The gateway URL is configurable via `NEXT_PUBLIC_GATEWAY_WS` /
`NEXT_PUBLIC_GATEWAY_HTTP` (see `web/.env.example`).

## Running it

```bash
make gateway       # :8000 — cached replay needs no cloud
make web-install   # once
make web           # :3000
```

Open <http://localhost:3000>, leave the toggle on **Cached**, and press
*Replay cascade* — the flagship unwind animates with no live call. Switch to
**Live** to run a fresh simulation against your own levers.
