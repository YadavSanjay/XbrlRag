-- ==========================================================================
-- mv_fiscal_period_bounds — fiscal <-> calendar translation  [Layer 2]
-- ==========================================================================
-- Translates {company, fiscal_year} into exact period bounds before any fact SQL
-- is issued (e.g. NVDA FY2026 -> 2025-01-27 .. 2026-01-25) and flags filers whose
-- fiscal year does not track the calendar. This stops the resolver silently
-- returning the wrong period when a user says "calendar 2025".
--
-- Reads the Layer-0 fiscal_calendar + entity_master + filing_registry tables.
--
-- Ordering: prefix 07_ places this BEFORE 10_mv_financial_facts —
--   nothing in scripts/ddl/mv/ reads it — it is queried directly by the resolver.
--
-- PROVENANCE: captured from the live xbrl_db with pg_get_viewdef() rather than
--   hand-written. This MV was created ad hoc and never committed, so a fresh
--   instance could not build 10_mv_financial_facts. This file is the canonical
--   definition; docs/design/XbrlRag_Materialized_Views.md §4 describes its
--   purpose.
-- ==========================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_fiscal_period_bounds CASCADE;

CREATE MATERIALIZED VIEW mv_fiscal_period_bounds AS
SELECT fc.cik,
    em.trading_symbol AS ticker,
    fc.fiscal_year,
    fc.fiscal_period,
    fc.period_start,
    fc.period_end,
    fc.fye_month,
    fc.fye_day,
    fc.fye_month <> 12 AS is_non_dec_fye,
    fc.fiscal_year <> EXTRACT(year FROM fc.period_end)::integer AS fy_label_differs_from_calendar_year,
    EXTRACT(year FROM fc.period_end)::integer AS period_end_calendar_year,
    ( SELECT fr.accession_no
           FROM filing_registry fr
          WHERE fr.cik = fc.cik AND fr.fiscal_year = fc.fiscal_year AND fr.fiscal_period = fc.fiscal_period AND fr.superseded_by IS NULL
          ORDER BY fr.filed_date DESC
         LIMIT 1) AS canonical_accession
   FROM fiscal_calendar fc
     LEFT JOIN entity_master em ON em.cik = fc.cik;

CREATE INDEX ix_mfpb_calyear ON public.mv_fiscal_period_bounds USING btree (cik, period_end_calendar_year);
CREATE INDEX ix_mfpb_lookup ON public.mv_fiscal_period_bounds USING btree (cik, fiscal_year);
CREATE INDEX ix_mfpb_ticker ON public.mv_fiscal_period_bounds USING btree (ticker, fiscal_year);
