"""ADK ``FunctionTool`` wrappers for the Positioning MCP."""

from __future__ import annotations

from google.adk.tools import FunctionTool

from mcp.positioning.data import (
    get_peer_crowding_evidence,
    get_public_positioning_summary,
    get_sec_holder_snapshot,
    ingest_user_holdings_csv,
)

sec_holder_snapshot_tool = FunctionTool(get_sec_holder_snapshot)
public_positioning_summary_tool = FunctionTool(get_public_positioning_summary)
peer_crowding_evidence_tool = FunctionTool(get_peer_crowding_evidence)
user_holdings_csv_tool = FunctionTool(ingest_user_holdings_csv)

POSITIONING_TOOLS = [
    sec_holder_snapshot_tool,
    public_positioning_summary_tool,
    peer_crowding_evidence_tool,
    user_holdings_csv_tool,
]


def positioning_tools():
    """Return deployed MCP tools when configured, otherwise local FunctionTools."""
    from mcp.client import deployed_or_local_tools

    return deployed_or_local_tools(
        "POSITIONING_MCP_URL",
        POSITIONING_TOOLS,
        name="egress-positioning",
    )
