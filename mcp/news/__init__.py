"""News MCP — event news and sentiment.

The deterministic backend (``data.py``) is dependency-free and unit-tested. It is
exposed as ADK ``FunctionTool``s for in-process agent use (``tools.py``) and as a
FastMCP server for deployment (``server.py``). The FastMCP server is not imported
here so importing this package stays light and offline.
"""

from mcp.news.data import get_event_news, get_sentiment

__all__ = ["get_event_news", "get_sentiment"]
