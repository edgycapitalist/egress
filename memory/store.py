"""Long-term memory interface for scenario history and calibration adjustments.

This is intentionally separate from ADK ``session.state``. Session state is one
run's scratchpad; this store persists across runs. Local development uses JSONL.
Deployed mode can route to Vertex AI Memory Bank through a thin adapter, or to
Cloud SQL/Postgres when ``DATABASE_URL`` is configured.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ScenarioHistoryRecord(BaseModel):
    run_id: str
    user_id: str = "local"
    symbol: str
    created_at: float = Field(default_factory=time.time)
    scenario: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    analysis: str | None = None


class CalibrationMemoryRecord(BaseModel):
    run_id: str
    symbol: str
    created_at: float = Field(default_factory=time.time)
    episode_id: str | None = None
    adjustments: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)


class MemoryStore(Protocol):
    name: str

    def write_run_outcome(self, record: ScenarioHistoryRecord) -> None:
        """Persist a completed run outcome."""

    def read_recent_scenarios(
        self, *, user_id: str = "local", symbol: str | None = None, limit: int = 5
    ) -> list[ScenarioHistoryRecord]:
        """Return recent scenario outcomes for this user/symbol."""

    def write_calibration_adjustment(self, record: CalibrationMemoryRecord) -> None:
        """Persist a critic adjustment for future setup/calibration."""

    def read_calibration_adjustments(
        self, *, symbol: str | None = None, limit: int = 5
    ) -> list[CalibrationMemoryRecord]:
        """Return recent calibration memories for similar scenarios."""

    def health(self) -> dict[str, Any]:
        """Return a lightweight backend health payload."""


class JsonlMemoryStore:
    """Deterministic local fallback for tests and no-cloud development."""

    name = "jsonl"

    def __init__(self, path: str | Path = "runs/memory.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, kind: str, payload: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "payload": payload}, sort_keys=True) + "\n")

    def _read(self, kind: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                raw = json.loads(line)
                if raw.get("kind") == kind and isinstance(raw.get("payload"), dict):
                    rows.append(raw["payload"])
        return rows

    def write_run_outcome(self, record: ScenarioHistoryRecord) -> None:
        self._append("scenario", record.model_dump())

    def read_recent_scenarios(
        self, *, user_id: str = "local", symbol: str | None = None, limit: int = 5
    ) -> list[ScenarioHistoryRecord]:
        rows = [ScenarioHistoryRecord.model_validate(row) for row in self._read("scenario")]
        rows = [
            row
            for row in rows
            if row.user_id == user_id and (symbol is None or row.symbol.upper() == symbol.upper())
        ]
        return sorted(rows, key=lambda row: row.created_at, reverse=True)[:limit]

    def write_calibration_adjustment(self, record: CalibrationMemoryRecord) -> None:
        self._append("calibration", record.model_dump())

    def read_calibration_adjustments(
        self, *, symbol: str | None = None, limit: int = 5
    ) -> list[CalibrationMemoryRecord]:
        rows = [
            CalibrationMemoryRecord.model_validate(row) for row in self._read("calibration")
        ]
        rows = [
            row for row in rows if symbol is None or row.symbol.upper() == symbol.upper()
        ]
        return sorted(rows, key=lambda row: row.created_at, reverse=True)[:limit]

    def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True, "path": str(self.path)}


class PostgresMemoryStore:
    """Cloud SQL/Postgres fallback for deployed environments without Memory Bank."""

    name = "postgres"

    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - only used with data extra
            raise RuntimeError("psycopg is required for Postgres memory") from exc
        self._psycopg = psycopg
        self.database_url = database_url
        self._ensure_schema()

    def _connect(self):
        return self._psycopg.connect(self.database_url)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_memory (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calibration_memory (
                    run_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )

    def write_run_outcome(self, record: ScenarioHistoryRecord) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scenario_memory(run_id, user_id, symbol, created_at, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (
                    record.run_id,
                    record.user_id,
                    record.symbol.upper(),
                    record.created_at,
                    Jsonb(record.model_dump()),
                ),
            )

    def read_recent_scenarios(
        self, *, user_id: str = "local", symbol: str | None = None, limit: int = 5
    ) -> list[ScenarioHistoryRecord]:
        where = "user_id = %s"
        params: list[Any] = [user_id]
        if symbol is not None:
            where += " AND symbol = %s"
            params.append(symbol.upper())
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM scenario_memory
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
        return [
            ScenarioHistoryRecord.model_validate(
                json.loads(row[0]) if isinstance(row[0], str) else row[0]
            )
            for row in rows
        ]

    def write_calibration_adjustment(self, record: CalibrationMemoryRecord) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO calibration_memory(run_id, symbol, created_at, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (
                    record.run_id,
                    record.symbol.upper(),
                    record.created_at,
                    Jsonb(record.model_dump()),
                ),
            )

    def read_calibration_adjustments(
        self, *, symbol: str | None = None, limit: int = 5
    ) -> list[CalibrationMemoryRecord]:
        where = "TRUE"
        params: list[Any] = []
        if symbol is not None:
            where = "symbol = %s"
            params.append(symbol.upper())
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM calibration_memory
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
        return [
            CalibrationMemoryRecord.model_validate(
                json.loads(row[0]) if isinstance(row[0], str) else row[0]
            )
            for row in rows
        ]

    def health(self) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("SELECT 1")
        return {"backend": self.name, "ok": True}


def _run_async_blocking(coro):
    """Run an ADK async memory call from sync or async callers.

    The existing product memory facade is synchronous, but the ADK MemoryService
    implementations are async. When already inside an event loop, run the coroutine
    on a short-lived thread with its own loop instead of nesting ``asyncio.run``.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _agent_engine_memory_id(raw: str) -> str:
    value = raw.strip()
    if "/" in value:
        return value.rstrip("/").split("/")[-1]
    return value


