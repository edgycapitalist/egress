"""Positioning MCP server (FastMCP) - free SEC/user/curated/synthetic evidence.

Run it as a path script to avoid the repo package named ``mcp`` shadowing the
PyPI MCP SDK::

    python mcp/positioning/server.py
    MCP_TRANSPORT=streamable-http PORT=8080 python mcp/positioning/server.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from data import (  # noqa: E402
    get_peer_crowding_evidence,
    get_public_positioning_summary,
    get_sec_holder_snapshot,
    ingest_user_holdings_csv,
)


def build_server():
    """Construct the FastMCP server. Imports the SDK lazily."""
    from mcp.server.fastmcp import FastMCP

    kwargs = _server_kwargs()
    try:
        server = FastMCP("egress-positioning", **kwargs)
    except TypeError:
        server = FastMCP("egress-positioning")
        _apply_settings(server, **kwargs)
    server.tool()(get_sec_holder_snapshot)
    server.tool()(get_public_positioning_summary)
    server.tool()(get_peer_crowding_evidence)
    server.tool()(ingest_user_holdings_csv)
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
