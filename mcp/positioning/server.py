"""Positioning MCP server (FastMCP) - free SEC/user/curated/synthetic evidence.

Run it as a path script to avoid the repo package named ``mcp`` shadowing the
PyPI MCP SDK::

    python mcp/positioning/server.py
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

    server = FastMCP("egress-positioning")
    server.tool()(get_sec_holder_snapshot)
    server.tool()(get_public_positioning_summary)
    server.tool()(get_peer_crowding_evidence)
    server.tool()(ingest_user_holdings_csv)
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
