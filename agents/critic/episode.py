"""Historical-episode loader and signature computation (the backtest anchor).

A curated episode is a short real price path (daily closes) for a crisis the system
is calibrated against. From that path we derive a *behavioural signature* — how deep
the decline went and how disorderly it was — which the calibration critic checks a
simulated unwind against (AGENTS.md §4, §7, §11). The data ships with the agents
package so the critic works in any deployment; the live path can refresh the exact
closes from the Market Data MCP, but this offline reference is the source of truth
for ``make eval`` and the test suite.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from agents.critic.schema import Episode, EpisodeSignature

EPISODES_DIR = Path(__file__).resolve().parent / "episodes"

# The flagship episode the CVNA scenario is calibrated against.
FLAGSHIP_EPISODE_ID = "cvna_2022"


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@lru_cache(maxsize=8)
def load_episode(episode_id: str) -> Episode:
    """Load a curated episode by id from the packaged corpus."""
    path = EPISODES_DIR / f"{episode_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return Episode.model_validate(data)


def episode_for_symbol(symbol: str | None) -> Episode | None:
    """Return the curated episode whose symbol matches, or ``None`` if we have none.

    Keeps the critic honest for off-flagship scenarios: with no reference episode it
    reports ``no_reference`` rather than inventing a comparison.
    """
    if not symbol:
        return None
    target = symbol.strip().upper()
    for path in sorted(EPISODES_DIR.glob("*.json")):
        try:
            ep = Episode.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if ep.symbol.strip().upper() == target:
            return ep
    return None


def signature(closes: list[float]) -> EpisodeSignature:
    """Derive the behavioural signature from a daily-close path.

    - ``max_drawdown`` is the peak-to-trough decline (the trough taken *after* the
      peak, so a late recovery does not mask how far it fell).
    - ``disorderliness`` is the share of the total log-decline that happened on the
      single worst day: ~1/N for a smooth glide, large for a violent cliff. It is the
      timescale-fair way to say "this fell in a disorderly cascade, not an orderly
      slide", which is the behaviour the critic checks the crowd reproduced.
    """
    if len(closes) < 2:
        raise ValueError("an episode needs at least two closes")

    peak_idx = max(range(len(closes)), key=lambda i: closes[i])
    peak = closes[peak_idx]
    tail = closes[peak_idx:]
    trough = min(tail)
    max_dd = (peak - trough) / peak if peak > 0 else 0.0

    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]
    worst_day = min(returns) if returns else 0.0

    if peak > 0 and trough > 0 and trough < peak:
        total_log = abs(math.log(trough / peak))
        worst_log = abs(math.log(1.0 + worst_day)) if worst_day > -1.0 else total_log
        disorder = _clip01(worst_log / total_log) if total_log > 0 else 0.0
    else:
        disorder = 0.0

    return EpisodeSignature(
        peak=round(peak, 4),
        trough=round(trough, 4),
        max_drawdown=round(max_dd, 4),
        worst_day_return=round(worst_day, 4),
        disorderliness=round(disorder, 4),
        n_days=len(closes),
    )
