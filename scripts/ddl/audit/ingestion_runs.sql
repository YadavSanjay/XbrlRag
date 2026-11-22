-- Ingestion-side run lineage for the XBRL flow.
-- docs/design/XbrlRag_Observability_Tracing.md.
--
-- Durable record of every ingestion run (Arelle xbrl_db load + Qdrant
-- vector-DB load) so an operator can answer, after the fact: when did this run
-- happen, how long did it take, what did it process, and which filings failed —
-- with retryable (timeout) failures distinguished from permanent
-- (validation / config) ones.
--
--   * audit.ingestion_run       — one row per pipeline invocation (the rollup)
--   * audit.ingestion_run_item  — one row per filing processed in that run
--
-- Lives in the same `audit` schema as the query-side tables
-- (scripts/ddl/audit/query_audit.sql).
--
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/audit/ingestion_runs.sql

CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.ingestion_run (
    run_id          TEXT PRIMARY KEY,            -- uuid generated at run start
    pipeline        TEXT NOT NULL,               -- 'arelle_xbrl_ingest' | 'load_to_vectordb'
    scope           TEXT,                        -- human-readable scope (all / ticker=NVDA / accessions=…)
    dry_run         BOOLEAN NOT NULL DEFAULT false,
    status          TEXT NOT NULL,               -- OK | PARTIAL | ERROR
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    duration_ms     DOUBLE PRECISION,
    items_total     INT NOT NULL DEFAULT 0,
    items_succeeded INT NOT NULL DEFAULT 0,
    items_skipped   INT NOT NULL DEFAULT 0,
    items_failed    INT NOT NULL DEFAULT 0,
    counts          JSONB,                       -- full scalar stats snapshot (pipeline-specific)
    errors          JSONB,                       -- array of error strings
    host            TEXT,                         -- machine the run executed on
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ingestion_run_pipeline ON audit.ingestion_run (pipeline);
CREATE INDEX IF NOT EXISTS ix_ingestion_run_started  ON audit.ingestion_run (started_at);
CREATE INDEX IF NOT EXISTS ix_ingestion_run_status   ON audit.ingestion_run (status);

CREATE TABLE IF NOT EXISTS audit.ingestion_run_item (
    item_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES audit.ingestion_run (run_id) ON DELETE CASCADE,
    seq           INT NOT NULL,                  -- processing order within the run
    accession_no  TEXT,
    ticker        TEXT,
    fiscal_year   TEXT,
    form_type     TEXT,
    status        TEXT NOT NULL,                 -- success | skipped_duplicate | skipped_no_manifest
                                                 -- | skipped_unsupported | failed | dry_run | error
    failure_class TEXT,                          -- NULL when not failed; else:
                                                 -- timeout (retryable) | validation_error (permanent)
                                                 -- | arelle_not_found | subprocess_error | unhandled
    returncode    INT,
    error         TEXT,
    attempts      INT NOT NULL DEFAULT 1,        -- processing attempts (>1 => a retry happened)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

-- Idempotent add for databases created before `attempts` existed.
ALTER TABLE audit.ingestion_run_item ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS ix_ingestion_item_run     ON audit.ingestion_run_item (run_id);
CREATE INDEX IF NOT EXISTS ix_ingestion_item_status  ON audit.ingestion_run_item (status);
CREATE INDEX IF NOT EXISTS ix_ingestion_item_failure ON audit.ingestion_run_item (failure_class);
