-- ==========================================================================
-- mv_dei_metadata — pivoted DEI cover-page facts per accession  [Layer 1a]
-- ==========================================================================
-- Every accession-level attribute the public-schema plugin leaves NULL
-- (document_type, entity_name, fiscal year/period focus, period end, trading
-- symbol, filer category, public float, shares outstanding) recovered by
-- pivoting the DEI cover-page facts the filer tagged themselves. The semantic
-- schema populates these as real columns; the public schema does not, so every
-- consumer reads them from here instead of from `accession`.
--
-- Reads raw xbrlDB tables only (accession, context, element, fact, qname).
--
-- Ordering: prefix 01_ places this BEFORE 10_mv_financial_facts —
--   10_mv_financial_facts.sql and 11_mv_financial_facts_multidim.sql both JOIN it.
--
-- PROVENANCE: captured from the live xbrl_db with pg_get_viewdef() rather than
--   hand-written. This MV was created ad hoc and never committed, so a fresh
--   instance could not build 10_mv_financial_facts. This file is the canonical
--   definition; docs/design/XbrlRag_Materialized_Views.md §4 describes its
--   purpose.
-- ==========================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_dei_metadata CASCADE;

CREATE MATERIALIZED VIEW mv_dei_metadata AS
SELECT a.accession_id,
    a.filing_accession_number AS accession_no,
    lpad(ctx.entity_identifier::text, 10, '0'::text) AS cik,
    a.filing_date,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'DocumentType'::text) AS document_type,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'DocumentFiscalYearFocus'::text)::integer AS fiscal_year_focus,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'DocumentFiscalPeriodFocus'::text) AS fiscal_period_focus,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'DocumentPeriodEndDate'::text)::date AS document_period_end,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityRegistrantName'::text) AS registrant_name,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityCentralIndexKey'::text) AS reported_cik,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'TradingSymbol'::text) AS trading_symbol,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'SecurityExchangeName'::text) AS exchange,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityFilerCategory'::text) AS filer_category,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityWellKnownSeasonedIssuer'::text) AS is_wksi,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityVoluntaryFilers'::text) AS is_voluntary_filer,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntitySmallBusiness'::text) AS is_small_business,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityEmergingGrowthCompany'::text) AS is_emerging_growth,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityShellCompany'::text) AS is_shell,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'CurrentFiscalYearEndDate'::text) AS current_fye,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityCommonStockSharesOutstanding'::text)::numeric AS shares_outstanding,
    max(f.fact_value) FILTER (WHERE q.local_name::text = 'EntityPublicFloat'::text)::numeric AS public_float
   FROM accession a
     JOIN fact f ON f.accession_id = a.accession_id
     JOIN context ctx ON ctx.context_id = f.context_id
     JOIN element el ON el.element_id = f.element_id
     JOIN qname q ON q.qname_id = el.qname_id
  WHERE q.namespace::text ~~ 'http://xbrl.sec.gov/dei/%'::text AND ctx.entity_scheme::text = 'http://www.sec.gov/CIK'::text
  GROUP BY a.accession_id, a.filing_accession_number, ctx.entity_identifier, a.filing_date;

CREATE UNIQUE INDEX ix_mdei_pk ON public.mv_dei_metadata USING btree (accession_id);
CREATE INDEX ix_mdei_form ON public.mv_dei_metadata USING btree (document_type, fiscal_year_focus);
CREATE INDEX ix_mdei_lookup ON public.mv_dei_metadata USING btree (cik, fiscal_year_focus, document_type);
