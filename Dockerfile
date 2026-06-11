# Multi-stage build. Each Cloud Run service selects its target:
#   docker build --target engine          -t egress-engine .
#   docker build --target market_data_mcp -t egress-market-data-mcp .
#   docker build --target news_mcp        -t egress-news-mcp .
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
COPY pyproject.toml README.md ./

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

# ---- Gateway / BFF (FastAPI, WebSocket hub, A2A) — Phase 3 ----
FROM base AS gateway
COPY gateway/ ./gateway/
RUN pip install ".[gateway]"
EXPOSE 8080
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
