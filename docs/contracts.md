# Egress boundary contract

This document defines the single boundary where the two halves of Egress meet:

- the **deterministic simulation engine** (`engine/`, no LLM), and
- the **ADK agents** (`agents/`, Gemini via Vertex AI) plus the orchestration
  that drives them.

They are built in parallel. This file is the agreement they build to: the
engine's **input** schema, its **output** schema, and the **`session.state`
keys** that the agents and the engine share. Change this file deliberately and
keep both sides in sync. It is versioned; see [Versioning](#versioning).

> Scope note: this is the data contract. It defines shapes and meanings, not
> implementation. The engine may run in-process (a Python class) or as a Cloud
> Run service; both expose the same shapes (see [Engine surface](#engine-surface)).

---

## Conventions

- **Money** is in the instrument's quote currency. **Sizes/quantities** are in
  shares. Rates and fractions are in `[0, 1]` unless a field says `_bps`
  (basis points, 1 bps = 0.01%) or `_pct` (percent as a fraction, e.g. `0.10`).
- **Time** is discrete: one `tick` is the engine's atomic step. A *window* is
  `ticks_per_window` (`k`) ticks — the cadence at which archetype stances refresh.
- **Determinism:** every random draw is seeded from `seed`. Same `RunConfig` +
  same stances ⇒ identical output, byte for byte in the NDJSON record.
- **Investor types** are a closed enum, used as keys throughout:
  `forced_seller`, `panic_seller`, `trend_follower`, `bargain_hunter`,
  `market_maker`, `holder`.
- All schemas are expressed for clarity here and are mirrored by Pydantic models
  in `agents/common/` (the shared, importable source of truth once Phase 1/2
  land). The JSON below is the normative shape.

---

## 1. Engine input — `RunConfig`

Produced by the **Scenario Author** agent (parsed from the user's plain language,
grounded on the Market Data MCP, then validated against this schema before the
run starts). Consumed by engine setup and by the archetype agents.

```jsonc
{
  "run_id": "9f1c…",              // uuid, assigned by the gateway/orchestrator
  "seed": 42,                      // int, master seed for reproducibility

  "instrument": {
    "symbol": "ACME",
    "reference_price": 100.0,      // pre-shock price, quote currency
    "tick_size": 0.01,             // minimum price increment
    "adv": 5000000,                // average daily volume, shares (from Market Data MCP)
    "free_float": 120000000,       // shares
    "halt_tier": 1                 // exchange halt tier -> drives halt_rule defaults
  },

  "position": {
    "side": "sell",                // this build exits a long; "sell" only for now
    "quantity": 250000,            // shares to exit
    "arrival_price": 100.0         // benchmark for implementation shortfall
  },

  "exit_speed": {
    "mode": "participation",       // "participation" | "twap" | "immediate"
    "participation_rate": 0.10,    // fraction of each tick's volume (mode=participation)
    "horizon_ticks": 120           // target ticks to finish (mode=twap)
  },

  "crowding_mix": {                // fractions of the population by type; MUST sum to 1.0
    "forced_seller": 0.15,
    "panic_seller": 0.20,
    "trend_follower": 0.20,
    "bargain_hunter": 0.15,
    "market_maker": 0.10,
    "holder": 0.20
  },
  "population_size": 5000,         // number of deterministic body-agents

  "shock_schedule": [              // exogenous events applied at given ticks
    { "tick": 0,  "kind": "news",  "severity": 0.8, "note": "rating downgrade" },
    { "tick": 40, "kind": "price", "severity": 0.5, "note": "gap down" }
  ],

  "halt_rule": {                   // FIXED constraint enforced by the engine, not user-tuned
    "band_pct": 0.10,              // move past +/- band within window_ticks -> halt
    "window_ticks": 5,
    "pause_ticks": 10              // ticks trading is paused once halted
  },

  "max_ticks": 600,                // hard cap on run length
  "ticks_per_window": 10,          // k: how often stances refresh
  "baseline_mode": true            // true = fixed-heuristic stances, zero LLM calls
}
```

**Validation rules (enforced before a run starts):**

- `crowding_mix` values are all ≥ 0 and sum to `1.0` (± 1e-6); keys are exactly
  the six investor types.
- `position.quantity > 0`; `population_size > 0`; `0 < ticks_per_window ≤ max_ticks`.
- `exit_speed.mode` is one of the three allowed values; the field its mode needs
  is present (`participation_rate` for `participation`, `horizon_ticks` for `twap`).
- `instrument.tick_size > 0`, `reference_price > 0`.
- Every `shock_schedule[i].tick` is in `[0, max_ticks)`; `severity` in `[0, 1]`.

---

## 2. Per-window input — archetype `Stance`

Each archetype agent (Tier A) outputs **one** stance for its whole investor type
and writes it to its own `session.state` key via `output_key` (see
[§4](#4-sessionstate-keys)). The engine reads the six stances at the **start of
each window** and parameterises that type's body-agents (Tier B) for the next
`k` ticks. The schema is shared; the engine interprets it per type (e.g. for a
`holder` low `aggressiveness` means inertia; for a `bargain_hunter` the
threshold is a *buy*-the-dip level).

```jsonc
{
  "aggressiveness": 0.7,           // [0,1] how hard this type acts on its trigger
  "sell_threshold_pct": 0.05,      // price move that arms this type's action (fraction)
  "participation": 0.6,            // [0,1] share of this type that may act this window
  "updated_at_tick": 120,          // tick at which this stance was set
  "rationale": "downgrade + falling tape"  // optional, free text for the analyst/UI
}
```

In `baseline_mode`, identical-shaped stances are produced by a fixed heuristic in
the engine instead of by Gemini, so the system runs with no LLM calls.

---

## 3. Engine output

The engine emits three shapes. All three appear in the NDJSON record (see
[§3.4](#34-ndjson-record--replay)).

### 3.1 `MarketState` (returned from each `advance`)

The current snapshot. Read by the archetype agents to set the next window's
stances, and by the analyst at the end.

```jsonc
{
  "run_id": "9f1c…",
  "tick": 120,
  "window_index": 12,
  "last_price": 88.50,
  "best_bid": 88.49,
  "best_ask": 88.52,
  "spread": 0.03,
  "depth": {                       // top-of-book ladder, a few levels each side
    "bids": [[88.49, 3200], [88.48, 4100]],
    "asks": [[88.52, 2800], [88.53, 5200]]
  },
  "cumulative_filled": 140000,     // of position.quantity sold so far
  "remaining_qty": 110000,
  "halted": false
}
```

### 3.2 `TickEvent` (one per tick, the replay stream)

```jsonc
{
  "type": "tick",
  "tick": 41,
  "last_price": 92.10,
  "best_bid": 92.05,
  "best_ask": 92.14,
  "depth_bid": 32000,              // total resting bid size (summary)
  "depth_ask": 41000,
  "fills": [ { "price": 92.08, "size": 1200, "aggressor": "sell" } ],
  "filled_this_tick": 1200,
  "cumulative_filled": 140000,
  "vwap_sold": 95.30,              // running VWAP of the exited position
  "actions_by_type": {             // how many of each type acted this tick
    "forced_seller": 320, "panic_seller": 210, "trend_follower": 180,
    "bargain_hunter": 40, "market_maker": 12, "holder": 3
  },
  "halted": false,
  "halt_started": false,           // true on the tick a halt begins
  "shock_applied": null            // or the shock_schedule entry applied this tick
}
```

### 3.3 `Metrics` (final, one per run)

The decision aid. Read by the analyst, the critic, and the orchestrator (which
writes the outcome to memory).

```jsonc
{
  "type": "metrics",
  "run_id": "9f1c…",
  "fill_rate": 0.56,                    // filled_qty / position.quantity
  "filled_qty": 140000,
  "stuck_qty": 110000,
  "pct_stuck": 0.44,
  "implementation_shortfall_bps": 850,  // vs arrival_price
  "slippage_bps": 720,
  "vwap_sold": 95.30,
  "arrival_price": 100.0,
  "final_price": 88.50,
  "max_drawdown_pct": 0.21,
  "time_to_exit_ticks": null,           // null if never fully exited
  "halt_triggered": true,
  "halt_count": 2,
  "ticks_run": 600
}
```

### 3.4 NDJSON record / replay

A run is recorded as a single NDJSON stream so the frontend can replay it exactly
with no live engine or LLM calls. Line order:

1. one `{"type": "meta", "config": <RunConfig>, "schema_version": "<v>"}`
2. one `{"type": "tick", …}` per tick (§3.2)
3. one `{"type": "metrics", …}` (§3.3) as the final line

This file is self-contained: meta + ticks + metrics fully describe the run.

---

## Engine surface

The same shapes are exposed two ways; agents/orchestration may use either.

**In-process** (`engine/`): an `Engine` object —

- `start(config: RunConfig) -> MarketState`
- `advance(stances: dict[type, Stance], ticks: int) -> tuple[MarketState, list[TickEvent]]`
- `metrics() -> Metrics`
- `replay_path -> str`

**As a service** (Cloud Run; wrapped as an ADK `AgentTool` inside the simulate
loop) —

| Method & path | Body | Returns |
| --- | --- | --- |
| `POST /runs` | `RunConfig` | `{ run_id, market_state }` |
| `POST /runs/{run_id}/advance` | `{ stances: {type: Stance}, ticks: k }` | `{ market_state, ticks: [TickEvent] }` |
| `GET /runs/{run_id}/metrics` | — | `Metrics` |
| `GET /runs/{run_id}/replay` | — | NDJSON stream (§3.4) |

The "advance k ticks" step is a **deterministic tool call**, never an LLM agent.

---

## 4. `session.state` keys

The ADK session is the short-term memory of a single run. These are the keys the
agents and the engine read and write. `output_key` columns name the literal key
an `LlmAgent` writes to. (Long-term memory across runs is separate — see
`AGENTS.md` §7A.)

| Key | Type | Written by | Read by | When |
| --- | --- | --- | --- | --- |
| `scenario_raw` | `str` | gateway (user input) | scenario_author | run start |
| `scenario_config` | `RunConfig` | scenario_author (`output_key`) | engine setup, archetypes | after parse + validate |
| `instrument_reference` | object | scenario_author (via Market Data MCP) | archetypes, engine | run start |
| `forced_seller_stance` | `Stance` | ForcedSellerMood (`output_key`) | engine advance | every `k` ticks |
| `panic_seller_stance` | `Stance` | PanicSellerMood (`output_key`) | engine advance | every `k` ticks |
| `trend_follower_stance` | `Stance` | TrendFollowerMood (`output_key`) | engine advance | every `k` ticks |
| `bargain_hunter_stance` | `Stance` | BargainHunterMood (`output_key`) | engine advance | every `k` ticks |
| `market_maker_stance` | `Stance` | MarketMakerMood (`output_key`) | engine advance | every `k` ticks |
| `holder_stance` | `Stance` | HolderMood (`output_key`) | engine advance | every `k` ticks |
| `latest_news` | object | archetypes (via News MCP) | archetypes | each window |
| `tick_window_index` | `int` | simulate loop | archetypes, engine | each window |
| `market_state` | `MarketState` | engine advance tool | archetypes (next window), analyst | each window |
| `run_metrics` | `Metrics` | engine finalize | analyst, critic, orchestrator | run end |
| `replay_ref` | `str` (path/URI) | engine | analyst, frontend | run end |
| `analysis` | object/`str` | analyst (`output_key`) | gateway/frontend, memory | after analyse |
| `calibration_report` | object | critic (`output_key`) | orchestrator, memory | after critic |
| `calibration_adjustments` | object | critic / memory | archetypes | run start (read), run end (write) |

**Rules that keep the parallel build clean:**

- The six `*_stance` keys are **distinct** — one per archetype — so the
  `ParallelAgent` fan-out never races on shared state (Google's documented
  parallel-write pattern).
- The engine **only** reads the six `*_stance` keys and `scenario_config`, and
  **only** writes `market_state`, `run_metrics`, and `replay_ref`. It never reads
  or writes agent-narrative keys. This is the firewall that keeps the engine
  LLM-free and independently testable.
- Archetype agents read `market_state` and `latest_news`; they never read each
  other's stance keys.

---

## Versioning

`schema_version` is stamped into the NDJSON `meta` line. Bump it on any
breaking change to a shape above, and note the change here. Replays carry their
version so an old recording is always interpretable.

| Version | Date | Change |
| --- | --- | --- |
| `0.1.0` | 2026-06-11 | Initial boundary: `RunConfig`, `Stance`, `MarketState`, `TickEvent`, `Metrics`, NDJSON record, and the `session.state` keys. |
