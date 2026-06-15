"""ADK ``FunctionTool`` wrappers for the Market Data MCP.

These expose the deterministic backend in ``data.py`` to the ADK agents as
in-process tools. This is the path the scenario author uses today: it needs no
running MCP server and no cloud, so the whole agent tree builds and tests offline.
The same functions are served over the wire by ``server.py`` for deployment.
"""

from __future__ import annotations

from google.adk.tools import FunctionTool

from mcp.market_data.data import (
    get_historical_window,
    get_instrument_reference,
    get_liquidity_profile,
)

instrument_reference_tool = FunctionTool(get_instrument_reference)
historical_window_tool = FunctionTool(get_historical_window)
liquidity_profile_tool = FunctionTool(get_liquidity_profile)

#: All Market Data tools, for attaching to an agent's ``tools=`` list.
MARKET_DATA_TOOLS = [
    instrument_reference_tool,
    historical_window_tool,
    liquidity_profile_tool,
]


def market_data_tools():
    """Return deployed MCP tools when configured, otherwise local FunctionTools."""
    from mcp.client import deployed_or_local_tools

    return deployed_or_local_tools(
        "MARKET_DATA_MCP_URL",
        MARKET_DATA_TOOLS,
        name="egress-market-data",
    )
