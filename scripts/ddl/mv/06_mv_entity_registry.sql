-- ==========================================================================
-- mv_entity_registry — company master: ticker/name -> CIK  [Layer 2]
-- ==========================================================================
-- Entity resolution without an LLM call: "Nvidia", "NVDA" or "Nvidia Corp" all
-- resolve to cik = '0001045810' via the trigram index on company_name. Also
-- surfaces the fiscal-year-end month so the plan generator can flag
-- non-December filers immediately.
--
-- Reads the Layer-0 entity_master + filing_registry tables directly: the Arelle
-- `entity` table is empty after a plugin load, and accession.entity_id /
-- entity_name / document_type are NULL without RSS, so the v1 join chain does
-- not work on this schema.
--
-- Ordering: prefix 06_ places this BEFORE 10_mv_financial_facts —
--   nothing in scripts/ddl/mv/ reads it — it is queried directly by the resolver.
--
-- PROVENANCE: captured from the live xbrl_db with pg_get_viewdef() rather than
--   hand-written. This MV was created ad hoc and never committed, so a fresh
--   instance could not build 10_mv_financial_facts. This file is the canonical
--   definition; docs/design/XbrlRag_Materialized_Views.md §4 describes its
--   purpose.
-- ==========================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_entity_registry CASCADE;

CREATE MATERIALIZED VIEW mv_entity_registry AS
SELECT cik,
    trading_symbol AS ticker,
    company_name,
    exchange,
    sic_code,
    filer_category,
    fye_month AS fiscal_year_end_month,
    fye_day AS fiscal_year_end_day,
    last_seen_fy AS latest_10k_fy,
    ( SELECT count(*) AS count
           FROM filing_registry fr
          WHERE fr.cik = em.cik AND (fr.form_type ~~ '10-K%'::text OR fr.form_type ~~ '20-F%'::text)) AS filings_indexed,
    ( SELECT max(fr.filed_date) AS max
           FROM filing_registry fr
          WHERE fr.cik = em.cik AND (fr.form_type ~~ '10-K%'::text OR fr.form_type ~~ '20-F%'::text)) AS last_filing_date
   FROM entity_master em;

CREATE UNIQUE INDEX ix_mer_cik ON public.mv_entity_registry USING btree (cik);
CREATE INDEX ix_mer_name_trgm ON public.mv_entity_registry USING gin (lower(company_name) gin_trgm_ops);
CREATE INDEX ix_mer_ticker ON public.mv_entity_registry USING btree (ticker);
