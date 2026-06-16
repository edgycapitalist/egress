"""News MCP server (FastMCP) — the deployment surface.

Tools (AGENTS.md §6, identical signatures to ``data.py``):

* ``get_event_news(instrument, period)``
* ``get_sentiment(text)``

Run it::

    python mcp/news/server.py        # stdio transport (default)
    MCP_TRANSPORT=streamable-http PORT=8080 python mcp/news/server.py

See the name-collision note in ``mcp/market_data/server.py``: run this file as a
path script so ``import mcp`` resolves to the PyPI SDK rather than the repo's
``mcp`` package. The in-process ``FunctionTool`` wrappers in ``tools.py`` are the
path the archetype agents use today and never touch the SDK.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from data import get_event_news, get_sentiment  # noqa: E402  (path set above)


def build_server():
    """Construct the FastMCP server. Imports the SDK lazily (see module docstring)."""
    from mcp.server.fastmcp import FastMCP

    kwargs = _server_kwargs()
    try:
        server = FastMCP("egress-news", **kwargs)
    except TypeError:
        server = FastMCP("egress-news")
        _apply_settings(server, **kwargs)
    server.tool()(get_event_news)
    server.tool()(get_sentiment)
    return server


def _server_kwargs() -> dict[str, int | str]:
    return {
        "host": os.getenv("MCP_HOST", "0.0.0.0"),
        "port": int(os.getenv("PORT") or os.getenv("MCP_PORT") or "8080"),
    }


def _apply_settings(server, *, host: str, port: int) -> None:
    settings = getattr(server, "settings", None)
    if settings is None:
        return
    for key, value in {"host": host, "port": port}.items():
        if hasattr(settings, key):
            setattr(settings, key, value)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    build_server().run(transport=transport)


if __name__ == "__main__":
    main()
