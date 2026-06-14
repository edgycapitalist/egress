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
- All schemas are expressed for clarity here and are mirrored by importable
  Pydantic models in [`engine/schema.py`](../engine/schema.py) — the engine owns
  them because it depends on nothing but the core deps (pydantic + numpy) and
  must stay LLM- and cloud-free. `agents/common/` re-exports them in Phase 2 so
  both halves share one definition. The JSON below is the normative shape.

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
    "halt_tier": 1,                // exchange halt tier -> drives halt_rule defaults
    "volatility": 0.03             // real daily realized vol; scales book depth + cascade
                                   //   propensity. Optional; defaults to the reference
                                   //   level (0.09) so an omitted value behaves as before.
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
  "baseline_mode": true,           // true = fixed-heuristic stances, zero LLM calls
  "peer_crowding": {               // optional; separate from behavioural crowding_mix
    "case": "base",                // "low" | "base" | "high" | "custom"
    "peer_fund_count": 12,
    "overlap_pct": 0.42,
    "avg_peer_position_pct_adv": 0.08,
    "shared_trigger_drawdown_pct": 0.12,
    "correlated_exit_probability": 0.70,
    "leverage_sensitivity": 0.50,
    "redemption_pressure": 0.40,
    "etf_flow_pressure": 0.20,
    "evidence_source": "sec_edgar",
    "confidence": "medium",
    "notes": "13F-derived overlap proxy"
  },
  "time_scale": {                  // optional; defaults preserve the current convention
    "tick_duration_seconds": 234.0,
    "session_ticks": 100,
    "exit_horizon_ticks": null,
    "exit_horizon_hours": null,
    "exit_horizon_days": null
  },
  "scenario_mode": "historical_saved",
  "evidence_summary": {
    "summary": "SEC-derived peer-crowding assumptions.",
    "items": [
      {
        "field": "peer_crowding",
        "source": "sec_edgar",
        "confidence": "medium",
        "label": "13F snapshot",
        "as_of": null,
        "notes": ""
      }
    ]
  },
  "crisis_intensity": 1.0          // crisis magnitude; 1.0 = neutral (omit = default)
}
```

**Validation rules (enforced before a run starts):**

- `crowding_mix` values are all ≥ 0 and sum to `1.0` (± 1e-6); keys are exactly
  the six investor types.
- `position.quantity > 0`; `population_size > 0`; `0 < ticks_per_window ≤ max_ticks`.
- `exit_speed.mode` is one of the three allowed values; the field its mode needs
  is present (`participation_rate` for `participation`, `horizon_ticks` for `twap`).
- `instrument.tick_size > 0`, `reference_price > 0`, `volatility > 0`.
- Every `shock_schedule[i].tick` is in `[0, max_ticks)`; `severity` in `[0, 1]`.
- `crisis_intensity ≥ 0` (optional; defaults to `1.0`, the neutral baseline).
- `peer_crowding` fields are optional as a group; when present, count fields are
  non-negative and probability/pressure/drawdown fields are fractions in `[0, 1]`.
- `time_scale` defaults to `100` ticks per ADV session and `234` seconds per tick.
  If an exit horizon is set, explicit ticks win over hours, and hours win over days.
  The derived horizon caps the run inside `max_ticks`; `max_ticks` remains a hard cap.

**Liquidity semantics (v0.2.0).** The engine consumes the real instrument data so a
run tracks the name, not just the scenario: resting book depth and per-agent order
sizes scale with `adv` (capped by `free_float`), the exit's participation works off
an `adv`-derived natural volume, and `volatility` (relative to the `0.09` reference)
scales how readily the name cascades — its stress transitions and the size of a
price-shock gap. A deep, low-vol name (e.g. SPY) absorbs a large `%ADV` exit without
halting; a thin, high-vol name (e.g. SIVB) closes. A name at the reference vol with
the flagship's ADV reproduces the pre-v0.2.0 behaviour exactly.

**Crisis intensity (v0.3.0).** `crisis_intensity` is the overall magnitude of the
described/news-driven crisis, **decoupled from trailing volatility**. Volatility is now
a fragility *amplifier* (floored, so never zero), not a gate: at a given intensity a
calm name responds less than a fragile one, but a severe enough crisis can still close
even a deep name's exit. `crisis_intensity` scales the price-shock gap and the
shock/drop-driven stress (which withdraws market-maker depth); `1.0` is the neutral
baseline and reproduces v0.2.0 exactly. The live gateway derives it deterministically
from the user's stress text and the instrument's real news sentiment
(`gateway/crisis.py`), so the description genuinely drives the outcome; the discrimination
harness pins a fixed *moderate* intensity across all names (no per-episode tuning).

**Product-accuracy fields (v0.4.0).** `peer_crowding`, `time_scale`,
`scenario_mode`, and `evidence_summary` are backward-compatible assumption-contract
fields for product-accuracy remediation. Old configs parse with `peer_crowding =
null`, a `time_scale` of `100` ticks per ADV session, `scenario_mode =
"historical_saved"`, and no evidence summary. Phase 2 wires `peer_crowding` into
deterministic peer-fund cohorts and `time_scale` into the exit horizon and
ADV-per-tick natural volume. Later phases add ensemble envelopes, SEC/user
positioning evidence, and frontend evidence labels.

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
  "peer_actions": {                // peer-fund cohort activity, zero when absent
    "triggered_funds": 0,
    "liquidating_funds": 0,
    "shares_sold": 0,
    "shares_remaining": 0
  },
  "impact_attribution": {          // bps; populated by the engine for new runs
    "exogenous_shock_bps": 0.0,
    "endogenous_trading_bps": 0.0,
    "liquidity_withdrawal_bps": 0.0
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
  "ticks_run": 600,
  "impact_attribution": {               // aggregate of tick-level attribution
    "exogenous_shock_bps": 0.0,
    "endogenous_trading_bps": 0.0,
    "liquidity_withdrawal_bps": 0.0
  },
  "ensemble_case": null,
  "ensemble_seed": null
}
```

