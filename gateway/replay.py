"""Read a recorded NDJSON run and turn it into a stream of WebSocket frames.

A run on disk is exactly the contract's NDJSON (``docs/contracts.md`` §3.4): one
``meta`` line, one ``tick`` line per tick, then a final ``metrics`` line. This
module reads that file and yields the frames the frontend consumes, **batching the
ticks** so a 300-tick run does not become 300 socket writes — the thundering-herd
lesson the gateway is meant to apply.

It deliberately depends on nothing but the standard library. Cached replay must run
end to end offline, with neither the engine, the agents, nor the cloud imported.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# How many ticks ride in one ``ticks`` frame. Small enough that the cascade still
# animates smoothly, large enough that even a long run is a handful of writes.
DEFAULT_BATCH = 4


def read_records(path: str | Path) -> tuple[dict, list[dict], dict | None]:
    """Parse an NDJSON replay into ``(meta, ticks, metrics)`` raw dicts.

    Pass-through JSON — no Pydantic, no engine import — so this works with only the
    standard library and never needs the simulation core installed.
    """
    meta: dict | None = None
    ticks: list[dict] = []
    metrics: dict | None = None
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            kind = rec.get("type")
            if kind == "meta":
                meta = rec
            elif kind == "tick":
                ticks.append(rec)
            elif kind == "metrics":
                metrics = rec
    if meta is None:
        raise ValueError(f"replay {path} has no meta line")
    return meta, ticks, metrics


def sidecar_analysis(path: str | Path) -> str | None:
    """Return the ``*.analysis.txt`` narrative beside a replay, if one exists.

    Cached runs ship a faithful analyst narrative as a sidecar so the demo shows the
    explanation panel without any LLM call. Live runs get their analysis from the
    orchestrator instead and do not use this.
    """
    sidecar = Path(path).with_suffix(".analysis.txt")
    if sidecar.exists():
        text = sidecar.read_text(encoding="utf-8").strip()
        return text or None
    return None


def batch(items: list[dict], size: int) -> Iterator[list[dict]]:
    for i in range(0, len(items), max(1, size)):
        yield items[i : i + size]


def frames_from_replay(
    path: str | Path,
    *,
    source: str,
    batch_size: int = DEFAULT_BATCH,
    analysis: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the ordered WebSocket frames for one recorded run.

    Frame protocol (server → client), in order:

    * ``meta``     — the run config + schema version + ``source`` label
    * ``ticks``    — a batch of TickEvents (repeated; batched per ``batch_size``)
    * ``metrics``  — the final Metrics
    * ``analysis`` — the plain-language narrative (sidecar for cached, orchestrator
      output for live)
    * ``done``     — terminal marker

    Pacing between frames is the caller's concern; this is a pure generator so it is
    trivially unit-testable offline.
    """
    meta, ticks, metrics = read_records(path)
    yield {
        "type": "meta",
        "source": source,
        "schema_version": meta.get("schema_version"),
        "config": meta.get("config"),
        "total_ticks": len(ticks),
    }
    for group in batch(ticks, batch_size):
        yield {"type": "ticks", "ticks": group}
    if metrics is not None:
        yield {"type": "metrics", "metrics": metrics}
    narrative = analysis if analysis is not None else sidecar_analysis(path)
    if narrative:
        yield {"type": "analysis", "analysis": narrative}
    yield {"type": "done"}
