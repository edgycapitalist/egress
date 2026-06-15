"""Offline historical episode corpus for stress-model validation.

The JSON files under ``eval/episodes`` are the committed public-case fixtures used
by Phase 6 evaluation. They are deliberately small and dependency-free: the tests,
CI, and demo can score model behavior without paid feeds, Vertex credentials, or
network access. Values are representative references for the episode windows, not
licensed market-data redistributions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

DEFAULT_POSITION_FRAC = 0.20
EPISODES_DIR = Path(__file__).resolve().parent / "episodes"

EvalSplit = Literal["calibration", "holdout"]
EvalGroup = Literal["illiquid", "liquid"]
ExpectedExit = Literal["closed", "open"]


@dataclass(frozen=True)
class InstrumentReference:
    reference_price: float
    adv: int
    free_float: int
    volatility: float
    halt_tier: int = 1


@dataclass(frozen=True)
class EvalEpisode:
    id: str
    symbol: str
    title: str
    window: str
    split: EvalSplit
    group: EvalGroup
    expected_exit: ExpectedExit
    instrument: InstrumentReference
    position_frac_adv: float
    crisis_intensity: float
    source: str
    note: str
    closes: tuple[float, ...]

    @property
    def key(self) -> str:
        return self.id

    @property
    def display_name(self) -> str:
        return f"{self.symbol} · {self.title}"


def _episode_from_dict(data: dict) -> EvalEpisode:
    instrument = data["instrument"]
    return EvalEpisode(
        id=str(data["id"]),
        symbol=str(data["symbol"]).upper(),
        title=str(data["title"]),
        window=str(data.get("window") or ""),
        split=data["split"],
        group=data["group"],
        expected_exit=data["expected_exit"],
        instrument=InstrumentReference(
            reference_price=float(instrument["reference_price"]),
            adv=int(instrument["adv"]),
            free_float=int(instrument["free_float"]),
            volatility=float(instrument["volatility"]),
            halt_tier=int(instrument.get("halt_tier", 1)),
        ),
        position_frac_adv=float(data.get("position_frac_adv", DEFAULT_POSITION_FRAC)),
        crisis_intensity=float(data.get("crisis_intensity", 0.4)),
        source=str(data.get("source") or ""),
        note=str(data.get("note") or ""),
        closes=tuple(float(x) for x in data.get("closes", [])),
    )


@lru_cache(maxsize=1)
def all_episodes() -> tuple[EvalEpisode, ...]:
    episodes: list[EvalEpisode] = []
    for path in sorted(EPISODES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        episodes.append(_episode_from_dict(data))
    return tuple(episodes)


def load_eval_episode(episode_id: str) -> EvalEpisode:
    target = episode_id.strip().lower()
    for episode in all_episodes():
        if episode.id.lower() == target:
            return episode
    raise FileNotFoundError(f"unknown eval episode: {episode_id}")


def episodes_for_split(split: str | None) -> tuple[EvalEpisode, ...]:
    """Return episodes for ``split``.

    ``quick`` preserves the original four-case smoke test, while ``all`` returns
    the whole corpus.
    """
    normalized = (split or "quick").strip().lower()
    episodes = all_episodes()
    if normalized == "all":
        return episodes
    if normalized == "quick":
        quick_ids = {"cvna_2022", "sivb_2023", "aapl_earnings_2024", "spy_2020"}
        return tuple(ep for ep in episodes if ep.id in quick_ids)
    if normalized in {"calibration", "holdout"}:
        return tuple(ep for ep in episodes if ep.split == normalized)
    raise ValueError(f"unknown episode split: {split}")

