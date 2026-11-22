-- Query-time observability persistence for the XBRL flow.
-- docs/design/XbrlRag_Observability_Tracing.md.
--
-- Durable, queryable home for the per-query telemetry that
-- src/observability/trace_context.py emits to the `xbrlrag.events` JSON stream:
--   * one `audit.query_audit` row per user query (the query_summary), and
--   * one `audit.query_event` row per structured event (decision / llm_call /
--     retry / numeric_lineage / verifier_error / query_start).
--
-- A dedicated `audit` schema keeps this separable from the Arelle public schema
-- and the XbrlRag Layer-0 tables (distinct grants / retention policy for a
-- financial-services audit trail). No FK to the fact tables — fact provenance is
-- carried inside `query_event.payload` (numeric_lineage.source_fact_ids).
--
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/audit/query_audit.sql

CREATE SCHEMA IF NOT EXISTS audit;

-- One row per query: the rollup that powers cost / latency / branch-mix dashboards.
CREATE TABLE IF NOT EXISTS audit.query_audit (
    query_id                TEXT PRIMARY KEY,           -- TraceContext.query_id (correlation id)
    user_id                 TEXT,
    query                   TEXT NOT NULL,
    started_at              TIMESTAMPTZ NOT NULL,       -- wall-clock query start
    total_latency_ms        DOUBLE PRECISION,
    llm_calls               INT  NOT NULL DEFAULT 0,
    total_prompt_tokens     INT  NOT NULL DEFAULT 0,
    total_completion_tokens INT  NOT NULL DEFAULT 0,
    total_tokens            INT  GENERATED ALWAYS AS (total_prompt_tokens + total_completion_tokens) STORED,
    total_cost_usd          NUMERIC(12, 6) NOT NULL DEFAULT 0,
    total_retries           INT  NOT NULL DEFAULT 0,
    branch                  TEXT,                       -- FACT_BRANCH_DIRECT / NARRATIVE_BRANCH / PLAN_GENERATOR
    intent                  TEXT,                       -- NUMERIC_LOOKUP / NUMERIC_COMPUTE / NARRATIVE / HYBRID
    degraded                BOOLEAN,                    -- routing degraded to narrative
    fully_answered          BOOLEAN,
    stage_latency_ms        JSONB,                      -- {stage: ms}
    error                   TEXT,                       -- non-null if the query raised
    answer                  JSONB,                      -- full final answer (XbrlRagResponse) for the Trace Explorer
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()  -- row insert time (~query end)
);

-- Additive migration for tables created before `answer` existed (CREATE TABLE
-- IF NOT EXISTS above is a no-op on an existing table, so add the column here).
ALTER TABLE audit.query_audit ADD COLUMN IF NOT EXISTS answer JSONB;

CREATE INDEX IF NOT EXISTS ix_query_audit_created_at ON audit.query_audit (created_at);
CREATE INDEX IF NOT EXISTS ix_query_audit_branch     ON audit.query_audit (branch);
CREATE INDEX IF NOT EXISTS ix_query_audit_user       ON audit.query_audit (user_id);

-- One row per structured event: drill-down + the numeric-lineage audit trail.
CREATE TABLE IF NOT EXISTS audit.query_event (
    event_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_id   TEXT NOT NULL REFERENCES audit.query_audit (query_id) ON DELETE CASCADE,
    seq        INT  NOT NULL,                           -- emission order within the query
    kind       TEXT NOT NULL,                           -- decision / llm_call / retry / numeric_lineage / ...
    stage      TEXT,
    payload    JSONB NOT NULL,                          -- the full event dict as emitted
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (query_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_query_event_query_id ON audit.query_event (query_id);
CREATE INDEX IF NOT EXISTS ix_query_event_kind     ON audit.query_event (kind);
-- Payload GIN index enables lineage drill-down, e.g. every numeric_lineage event
-- whose source_fact_ids reference a given fact:
--   WHERE kind = 'numeric_lineage' AND payload -> 'source_fact_ids' @> '{"...": 12345}'
CREATE INDEX IF NOT EXISTS ix_query_event_payload  ON audit.query_event USING GIN (payload);