### 3.4 NDJSON record / replay

A run is recorded as a single NDJSON stream so the frontend can replay it exactly
with no live engine or LLM calls. Line order:

1. one `{"type": "meta", "config": <RunConfig>, "schema_version": "<v>"}`
2. one `{"type": "tick", …}` per tick (§3.2)
3. one `{"type": "metrics", …}` (§3.3) as the final line

This file is self-contained: meta + ticks + metrics fully describe the run.

### 3.5 `EnsembleResult` (multi-case summary)

The ensemble envelope is not part of a single-run NDJSON stream. It is the gateway
and frontend summary shape for low/base/high peer-crowding cases across multiple
deterministic seeds. The gateway streams the selected representative replay as
normal `meta`/`ticks`/`metrics` frames, then emits one `ensemble` frame containing
this envelope before `done`.

```jsonc
{
  "type": "ensemble",
  "run_id": "ensemble-9f1c",
  "cases": [
    {
      "case": "base",
      "seeds": [42, 43, 44],
      "peer_crowding": null,
      "metrics": { "type": "metrics" },
      "representative_replay_ref": "runs/base-42.ndjson"
    }
  ],
  "bands": {
    "fill_rate": { "low": 0.45, "median": 0.56, "high": 0.64 },
    "pct_stuck": { "low": 0.36, "median": 0.44, "high": 0.55 }
  },
  "representative_case": "base",
  "representative_replay_ref": "runs/base-42.ndjson",
  "evidence_summary": null
}
```

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
| `0.2.0` | 2026-06-13 | Added `instrument.volatility` (optional, defaults to the `0.09` reference). The engine now consumes real liquidity: book depth/order sizes scale with `adv`/`free_float` and cascade propensity scales with `volatility`, so a run discriminates liquid from illiquid names without per-episode tuning. Backward-compatible — an omitted `volatility` reproduces v0.1.0 behaviour. |
| `0.3.0` | 2026-06-13 | Added `crisis_intensity` (optional, defaults to `1.0`). Crisis magnitude is now decoupled from trailing volatility: volatility is a floored fragility amplifier, not a gate, so a severe enough crisis can close even a calm, deep name while a mild one leaves it open. The live path derives the intensity from the stress text + real news sentiment. Backward-compatible — an omitted `crisis_intensity` reproduces v0.2.0 behaviour. |
| `0.4.0` | 2026-06-14 | Added backward-compatible product-accuracy contract fields: `peer_crowding`, `time_scale`, `scenario_mode`, `evidence_summary`, tick/metrics `impact_attribution`, tick `peer_actions`, and the `EnsembleResult` envelope. Phase 2 wired peer cohorts/time scale into engine behavior, and Phase 3 streams deterministic low/base/high ensemble summaries through the gateway while preserving cached replays. |
