-- Enable pgvector for the history cache, the episode-corpus embeddings, and the
-- local memory fallback (calibration memory + scenario history). Runs once on
-- first container start.
CREATE EXTENSION IF NOT EXISTS vector;

-- MCP response cache. The market-data and news MCPs cache every external feed
-- response here keyed by (provider, symbol+period), so a run makes at most a few
-- real Alpha Vantage calls and serves every later window from cache (the free tier
-- is ~25 calls/day). The MCP backends also CREATE this table on first use, so a
-- pre-existing database without this line still works.
CREATE TABLE IF NOT EXISTS mcp_cache (
    provider   text        NOT NULL,
    cache_key  text        NOT NULL,
    payload    jsonb       NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, cache_key)
);