def _record_text(kind: str, payload: dict[str, Any]) -> str:
    return json.dumps({"kind": kind, "payload": payload}, sort_keys=True)


def _entry_texts(entry: Any) -> list[str]:
    content = getattr(entry, "content", None)
    parts = getattr(content, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            texts.append(str(text))
    return texts


def _parse_memory_payload(text: str, expected_kind: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if raw.get("kind") != expected_kind or not isinstance(raw.get("payload"), dict):
        return None
    return raw["payload"]


class VertexMemoryBankStore:
    """Real ADK MemoryService backend using Vertex AI Memory Bank.

    ADK exposes Memory Bank through ``VertexAiMemoryBankService`` scoped to an
    Agent Engine resource id. Egress writes explicit scenario/calibration facts
    with structured JSON payloads, then searches the same memory service for
    comparable prior runs.
    """

    name = "vertex_ai_memory_bank"

    def __init__(
        self,
        *,
        agent_engine_id: str,
        project: str | None = None,
        location: str | None = None,
        app_name: str = "egress",
    ) -> None:
        from google.adk.memory.vertex_ai_memory_bank_service import (
            VertexAiMemoryBankService,
        )
        from google.genai import types

        self.agent_engine_id = _agent_engine_memory_id(agent_engine_id)
        self.project = project
        self.location = location
        self.app_name = app_name
        self._types = types
        self._service = VertexAiMemoryBankService(
            project=project,
            location=location,
            agent_engine_id=self.agent_engine_id,
        )

    def _memory_entry(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        record_id: str,
        symbol: str,
    ):
        from google.adk.memory.memory_entry import MemoryEntry

        created_at = float(payload.get("created_at") or time.time())
        timestamp = datetime.datetime.fromtimestamp(
            created_at, tz=datetime.UTC
        ).isoformat()
        return MemoryEntry(
            id=record_id,
            author="egress",
            timestamp=timestamp,
            content=self._types.Content(
                role="user",
                parts=[self._types.Part(text=_record_text(kind, payload))],
            ),
            custom_metadata={
                "kind": kind,
                "symbol": symbol.upper(),
                "run_id": record_id,
            },
        )

    def write_run_outcome(self, record: ScenarioHistoryRecord) -> None:
        _run_async_blocking(
            self._service.add_memory(
                app_name=self.app_name,
                user_id=record.user_id,
                memories=[
                    self._memory_entry(
                        kind="scenario",
                        payload=record.model_dump(),
                        record_id=record.run_id,
                        symbol=record.symbol,
                    )
                ],
                custom_metadata={
                    "kind": "scenario",
                    "symbol": record.symbol.upper(),
                    "run_id": record.run_id,
                },
            )
        )

    def read_recent_scenarios(
        self, *, user_id: str = "local", symbol: str | None = None, limit: int = 5
    ) -> list[ScenarioHistoryRecord]:
        query = "Egress scenario history"
        if symbol:
            query += f" for {symbol.upper()}"
        response = _run_async_blocking(
            self._service.search_memory(
                app_name=self.app_name,
                user_id=user_id,
                query=query,
            )
        )
        rows: list[ScenarioHistoryRecord] = []
        for entry in response.memories:
            for text in _entry_texts(entry):
                payload = _parse_memory_payload(text, "scenario")
                if payload is None:
                    continue
                record = ScenarioHistoryRecord.model_validate(payload)
                if symbol is None or record.symbol.upper() == symbol.upper():
                    rows.append(record)
        return sorted(rows, key=lambda row: row.created_at, reverse=True)[:limit]

    def write_calibration_adjustment(self, record: CalibrationMemoryRecord) -> None:
        _run_async_blocking(
            self._service.add_memory(
                app_name=self.app_name,
                user_id="calibration",
                memories=[
                    self._memory_entry(
                        kind="calibration",
                        payload=record.model_dump(),
                        record_id=record.run_id,
                        symbol=record.symbol,
                    )
                ],
                custom_metadata={
                    "kind": "calibration",
                    "symbol": record.symbol.upper(),
                    "run_id": record.run_id,
                },
            )
        )

    def read_calibration_adjustments(
        self, *, symbol: str | None = None, limit: int = 5
    ) -> list[CalibrationMemoryRecord]:
        query = "Egress calibration adjustments"
        if symbol:
            query += f" for {symbol.upper()}"
        response = _run_async_blocking(
            self._service.search_memory(
                app_name=self.app_name,
                user_id="calibration",
                query=query,
            )
        )
        rows: list[CalibrationMemoryRecord] = []
        for entry in response.memories:
            for text in _entry_texts(entry):
                payload = _parse_memory_payload(text, "calibration")
                if payload is None:
                    continue
                record = CalibrationMemoryRecord.model_validate(payload)
                if symbol is None or record.symbol.upper() == symbol.upper():
                    rows.append(record)
        return sorted(rows, key=lambda row: row.created_at, reverse=True)[:limit]

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "ok": True,
            "agent_engine_id": self.agent_engine_id,
            "project": self.project,
            "location": self.location,
        }


def build_memory_store() -> MemoryStore:
    memory_agent_engine_id = (
        os.getenv("EGRESS_MEMORY_AGENT_ENGINE_ID")
        or os.getenv("EGRESS_AGENT_ENGINE_ID")
        or ""
    ).strip()
    if memory_agent_engine_id:
        return VertexMemoryBankStore(
            agent_engine_id=memory_agent_engine_id,
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID") or None,
            location=(
                os.getenv("GOOGLE_CLOUD_LOCATION")
                or os.getenv("GOOGLE_CLOUD_REGION")
                or None
            ),
            app_name=os.getenv("EGRESS_MEMORY_APP_NAME", "egress"),
        )
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresMemoryStore(database_url)
    return JsonlMemoryStore(os.getenv("EGRESS_MEMORY_JSONL", "runs/memory.jsonl"))


def memory_context_for(
    scenario: dict[str, Any],
    *,
    user_id: str = "local",
    limit: int = 3,
    store: MemoryStore | None = None,
) -> dict[str, Any]:
    store = store or build_memory_store()
    symbol = str((scenario.get("instrument") or {}).get("symbol") or "").upper() or None
    return {
        "backend": store.name,
        "recent_scenarios": [
            record.model_dump()
            for record in store.read_recent_scenarios(user_id=user_id, symbol=symbol, limit=limit)
        ],
        "calibration_adjustments": [
            record.model_dump()
            for record in store.read_calibration_adjustments(symbol=symbol, limit=limit)
        ],
    }


def write_run_outcome(
    scenario: dict[str, Any],
    metrics: dict[str, Any],
    *,
    analysis: str | None = None,
    user_id: str = "local",
    store: MemoryStore | None = None,
) -> ScenarioHistoryRecord:
    store = store or build_memory_store()
    run_id = str(scenario.get("run_id") or metrics.get("run_id") or "unknown")
    symbol = str((scenario.get("instrument") or {}).get("symbol") or "UNKNOWN").upper()
    record = ScenarioHistoryRecord(
        run_id=run_id,
        user_id=user_id,
        symbol=symbol,
        scenario=scenario,
        metrics=metrics,
        analysis=analysis,
    )
    store.write_run_outcome(record)
    return record


def write_calibration_adjustment(
    scenario: dict[str, Any],
    report: dict[str, Any],
    adjustments: dict[str, Any],
    *,
    store: MemoryStore | None = None,
) -> CalibrationMemoryRecord:
    store = store or build_memory_store()
    run_id = str(scenario.get("run_id") or report.get("run_id") or "unknown")
    symbol = str((scenario.get("instrument") or {}).get("symbol") or "UNKNOWN").upper()
    record = CalibrationMemoryRecord(
        run_id=run_id,
        symbol=symbol,
        episode_id=report.get("episode_id"),
        adjustments=adjustments,
        report=report,
    )
    store.write_calibration_adjustment(record)
    return record
