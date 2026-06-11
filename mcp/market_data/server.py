"""Market Data MCP server (FastMCP) — the deployment surface.

Tools (AGENTS.md §6, identical signatures to ``data.py``):

* ``get_historical_window(instrument, start, end)``
* ``get_instrument_reference(instrument)``
* ``get_liquidity_profile(instrument)``

Run it::

    python mcp/market_data/server.py        # stdio transport (default)

────────────────────────────────────────────────────────────────────────────
Name-collision note. This repository's package is also named ``mcp`` (the repo
map in AGENTS.md §9), which shadows the PyPI ``mcp`` SDK. Run this file **as a
path script** (as above), not ``python -m mcp.market_data.server``: a path script
puts *this directory* on ``sys.path`` rather than the repo root, so ``import mcp``
resolves to the installed SDK and the sibling backend imports cleanly. In a
container the server code is the only ``mcp`` on the path, so the question does
not arise. The in-process ``FunctionTool`` wrappers in ``tools.py`` are the path
the agents use today and never touch the SDK, so the offline suite is unaffected.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import sys

# Make the sibling backend importable as a top-level module, so this server does
# not depend on the repo's ``mcp`` package (which would shadow the MCP SDK).
sys.path.insert(0, os.path.dirname(__file__))

from data import (  # noqa: E402  (path set above)
    get_historical_window,
    get_instrument_reference,
    get_liquidity_profile,
)


def build_server():
    """Construct the FastMCP server. Imports the SDK lazily (see module docstring)."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("egress-market-data")
    server.tool()(get_instrument_reference)
    server.tool()(get_historical_window)
    server.tool()(get_liquidity_profile)
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
