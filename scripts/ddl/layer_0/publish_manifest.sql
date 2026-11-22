-- ============================================================================
-- publish_manifest — per-filing publish gate record  [WS-1 / B5]
-- ============================================================================
-- The control-plane table that decides which ingested filings are visible to
-- queries. After a filing's facts are loaded and the gate MVs are refreshed, the
-- ingest publish gate (src/ingest/sec_edgar/xbrl/promotion.py) evaluates:
--
--   GATE-A (structural): mv_anchor_coverage.xmlid_coverage_pct >= 0.95
--                        (a low value == a wrong-source-file load).
--   GATE-B (semantic)  : mv_filing_completeness.coverage_pct >= 0.80  AND
--                        no new strict calc failure (mv_sum_of_parts_checks,
--                        per accession, minus configs/ingest/calc_c11_exceptions.csv).
--
-- is_published = gate_a_passed AND gate_b_passed. One row per evaluated filing
-- (both passed and held-back), so the manifest is a complete audit record.
--
-- ENFORCEMENT (read-time, per-filing deny-list): the factstore query surface
-- (xbrl_factstore_lookup / xbrl_factstore_screen) excludes any fact whose
-- (cik, fiscal_year, fiscal_period) has an is_published = FALSE row here. A
-- filing with NO manifest row is served (backward-compatible: the pre-existing
-- corpus keeps working without a backfill); a filing explicitly held back is
-- invisible to every query path. Its facts remain physically present in the
-- fact store / MVs — the gate is a read-time filter, not a delete.
--
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/layer_0/publish_manifest.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS publish_manifest (
    accession_no          TEXT NOT NULL PRIMARY KEY,   -- = accession.filing_accession_number
    cik                   TEXT NOT NULL,
    fiscal_year           INT,
    fiscal_period         TEXT,                         -- FY | Q1 | Q2 | Q3
    form_type             TEXT,
    is_published          BOOLEAN NOT NULL,             -- gate_a_passed AND gate_b_passed
    gate_a_passed         BOOLEAN NOT NULL,
    gate_b_passed         BOOLEAN NOT NULL,
    anchor_coverage_pct   DOUBLE PRECISION,             -- GATE-A signal
    completeness_pct      DOUBLE PRECISION,             -- GATE-B signal
    sum_of_parts_failures INT,                          -- GATE-B: new strict calc failures (0 == clean)
    held_back_reasons     JSONB,                        -- array of human-readable reason strings
    run_id                TEXT,                         -- audit.ingestion_run.run_id that evaluated it
    evaluated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Read-time enforcement lookups by the query surface (keyed on the filing grain).
CREATE INDEX IF NOT EXISTS ix_pm_period
    ON publish_manifest (cik, fiscal_year, fiscal_period);
-- Held-back deny-list — the only rows the read-time gate cares about.
CREATE INDEX IF NOT EXISTS ix_pm_held_back
    ON publish_manifest (cik, fiscal_year, fiscal_period)
    WHERE is_published = FALSE;
CREATE INDEX IF NOT EXISTS ix_pm_run ON publish_manifest (run_id);
