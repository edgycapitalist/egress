# Evaluation

Egress keeps validation offline by default. The eval suite uses committed public-case
fixtures under [`eval/episodes`](../eval/episodes), deterministic Alpha Vantage/SEC
fallbacks, and recorded Gemini assumption fixtures under
[`eval/fixtures`](../eval/fixtures). No target below calls Vertex, Alpha Vantage, or
SEC.

## Episode Corpus

The evaluation corpus contains 12 representative historical stress cases:

- `calibration`: cases used to check whether the model reproduces known behavioural
  signatures and known liquid/open controls.
- `holdout`: cases not used by the calibration backtest, used to falsify whether the
  fixed model discriminates closed exits from open exits.

Each `eval/episodes/*.json` file records:

- the ticker, title, window, split, and expected exit outcome;
- representative public reference data: price, ADV, free float proxy, volatility, and
  halt tier;
- a position size as a fixed fraction of ADV;
- a short daily-close path for closed-case signature scoring.

The values are offline reference fixtures, not a licensed historical-data feed. They
exist to make the model testable without paid data.

## Targets

```bash
make eval-discrimination-full
make eval-holdout
make eval-latency
```

`eval-discrimination-full` runs the whole corpus and prints calibration and holdout
sections separately. The baseline mode uses one fixed deterministic configuration
per episode. The recorded-Gemini mode replays committed Gemini scenario-author
assumptions for the same evidence, then runs the same deterministic engine.

`eval-holdout` runs only the holdout split. This is the main falsification check: a
credible model should not only pass the flagship case.

`eval-latency` measures p50, p95, and max deterministic engine runtime across the
episode corpus. It fails if p95 exceeds the configured threshold.

The quick smoke remains:

```bash
python3 -m eval.discrimination
```

That command runs the original four-case CVNA/SIVB/AAPL/SPY discrimination set.

## Reading The Gemini Delta

The Gemini comparison is intentionally fixture-based in CI. It answers a narrow
offline question: if the live Scenario Author's recorded assumptions are replayed
against the same episode evidence, do they improve or worsen discrimination and
closed-case signature fit versus the deterministic baseline?

Positive deltas mean the recorded Gemini assumptions helped. Zero or negative deltas
mean no measurable lift. The report is allowed to say Gemini adds no value; that is
more useful than assuming it does.
