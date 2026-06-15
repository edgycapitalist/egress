"""ADK ``FunctionTool`` wrappers for the News MCP.

Expose the deterministic news backend in ``data.py`` to the ADK archetype agents
as in-process tools — no running MCP server, no cloud, so the agent tree builds
and tests offline. The same functions are served over the wire by ``server.py``.
"""

from __future__ import annotations

from google.adk.tools import FunctionTool

from mcp.news.data import get_event_news, get_sentiment

event_news_tool = FunctionTool(get_event_news)
sentiment_tool = FunctionTool(get_sentiment)

#: All News tools, for attaching to an agent's ``tools=`` list.
NEWS_TOOLS = [event_news_tool, sentiment_tool]


def news_tools():
    """Return deployed MCP tools when configured, otherwise local FunctionTools."""
    from mcp.client import deployed_or_local_tools

    return deployed_or_local_tools("NEWS_MCP_URL", NEWS_TOOLS, name="egress-news")
