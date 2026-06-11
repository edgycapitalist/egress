-- Enable pgvector for the history cache, the episode-corpus embeddings, and the
-- local memory fallback (calibration memory + scenario history). Runs once on
-- first container start.
CREATE EXTENSION IF NOT EXISTS vector;
