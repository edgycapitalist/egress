"""Egress evals for calibration, holdout discrimination, and latency.

Phase 4 (AGENTS.md §11): ``backtest.py`` runs the
generator-critic loop — start a crowd, have the calibration critic judge it against
the real CVNA 2022 unwind, apply its nudges, and re-run until the simulated crowd
reproduces the episode's behavioural signature or a cap is hit. Runs fully offline on
the deterministic baseline (no LLM, no cloud), so ``make eval`` is reproducible and
free.

Phase 6 adds ``eval/episodes/*.json`` as a calibration/holdout corpus,
``discrimination.py`` as the full stress-model discrimination harness, recorded
Gemini assumption fixtures for offline model-vs-baseline comparison, and
``latency.py`` for deterministic runtime checks.
"""
