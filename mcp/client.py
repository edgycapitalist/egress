"""Helpers for switching ADK tools between local FunctionTools and deployed MCP URLs."""

from __future__ import annotations

import os
from typing import Any


def configured_url(env_name: str) -> str | None:
    value = os.getenv(env_name, "").strip()
    return value or None


def mcp_toolset_from_url(url: str, *, name: str) -> list[Any]:
    """Build an ADK MCP toolset for a deployed MCP service URL.

    ADK's MCP helper module has moved between versions, so this dynamic adapter
    tries the known surfaces and raises a clear error if the installed ADK does
    not provide one. Local tests never call this without URL env vars.
    """
    import_errors: list[str] = []
    candidates = (
        (
            "google.adk.tools.mcp_tool.mcp_toolset",
            "MCPToolset",
            "StreamableHTTPServerParams",
        ),
        (
            "google.adk.tools.mcp_tool.mcp_toolset",
            "MCPToolset",
            "SseServerParams",
        ),
    )
    for module_name, toolset_name, params_name in candidates:
        try:
            module = __import__(module_name, fromlist=[toolset_name, params_name])
            toolset = getattr(module, toolset_name)
            params = getattr(module, params_name)
            try:
                return [toolset(connection_params=params(url=url))]
            except TypeError:
                return [toolset(connection_params=params(url=url, name=name))]
        except Exception as exc:
            import_errors.append(f"{params_name}: {exc}")
    raise RuntimeError(f"ADK MCP URL toolset unavailable for {name}: {'; '.join(import_errors)}")


def deployed_or_local_tools(env_name: str, local_tools: list[Any], *, name: str) -> list[Any]:
    url = configured_url(env_name)
    if not url:
        return local_tools
    return mcp_toolset_from_url(url, name=name)


def mcp_urls_status() -> dict[str, str | None]:
    return {
        "market_data": configured_url("MARKET_DATA_MCP_URL"),
        "news": configured_url("NEWS_MCP_URL"),
        "positioning": configured_url("POSITIONING_MCP_URL"),
    }
