"""Out-of-sample discrimination test — the opposite of the critic.

The critic *tunes* the crowd per episode. This asks the harder, falsifying question:
with ONE fixed engine/agent configuration and NO per-episode tuning, does the
simulation separate illiquid episodes (where a crisis exit closes) from liquid ones
(where it stays open) using only each name's real instrument data?

Method:
- One fixed configuration for every episode: the flagship crowd mix, shock schedule,
  halt rule, exit speed, population size, and baseline stances are identical across
  all runs. No critic, no calibration_adjustments.
- The only thing that changes per episode is the real instrument data — reference
  price, ADV, free float — for that ticker/period (representative public values for
  the episode window; the same offline-fixture approach as the critic's episodes).
- The test position is a FIXED fraction of each name's ADV (``POSITION_FRAC``), so it
  is comparable in liquidity terms across names rather than a fixed share count.

Run it with ``python -m eval.discrimination``. It prints one table and an honest
verdict on whether the fixed model discriminates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents.orchestrator.driver import run_baseline_simulation
from engine.presets import DEFAULT_POSITION_FRAC, PRESETS
from engine.scenarios import flagship_scenario

# Fixed across every episode: the position is this fraction of the name's ADV.
POSITION_FRAC = DEFAULT_POSITION_FRAC

# A single fixed, *moderate* crisis intensity applied identically to every episode —
# the whole point of the test is no per-episode tuning. It is deliberately below the
# flagship's full-crisis 1.0: a moderate stress that a genuinely deep name absorbs
# (so the liquid group stays open) while a thin, fragile name still cannot. A severe
# crisis (intensity well above this) can close even a liquid name — see the live
# path — which is exactly why severity, not volatility, must be the gate.
MODERATE_CRISIS = 0.4


@dataclass
class Episode:
    key: str
    name: str
    group: str  # "ILLIQUID" | "LIQUID"
    reference_price: float
    adv: int  # average daily volume, shares (real, for the episode window)
    free_float: int
    volatility: float  # real daily realized volatility over the episode window
    halt_tier: int = 1


# Built from the shared curated presets (engine/presets.py) so the gateway's ticker
# picker and this harness use exactly the same real reference values.
EPISODES: list[Episode] = [
    Episode(
        key=p.symbol.lower(),
        name=f"{p.symbol} · {p.name}",
        group=p.group.upper(),
        reference_price=p.reference_price,
        adv=p.adv,
        free_float=p.free_float,
        volatility=p.volatility,
    )
    for p in PRESETS.values()
]


@dataclass
class Outcome:
    episode: Episode
    position_qty: int
    fill_rate: float
    pct_stuck: float
    halt_count: int

    @property
    def pos_pct_adv(self) -> float:
        return self.position_qty / self.episode.adv if self.episode.adv else 0.0

    @property
    def verdict(self) -> str:
        # Fixed rule from the metrics: the exit "closes" if most of the position
        # could not be sold or trading halted.
        closed = self.fill_rate < 0.5 or self.halt_count > 0 or self.pct_stuck > 0.5
        return "exit closes" if closed else "exit stays open"


def _config_for(ep: Episode):
    """The one fixed config with only this episode's instrument + position swapped in."""
    base = flagship_scenario()
    instrument = base.instrument.model_copy(
        update={
            "symbol": ep.key.upper(),
            "reference_price": ep.reference_price,
            "adv": ep.adv,
            "free_float": ep.free_float,
            "volatility": ep.volatility,
            "halt_tier": ep.halt_tier,
        }
    )
    qty = max(1, round(POSITION_FRAC * ep.adv))
    position = base.position.model_copy(
        update={"quantity": qty, "arrival_price": ep.reference_price}
    )
    return base.model_copy(
        update={
            "run_id": f"disc-{ep.key}",
            "instrument": instrument,
            "position": position,
            "crisis_intensity": MODERATE_CRISIS,
        }
    )


async def run_discrimination() -> list[Outcome]:
    outcomes: list[Outcome] = []
    for ep in EPISODES:
        cfg = _config_for(ep)
        res = await run_baseline_simulation(cfg)  # fixed config, no critic, no nudges
        m = res["run_metrics"]
        outcomes.append(
            Outcome(
                episode=ep,
                position_qty=cfg.position.quantity,
                fill_rate=m["fill_rate"],
                pct_stuck=m["pct_stuck"],
                halt_count=m["halt_count"],
            )
        )
    return outcomes


def render_table(outcomes: list[Outcome]) -> str:
    bar = "─" * 100
    lines = [
        bar,
        f"  EGRESS · out-of-sample discrimination test   (position = {POSITION_FRAC:.0%} of ADV, "
        f"crisis={MODERATE_CRISIS}, one fixed config, no tuning)",
        bar,
        f"  {'Episode':<32}{'group':<10}{'real ADV':>13}{'pos (%ADV)':>13}"
        f"{'fill':>8}{'stuck':>8}{'halts':>7}  verdict",
        bar,
    ]
    for o in outcomes:
        lines.append(
            f"  {o.episode.name:<32}{o.episode.group:<10}{o.episode.adv:>13,}"
            f"{o.pos_pct_adv:>12.0%} {o.fill_rate:>7.0%}{o.pct_stuck:>8.0%}{o.halt_count:>7}"
            f"  {o.verdict}"
        )
    lines.append(bar)
    return "\n".join(lines)


def main() -> int:
    outcomes = asyncio.run(run_discrimination())
    print(render_table(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
