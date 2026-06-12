"""Egress evals — the calibration backtest against a real historical episode.

Phase 4 (AGENTS.md §11): the credibility layer. ``backtest.py`` runs the
generator-critic loop — start a crowd, have the calibration critic judge it against
the real CVNA 2022 unwind, apply its nudges, and re-run until the simulated crowd
reproduces the episode's behavioural signature or a cap is hit. Runs fully offline on
the deterministic baseline (no LLM, no cloud), so ``make eval`` is reproducible and
free.
"""
