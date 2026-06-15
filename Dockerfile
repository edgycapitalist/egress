# Multi-stage build. Each Cloud Run service selects its target:
#   docker build --target engine          -t egress-engine .
#   docker build --target market_data_mcp -t egress-market-data-mcp .
#   docker build --target news_mcp        -t egress-news-mcp .
#   docker build --target positioning_mcp -t egress-positioning-mcp .
#   docker build --target gateway         -t egress-gateway .
#
# Service entrypoints are wired in their respective phases (engine = Phase 1,
# MCP = Phase 2, gateway = Phase 3). The stage layout exists now so the build
# topology mirrors the deployment from the start.

# ---- Base: Python + project metadata, no service code yet ----
FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN pip install --upgrade pip
# LICENSE is referenced by pyproject (license = { file = "LICENSE" }), so the
# wheel build needs it present.
COPY pyproject.toml README.md LICENSE ./

# ---- Engine service (deterministic core, no LLM) — Phase 1 ----
FROM base AS engine
COPY engine/ ./engine/
RUN pip install ".[data]"
EXPOSE 8000
CMD ["python", "-m", "engine.service"]

# ---- Market Data MCP server — Phase 2 ----
FROM base AS market_data_mcp
COPY mcp/ ./mcp/
RUN pip install ".[mcp,data]"
EXPOSE 8101
CMD ["python", "-m", "mcp.market_data.server"]

# ---- News MCP server — Phase 2 ----
FROM base AS news_mcp
COPY mcp/ ./mcp/
RUN pip install ".[mcp]"
EXPOSE 8102
CMD ["python", "-m", "mcp.news.server"]

# ---- Positioning MCP server — Phase 4 ----
FROM base AS positioning_mcp
COPY engine/ ./engine/
COPY mcp/ ./mcp/
RUN pip install ".[mcp,data]"
EXPOSE 8103
CMD ["python", "mcp/positioning/server.py"]

# ---- Gateway / BFF (FastAPI, WebSocket hub, A2A) — Phase 3 ----
# The gateway streams the cached NDJSON replay and exposes /api/instrument via the
# Market Data MCP, so it needs engine + mcp + the committed replay. The agents/ and
# memory/ trees are copied only to satisfy the wheel's package list; the live
# (Gemini) path and its heavy ADK deps are intentionally not installed here.
FROM base AS gateway
COPY agents/ ./agents/
COPY engine/ ./engine/
COPY mcp/ ./mcp/
COPY memory/ ./memory/
COPY gateway/ ./gateway/
COPY eval/ ./eval/
COPY docs/replays/ ./docs/replays/
# gateway + agents (ADK / google-genai) so the deployed service can run the live
# Gemini pipeline as well as cached replay. Gemini is reached only via Vertex AI
# using the Cloud Run service account's ADC (no API key).
RUN pip install ".[gateway,agents]"
EXPOSE 8080
# Respect Cloud Run's injected $PORT (defaults to 8080 locally).
CMD ["sh", "-c", "uvicorn gateway.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
