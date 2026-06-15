"""Run-state and fanout backends for deployed service wrappers.

The simulation core stays deterministic and in-process. This module is the thin
platform layer around it: local tests use memory, while deployed Cloud Run services
can require Redis for active run state, replay indexing, and tick/status fanout.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Protocol


class RunStateStore(Protocol):
    """Minimal state contract shared by engine service and gateway plumbing."""

    name: str

    def set_run_state(self, run_id: str, state: dict[str, Any]) -> None:
        """Persist the latest run state snapshot."""

    def get_run_state(self, run_id: str) -> dict[str, Any] | None:
        """Read the latest run state snapshot, if present."""

    def append_replay_record(self, run_id: str, record: dict[str, Any]) -> None:
        """Append one NDJSON-style replay record for out-of-process readers."""

    def get_replay_records(self, run_id: str) -> list[dict[str, Any]]:
        """Return replay records previously indexed for ``run_id``."""

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Fan out a tick/status payload to subscribers."""

    def health(self) -> dict[str, Any]:
        """Return a lightweight backend health payload."""


class InMemoryRunStateStore:
    """Offline/local backend. Deterministic, process-local, and dependency-free."""

    name = "memory"

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._replays: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._channels: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def set_run_state(self, run_id: str, state: dict[str, Any]) -> None:
        self._states[run_id] = dict(state)

    def get_run_state(self, run_id: str) -> dict[str, Any] | None:
        state = self._states.get(run_id)
        return dict(state) if state is not None else None

    def append_replay_record(self, run_id: str, record: dict[str, Any]) -> None:
        self._replays[run_id].append(dict(record))

    def get_replay_records(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(record) for record in self._replays.get(run_id, [])]

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self._channels[channel].append(dict(payload))

    def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True}


class RedisRunStateStore:
    """Redis-backed run state for deployed Cloud Run paths."""

    name = "redis"

    def __init__(self, url: str, *, prefix: str = "egress", ttl_seconds: int = 3600) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - exercised only in slim installs
            raise RuntimeError("redis package is required for Redis run state") from exc

        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = prefix.strip(":") or "egress"
        self._ttl = ttl_seconds

    def _key(self, *parts: str) -> str:
        return ":".join([self._prefix, *parts])

    def set_run_state(self, run_id: str, state: dict[str, Any]) -> None:
        self._client.setex(
            self._key("run", run_id, "state"),
            self._ttl,
            json.dumps(state, sort_keys=True),
        )

    def get_run_state(self, run_id: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key("run", run_id, "state"))
        return json.loads(raw) if raw else None

    def append_replay_record(self, run_id: str, record: dict[str, Any]) -> None:
        key = self._key("run", run_id, "replay")
        self._client.rpush(key, json.dumps(record, sort_keys=True))
        self._client.expire(key, self._ttl)

    def get_replay_records(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._client.lrange(self._key("run", run_id, "replay"), 0, -1)
        return [json.loads(row) for row in rows]

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self._client.publish(self._key("channel", channel), json.dumps(payload, sort_keys=True))

    def health(self) -> dict[str, Any]:
        self._client.ping()
        return {"backend": self.name, "ok": True}


def redis_required_for_deployed() -> bool:
    """True when this process has been configured as a deployed platform service."""
    return os.getenv("EGRESS_DEPLOYED_MODE", "").lower() in {"1", "true", "yes"} or os.getenv(
        "EGRESS_REQUIRE_REDIS", ""
    ).lower() in {"1", "true", "yes"}


def build_run_state_store() -> RunStateStore:
    """Build the configured run-state backend.

    Local/dev defaults to memory. Deployed mode must set ``REDIS_URL`` so the
    platform path does not silently run with per-instance state.
    """
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        ttl = int(os.getenv("EGRESS_RUN_STATE_TTL_SECONDS", "3600"))
        return RedisRunStateStore(
            redis_url,
            prefix=os.getenv("EGRESS_REDIS_PREFIX", "egress"),
            ttl_seconds=ttl,
        )
    if redis_required_for_deployed():
        raise RuntimeError("REDIS_URL is required when EGRESS_DEPLOYED_MODE is enabled")
    return InMemoryRunStateStore()
