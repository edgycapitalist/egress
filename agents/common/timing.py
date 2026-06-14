"""Lightweight latency instrumentation for ADK runs.

The records live in ``session.state`` so tests, the gateway, and later telemetry
export can inspect them without depending on ADK internals. The callbacks are
best-effort by design: timing must never change a run's market behavior.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agents.common.state import TIMING_PENDING, TIMING_REPORT

LOGGER = logging.getLogger("egress.timing")


def _ctx_state(ctx: Any) -> dict | None:
    state = getattr(ctx, "state", None)
    if state is not None:
        return state
    session = getattr(ctx, "session", None)
    return getattr(session, "state", None)


def _callback_context(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return (
        kwargs.get("callback_context")
        or kwargs.get("tool_context")
        or kwargs.get("ctx")
        or (args[0] if args else None)
    )


def _agent_name(ctx: Any, fallback: str = "unknown") -> str:
    agent = getattr(ctx, "agent", None)
    return str(
        getattr(ctx, "agent_name", None)
        or getattr(agent, "name", None)
        or getattr(ctx, "name", None)
        or fallback
    )


def _tool_name(tool: Any) -> str:
    return str(
        getattr(tool, "name", None)
        or getattr(getattr(tool, "func", None), "__name__", None)
        or getattr(tool, "__name__", None)
        or tool.__class__.__name__
    )


def _report(state: dict) -> dict[str, Any]:
    report = state.get(TIMING_REPORT)
    if not isinstance(report, dict):
        report = {
            "version": 1,
            "events": [],
            "summary": {
                "agent_calls": 0,
                "gemini_calls": 0,
                "tool_calls": 0,
                "tool_cache_hits": 0,
                "engine_windows": 0,
                "total_duration_ms": 0.0,
            },
        }
        state[TIMING_REPORT] = report
    report.setdefault("events", [])
    report.setdefault("summary", {})
    return report


def _pending(state: dict) -> dict[str, float]:
    pending = state.get(TIMING_PENDING)
    if not isinstance(pending, dict):
        pending = {}
        state[TIMING_PENDING] = pending
    return pending


def _increment(summary: dict[str, Any], key: str, amount: float = 1.0) -> None:
    summary[key] = round(float(summary.get(key, 0.0)) + amount, 6)


def _extract_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump(exclude_none=True)
    elif isinstance(usage, dict):
        raw = dict(usage)
    else:
        raw = {
            key: getattr(usage, key)
            for key in (
                "prompt_token_count",
                "candidates_token_count",
                "thoughts_token_count",
                "total_token_count",
            )
            if getattr(usage, key, None) is not None
        }
    return raw or None


def _cache_hit(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    for key in ("cache_hit", "from_cache", "cached"):
        if isinstance(result.get(key), bool):
            return bool(result[key])
    source = str(result.get("source") or "").lower()
    return "cache" in source or "cached" in source


def _source(result: Any) -> str | None:
    if isinstance(result, dict):
        source = result.get("source") or result.get("selected_source")
        if source:
            return str(source)
    return None


def _record(state: dict, event: dict[str, Any]) -> None:
    report = _report(state)
    event = {k: v for k, v in event.items() if v is not None}
    events = report["events"]
    events.append(event)
    summary = report["summary"]
    duration = float(event.get("duration_ms", 0.0) or 0.0)
    _increment(summary, "total_duration_ms", duration)

    kind = event.get("kind")
    if kind == "agent":
        _increment(summary, "agent_calls")
        _increment(summary, "agent_duration_ms", duration)
    elif kind == "gemini_call":
        _increment(summary, "gemini_calls")
        _increment(summary, "gemini_duration_ms", duration)
        usage = event.get("usage")
        if isinstance(usage, dict):
            for key, value in usage.items():
                if isinstance(value, int | float):
                    _increment(summary, key, float(value))
    elif kind == "tool_call":
        _increment(summary, "tool_calls")
        _increment(summary, "tool_duration_ms", duration)
        if event.get("cache_hit"):
            _increment(summary, "tool_cache_hits")
    elif kind == "engine_window":
        _increment(summary, "engine_windows")
        _increment(summary, "engine_window_duration_ms", duration)
    elif kind in {"engine_setup", "engine_finalize", "ensemble"}:
        _increment(summary, f"{kind}_duration_ms", duration)
    elif kind == "fallback":
        _increment(summary, "fallback_count")

    state[TIMING_REPORT] = report
    LOGGER.info("egress_timing %s", event)


def record_timing(
    state: dict | None,
    *,
    kind: str,
    name: str,
    duration_ms: float,
    ok: bool = True,
    **fields: Any,
) -> None:
    """Append one timing event to ``state``.

    ``state`` may be ``None`` to keep callers simple in tests and error paths.
    """
    if state is None:
        return
    event = {
        "kind": kind,
        "name": name,
        "duration_ms": round(max(0.0, float(duration_ms)), 3),
        "ok": ok,
        **fields,
    }
    _record(state, event)


def _start(state: dict | None, key: str) -> None:
    if state is None:
        return
    _pending(state)[key] = time.perf_counter()


def _finish(state: dict | None, key: str) -> float | None:
    if state is None:
        return None
    pending = _pending(state)
    started = pending.pop(key, None)
    state[TIMING_PENDING] = pending
    if started is None:
        return None
    return (time.perf_counter() - float(started)) * 1000.0


def before_agent(name: str | None = None):
    def callback(*args: Any, **kwargs: Any):
        ctx = _callback_context(args, kwargs)
        agent = name or _agent_name(ctx)
        _start(_ctx_state(ctx), f"agent:{agent}")
        return None

    return callback


def after_agent(name: str | None = None):
    def callback(*args: Any, **kwargs: Any):
        ctx = _callback_context(args, kwargs)
        agent = name or _agent_name(ctx)
        state = _ctx_state(ctx)
        elapsed = _finish(state, f"agent:{agent}")
        if elapsed is not None:
            record_timing(state, kind="agent", name=agent, duration_ms=elapsed)
        return None

    return callback


def before_model(*args: Any, **kwargs: Any):
    ctx = _callback_context(args, kwargs)
    agent = _agent_name(ctx)
    _start(_ctx_state(ctx), f"model:{agent}")
    return None


def after_model(*args: Any, **kwargs: Any):
    ctx = _callback_context(args, kwargs)
    response = (
        kwargs.get("llm_response")
        or kwargs.get("response")
        or (args[1] if len(args) > 1 else None)
    )
    agent = _agent_name(ctx)
    state = _ctx_state(ctx)
    elapsed = _finish(state, f"model:{agent}")
    if elapsed is not None:
        model = getattr(response, "model_version", None) or getattr(response, "model", None)
        record_timing(
            state,
            kind="gemini_call",
            name=agent,
            duration_ms=elapsed,
            model=str(model) if model else None,
            usage=_extract_usage(response),
        )
    return None


def on_model_error(*args: Any, **kwargs: Any):
    ctx = _callback_context(args, kwargs)
    error = kwargs.get("error") or (args[2] if len(args) > 2 else Exception("model_error"))
    agent = _agent_name(ctx)
    state = _ctx_state(ctx)
    elapsed = _finish(state, f"model:{agent}") or 0.0
    record_timing(
        state,
        kind="gemini_call",
        name=agent,
        duration_ms=elapsed,
        ok=False,
        error=error.__class__.__name__,
    )
    return None


def before_tool(*args: Any, **kwargs: Any):
    tool = kwargs.get("tool") or (args[0] if args else None)
    ctx = _callback_context(args[2:] if len(args) > 2 else (), kwargs)
    agent = _agent_name(ctx)
    name = _tool_name(tool)
    _start(_ctx_state(ctx), f"tool:{agent}:{name}")
    return None


def after_tool(*args: Any, **kwargs: Any):
    tool = kwargs.get("tool") or (args[0] if args else None)
    ctx = _callback_context(args[2:] if len(args) > 2 else (), kwargs)
    result = kwargs.get("tool_response") or kwargs.get("result") or (
        args[3] if len(args) > 3 else {}
    )
    agent = _agent_name(ctx)
    name = _tool_name(tool)
    state = _ctx_state(ctx)
    elapsed = _finish(state, f"tool:{agent}:{name}")
    if elapsed is not None:
        record_timing(
            state,
            kind="tool_call",
            name=name,
            duration_ms=elapsed,
            agent=agent,
            cache_hit=_cache_hit(result),
            source=_source(result),
        )
    return None


def on_tool_error(*args: Any, **kwargs: Any):
    tool = kwargs.get("tool") or (args[0] if args else None)
    ctx = _callback_context(args[2:] if len(args) > 2 else (), kwargs)
    error = kwargs.get("error") or (args[3] if len(args) > 3 else Exception("tool_error"))
    agent = _agent_name(ctx)
    name = _tool_name(tool)
    state = _ctx_state(ctx)
    elapsed = _finish(state, f"tool:{agent}:{name}") or 0.0
    record_timing(
        state,
        kind="tool_call",
        name=name,
        duration_ms=elapsed,
        ok=False,
        agent=agent,
        error=error.__class__.__name__,
    )
    return None


@contextmanager
def timing_block(
    state: dict | None,
    *,
    kind: str,
    name: str,
    **fields: Any,
) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        record_timing(
            state,
            kind=kind,
            name=name,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            ok=False,
            error=exc.__class__.__name__,
            **fields,
        )
        raise
    else:
        record_timing(
            state,
            kind=kind,
            name=name,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            **fields,
        )
