"""News MCP server (FastMCP) — the deployment surface.

Tools (AGENTS.md §6, identical signatures to ``data.py``):

* ``get_event_news(instrument, period)``
* ``get_sentiment(text)``

Run it::

    python mcp/news/server.py        # stdio transport (default)

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

    server = FastMCP("egress-news")
    server.tool()(get_event_news)
    server.tool()(get_sentiment)
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
