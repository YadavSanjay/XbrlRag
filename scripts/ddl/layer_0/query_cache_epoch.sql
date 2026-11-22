-- ============================================================================
-- query_cache_epoch — cross-process cache-invalidation marker  [WS-4 / B1]
-- ============================================================================
-- The semantic query cache (src/pipelines/xbrl/query_cache.py) lives in-process in
-- the API/MCP server, but the publish gate that makes new data visible
-- (src/ingest/sec_edgar/xbrl/promotion.py) runs in a SEPARATE ingest process. A
-- direct in-process call can't cross that boundary, and there is no event bus.
--
-- This single-row table is the shared invalidation signal: the publish gate bumps
-- `epoch` whenever it publishes a filing; the query side reads it (cheap, TTL-cached)
-- and flushes its cache when the epoch changes. So a newly ingested/published filing
-- never serves a stale cached answer.
--
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/layer_0/query_cache_epoch.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS query_cache_epoch (
    singleton   BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    epoch       BIGINT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ensure the single row exists.
INSERT INTO query_cache_epoch (singleton, epoch) VALUES (TRUE, 0)
    ON CONFLICT (singleton) DO NOTHING;
