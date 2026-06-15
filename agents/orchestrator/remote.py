"""Remote orchestrator client for deployed Agent Engine mode.

Local development keeps using ``agents.orchestrator.driver`` directly. When the
gateway is configured with ``EGRESS_ORCHESTRATOR_MODE=agent_engine``, this module
routes the run request to a deployed orchestrator facade and lets the gateway fall
back deterministically if that remote path is unavailable.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any


class RemoteOrchestratorError(RuntimeError):
    """Raised when the configured remote orchestrator cannot return a run result."""


def orchestrator_mode() -> str:
    mode = os.getenv("EGRESS_ORCHESTRATOR_MODE", "in_process").strip().lower()
    return "agent_engine" if mode in {"agent_engine", "agent-engine", "remote"} else "in_process"


def remote_configured() -> bool:
    return bool(os.getenv("EGRESS_AGENT_ENGINE_URL") or os.getenv("EGRESS_AGENT_ENGINE_ID"))


async def run_remote_orchestrator(
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Call the configured remote orchestrator and return its JSON result."""
    url = os.getenv("EGRESS_AGENT_ENGINE_URL", "").strip()
    if url:
        return await _run_http_facade(url, payload, timeout_seconds=timeout_seconds)

    resource_id = os.getenv("EGRESS_AGENT_ENGINE_ID", "").strip()
    if resource_id:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_agent_engine_sdk, resource_id, payload),
            timeout=timeout_seconds,
        )
    raise RemoteOrchestratorError(
        "EGRESS_ORCHESTRATOR_MODE=agent_engine requires EGRESS_AGENT_ENGINE_URL "
        "or EGRESS_AGENT_ENGINE_ID"
    )


async def _run_http_facade(
    url: str, payload: dict[str, Any], *, timeout_seconds: float
) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - gateway extra provides this
        raise RemoteOrchestratorError("httpx is required for remote orchestrator mode") from exc

    endpoint = url.rstrip("/")
    if not endpoint.endswith("/run"):
        endpoint = f"{endpoint}/run"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(endpoint, json=payload)
    if response.status_code >= 400:
        raise RemoteOrchestratorError(
            f"remote orchestrator returned HTTP {response.status_code}: {response.text[:200]}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RemoteOrchestratorError("remote orchestrator returned non-object JSON")
    return data


def _run_agent_engine_sdk(resource_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort SDK path across Agent Engine SDK versions.

    The Vertex SDK has exposed this capability under both ``vertexai.agent_engines``
    and preview reasoning-engine surfaces. Keep the import dynamic so local tests
    do not need the cloud package, and fail with a clear message when the installed
    SDK lacks the expected API.
    """
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION")
    try:
        import vertexai

        if project or location:
            vertexai.init(project=project, location=location or "us-central1")
    except Exception as exc:
        raise RemoteOrchestratorError(f"could not initialize Vertex AI: {exc}") from exc

    module_errors: list[str] = []
    for module_name in ("vertexai.agent_engines", "vertexai.preview.reasoning_engines"):
        try:
            module = __import__(module_name, fromlist=["get"])
            remote = module.get(resource_id)
        except Exception as exc:
            module_errors.append(f"{module_name}: {exc}")
            continue
        for method_name in ("query", "run"):
            method = getattr(remote, method_name, None)
            if method is None:
                continue
            result = method(input=payload)
            if isinstance(result, dict):
                return result
            if hasattr(result, "to_dict"):
                converted = result.to_dict()
                if isinstance(converted, dict):
                    return converted
            raise RemoteOrchestratorError(
                f"Agent Engine {method_name} returned unsupported {type(result).__name__}"
            )
    raise RemoteOrchestratorError(
        "installed Vertex SDK does not expose an Agent Engine get/query surface: "
        + "; ".join(module_errors)
    )
