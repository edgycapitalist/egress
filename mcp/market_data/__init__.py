"""Market Data MCP — historical windows, instrument reference, liquidity profile.

The deterministic backend (``data.py``) is dependency-free and unit-tested. It is
exposed two ways: as ADK ``FunctionTool``s for in-process agent use (``tools.py``)
and as a FastMCP server for deployment (``server.py``). Importing this package
must stay light, so the FastMCP server is *not* imported here.
"""

from mcp.market_data.data import (
    get_historical_window,
    get_instrument_reference,
    get_liquidity_profile,
)

__all__ = [
    "get_historical_window",
    "get_instrument_reference",
    "get_liquidity_profile",
]
