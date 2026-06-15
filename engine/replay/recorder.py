"""NDJSON record and replay (contract §3.4).

A run is written as one NDJSON stream: a ``meta`` line, one ``tick`` line per
tick, then a final ``metrics`` line. The stream is self-contained — the frontend
can replay it exactly with no live engine or LLM calls, and it is the
deterministic baseline a backtest compares against.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from engine.schema import MetaRecord, Metrics, RunConfig, TickEvent


class Recorder:
    """Streams a run to an NDJSON file, one record per line."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO | None = None

    def __enter__(self) -> Recorder:
        self._fh = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def _write(self, line: str) -> None:
        assert self._fh is not None, "Recorder must be used as a context manager"
        self._fh.write(line + "\n")

    def write_meta(self, config: RunConfig) -> None:
        self._write(MetaRecord(config=config).model_dump_json())

    def write_tick(self, event: TickEvent) -> None:
        self._write(event.model_dump_json())

    def write_metrics(self, metrics: Metrics) -> None:
        self._write(metrics.model_dump_json())


def iter_records(path: str | Path) -> Iterator[MetaRecord | TickEvent | Metrics]:
    """Yield parsed records from an NDJSON replay, in file order."""
    parsers = {"meta": MetaRecord, "tick": TickEvent, "metrics": Metrics}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            model = parsers[raw["type"]]
            yield model.model_validate(raw)


def load_replay(path: str | Path) -> tuple[MetaRecord, list[TickEvent], Metrics | None]:
    """Load a full replay into (meta, ticks, metrics)."""
    meta: MetaRecord | None = None
    ticks: list[TickEvent] = []
    metrics: Metrics | None = None
    for rec in iter_records(path):
        if isinstance(rec, MetaRecord):
            meta = rec
        elif isinstance(rec, TickEvent):
            ticks.append(rec)
        else:
            metrics = rec
    if meta is None:
        raise ValueError(f"{path} has no meta record")
    return meta, ticks, metrics


def replace_metrics(path: str | Path, metrics: Metrics) -> None:
    """Replace or append the final metrics record in an existing replay."""
    replay_path = Path(path)
    lines: list[str] = []
    replaced = False
    with replay_path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            raw = json.loads(stripped)
            if raw.get("type") == "metrics":
                lines.append(metrics.model_dump_json())
                replaced = True
            else:
                lines.append(stripped)
    if not replaced:
        lines.append(metrics.model_dump_json())
    replay_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
